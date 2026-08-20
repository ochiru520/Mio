param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "restart")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$RuntimeRoot = [string]$env:MIO_RUNTIME_ROOT
$EnvFile = ""
if ($RuntimeRoot) {
    $RuntimeEnvCandidates = @(
        (Join-Path $RuntimeRoot ".env"),
        (Join-Path $RuntimeRoot "backend\.env")
    )
    $EnvFile = $RuntimeEnvCandidates |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not $EnvFile) {
    $WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $LegacyRoot = Get-ChildItem -LiteralPath $WorkspaceRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "backend\app\main.py") } |
        Select-Object -First 1 -ExpandProperty FullName
    if ($LegacyRoot) {
        $EnvFile = Join-Path $LegacyRoot "backend\.env"
    }
}
function Get-EnvValue {
    param([string]$Name, [string]$Default = "")
    if ($EnvFile -and (Test-Path -LiteralPath $EnvFile)) {
        foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
            $trimmed = $line.Trim()
            if ($trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
            $parts = $trimmed.Split("=", 2)
            if ($parts[0].Trim() -eq $Name -and $parts[1].Trim()) {
                return $parts[1].Trim()
            }
        }
    }
    return $Default
}

$NapCatDir = if ($env:MIO_NAPCAT_DIR) { $env:MIO_NAPCAT_DIR } else { Get-EnvValue -Name "NAPCAT_DIR" }
$NapCatAccount = if ($env:MIO_NAPCAT_ACCOUNT) { $env:MIO_NAPCAT_ACCOUNT } else { Get-EnvValue -Name "NAPCAT_ACCOUNT" }
$NapCatWebUiUrl = if ($env:MIO_NAPCAT_WEBUI_URL) { $env:MIO_NAPCAT_WEBUI_URL } else { Get-EnvValue -Name "NAPCAT_WEBUI_URL" -Default "http://127.0.0.1:6099" }
$ForceQrLogin = [string]$env:MIO_NAPCAT_FORCE_QR -eq "1"
$AppPort = if ($env:MIO_APP_PORT) { [int]$env:MIO_APP_PORT } else { [int](Get-EnvValue -Name "APP_PORT" -Default "8000") }
if (-not $NapCatDir) {
    throw "NAPCAT_DIR is not configured. Set it in backend/.env or the runtime .env file."
}
if ($Action -ne "stop" -and -not $NapCatAccount) {
    throw "NAPCAT_ACCOUNT is not configured. Set it in backend/.env or the runtime .env file."
}
$NapCatLauncher = @(
    (Join-Path $NapCatDir "NapCatWinBootMain.exe"),
    (Join-Path $NapCatDir "launcher-user.bat"),
    (Join-Path $NapCatDir "launcher-win10-user.bat"),
    (Join-Path $NapCatDir "launcher.bat"),
    (Join-Path $NapCatDir "launcher-win10.bat"),
    (Join-Path $NapCatDir "napcat.quick.bat"),
    (Join-Path $NapCatDir "napcat.bat")
) | Where-Object { Test-Path -LiteralPath $_ }
if (-not $NapCatLauncher) {
    foreach ($launcherName in @("NapCatWinBootMain.exe", "launcher-user.bat", "launcher-win10-user.bat", "launcher.bat", "launcher-win10.bat", "napcat.quick.bat", "napcat.bat")) {
        $NapCatLauncher = Get-ChildItem -LiteralPath $NapCatDir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ieq $launcherName } |
            Sort-Object FullName |
            Select-Object -First 1 -ExpandProperty FullName
        if ($NapCatLauncher) { break }
    }
} else {
    $NapCatLauncher = $NapCatLauncher | Select-Object -First 1
}
$ManagedPrefix = [IO.Path]::GetFullPath($NapCatDir).TrimEnd("\") + "\"

function Stop-NapCatManagedProcesses {
    $processes = @(Get-CimInstance Win32_Process)
    $managedIds = [Collections.Generic.HashSet[int]]::new()
    foreach ($process in $processes) {
        if ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($ManagedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            $null = $managedIds.Add([int]$process.ProcessId)
        }
    }
    # NapCat WebUI lives inside the injected robot QQ process. Its listening PID
    # is a stronger ownership signal than an executable path or an old parent PID.
    try {
        $webUiPort = ([Uri]$NapCatWebUiUrl).Port
        $webUiOwners = @(Get-NetTCPConnection -State Listen -LocalPort $webUiPort -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($ownerId in $webUiOwners) {
            $currentId = [int]$ownerId
            while ($currentId -gt 0) {
                $entry = $processes | Where-Object { [int]$_.ProcessId -eq $currentId } | Select-Object -First 1
                if (-not $entry) { break }
                $normalizedName = ([string]$entry.Name).ToLowerInvariant()
                $normalizedPath = [string]$entry.ExecutablePath
                $isManagedPath = $normalizedPath -and $normalizedPath.StartsWith($ManagedPrefix, [StringComparison]::OrdinalIgnoreCase)
                if (-not $isManagedPath -and $normalizedName -notin @("qq.exe", "napcatwinbootmain.exe")) { break }
                $null = $managedIds.Add($currentId)
                $currentId = [int]$entry.ParentProcessId
            }
        }
    } catch {
    }
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $processes) {
            if ($managedIds.Contains([int]$process.ParentProcessId) -and -not $managedIds.Contains([int]$process.ProcessId)) {
                $null = $managedIds.Add([int]$process.ProcessId)
                $changed = $true
            }
        }
    }
    foreach ($processId in @($managedIds) | Sort-Object -Descending) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

function Get-InstalledQQPath {
    $candidates = [Collections.Generic.List[string]]::new()
    if ($env:MIO_QQ_EXE) { $candidates.Add([string]$env:MIO_QQ_EXE) }
    foreach ($registryPath in @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
        "Registry::HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"
    )) {
        try {
            $entry = Get-ItemProperty -LiteralPath $registryPath -ErrorAction Stop
            if ($entry.InstallLocation) {
                $candidates.Add((Join-Path ([string]$entry.InstallLocation) "QQ.exe"))
            }
            if ($entry.UninstallString) {
                $uninstallPath = ([string]$entry.UninstallString).Trim().Trim('"')
                $uninstallDir = Split-Path -Parent $uninstallPath
                if ($uninstallDir) { $candidates.Add((Join-Path $uninstallDir "QQ.exe")) }
            }
        } catch {
        }
    }
    if ($env:ProgramFiles) { $candidates.Add((Join-Path $env:ProgramFiles "Tencent\QQNT\QQ.exe")) }
    if (${env:ProgramFiles(x86)}) { $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Tencent\QQNT\QQ.exe")) }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Tencent\QQNT\QQ.exe"))
        $candidates.Add((Join-Path $env:LOCALAPPDATA "Tencent\QQNT\QQ.exe"))
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return ""
}

function Get-NapCatShellFiles {
    $bootMain = Get-ChildItem -LiteralPath $NapCatDir -Recurse -File -Filter "NapCatWinBootMain.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $bootMain) { return $null }
    $shellRoot = Split-Path -Parent $bootMain
    $hook = Join-Path $shellRoot "NapCatWinBootHook.dll"
    $main = Join-Path $shellRoot "napcat.mjs"
    $patch = Join-Path $shellRoot "qqnt.json"
    if (-not (Test-Path -LiteralPath $hook -PathType Leaf) -or -not (Test-Path -LiteralPath $main -PathType Leaf)) {
        return $null
    }
    return [pscustomobject]@{
        BootMain = $bootMain
        Root = $shellRoot
        Hook = $hook
        Main = $main
        Patch = $patch
        Load = (Join-Path $shellRoot "loadNapCat.js")
    }
}

function Clear-NapCatAutoLogin {
    $configs = @(Get-ChildItem -LiteralPath $NapCatDir -Recurse -File -Filter "webui.json" -ErrorAction SilentlyContinue)
    foreach ($item in $configs) {
        try {
            $payload = Get-Content -LiteralPath $item.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -eq $payload) { continue }
            if ($payload.PSObject.Properties.Name -contains "autoLoginAccount") {
                $payload.autoLoginAccount = ""
            } else {
                $payload | Add-Member -NotePropertyName "autoLoginAccount" -NotePropertyValue ""
            }
            $json = $payload | ConvertTo-Json -Depth 20
            [IO.File]::WriteAllText($item.FullName, $json, [Text.UTF8Encoding]::new($false))
        } catch {
            Write-Output ("NapCat auto-login setting could not be cleared: " + $_.Exception.Message)
        }
    }
}

function Test-NapCatWebUi {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $NapCatWebUiUrl -TimeoutSec 1 | Out-Null
        return $true
    } catch {
        return $null -ne $_.Exception.Response
    }
}

function Start-NapCatManagedProcess {
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "NapCatWinBootMain.exe" -and
            $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($ManagedPrefix, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($running -and (Test-NapCatWebUi)) {
        Write-Output "NapCat WebUI is already running."
        return
    }
    if ($running) {
        $recentlyStarted = $false
        foreach ($process in $running) {
            try {
                $age = (Get-Date) - (Get-Process -Id $process.ProcessId -ErrorAction Stop).StartTime
                if ($age.TotalSeconds -lt 12) { $recentlyStarted = $true; break }
            } catch {
            }
        }
        if ($recentlyStarted) {
            Write-Output "NapCat start is already in progress."
            return
        }
        Write-Output "NapCat shell is stale; restarting managed processes."
        Stop-NapCatManagedProcesses
        Start-Sleep -Milliseconds 500
    }
    if (-not $NapCatLauncher) {
        throw "NapCat executable was not found."
    }

    $shell = Get-NapCatShellFiles
    if (
        -not $shell -and
        [IO.Path]::GetFileName([string]$NapCatLauncher) -ieq "NapCatWinBootMain.exe"
    ) {
        throw "检测到旧版或不完整的 NapCat Shell（缺少 napcat.mjs）。请回到 Mio 点击安装并配置，让 Mio 补齐新版 Shell；不要重装官方 QQ。"
    }
    if ($shell) {
        $qqPath = Get-InstalledQQPath
        if (-not $qqPath) {
            throw "没有找到官方 NT QQ。请先从腾讯官网安装新版 QQ，再回到 Mio 重试。"
        }
        $ordinaryQQ = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -ieq "QQ.exe" -and
            $_.ExecutablePath -and
            -not $_.ExecutablePath.StartsWith($ManagedPrefix, [StringComparison]::OrdinalIgnoreCase)
        })
        if ($ordinaryQQ) {
            throw "检测到普通 QQ 正在运行。请先从系统托盘彻底退出 QQ，再由 Mio 启动机器人 QQ。"
        }

        $mainUrl = ([IO.Path]::GetFullPath($shell.Main)).Replace("\", "/")
        [IO.File]::WriteAllText(
            $shell.Load,
            "(async () => {await import(`"file:///$mainUrl`")})()",
            [Text.UTF8Encoding]::new($false)
        )
        if (Test-Path -LiteralPath $shell.Patch -PathType Leaf) {
            $env:NAPCAT_PATCH_PACKAGE = $shell.Patch
        }
        $env:NAPCAT_LOAD_PATH = $shell.Load
        $env:NAPCAT_INJECT_PATH = $shell.Hook
        $env:NAPCAT_LAUNCHER_PATH = $shell.BootMain
        $env:NAPCAT_MAIN_PATH = $shell.Main
        $arguments = @(
            ('"' + $qqPath + '"'),
            ('"' + $shell.Hook + '"')
        )
        if (-not $ForceQrLogin) {
            $arguments += @("-q", $NapCatAccount)
        }
        $process = Start-Process `
            -FilePath $shell.BootMain `
            -ArgumentList $arguments `
            -WorkingDirectory $shell.Root `
            -WindowStyle Hidden `
            -PassThru
        Start-Sleep -Milliseconds 800
        if ($process.HasExited -and $process.ExitCode -ne 0) {
            throw "NapCat 启动器立即退出，代码 $($process.ExitCode)。请检查安全软件是否拦截 NapCatWinBootHook.dll。"
        }
        if ($ForceQrLogin) {
            Write-Output "NapCat Shell was started in forced QR login mode."
        } else {
            Write-Output "NapCat Shell was started directly with installed QQ: $qqPath"
        }
        return
    }

    $versionsRoot = Join-Path $NapCatDir "versions"
    if (Test-Path -LiteralPath $versionsRoot) {
        $versionDir = Get-ChildItem -LiteralPath $versionsRoot -Directory |
            Sort-Object Name -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if ($versionDir) {
            $webUiConfig = Join-Path $versionDir "resources\app\napcat\config\webui.json"
            if (Test-Path -LiteralPath $webUiConfig) {
                $config = Get-Content -LiteralPath $webUiConfig -Raw | ConvertFrom-Json
                if ($config.token) {
                    $env:NAPCAT_WEBUI_SECRET_KEY = [string]$config.token
                }
            }
        }
    }

    if (-not $env:NAPCAT_WEBUI_SECRET_KEY) {
        foreach ($candidate in @(
            (Join-Path $NapCatDir "napcat\config\webui.json"),
            (Join-Path $NapCatDir "config\webui.json")
        )) {
            if (Test-Path -LiteralPath $candidate) {
                $config = Get-Content -LiteralPath $candidate -Raw | ConvertFrom-Json
                if ($config.token) { $env:NAPCAT_WEBUI_SECRET_KEY = [string]$config.token; break }
            }
        }
    }

    $LauncherDirectory = Split-Path -Parent $NapCatLauncher
    if ($ForceQrLogin) {
        Start-Process `
            -FilePath $NapCatLauncher `
            -WorkingDirectory $LauncherDirectory `
            -WindowStyle Hidden
    } else {
        Start-Process `
            -FilePath $NapCatLauncher `
            -ArgumentList $NapCatAccount `
            -WorkingDirectory $LauncherDirectory `
            -WindowStyle Hidden
    }
    Write-Output "NapCat start command was sent from $LauncherDirectory."
}

if ($Action -eq "stop") {
    Stop-NapCatManagedProcesses
    Write-Output "NapCat channel stopped."
    exit 0
}

if ($Action -eq "restart") {
    Stop-NapCatManagedProcesses
    Start-Sleep -Seconds 2
    if ($ForceQrLogin) {
        Clear-NapCatAutoLogin
        Write-Output "NapCat quick login was cleared for a fresh QR code."
    }
}

Start-NapCatManagedProcess
Write-Output "NapCat launch was dispatched; readiness is reported by the app status endpoint."
