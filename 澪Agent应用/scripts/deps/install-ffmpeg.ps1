# Install a fixed Windows FFmpeg build in the shared Mio media tool directory.
param()
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "ffmpeg"
. (Join-Path $PSScriptRoot "common.ps1")
$voiceDir = if ($env:MIO_VOICE_TRAINING_DIR) { $env:MIO_VOICE_TRAINING_DIR } else { Join-Path $env:MIO_WORKSPACE_ROOT "音色训练" }
$binDir = Join-Path $voiceDir "cache\bin"
$downloadDir = Join-Path $voiceDir "downloads"
$zipPath = Join-Path $downloadDir "ffmpeg-9.0.2-essentials_build.zip"
$stagingDir = Join-Path $downloadDir ("ffmpeg.installing-" + $PID)

try {
    Write-DepsStatus -Stage "prepare" -Percent 2 -Message "正在准备 FFmpeg 安装目录"
    New-Item -ItemType Directory -Force -Path $downloadDir, $binDir | Out-Null
    # GyanD/codexffmpeg release 9.0.2 asset metadata, checked 2026-10-11.
    $official = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
    Invoke-Download -Urls @(("https://ghfast.top/" + $official), ("https://gh-proxy.com/" + $official), $official) -Output $zipPath -Stage "download" -StartPercent 5 -EndPercent 75 -Label " FFmpeg" -ExpectedSize 114768076 -ExpectedSha256 "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
    Assert-DepsZipArchive -Path $zipPath -RequiredFiles @("ffmpeg.exe", "ffprobe.exe")
    Write-DepsStatus -Stage "extract" -Percent 80 -Message "正在解压并验证 FFmpeg"
    New-Item -ItemType Directory -Path $stagingDir | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $stagingDir -Force
    foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
        $binary = Get-ChildItem -LiteralPath $stagingDir -Recurse -File -Filter $name | Select-Object -First 1
        if (-not $binary -or $binary.Length -le 0) { throw ("FFmpeg 包缺少 " + $name) }
        if ((Invoke-Native -FilePath $binary.FullName -Arguments @("-version")) -ne 0) { throw ($name + " 无法在此电脑运行。") }
        Copy-Item -LiteralPath $binary.FullName -Destination (Join-Path $binDir $name) -Force
    }
    Write-DepsStatus -Stage "done" -Percent 100 -Message "FFmpeg 已安装并通过运行检查，可用于音视频处理" -Done $true
} catch {
    Write-DepsFail -Message ("FFmpeg 安装失败：" + $_.Exception.Message)
    exit 1
} finally {
    # The PID-specific staging directory is owned by this installer only.
    if (Test-Path -LiteralPath $stagingDir) { Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue }
}
