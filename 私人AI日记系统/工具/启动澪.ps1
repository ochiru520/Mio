$ErrorActionPreference = "Stop"

# Keep this loader ASCII-only for Windows PowerShell 5.1 compatibility.
$MioEnvironmentScriptName = ([char]0x6FAA).ToString() + ([char]0x73AF).ToString() + ([char]0x5883).ToString() + ".ps1"
. (Join-Path $PSScriptRoot $MioEnvironmentScriptName)

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

function Get-OneBotStatus {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:8000/onebot/status" -TimeoutSec 5
    } catch {
        return $null
    }
}

function Wait-OneBotConnection {
    param([int]$TimeoutSeconds = 60)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $status = Get-OneBotStatus
        if ($status -and [int]$status.websocket_connections -gt 0) {
            return $status
        }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)

    return Get-OneBotStatus
}

function Show-LoginQrCodeIfNeeded {
    param($Status)

    if ($Status -and [int]$Status.websocket_connections -gt 0) {
        Write-Host "QQ OneBot websocket connected."
        return
    }

    Write-Host "QQ OneBot websocket is not connected."
    if ($NapCatQrCode -and (Test-Path -LiteralPath $NapCatQrCode)) {
        Write-Host "Opening QQ login QR code: $NapCatQrCode"
        Start-Process -FilePath $NapCatQrCode
    } else {
        Write-Host "QR code not found yet. If QQ is not logged in, check NapCat cache later: $NapCatQrCode"
    }
}

function Start-Backend {
    $running = Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -like "*uvicorn app.main:app*" }

    if ($running -or (Test-HttpOk "http://127.0.0.1:8000/onebot/status")) {
        Write-Host "Backend is already running."
        return
    }

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

    Start-Sleep -Seconds 4
    if (Test-HttpOk "http://127.0.0.1:8000/onebot/status") {
        Write-Host "Backend started."
    } else {
        Write-Host "Backend start was requested, but it is not responding yet. Check: $ServerErrLog"
    }
}

function Start-NapCat {
    $running = Get-CimInstance Win32_Process |
        Where-Object { $_.ExecutablePath -like "$NapCatDir*" -and $_.Name -eq "NapCatWinBootMain.exe" }

    if ($running) {
        Write-Host "NapCat is already running."
        return
    }

    if (-not (Test-Path -LiteralPath $NapCatExe)) {
        throw "NapCat not found: $NapCatExe"
    }

    if ($NapCatWebUiConfig -and (Test-Path -LiteralPath $NapCatWebUiConfig)) {
        $webUiConfig = Get-Content -LiteralPath $NapCatWebUiConfig -Raw | ConvertFrom-Json
        if ($webUiConfig.token) {
            $env:NAPCAT_WEBUI_SECRET_KEY = [string]$webUiConfig.token
        }
    }

    Start-Process `
        -FilePath $NapCatExe `
        -ArgumentList $NapCatAccount `
        -WorkingDirectory $NapCatDir `
        -WindowStyle Hidden

    Start-Sleep -Seconds 6
    Write-Host "NapCat start command sent."
}

Start-Backend
Start-NapCat
$oneBotStatus = Wait-OneBotConnection -TimeoutSeconds 60
Show-LoginQrCodeIfNeeded -Status $oneBotStatus

Write-Host ""
Write-Host "Mio startup flow finished."
Write-Host "Web entry: http://127.0.0.1:8000/chat"
Write-Host "QQ login account: $NapCatAccount"
