param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $AppRoot
$BackendRoot = Get-ChildItem -LiteralPath $WorkspaceRoot -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "backend\app") } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $BackendRoot) {
    throw "Mio backend project was not found."
}
$BackendRoot = Join-Path $BackendRoot "backend"
$PythonExe = if ($env:MIO_BACKEND_PYTHON) { $env:MIO_BACKEND_PYTHON } else { Join-Path $BackendRoot ".venv\Scripts\python.exe" }
$DesktopRoot = Join-Path $AppRoot "desktop"
$Live2DDesktopRoot = Join-Path $AppRoot "live2d-desktop"
$PublishRoot = Join-Path $AppRoot "release"
$SpecFile = Join-Path $DesktopRoot "mio-agent.spec"
$BuildManifestScript = Join-Path $DesktopRoot "build_manifest.py"
$BuildIdentityPath = Join-Path $DesktopRoot "generated\build_identity.json"
$CacheRoot = Join-Path $AppRoot ".desktop-cache"
$PipCacheRoot = Join-Path $CacheRoot "pip"
$TempRoot = Join-Path $CacheRoot "temp"
$PipIndexUrl = if ($env:MIO_PIP_INDEX_URL) { $env:MIO_PIP_INDEX_URL } else { "https://pypi.org/simple" }
$ElectronMirror = if ($env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR } else { "https://npmmirror.com/mirrors/electron/" }
$ElectronBuilderMirror = if ($env:ELECTRON_BUILDER_BINARIES_MIRROR) { $env:ELECTRON_BUILDER_BINARIES_MIRROR } else { "https://npmmirror.com/mirrors/electron-builder-binaries/" }

New-Item -ItemType Directory -Force -Path $PipCacheRoot, $TempRoot | Out-Null
$env:PIP_CACHE_DIR = $PipCacheRoot
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $CacheRoot "pyinstaller"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python virtual environment was not found: $PythonExe"
}

Push-Location $AppRoot
try {
    Write-Host "[1/8] Capturing source build identity..."
    & $PythonExe $BuildManifestScript prepare `
        --desktop-root $AppRoot `
        --backend-project-root (Split-Path -Parent $BackendRoot) `
        --output $BuildIdentityPath
    if ($LASTEXITCODE -ne 0) { throw "Build identity generation failed." }

    Write-Host "[2/8] Building Vue frontend..."
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Vue frontend build failed." }

    Write-Host "[3/8] Installing desktop build dependencies..."
    & $PythonExe -m pip install `
        --index-url $PipIndexUrl `
        --retries 2 `
        --progress-bar off `
        -r (Join-Path $DesktopRoot "requirements-desktop.txt")
    if ($LASTEXITCODE -ne 0) { throw "Desktop dependency installation failed." }

    Write-Host "[4/8] Creating Windows icon..."
    & $PythonExe (Join-Path $DesktopRoot "create_icon.py")
    if ($LASTEXITCODE -ne 0) { throw "Windows icon creation failed." }

    Write-Host "[5/8] Building independent Live2D desktop renderer..."
    $env:ELECTRON_MIRROR = $ElectronMirror
    $env:ELECTRON_BUILDER_BINARIES_MIRROR = $ElectronBuilderMirror
    Push-Location $Live2DDesktopRoot
    try {
        npm ci --ignore-scripts --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "Live2D desktop dependencies failed to install." }
        node (Join-Path $Live2DDesktopRoot "node_modules\electron\install.js")
        if ($LASTEXITCODE -ne 0) { throw "Electron runtime failed to install." }
        npm run build:dir
        if ($LASTEXITCODE -ne 0) { throw "Live2D desktop build failed." }
    } finally {
        Pop-Location
    }

    Write-Host "[6/8] Packaging Mio desktop app..."
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $PublishRoot `
        --workpath (Join-Path $DesktopRoot "build") `
        $SpecFile
    if ($LASTEXITCODE -ne 0) { throw "Desktop application packaging failed." }

    Write-Host "[7/8] Finalizing release manifest..."
    $ReleaseAppRoot = Get-ChildItem -LiteralPath $PublishRoot -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "_internal\build_identity.json") } |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $ReleaseAppRoot) {
        throw "Packaged application root was not found."
    }
    $DiscoveryProbe = @'
import sys
from pathlib import Path

internal = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(internal))
from app.environment_check_service import WHISPER_DISCOVERY_VERSION

if WHISPER_DISCOVERY_VERSION < 2:
    raise SystemExit(f"stale whisper discovery module: {WHISPER_DISCOVERY_VERSION}")
'@
    $DiscoveryProbePath = Join-Path $TempRoot "verify-packaged-discovery.py"
    [System.IO.File]::WriteAllText(
        $DiscoveryProbePath,
        $DiscoveryProbe,
        (New-Object System.Text.UTF8Encoding($false))
    )
    try {
        & $PythonExe $DiscoveryProbePath (Join-Path $ReleaseAppRoot "_internal")
        if ($LASTEXITCODE -ne 0) { throw "Packaged dependency discovery module is stale." }
    }
    finally {
        Remove-Item -LiteralPath $DiscoveryProbePath -Force -ErrorAction SilentlyContinue
    }
    $RequiredGenieFiles = @(
        # PyInstaller noarchive=True emits backend Python modules as .pyc files.
        (Join-Path $ReleaseAppRoot "_internal\app\genie_tts_service.pyc"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\genie_tts_worker.py"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\download-from-modelscope.py"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\prepare-genie-resources.py"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\install-gpt-sovits.ps1"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\install-genie-runtime.ps1"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\install-mio-voice-package.py"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\mio-voice-package-source.json"),
        (Join-Path $ReleaseAppRoot "_internal\agent_scripts\deps\mio-genie-runtime-source.json")
    )
    $MissingGenieFiles = @($RequiredGenieFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($MissingGenieFiles.Count -gt 0) {
        throw ("Packaged Genie runtime files are missing: " + ($MissingGenieFiles -join ", "))
    }
    & $PythonExe $BuildManifestScript finalize `
        --identity $BuildIdentityPath `
        --release-root $ReleaseAppRoot
    if ($LASTEXITCODE -ne 0) { throw "Release manifest finalization failed." }

    $RegisteredInnoLocation = Get-ChildItem -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue } |
        Where-Object { $_.DisplayName -like "Inno Setup*" } |
        Select-Object -First 1 -ExpandProperty InstallLocation
    $IsccCandidates = @(@(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        $(if ($RegisteredInnoLocation) { Join-Path $RegisteredInnoLocation "ISCC.exe" }),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

    if ($SkipInstaller) {
        Write-Host "[8/8] Installer build skipped by request."
    } elseif ($IsccCandidates) {
        Write-Host "[8/8] Building installer..."
        & $IsccCandidates[0] (Join-Path $DesktopRoot "installer.iss")
        if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
    } else {
        Write-Host "[8/8] Inno Setup is not installed; skipped installer build."
    }

    $ExePath = Get-ChildItem -LiteralPath $PublishRoot -Recurse -Filter *.exe |
        Where-Object { $_.DirectoryName -ne $PublishRoot -and $_.Name -ne "setup.exe" } |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $ExePath) {
        throw "Packaging finished without an executable."
    }
    Write-Host "Build complete: $ExePath"
} finally {
    Pop-Location
}
