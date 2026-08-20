$ErrorActionPreference = "Stop"

$AppRoot = $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $AppRoot
$LegacyRoot = Get-ChildItem -LiteralPath $WorkspaceRoot -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "backend\app\main.py") } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $LegacyRoot) {
    throw "Mio backend project was not found."
}
$BackendDir = Join-Path $LegacyRoot "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$NodeExe = "D:\实用工具\Node\node.exe"
$ViteEntry = Join-Path $AppRoot "node_modules\vite\bin\vite.js"

function Test-LocalUrl {
    param([string]$Url)
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not (Test-LocalUrl "http://127.0.0.1:8000/health")) {
    Start-Process `
        -FilePath $PythonExe `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden
}

if (-not (Test-LocalUrl "http://127.0.0.1:1420")) {
    Start-Process `
        -FilePath $NodeExe `
        -ArgumentList $ViteEntry, "--host", "127.0.0.1", "--port", "1420" `
        -WorkingDirectory $AppRoot `
        -WindowStyle Hidden
}

$deadline = (Get-Date).AddSeconds(20)
do {
    if (Test-LocalUrl "http://127.0.0.1:1420") {
        Start-Process "http://127.0.0.1:1420"
        exit 0
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

throw "Mio Agent preview did not start in time."
