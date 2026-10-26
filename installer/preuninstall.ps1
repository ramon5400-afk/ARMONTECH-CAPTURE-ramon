# Para o ScreenCam. Com -StopOnly apenas para (usado em atualizacoes); sem ele remove tarefa e firewall.
param([switch]$StopOnly)
$app = $PSScriptRoot
Stop-ScheduledTask -TaskName 'ScreenCam' -ErrorAction SilentlyContinue
Get-Process sc_ffmpeg, sc_mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -like "$app\python\*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if (-not $StopOnly) {
    Unregister-ScheduledTask -TaskName 'ScreenCam' -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName 'ScreenCam*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}
