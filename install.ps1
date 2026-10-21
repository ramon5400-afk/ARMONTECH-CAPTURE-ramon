#Requires -RunAsAdministrator
# ScreenCam - instalador. Clique direito > "Executar com o PowerShell" (como administrador).
$ErrorActionPreference = 'Stop'
$dest = 'C:\ScreenCam'
$bin  = Join-Path $dest 'bin'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 1) Python
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1).Source
if (-not $py) {
    Write-Host 'Python nao encontrado. Instale com:  winget install Python.Python.3.12  (marque "Add to PATH") e rode este script de novo.' -ForegroundColor Red
    exit 1
}

# 2) Arquivos
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'screencam.py') $dest -Force
icacls $dest /grant 'Users:(OI)(CI)M' | Out-Null   # o painel precisa gravar config/logs

# 3) Para o programa se ja estiver rodando
Stop-ScheduledTask -TaskName 'ScreenCam' -ErrorAction SilentlyContinue
Get-Process sc_ffmpeg, sc_mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force

# 4) MediaMTX (servidor RTSP)
if (-not (Test-Path (Join-Path $bin 'sc_mediamtx.exe'))) {
    Write-Host 'Baixando MediaMTX...'
    $z = Join-Path $env:TEMP 'mediamtx.zip'; $x = Join-Path $env:TEMP 'mediamtx_x'
    Invoke-WebRequest 'https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_windows_amd64.zip' -OutFile $z
    Expand-Archive $z $x -Force
    Copy-Item (Join-Path $x 'mediamtx.exe') (Join-Path $bin 'sc_mediamtx.exe') -Force
}

# 5) FFmpeg (captura)
if (-not (Test-Path (Join-Path $bin 'sc_ffmpeg.exe'))) {
    Write-Host 'Baixando FFmpeg (pode demorar)...'
    $z = Join-Path $env:TEMP 'ffmpeg.zip'; $x = Join-Path $env:TEMP 'ffmpeg_x'
    Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z
    Expand-Archive $z $x -Force
    $f = Get-ChildItem $x -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $f.FullName (Join-Path $bin 'sc_ffmpeg.exe') -Force
}
foreach ($b in 'sc_mediamtx.exe', 'sc_ffmpeg.exe') {
    if (-not (Test-Path (Join-Path $bin $b))) { Write-Host "Falta bin\$b. Copie manualmente e rode de novo." -ForegroundColor Red; exit 1 }
}

# 6) Firewall (RTSP 554, ONVIF 8080, painel 8090, RTP/UDP 8000-8001)
Get-NetFirewallRule -DisplayName 'ScreenCam*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'ScreenCam TCP' -Direction Inbound -Protocol TCP -LocalPort 554, 8080, 8090 -Action Allow -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'ScreenCam UDP' -Direction Inbound -Protocol UDP -LocalPort 8000, 8001 -Action Allow -Profile Any | Out-Null

# 7) Inicio automatico ao fazer logon (a captura precisa da sessao do usuario)
$a = New-ScheduledTaskAction -Execute $py -Argument "`"$dest\screencam.py`"" -WorkingDirectory $dest
$t = New-ScheduledTaskTrigger -AtLogOn
$s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
     -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'ScreenCam' -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName 'ScreenCam'

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127|169)' } | Select-Object -First 1).IPAddress
Write-Host ''
Write-Host 'ScreenCam instalado e em execucao.' -ForegroundColor Green
Write-Host "Painel:  http://${ip}:8090   (usuario admin / senha admin123 - TROQUE no painel)"
Write-Host "No DVR:  protocolo Onvif, IP $ip, porta HTTP 8080, porta RTSP 554, usuario/senha acima."
