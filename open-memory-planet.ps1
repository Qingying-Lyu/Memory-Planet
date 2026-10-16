param([switch]$BackgroundOnly)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = 'D:\Apps\Python\installhere\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = (Get-Command python).Source }
$running = $false
try { $running = (Invoke-RestMethod -Uri 'http://127.0.0.1:18766/health' -TimeoutSec 2).status -eq 'ok' } catch { }
if (-not $running) {
    New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
    $taskBackend = Start-Process -FilePath $taskPython -ArgumentList '-m','backend.dev' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data/backend-stdout.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data/backend-stderr.log') -PassThru
    @{ pid = $taskBackend.Id; started_utc = $taskBackend.StartTime.ToUniversalTime().ToString('o'); root = $PSScriptRoot } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data/backend-process.json') -Encoding UTF8
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        try { $running = (Invoke-RestMethod -Uri 'http://127.0.0.1:18766/health' -TimeoutSec 1).status -eq 'ok' } catch { }
        if ($running) { break }
    }
}
if (-not $running) { throw '记忆星球服务启动失败，请查看 data/backend-stderr.log。' }
if ($BackgroundOnly) { exit 0 }
$taskLink = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'data/lab-url.txt') -Raw).Trim()
if (-not ($taskLink.StartsWith('http://127.0.0.1:18766/demo/index.html#token=') -or $taskLink.StartsWith('http://127.0.0.1:18766/lab#token='))) { throw '本机访问链接无效。' }
Start-Process $taskLink
