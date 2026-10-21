# Gera installer\Output\ScreenCam_Setup.exe (rodar UMA vez, em um PC Windows com internet).
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$pl   = Join-Path $root 'installer\payload'
$tmp  = Join-Path $env:TEMP 'screencam_build'
New-Item -ItemType Directory -Force (Join-Path $pl 'bin'), $tmp | Out-Null

function Get-File($url, $out) { Write-Host "Baixando $url"; Invoke-WebRequest $url -OutFile $out -UseBasicParsing }

# Python embutido (o PC do cliente nao precisa ter Python)
if (-not (Test-Path (Join-Path $pl 'python\pythonw.exe'))) {
    $z = Join-Path $tmp 'py.zip'
    Get-File 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip' $z
    Expand-Archive $z (Join-Path $pl 'python') -Force
}
# MediaMTX
if (-not (Test-Path (Join-Path $pl 'bin\sc_mediamtx.exe'))) {
    $z = Join-Path $tmp 'mtx.zip'
    Get-File 'https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_windows_amd64.zip' $z
    Expand-Archive $z (Join-Path $tmp 'mtx') -Force
    Copy-Item (Join-Path $tmp 'mtx\mediamtx.exe') (Join-Path $pl 'bin\sc_mediamtx.exe') -Force
}
# FFmpeg
if (-not (Test-Path (Join-Path $pl 'bin\sc_ffmpeg.exe'))) {
    $z = Join-Path $tmp 'ff.zip'
    Get-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $z
    Expand-Archive $z (Join-Path $tmp 'ff') -Force
    $f = Get-ChildItem (Join-Path $tmp 'ff') -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $f.FullName (Join-Path $pl 'bin\sc_ffmpeg.exe') -Force
}

# Inno Setup (compilador)
function Find-Iscc {
    @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
      "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
$iscc = Find-Iscc
if (-not $iscc) {
    $setup = Join-Path $tmp 'innosetup.exe'
    Get-File 'https://jrsoftware.org/download.php/is.exe' $setup
    Start-Process $setup -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER' -Wait
    $iscc = Find-Iscc
}
if (-not $iscc) { throw 'Nao foi possivel instalar o Inno Setup. Instale manualmente (jrsoftware.org) e rode de novo.' }

& $iscc (Join-Path $root 'installer\ScreenCam.iss')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o instalador.' }
$out = Join-Path $root 'installer\Output\ScreenCam_Setup.exe'
Write-Host ''
Write-Host "PRONTO: $out" -ForegroundColor Green
Start-Process explorer.exe (Split-Path $out)
