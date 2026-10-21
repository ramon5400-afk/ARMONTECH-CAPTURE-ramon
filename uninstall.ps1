#Requires -RunAsAdministrator
# Remove o ScreenCam (tarefa, firewall e processos). Os arquivos em C:\ScreenCam sao mantidos.
Stop-ScheduledTask -TaskName 'ScreenCam' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'ScreenCam' -Confirm:$false -ErrorAction SilentlyContinue
Get-Process sc_ffmpeg, sc_mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -match 'screencam\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-NetFirewallRule -DisplayName 'ScreenCam*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host 'ScreenCam removido. Para apagar tudo: Remove-Item C:\ScreenCam -Recurse'
