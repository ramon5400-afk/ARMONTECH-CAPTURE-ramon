#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScreenCam - publica a tela do PC como uma camera IP (ONVIF + RTSP) para DVR/NVR.

  * Servidor ONVIF (porta 8080)  -> o DVR cadastra o PC como dispositivo ONVIF
  * Servidor RTSP  (porta 554)   -> MediaMTX entrega o video ao DVR (com usuario/senha)
  * Captura        (FFmpeg)      -> gdigrab -> H.264 -> MediaMTX
  * Painel web     (porta 8090)  -> configuracao e status
  * Watchdog                     -> reinicia captura/servidor RTSP se cairem

Somente biblioteca padrao do Python (3.9+). Binarios externos em .\\bin:
  sc_mediamtx.exe  (MediaMTX renomeado)   sc_ffmpeg.exe (FFmpeg renomeado)
"""
import base64
import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
BIN = os.path.join(BASE, "bin")
CFG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "screencam.log")
FF_LOG = os.path.join(BASE, "ffmpeg.log")
MTX_LOG = os.path.join(BASE, "mediamtx.log")
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""
TEST_MODE = "--test" in sys.argv  # sobe so ONVIF + painel (sem FFmpeg/MediaMTX)

DEFAULTS = {
    "device_name": "PDV",
    "username": "admin",
    "password": "admin123",
    "monitor": "primary",      # primary | all
    "fps": 8,
    "width": 1280,             # largura de saida (0 = original)
    "bitrate_kbps": 1500,
    "rtsp_port": 554,
    "onvif_port": 8080,
    "panel_port": 8090,
}

_cfg_lock = threading.Lock()
_cfg = dict(DEFAULTS)


# ----------------------------------------------------------------- utilitarios
def log(msg):
    line = "%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if not IS_WIN or sys.stdout:
        try:
            sys.stdout.write(line)
        except Exception:
            pass


def load_cfg():
    global _cfg
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    with _cfg_lock:
        _cfg = cfg
    save_cfg(cfg)


def save_cfg(cfg):
    global _cfg
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CFG_PATH)
    with _cfg_lock:
        _cfg = dict(cfg)


def get_cfg():
    with _cfg_lock:
        return dict(_cfg)


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def screen_geometry(cfg):
    """(x, y, w, h) da area capturada. Em Windows usa a API; senao 1920x1080."""
    x = y = 0
    w, h = 1920, 1080
    if IS_WIN:
        try:
            import ctypes
            u = ctypes.windll.user32
            try:
                u.SetProcessDPIAware()
            except Exception:
                pass
            if cfg["monitor"] == "all":
                x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
                w, h = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            else:
                w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        except Exception:
            pass
    return x, y, w, h


def out_size(cfg):
    _, _, w, h = screen_geometry(cfg)
    ow = int(cfg["width"]) if int(cfg["width"]) > 0 else w
    ow = max(2, ow // 2 * 2)
    oh = max(2, int(round(h * ow / float(w) / 2.0)) * 2)
    return ow, oh


# --------------------------------------------------------------------- engine
class Engine:
    """Gerencia MediaMTX + FFmpeg e reinicia se algum cair (watchdog)."""

    def __init__(self):
        self.mtx = None
        self.ff = None
        self.lock = threading.RLock()
        self.ff_restarts = 0
        self.mtx_restarts = 0
        self.t0 = time.time()
        self.ff_started = 0.0
        self.stop_flag = False

    def _spawn(self, args, logfile):
        if os.path.exists(logfile) and os.path.getsize(logfile) > 2_000_000:
            open(logfile, "wb").close()
        lf = open(logfile, "ab")
        flags = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
        return subprocess.Popen(args, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
                                creationflags=flags, cwd=BIN)

    def _write_mtx_cfg(self, c):
        q = json.dumps  # string JSON e YAML valido (aspas duplas)
        yml = (
            "logLevel: warn\n"
            "rtsp: yes\n"
            "rtspAddress: :%d\n"
            "protocols: [tcp, udp]\n"
            "rtmp: no\nhls: no\nwebrtc: no\nsrt: no\napi: no\nmetrics: no\npprof: no\nplayback: no\n"
            "authMethod: internal\n"
            "authInternalUsers:\n"
            "- user: any\n"
            "  pass:\n"
            "  ips: ['127.0.0.1', '::1']\n"
            "  permissions:\n"
            "  - action: publish\n"
            "    path: live\n"
            "  - action: read\n"
            "    path: live\n"
            "- user: %s\n"
            "  pass: %s\n"
            "  ips: []\n"
            "  permissions:\n"
            "  - action: read\n"
            "    path: live\n"
            "paths:\n"
            "  live:\n"
        ) % (int(c["rtsp_port"]), q(c["username"]), q(c["password"]))
        with open(os.path.join(BIN, "mediamtx.yml"), "w", encoding="utf-8") as f:
            f.write(yml)

    def _start_mtx(self, c):
        self._write_mtx_cfg(c)
        self.mtx = self._spawn([os.path.join(BIN, "sc_mediamtx" + EXE), "mediamtx.yml"], MTX_LOG)
        log("MediaMTX iniciado (pid %s)" % self.mtx.pid)

    def _start_ff(self, c):
        fps = int(c["fps"])
        x, y, w, h = screen_geometry(c)
        ow, oh = out_size(c)
        cmd = [os.path.join(BIN, "sc_ffmpeg" + EXE), "-hide_banner", "-nostats", "-loglevel", "warning",
               "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1"]
        if c["monitor"] != "all":
            cmd += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", "%dx%d" % (w, h)]
        cmd += ["-i", "desktop", "-vf", "scale=%d:%d" % (ow, oh),
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "main", "-pix_fmt", "yuv420p", "-g", str(fps * 2),
                "-b:v", "%dk" % c["bitrate_kbps"], "-maxrate", "%dk" % c["bitrate_kbps"],
                "-bufsize", "%dk" % (int(c["bitrate_kbps"]) * 2), "-an",
                "-f", "rtsp", "-rtsp_transport", "tcp",
                "rtsp://127.0.0.1:%d/live" % int(c["rtsp_port"])]
        self.ff = self._spawn(cmd, FF_LOG)
        self.ff_started = time.time()
        log("FFmpeg iniciado (pid %s): %dx%d @%dfps %dkbps" % (self.ff.pid, ow, oh, fps, c["bitrate_kbps"]))

    @staticmethod
    def _kill(p):
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    def kill_orphans(self):
        if IS_WIN:
            for name in ("sc_ffmpeg.exe", "sc_mediamtx.exe"):
                subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, creationflags=0x08000000)

    def start_all(self):
        with self.lock:
            c = get_cfg()
            self.kill_orphans()
            self._start_mtx(c)
            time.sleep(1.5)
            self._start_ff(c)

    def stop_all(self):
        with self.lock:
            self._kill(self.ff)
            self._kill(self.mtx)
            self.ff = self.mtx = None

    def restart_all(self):
        log("Reiniciando captura e servidor RTSP")
        self.stop_all()
        time.sleep(1)
        self.start_all()

    def alive(self):
        return (self.mtx is not None and self.mtx.poll() is None,
                self.ff is not None and self.ff.poll() is None)

    def watchdog(self):
        while not self.stop_flag:
            time.sleep(3)
            try:
                with self.lock:
                    c = get_cfg()
                    if self.mtx is None or self.mtx.poll() is not None:
                        self.mtx_restarts += 1
                        log("MediaMTX parou (codigo %s) - reiniciando" % (self.mtx.poll() if self.mtx else "?"))
                        self._kill(self.ff)
                        self._start_mtx(c)
                        time.sleep(1.5)
                        self._start_ff(c)
                    elif self.ff is None or self.ff.poll() is not None:
                        # espera um pouco entre tentativas para nao entrar em loop apertado
                        if time.time() - self.ff_started > 3:
                            self.ff_restarts += 1
                            log("FFmpeg parou (codigo %s) - reiniciando. Ultimas linhas: %s" % (
                                self.ff.poll() if self.ff else "?", " | ".join(tail(FF_LOG, 3).splitlines())))
                            self._start_ff(c)
            except Exception as e:  # watchdog nunca pode morrer
                log("erro no watchdog: %r" % e)


ENGINE = Engine()


def restart_self():
    """Reinicia o proprio programa (usado quando mudam as portas)."""
    log("Reiniciando o programa")
    ENGINE.stop_all()
    subprocess.Popen([sys.executable] + sys.argv, cwd=BASE,
                     creationflags=(0x00000008 | 0x08000000) if IS_WIN else 0)
    os._exit(0)


# ---------------------------------------------------------------------- ONVIF
NS = ('xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
      'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
      'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
      'xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" '
      'xmlns:tt="http://www.onvif.org/ver10/schema" '
      'xmlns:ter="http://www.onvif.org/ver10/error"')
PUBLIC_ACTIONS = {"GetSystemDateAndTime", "GetCapabilities", "GetServices", "GetServiceCapabilities"}


def lname(tag):
    return tag.rsplit("}", 1)[-1]


def envelope(body):
    return ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope %s><s:Body>%s</s:Body></s:Envelope>'
            % (NS, body)).encode("utf-8")


def fault(subcode, text):
    return envelope('<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>%s</s:Value></s:Subcode>'
                    '</s:Code><s:Reason><s:Text xml:lang="en">%s</s:Text></s:Reason></s:Fault>'
                    % (subcode, escape(text)))


def check_auth(root, cfg):
    """WS-UsernameToken (PasswordDigest ou PasswordText)."""
    tok = None
    for el in root.iter():
        if lname(el.tag) == "UsernameToken":
            tok = el
            break
    if tok is None:
        return False
    f = {lname(e.tag): e for e in tok}
    try:
        if f["Username"].text != cfg["username"]:
            return False
        pw = f["Password"]
        if "PasswordDigest" in pw.get("Type", ""):
            nonce = base64.b64decode(f["Nonce"].text)
            created = (f["Created"].text or "").encode()
            d = base64.b64encode(hashlib.sha1(nonce + created + cfg["password"].encode()).digest()).decode()
            return d == (pw.text or "").strip()
        return (pw.text or "") == cfg["password"]
    except (KeyError, TypeError, ValueError):
        return False


def vsc_xml(tag, ow, oh):
    return ('<%s token="vsc1"><tt:Name>VideoSource</tt:Name><tt:UseCount>1</tt:UseCount>'
            '<tt:SourceToken>vs1</tt:SourceToken><tt:Bounds x="0" y="0" width="%d" height="%d"/></%s>'
            % (tag, ow, oh, tag))


def vec_xml(tag, c, ow, oh):
    return ('<%s token="vec1"><tt:Name>H264</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding>'
            '<tt:Resolution><tt:Width>%d</tt:Width><tt:Height>%d</tt:Height></tt:Resolution>'
            '<tt:Quality>5</tt:Quality><tt:RateControl><tt:FrameRateLimit>%d</tt:FrameRateLimit>'
            '<tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>%d</tt:BitrateLimit></tt:RateControl>'
            '<tt:H264><tt:GovLength>%d</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>'
            '<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>'
            '<tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>'
            '<tt:SessionTimeout>PT60S</tt:SessionTimeout></%s>'
            % (tag, ow, oh, int(c["fps"]), int(c["bitrate_kbps"]), int(c["fps"]) * 2, tag))


def profile_xml(tag, c):
    ow, oh = out_size(c)
    return ('<%s fixed="true" token="profile1"><tt:Name>mainStream</tt:Name>%s%s</%s>'
            % (tag, vsc_xml("tt:VideoSourceConfiguration", ow, oh),
               vec_xml("tt:VideoEncoderConfiguration", c, ow, oh), tag))


def onvif_response(action, c, host):
    """Retorna (http_status, bytes)."""
    dev = "http://%s:%d/onvif/device_service" % (host, c["onvif_port"])
    med = "http://%s:%d/onvif/media_service" % (host, c["onvif_port"])
    img = "http://%s:%d/onvif/imaging_service" % (host, c["onvif_port"])
    ow, oh = out_size(c)
    if action == "GetSystemDateAndTime":
        n = datetime.datetime.now(datetime.timezone.utc)
        return 200, envelope(
            '<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:DateTimeType>NTP</tt:DateTimeType>'
            '<tt:DaylightSavings>false</tt:DaylightSavings><tt:TimeZone><tt:TZ>UTC0</tt:TZ></tt:TimeZone>'
            '<tt:UTCDateTime><tt:Time><tt:Hour>%d</tt:Hour><tt:Minute>%d</tt:Minute><tt:Second>%d</tt:Second></tt:Time>'
            '<tt:Date><tt:Year>%d</tt:Year><tt:Month>%d</tt:Month><tt:Day>%d</tt:Day></tt:Date></tt:UTCDateTime>'
            '</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>'
            % (n.hour, n.minute, n.second, n.year, n.month, n.day))
    if action == "GetCapabilities":
        return 200, envelope(
            '<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Device><tt:XAddr>%s</tt:XAddr></tt:Device>'
            '<tt:Media><tt:XAddr>%s</tt:XAddr><tt:StreamingCapabilities><tt:RTPMulticast>false</tt:RTPMulticast>'
            '<tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP></tt:StreamingCapabilities></tt:Media>'
            '<tt:Imaging><tt:XAddr>%s</tt:XAddr></tt:Imaging>'
            '</tds:Capabilities></tds:GetCapabilitiesResponse>' % (dev, med, img))
    if action == "GetServices":
        svc = ('<tds:Service><tds:Namespace>%s</tds:Namespace><tds:XAddr>%s</tds:XAddr>'
               '<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version></tds:Service>')
        return 200, envelope('<tds:GetServicesResponse>%s%s</tds:GetServicesResponse>' % (
            svc % ("http://www.onvif.org/ver10/device/wsdl", dev),
            svc % ("http://www.onvif.org/ver10/media/wsdl", med) +
            svc % ("http://www.onvif.org/ver20/imaging/wsdl", img)))
    if action == "GetServiceCapabilities":
        return 200, envelope('<tds:GetServiceCapabilitiesResponse><tds:Capabilities/></tds:GetServiceCapabilitiesResponse>')
    if action == "GetDeviceInformation":
        return 200, envelope(
            '<tds:GetDeviceInformationResponse><tds:Manufacturer>ScreenCam</tds:Manufacturer>'
            '<tds:Model>%s</tds:Model><tds:FirmwareVersion>1.0.0</tds:FirmwareVersion>'
            '<tds:SerialNumber>%s</tds:SerialNumber><tds:HardwareId>screencam-pc</tds:HardwareId>'
            '</tds:GetDeviceInformationResponse>'
            % (escape(c["device_name"]), hashlib.md5(socket.gethostname().encode()).hexdigest()[:12]))
    if action == "GetScopes":
        items = ["onvif://www.onvif.org/type/video_encoder", "onvif://www.onvif.org/Profile/Streaming",
                 "onvif://www.onvif.org/name/" + escape(c["device_name"].replace(" ", "_"))]
        return 200, envelope('<tds:GetScopesResponse>%s</tds:GetScopesResponse>' % "".join(
            '<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>%s</tt:ScopeItem></tds:Scopes>' % i
            for i in items))
    if action == "GetHostname":
        return 200, envelope('<tds:GetHostnameResponse><tds:HostnameInformation><tt:FromDHCP>false</tt:FromDHCP>'
                             '<tt:Name>%s</tt:Name></tds:HostnameInformation></tds:GetHostnameResponse>'
                             % escape(socket.gethostname()))
    if action == "GetProfiles":
        return 200, envelope('<trt:GetProfilesResponse>%s</trt:GetProfilesResponse>' % profile_xml("trt:Profiles", c))
    if action == "GetProfile":
        return 200, envelope('<trt:GetProfileResponse>%s</trt:GetProfileResponse>' % profile_xml("trt:Profile", c))
    if action == "GetStreamUri":
        return 200, envelope(
            '<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://%s:%d/live</tt:Uri>'
            '<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>'
            '<tt:Timeout>PT0S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse>' % (host, c["rtsp_port"]))
    if action == "GetVideoSources":
        return 200, envelope(
            '<trt:GetVideoSourcesResponse><trt:VideoSources token="vs1"><tt:Framerate>%d</tt:Framerate>'
            '<tt:Resolution><tt:Width>%d</tt:Width><tt:Height>%d</tt:Height></tt:Resolution></trt:VideoSources>'
            '</trt:GetVideoSourcesResponse>' % (int(c["fps"]), ow, oh))
    if action == "GetVideoSourceConfigurations":
        return 200, envelope('<trt:GetVideoSourceConfigurationsResponse>%s</trt:GetVideoSourceConfigurationsResponse>'
                             % vsc_xml("trt:Configurations", ow, oh))
    if action in ("GetVideoEncoderConfigurations", "GetCompatibleVideoEncoderConfigurations"):
        return 200, envelope('<trt:%sResponse>%s</trt:%sResponse>' % (action, vec_xml("trt:Configurations", c, ow, oh), action))
    if action == "GetVideoEncoderConfiguration":
        return 200, envelope('<trt:GetVideoEncoderConfigurationResponse>%s</trt:GetVideoEncoderConfigurationResponse>'
                             % vec_xml("trt:Configuration", c, ow, oh))
    if action == "GetImagingSettings":
        return 200, envelope(
            '<timg:GetImagingSettingsResponse><timg:ImagingSettings><tt:Brightness>50</tt:Brightness>'
            '<tt:ColorSaturation>50</tt:ColorSaturation><tt:Contrast>50</tt:Contrast><tt:Sharpness>50</tt:Sharpness>'
            '</timg:ImagingSettings></timg:GetImagingSettingsResponse>')
    if action == "GetOptions":
        rng = '<tt:%s><tt:Min>0</tt:Min><tt:Max>100</tt:Max></tt:%s>'
        return 200, envelope('<timg:GetOptionsResponse><timg:ImagingOptions>%s</timg:ImagingOptions></timg:GetOptionsResponse>'
                             % "".join(rng % (n, n) for n in ("Brightness", "ColorSaturation", "Contrast", "Sharpness")))
    if action == "SetImagingSettings":
        return 200, envelope('<timg:SetImagingSettingsResponse/>')
    if action == "GetVideoEncoderConfigurationOptions":
        return 200, envelope(
            '<trt:GetVideoEncoderConfigurationOptionsResponse><trt:Options>'
            '<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:QualityRange><tt:H264>'
            '<tt:ResolutionsAvailable><tt:Width>%d</tt:Width><tt:Height>%d</tt:Height></tt:ResolutionsAvailable>'
            '<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>60</tt:Max></tt:GovLengthRange>'
            '<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>30</tt:Max></tt:FrameRateRange>'
            '<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>'
            '<tt:H264ProfilesSupported>Main</tt:H264ProfilesSupported></tt:H264>'
            '</trt:Options></trt:GetVideoEncoderConfigurationOptionsResponse>' % (ow, oh))
    return 400, fault("ter:ActionNotSupported", "Action not supported: %s" % action)


class OnvifHandler(BaseHTTPRequestHandler):
    server_version = "ScreenCam"

    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/soap+xml; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, b"ScreenCam ONVIF", "text/plain")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return self._send(400, fault("ter:InvalidArgs", "Invalid XML"))
        action = None
        for el in root:
            if lname(el.tag) == "Body" and len(el):
                action = lname(el[0].tag)
        if not action:
            return self._send(400, fault("ter:InvalidArgs", "Empty request"))
        c = get_cfg()
        if action not in PUBLIC_ACTIONS and not check_auth(root, c):
            log("ONVIF: acesso negado (%s) de %s" % (action, self.client_address[0]))
            return self._send(400, fault("ter:NotAuthorized", "Sender not authorized"))
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0] or local_ip()
        code, data = onvif_response(action, c, host)
        if action not in ("GetSystemDateAndTime",):
            log("ONVIF: %s de %s -> %d" % (action, self.client_address[0], code))
        self._send(code, data)


# --------------------------------------------------------- WS-Discovery (Buscar no DVR)
WSD_GROUP = "239.255.255.250"
WSD_PORT = 3702
DEVICE_UUID = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "screencam-" + socket.gethostname() + "-%012x" % uuid.getnode()))


def ip_for(remote):
    """IP local da interface usada para falar com 'remote' (o DVR que fez a busca)."""
    try:
        t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        t.connect((remote, 9))
        ip = t.getsockname()[0]
        t.close()
        return ip
    except OSError:
        return local_ip()


def probe_match(relates_to, c, ip):
    name = quote(c["device_name"].replace(" ", "_"), safe="_-")
    scopes = ("onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/Profile/Streaming "
              "onvif://www.onvif.org/name/%s onvif://www.onvif.org/hardware/ScreenCam "
              "onvif://www.onvif.org/location/" % name)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
            'xmlns:dn="http://www.onvif.org/ver10/network/wsdl" '
            'xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><s:Header>'
            '<a:MessageID>urn:uuid:%s</a:MessageID><a:RelatesTo>%s</a:RelatesTo>'
            '<a:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:To>'
            '<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action></s:Header>'
            '<s:Body><d:ProbeMatches><d:ProbeMatch><a:EndpointReference><a:Address>%s</a:Address></a:EndpointReference>'
            '<d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types><d:Scopes>%s</d:Scopes>'
            '<d:XAddrs>http://%s:%d/onvif/device_service</d:XAddrs><d:MetadataVersion>1</d:MetadataVersion>'
            '</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>'
            % (uuid.uuid4(), escape(relates_to or ""), DEVICE_UUID, escape(scopes), ip, c["onvif_port"])).encode("utf-8")


def discovery_loop():
    """Responde as buscas ONVIF (WS-Discovery) para o PC aparecer na lista 'Buscar' do DVR."""
    s = None
    for attempt in range(20):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", WSD_PORT))
            break
        except OSError as e:
            s = None
            if attempt == 19:
                log("AVISO: descoberta automatica indisponivel (UDP %d): %s" % (WSD_PORT, e))
                return
            time.sleep(1)
    ips = set()
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    ips.add(local_ip())
    joined = 0
    for ip in ips:
        if ip.startswith("127."):
            continue
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                         socket.inet_aton(WSD_GROUP) + socket.inet_aton(ip))
            joined += 1
        except OSError:
            pass
    log("Descoberta automatica (WS-Discovery) ativa em %d interface(s)" % joined)
    while True:
        try:
            data, addr = s.recvfrom(65535)
            root = ET.fromstring(data)
        except (OSError, ET.ParseError):
            continue
        is_probe, msgid, types = False, None, ""
        for el in root.iter():
            n = lname(el.tag)
            if n == "Action" and (el.text or "").strip().endswith("/Probe"):
                is_probe = True
            elif n == "MessageID":
                msgid = (el.text or "").strip()
            elif n == "Types":
                types = el.text or ""
        if not is_probe or (types and "NetworkVideoTransmitter" not in types and "Device" not in types):
            continue
        try:
            time.sleep(0.05 + (uuid.uuid4().int % 100) / 1000.0)  # pequeno atraso aleatorio (padrao WS-Discovery)
            s.sendto(probe_match(msgid, get_cfg(), ip_for(addr[0])), addr)
            log("Descoberta: respondi a busca de %s" % addr[0])
        except OSError as e:
            log("Descoberta: falha ao responder %s: %s" % (addr[0], e))


# ------------------------------------------------------- teste de captura (painel)
_snap_lock = threading.Lock()


def snapshot():
    """Retorna (jpeg_bytes, origem, erro). Tenta a transmissao enviada ao DVR; se nao houver, captura direto."""
    c = get_cfg()
    ff = os.path.join(BIN, "sc_ffmpeg" + EXE)
    if not os.path.exists(ff):
        return None, None, "FFmpeg nao encontrado em bin\\"
    flags = 0x08000000 if IS_WIN else 0
    tries = [("stream", [ff, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
                         "-i", "rtsp://127.0.0.1:%d/live" % int(c["rtsp_port"]),
                         "-frames:v", "1", "-f", "mjpeg", "-q:v", "4", "pipe:1"])]
    x, y, w, h = screen_geometry(c)
    ow, oh = out_size(c)
    direct = [ff, "-hide_banner", "-loglevel", "error", "-f", "gdigrab", "-framerate", "1", "-draw_mouse", "1"]
    if c["monitor"] != "all":
        direct += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", "%dx%d" % (w, h)]
    direct += ["-i", "desktop", "-frames:v", "1", "-vf", "scale=%d:%d" % (ow, oh), "-f", "mjpeg", "-q:v", "4", "pipe:1"]
    tries.append(("direto", direct))
    err = ""
    with _snap_lock:
        for origem, cmd in tries:
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=10, creationflags=flags, cwd=BIN)
                if r.returncode == 0 and len(r.stdout) > 1000:
                    return r.stdout, origem, ""
                err = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:] or "sem imagem"
            except (subprocess.TimeoutExpired, OSError) as e:
                err = "tempo esgotado" if isinstance(e, subprocess.TimeoutExpired) else str(e)
    return None, None, err


# --------------------------------------------------------------------- painel
PANEL_HTML = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ScreenCam - Painel de Controle</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--tx:#1b2430;--mut:#667085;--bd:#d9dee5;--ac:#0b6bcb;--ok:#168a45;--er:#c0392b}
@media(prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18202a;--tx:#e6ebf1;--mut:#93a0b0;--bd:#2a3542;--ac:#4aa3ff;--ok:#3ecf79;--er:#ff6b5b}}
body{margin:0;font:15px system-ui,Segoe UI,Arial;background:var(--bg);color:var(--tx)}
main{max-width:760px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:14px}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>label{flex:1 1 200px}
label{display:block;font-size:13px;color:var(--mut)}
input,select{width:100%;box-sizing:border-box;margin-top:4px;padding:8px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--tx);font-size:15px}
button{padding:9px 16px;border:0;border-radius:6px;background:var(--ac);color:#fff;font-size:15px;cursor:pointer;margin-right:8px}
button.sec{background:transparent;color:var(--tx);border:1px solid var(--bd)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:var(--er)}.dot.on{background:var(--ok)}
pre{background:var(--bg);border:1px solid var(--bd);border-radius:6px;padding:10px;max-height:180px;overflow:auto;font-size:12px;white-space:pre-wrap}
code{background:var(--bg);padding:2px 5px;border-radius:4px}
#msg{margin-left:6px;color:var(--mut)}
</style></head><body><main>
<h1>ScreenCam - Painel de Controle</h1>
<div class="card"><b>Status</b>
 <p><span id="d1" class="dot"></span>Servidor RTSP &nbsp; <span id="d2" class="dot"></span>Captura de tela &nbsp;
 <span style="color:var(--mut)" id="up"></span></p>
 <p style="margin:0">No DVR: clique em <b>Buscar</b> (ele aparece na lista) ou use Ad. Manual: protocolo <b>Onvif</b>, IP <code id="ip"></code>, porta HTTP <code id="po"></code>,
 porta RTSP <code id="pr"></code>.<br>Teste no VLC: <code id="url"></code> &nbsp; Video: <span id="res"></span></p></div>
<div class="card"><b>Configuracao</b>
 <div class="row"><label>Nome do dispositivo<input id="device_name"></label>
 <label>Tela<select id="monitor"><option value="primary">Monitor principal</option><option value="all">Todos os monitores</option></select></label></div>
 <div class="row"><label>FPS<input id="fps" type="number" min="1" max="30"></label>
 <label>Largura de saida (0 = original)<input id="width" type="number" min="0" max="3840"></label>
 <label>Bitrate (kbps)<input id="bitrate_kbps" type="number" min="200" max="8000"></label></div>
 <div class="row"><label>Usuario<input id="username"></label>
 <label>Nova senha (vazio = manter)<input id="password" type="password" autocomplete="new-password"></label></div>
 <div class="row"><label>Porta RTSP<input id="rtsp_port" type="number"></label>
 <label>Porta ONVIF/HTTP<input id="onvif_port" type="number"></label>
 <label>Porta do painel<input id="panel_port" type="number"></label></div>
 <p><button onclick="save()">Salvar e aplicar</button><button class="sec" onclick="act('restart')">Reiniciar captura</button><span id="msg"></span></p></div>
<div class="card"><b>Ver o que está sendo capturado</b>
 <p style="margin:6px 0 10px;color:var(--mut)">Mostra a imagem que o DVR está recebendo (ou a captura direta da tela, se o vídeo ainda não estiver no ar).</p>
 <button onclick="shot()">Testar captura</button>
 <label style="display:inline;margin-left:6px"><input id="auto" type="checkbox" style="width:auto" onchange="auto()"> atualizar a cada 2 s</label>
 <span id="shotmsg" style="margin-left:8px;color:var(--mut)"></span>
 <img id="shot" alt="captura" style="display:none;max-width:100%;margin-top:12px;border:1px solid var(--bd);border-radius:6px"></div>
<div class="card"><b>Log do FFmpeg</b><pre id="log"></pre></div>
</main><script>
const F=['device_name','monitor','fps','width','bitrate_kbps','username','rtsp_port','onvif_port','panel_port'];
const $=i=>document.getElementById(i);
async function load(){const c=await (await fetch('/api/config')).json();F.forEach(k=>$(k).value=c[k]);}
async function status(){try{const s=await (await fetch('/api/status')).json();
$('d1').className='dot'+(s.rtsp?' on':'');$('d2').className='dot'+(s.capture?' on':'');
$('up').textContent='ativo ha '+s.uptime+' | reinicios: captura '+s.ff_restarts+', rtsp '+s.mtx_restarts;
$('ip').textContent=s.ip;$('po').textContent=s.onvif_port;$('pr').textContent=s.rtsp_port;
$('url').textContent='rtsp://'+s.ip+':'+s.rtsp_port+'/live';$('res').textContent=s.resolution;
$('log').textContent=s.log||'(sem mensagens)';}catch(e){}}
async function save(){const b={};F.forEach(k=>b[k]=$(k).value);if($('password').value)b.password=$('password').value;
$('msg').textContent='Aplicando...';const r=await fetch('/api/config',{method:'POST',body:JSON.stringify(b)});
const j=await r.json();$('msg').textContent=j.ok?(j.reload?'Salvo. Programa reiniciando, reabra a pagina na nova porta.':'Salvo e aplicado.'):('Erro: '+j.error);$('password').value='';}
async function act(a){await fetch('/api/'+a,{method:'POST'});$('msg').textContent='Reiniciando captura...';}
let timer=null,prev=null;
async function shot(){$('shotmsg').textContent='Capturando...';
 try{const r=await fetch('/api/snapshot?'+Date.now());
 if(!r.ok){let m='falha';try{m=(await r.json()).error}catch(e){}$('shotmsg').textContent='Erro: '+m;return;}
 const u=URL.createObjectURL(await r.blob());$('shot').src=u;$('shot').style.display='block';if(prev)URL.revokeObjectURL(prev);prev=u;
 $('shotmsg').textContent='Origem: '+(r.headers.get('X-Source')=='stream'?'vídeo enviado ao DVR':'captura direta da tela')+' - '+new Date().toLocaleTimeString();
 }catch(e){$('shotmsg').textContent='Erro ao capturar';}}
function auto(){clearInterval(timer);if($('auto').checked){shot();timer=setInterval(shot,2000);}}
load();status();setInterval(status,3000);
</script></body></html>"""


def tail(path, n=25):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 20000))
            lines = f.read().decode("utf-8", "replace").splitlines()
            lines = [l for l in lines if "not enough frames to estimate rate" not in l]  # ruido
            return "\n".join(lines[-n:])
    except OSError:
        return ""


def validate(new, old):
    out = dict(old)
    try:
        out["device_name"] = str(new.get("device_name", old["device_name"])).strip()[:40] or old["device_name"]
        out["username"] = str(new.get("username", old["username"])).strip()
        if "password" in new:
            out["password"] = str(new["password"])
        out["monitor"] = new.get("monitor", old["monitor"])
        for k, lo, hi in (("fps", 1, 30), ("bitrate_kbps", 200, 8000), ("width", 0, 3840),
                          ("rtsp_port", 1, 65535), ("onvif_port", 1, 65535), ("panel_port", 1, 65535)):
            out[k] = int(new.get(k, old[k]))
            if not lo <= out[k] <= hi:
                return None, "valor fora do limite em '%s' (%d a %d)" % (k, lo, hi)
    except (TypeError, ValueError):
        return None, "valor invalido"
    if out["width"] and out["width"] < 320:
        return None, "largura minima: 320 (ou 0)"
    if out["monitor"] not in ("primary", "all"):
        return None, "tela invalida"
    if not out["username"] or len(out["password"]) < 4 or any(ch in out["username"] + out["password"] for ch in ":\r\n"):
        return None, "usuario vazio, senha com menos de 4 caracteres ou caractere ':' proibido"
    if len({out["rtsp_port"], out["onvif_port"], out["panel_port"]}) < 3:
        return None, "as tres portas devem ser diferentes"
    return out, None


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "ScreenCam"

    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _auth(self):
        c = get_cfg()
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                if u == c["username"] and p == c["password"]:
                    return True
            except Exception:
                pass
        self._send(401, "Autenticacao necessaria", "text/plain; charset=utf-8",
                   {"WWW-Authenticate": 'Basic realm="ScreenCam"'})
        return False

    def do_GET(self):
        if not self._auth():
            return
        c = get_cfg()
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, PANEL_HTML, "text/html; charset=utf-8")
        if path == "/api/snapshot":
            img, origem, err = snapshot()
            if img is None:
                return self._send(503, {"error": err or "nao foi possivel capturar"})
            return self._send(200, img, "image/jpeg", {"X-Source": "stream" if origem == "stream" else "direto",
                                                       "Cache-Control": "no-store"})
        if path == "/api/config":
            return self._send(200, {k: v for k, v in c.items() if k != "password"})
        if path == "/api/status":
            rtsp, cap = ENGINE.alive()
            s = int(time.time() - ENGINE.t0)
            ow, oh = out_size(c)
            return self._send(200, {
                "rtsp": rtsp, "capture": cap, "ip": local_ip(), "onvif_port": c["onvif_port"],
                "rtsp_port": c["rtsp_port"], "resolution": "%dx%d @ %dfps" % (ow, oh, c["fps"]),
                "uptime": "%dh%02dm" % (s // 3600, s % 3600 // 60),
                "ff_restarts": ENGINE.ff_restarts, "mtx_restarts": ENGINE.mtx_restarts,
                "log": tail(FF_LOG) or tail(LOG_PATH)})
        self._send(404, {"error": "nao encontrado"})

    def do_POST(self):
        if not self._auth():
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path == "/api/restart":
            threading.Thread(target=ENGINE.restart_all if not TEST_MODE else (lambda: None), daemon=True).start()
            return self._send(200, {"ok": True})
        if self.path == "/api/config":
            try:
                new = json.loads(raw.decode("utf-8"))
            except ValueError:
                return self._send(400, {"ok": False, "error": "JSON invalido"})
            old = get_cfg()
            cfg, err = validate(new, old)
            if err:
                return self._send(400, {"ok": False, "error": err})
            save_cfg(cfg)
            log("Configuracao alterada pelo painel")
            ports = any(cfg[k] != old[k] for k in ("onvif_port", "panel_port"))
            self._send(200, {"ok": True, "reload": ports})
            if not TEST_MODE:
                threading.Thread(target=restart_self if ports else ENGINE.restart_all, daemon=True).start()
            return
        self._send(404, {"error": "nao encontrado"})


def serve(handler, port, name):
    for attempt in range(20):  # porta pode demorar a liberar apos reinicio
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            log("%s escutando na porta %d" % (name, port))
            return srv
        except OSError as e:
            if attempt == 19:
                log("ERRO: nao foi possivel abrir a porta %d (%s): %s" % (port, name, e))
                return None
            time.sleep(1)


def main():
    load_cfg()
    c = get_cfg()
    log("ScreenCam iniciando%s" % (" [modo teste]" if TEST_MODE else ""))
    serve(OnvifHandler, c["onvif_port"], "ONVIF")
    serve(PanelHandler, c["panel_port"], "Painel")
    threading.Thread(target=discovery_loop, daemon=True).start()
    if not TEST_MODE:
        for b in ("sc_mediamtx" + EXE, "sc_ffmpeg" + EXE):
            if not os.path.exists(os.path.join(BIN, b)):
                log("ERRO: arquivo ausente em bin\\%s - rode install.ps1" % b)
        ENGINE.start_all()
        threading.Thread(target=ENGINE.watchdog, daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        ENGINE.stop_flag = True
        ENGINE.stop_all()


if __name__ == "__main__":
    main()
