param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRecordPath = Join-Path $taskRoot 'data/backend-process.json'
if (-not (Test-Path -LiteralPath $taskRecordPath)) {
    Write-Host '没有本目录启动的后台记录，无需关闭。若后台由旧脚本或终端启动，请从原入口结束。'
    exit 0
}
$taskRecord = Get-Content -LiteralPath $taskRecordPath -Raw | ConvertFrom-Json
if ($taskRecord.root -ne $taskRoot) { throw '后台记录目录不匹配，未关闭任何进程。' }
$taskProcess = Get-Process -Id ([int]$taskRecord.pid) -ErrorAction SilentlyContinue
if (-not $taskProcess) { Write-Host '记忆星球后台已关闭。'; exit 0 }
$taskStarted = [datetime]::Parse($taskRecord.started_utc).ToUniversalTime()
if ($taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskStarted.Ticks) { throw '后台进程已变化，未关闭任何进程。' }
$taskDetails = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskRecord.pid)"
if ($taskDetails.CommandLine -notmatch '(?i)\s-m\s+backend\.dev(?:\s|$)') { throw '进程不是记忆星球后台，未关闭任何进程。' }
if ($CheckOnly) { Write-Host '后台身份检查通过；未关闭进程。'; exit 0 }
Write-Host '关闭将中断正在进行的材料处理和模型任务，已发出的模型请求可能仍计费。'
$taskAnswer = Read-Host '确认关闭记忆星球后台？输入 Y 确认'
if ($taskAnswer -ne 'Y') { Write-Host '已取消关闭。'; exit 0 }
# Recheck process identity after the user prompt to avoid stopping a reused PID.
$taskProcess = Get-Process -Id ([int]$taskRecord.pid) -ErrorAction SilentlyContinue
if (-not $taskProcess) { Write-Host '记忆星球后台已关闭。'; exit 0 }
if ($taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskStarted.Ticks) { throw '后台进程已变化，未关闭任何进程。' }
Stop-Process -InputObject $taskProcess -ErrorAction Stop
Write-Host '记忆星球后台已关闭。再次双击“打开记忆星球.cmd”即可启动。'
