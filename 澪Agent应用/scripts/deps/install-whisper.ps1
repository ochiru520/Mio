# 一键安装：系统声音理解（faster-whisper + base 模型）
# 复用「音色训练\.voice-env」独立 Python 环境；进度写入 MIO_STATUS_FILE。
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "whisper"

. (Join-Path $PSScriptRoot "common.ps1")

$voiceDir = if ($env:MIO_VOICE_TRAINING_DIR) { $env:MIO_VOICE_TRAINING_DIR } else { Join-Path $env:MIO_WORKSPACE_ROOT "音色训练" }
$cacheDir = Join-Path $voiceDir "cache"
$whisperCache = Join-Path $cacheDir "faster-whisper"
$modelMarker = Join-Path $whisperCache "models--Systran--faster-whisper-base"
$requiredModelFiles = @("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

function Test-WhisperModel {
    if (-not (Test-Path -LiteralPath $modelMarker)) { return $false }
    $snapshotsDir = Join-Path $modelMarker "snapshots"
    if (-not (Test-Path -LiteralPath $snapshotsDir)) { return $false }
    foreach ($snapshot in Get-ChildItem -LiteralPath $snapshotsDir -Directory -ErrorAction SilentlyContinue) {
        $complete = $true
        foreach ($name in $requiredModelFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $snapshot.FullName $name))) {
                $complete = $false
                break
            }
        }
        if ($complete) { return $true }
    }
    return $false
}

try {
    Write-Host "=== 系统声音理解一键安装 ==="
    Write-DepsStatus -Stage "prepare" -Percent 2 -Message "正在准备 Python 环境"

    $python = Ensure-DepsVenv

    Write-DepsStatus -Stage "pip" -Percent 20 -Message "正在安装语音转写组件（清华镜像）"
    Invoke-DepsPip -Python $python -Packages @("faster-whisper", "huggingface_hub") -Message "正在安装 faster-whisper..."

    if (-not (Test-WhisperModel)) {
        Write-DepsStatus -Stage "model" -Percent 55 -Message "正在下载语音识别模型（约 145 MB，国内魔搭源）"
        $downloadScript = Join-Path $PSScriptRoot "download-from-modelscope.py"
        $env:MIO_DOWNLOAD_STAGE = "model"
        $env:MIO_DOWNLOAD_START_PERCENT = "55"
        $env:MIO_DOWNLOAD_END_PERCENT = "98"
        $modelCode = Invoke-Native -FilePath $python -Arguments @(
            $downloadScript,
            "--dir-name",
            "models--Systran--faster-whisper-base",
            "pengzhendong/faster-whisper-base",
            $whisperCache,
            "base",
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.txt"
        )
        if ($modelCode -ne 0) { throw "语音识别模型下载失败。" }
    }

    if (-not (Test-WhisperModel)) {
        throw "语音识别模型校验失败，请重试。"
    }
    Write-DepsStatus -Stage "done" -Percent 100 -Message "系统声音理解安装完成，回到应用点击「重新检查」即可使用" -Done $true
    Write-Host "=== 安装完成 ==="
    Write-Host "回到澪的界面，点击「重新检查」，屏幕观察就能听懂系统声音了。"
} catch {
    Write-DepsFail -Message ("系统声音理解安装失败：" + $_.Exception.Message)
    Write-Host "按任意键关闭窗口..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
