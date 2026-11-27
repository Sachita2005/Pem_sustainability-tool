# Starts the PEM Sustainability app. Settings are read from .env in this folder.
#   .\run.ps1            start the server and open the browser
#   .\run.ps1 -NoBrowser start the server only
param([switch]$NoBrowser)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Python environment not found. Run:  python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

$hostName = "127.0.0.1"; $port = "8800"
Get-Content (Join-Path $root ".env") -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*PEM_HOST\s*=\s*(.+)$') { $hostName = $Matches[1].Trim() }
    if ($_ -match '^\s*PEM_PORT\s*=\s*(.+)$') { $port = $Matches[1].Trim() }
}

if (-not $NoBrowser) {
    $shown = if ($hostName -eq "0.0.0.0") { "127.0.0.1" } else { $hostName }
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 3; Start-Process $u } -ArgumentList "http://${shown}:$port/" | Out-Null
}

Set-Location (Join-Path $root "backend")
& $python -m uvicorn pem.app:app --host $hostName --port $port
