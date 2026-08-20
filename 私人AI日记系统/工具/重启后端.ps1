$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $ProjectRoot "backend"
$PythonExe = Join-Path $BackendDir ".venv\Scripts\python.exe"
$ServerLog = Join-Path $BackendDir "server.log"
$ServerErrLog = Join-Path $BackendDir "server.err.log"

function Test-HttpOk {
    param([string]$Url)
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

Write-Host "Restarting Mio backend only. NapCat and QQ will stay running."

$backendProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*uvicorn app.main:app*" }

foreach ($process in $backendProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python venv not found: $PythonExe"
}

Start-Process `
    -FilePath $PythonExe `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $ServerLog `
    -RedirectStandardError $ServerErrLog

Start-Sleep -Seconds 5

if (Test-HttpOk "http://127.0.0.1:8000/onebot/status") {
    Write-Host "Backend restarted."
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/onebot/status" -TimeoutSec 5
        Write-Host "OneBot websocket connections: $($status.websocket_connections)"
    } catch {
        Write-Host "Backend responded, but status check failed."
    }
} else {
    Write-Host "Backend restart was requested, but it is not responding yet. Check: $ServerErrLog"
}
