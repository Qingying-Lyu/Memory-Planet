param([switch]$BackgroundOnly)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython=Join-Path $taskRoot 'runtime/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {throw '运行环境不完整，请完整解压ZIP后启动。'}
try {$taskProbe=Join-Path $taskRoot '.write-check';[IO.File]::WriteAllText($taskProbe,'ok');Remove-Item -LiteralPath $taskProbe} catch {throw '请解压到有写入权限的普通目录，例如D盘文件夹。'}
$env:PYTHONHOME=Join-Path $taskRoot 'runtime'
$env:PYTHONNOUSERSITE='1'
$env:PYTHONPATH=''
$env:PYTHONSAFEPATH=''
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $taskRoot 'browsers'
$env:IMAGEIO_FFMPEG_EXE=(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'runtime/Lib/site-packages/imageio_ffmpeg/binaries') -Filter '*win*.exe' | Select-Object -First 1).FullName
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'data') -Force | Out-Null
$taskRecordPath=Join-Path $taskRoot 'data/backend-process.json'
$taskLinkPath=Join-Path $taskRoot 'data/lab-url.txt'
function Test-OwnService {
    try {
        $taskRecord=Get-Content -LiteralPath $taskRecordPath -Raw | ConvertFrom-Json
        if ($taskRecord.root -ne $taskRoot) {return $false}
        $taskP=Get-Process -Id ([int]$taskRecord.pid) -ErrorAction Stop
        if ($taskP.StartTime.ToUniversalTime().Ticks -ne ([datetime]::Parse($taskRecord.started_utc).ToUniversalTime().Ticks)) {return $false}
        $taskDetails=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskRecord.pid)"
        if ($taskDetails.ExecutablePath -ne $taskPython -or $taskDetails.CommandLine -notmatch 'backend\.dev') {return $false}
        $taskLink=(Get-Content -LiteralPath $taskLinkPath -Raw).Trim()
        if ($taskLink -notmatch '^http://127\.0\.0\.1:(18[78][0-9]{2})/entry#token=([A-Za-z0-9_-]+)$') {return $false}
        $taskBase=($taskLink -split '/entry#')[0];$taskToken=($taskLink -split '#token=')[1]
        $null=Invoke-RestMethod -Uri ($taskBase+'/setup/status') -Headers @{Authorization=('Bearer '+$taskToken)} -TimeoutSec 2
        return $true
    } catch {return $false}
}
if (-not (Test-OwnService)) {
    # Never start a second instance over the same writable data directory.
    if (Test-Path -LiteralPath $taskRecordPath) {
        try {
            $taskOld=Get-Content -LiteralPath $taskRecordPath -Raw | ConvertFrom-Json
            $taskOldP=Get-Process -Id ([int]$taskOld.pid) -ErrorAction Stop
            if ($taskOldP.StartTime.ToUniversalTime().Ticks -eq ([datetime]::Parse($taskOld.started_utc).ToUniversalTime().Ticks)) {throw 'OWN_PROCESS_STILL_RUNNING'}
        } catch {if ($_.Exception.Message -match 'OWN_PROCESS_STILL_RUNNING') {throw '本目录后台仍在启动或旧目录后台仍运行，请先关闭后台后再启动。'}}
    }
    Remove-Item -LiteralPath $taskLinkPath -ErrorAction SilentlyContinue
    $taskBackend=Start-Process -FilePath $taskPython -ArgumentList '-m','backend.dev' -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'data/backend-stdout.log') -RedirectStandardError (Join-Path $taskRoot 'data/backend-stderr.log') -PassThru
    @{pid=$taskBackend.Id;started_utc=$taskBackend.StartTime.ToUniversalTime().ToString('o');root=$taskRoot} | ConvertTo-Json | Set-Content -LiteralPath $taskRecordPath -Encoding UTF8
    $taskReady=$false
    for ($taskAttempt=0;$taskAttempt -lt 120;$taskAttempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-OwnService) {$taskReady=$true;break}
        if ($taskBackend.HasExited) {break}
    }
    if (-not $taskReady) {throw '记忆星球启动未完成，请查看data/backend-stderr.log。'}
}
if (-not $BackgroundOnly) {Start-Process ((Get-Content -LiteralPath $taskLinkPath -Raw).Trim())}
