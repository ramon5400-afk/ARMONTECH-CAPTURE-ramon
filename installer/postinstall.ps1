# Executado pelo instalador (como administrador). Nao precisa rodar manualmente.
param([string]$AppDir, [string]$DeviceName = '', [string]$User = 'admin', [string]$Pass = '')
$ErrorActionPreference = 'Stop'
$log = Join-Path $AppDir 'install.log'
try {
    # 1) configuracao inicial (somente se ainda nao existir)
    $cfgPath = Join-Path $AppDir 'config.json'
    if ($Pass -and -not (Test-Path $cfgPath)) {
        $cfg = [ordered]@{ device_name = $DeviceName; username = $User; password = $Pass }
        [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    }

    # 2) firewall
    Get-NetFirewallRule -DisplayName 'ScreenCam*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName 'ScreenCam TCP' -Direction Inbound -Protocol TCP -LocalPort 554, 8080, 8090 -Action Allow -Profile Any | Out-Null
    New-NetFirewallRule -DisplayName 'ScreenCam UDP' -Direction Inbound -Protocol UDP -LocalPort 8000, 8001, 3702 -Action Allow -Profile Any | Out-Null

    # 3) inicio automatico no logon do usuario que esta usando o PC
    $u = (Get-CimInstance Win32_ComputerSystem).UserName
    if (-not $u) { $u = "$env:USERDOMAIN\$env:USERNAME" }
    $a = New-ScheduledTaskAction -Execute (Join-Path $AppDir 'python\pythonw.exe') -Argument "`"$(Join-Path $AppDir 'screencam.py')`"" -WorkingDirectory $AppDir
    $t = New-ScheduledTaskTrigger -AtLogOn -User $u
    $s = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
         -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName 'ScreenCam' -Action $a -Trigger $t -Settings $s -Principal $p -Force | Out-Null
    Start-ScheduledTask -TaskName 'ScreenCam'
    "OK $(Get-Date) usuario=$u" | Out-File $log -Append
} catch {
    "ERRO $(Get-Date): $_" | Out-File $log -Append
    exit 1
}
