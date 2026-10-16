$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'backups') -Filter 'planet-*.zip' | Select-Object -ExpandProperty Name
$taskBackupName = Read-Host '请输入要恢复的完整备份文件名'
if ([IO.Path]::GetFileName($taskBackupName) -ne $taskBackupName) { throw '只接受备份文件名' }
$taskListener = Get-NetTCPConnection -LocalPort 18766 -State Listen -ErrorAction SilentlyContinue
foreach ($taskConnection in $taskListener) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskConnection.OwningProcess)"
    if ($taskProcess.CommandLine -match 'backend.dev' -and $taskProcess.ExecutablePath -eq 'D:\Apps\Python\installhere\python.exe') { Stop-Process -Id $taskConnection.OwningProcess }
}
& 'D:\Apps\Python\installhere\python.exe' -m backend.maintenance restore $taskBackupName
if ($LASTEXITCODE -ne 0) { throw '恢复未完成，当前数据或回滚数据仍保留在本机。' }
& (Join-Path $PSScriptRoot 'open-memory-planet.ps1')
