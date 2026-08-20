$ErrorActionPreference = "Stop"

# Keep this loader ASCII-only for Windows PowerShell 5.1 compatibility.
$MioEnvironmentScriptName = ([char]0x6FAA).ToString() + ([char]0x73AF).ToString() + ([char]0x5883).ToString() + ".ps1"
. (Join-Path $PSScriptRoot $MioEnvironmentScriptName)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPython = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"

$backendProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "python.exe" -and
        ($_.CommandLine -like "*uvicorn app.main:app*" -or $_.ExecutablePath -eq $BackendPython)
    }

foreach ($process in $backendProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

$napcatProcesses = Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -like "$NapCatDir*" }

foreach ($process in $napcatProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "Mio backend and NapCat processes stopped."
Write-Host "Note: stopping NapCat/QQ may make QQ require QR login next time."
