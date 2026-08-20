# 一键安装：Mio 本地原声音色（独立数据包 + Genie ONNX CPU 推理）
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "gpt_sovits"

. (Join-Path $PSScriptRoot "common.ps1")

$voiceDir = if ($env:MIO_VOICE_TRAINING_DIR) { $env:MIO_VOICE_TRAINING_DIR } else { Join-Path $env:MIO_DATA_DIR "音色训练" }
$genieEnvDir = Join-Path $voiceDir ".genie-env"
$geniePython = Join-Path $genieEnvDir "Scripts\python.exe"
$genieData = Join-Path $voiceDir "GenieData"
$mioOnnx = Join-Path $voiceDir "models\genie\mio-v1"
$installMarker = Join-Path $voiceDir ".mio-native-voice-complete"
$packageInstaller = Join-Path $PSScriptRoot "install-mio-voice-package.py"
$sourceManifestPath = Join-Path $PSScriptRoot "mio-voice-package-source.json"

function Test-MioVoiceData {
    $required = @(
        (Join-Path $mioOnnx "t2s_encoder_fp32.onnx"),
        (Join-Path $mioOnnx "t2s_encoder_fp32.bin"),
        (Join-Path $mioOnnx "t2s_shared_fp16.bin"),
        (Join-Path $mioOnnx "vits_fp32.onnx"),
        (Join-Path $mioOnnx "vits_fp16.bin"),
        (Join-Path $mioOnnx "mio-genie-v2.json"),
        (Join-Path $voiceDir "emotion-references.json"),
        (Join-Path $voiceDir "materials\prepared\wav32k_v2\mio_v2_00.wav")
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -le 0) { return $false }
    }
    return $true
}

function Test-GenieRuntimeData {
    $required = @(
        (Join-Path $genieData "chinese-hubert-base\chinese-hubert-base.onnx"),
        (Join-Path $genieData "G2P\ChineseG2P\opencpop-strict.txt"),
        (Join-Path $genieData "G2P\ChineseG2P\polyphonic.pickle")
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -le 0) { return $false }
    }
    return $true
}

try {
    Write-Host "=== Mio 本地原声音色一键安装 ==="
    Write-Host ("安装位置：" + $voiceDir)
    Write-DepsStatus -Stage "prepare" -Percent 2 -Message ("正在准备 Mio 本地原声音色；安装到 " + $voiceDir)

    if (-not (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf)) { throw "安装包缺少 Mio 音色下载清单。" }
    if (-not (Test-Path -LiteralPath $packageInstaller -PathType Leaf)) { throw "安装包缺少 Mio 音色校验安装器。" }
    $source = Get-Content -LiteralPath $sourceManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($source.format -ne "mio-native-voice-source") { throw "Mio 音色下载清单格式不正确。" }
    $packageName = [string]$source.file_name
    $expectedSize = [long]$source.size_bytes
    $expectedHash = [string]$source.sha256
    $downloadDir = Join-Path $voiceDir "downloads"
    $downloadPath = Join-Path $downloadDir $packageName
    New-Item -ItemType Directory -Force -Path $voiceDir, $downloadDir | Out-Null

    if (-not (Test-Path -LiteralPath $geniePython -PathType Leaf) -or -not (Test-GenieRuntimeData)) {
        throw "请先安装 Genie 本地语音运行引擎，再安装 Mio 原声音色。"
    }
    if (-not (Test-MioVoiceData)) {
        $packagePath = Find-MioModelPackage -FileName $packageName -ExplicitPath $env:MIO_VOICE_PACKAGE
        if (-not $packagePath) {
            $urls = @($source.urls | Where-Object { $_ -and ([string]$_).StartsWith("https://") })
            if ($urls.Count -eq 0) {
                throw ("没有找到固定模型包 " + $packageName + "。请放到 Mio\模型、安装器旁\模型或系统下载\模型目录。")
            }
            Write-DepsStatus -Stage "voice-package" -Percent 35 -Message ("正在下载 " + $packageName) -TargetPath $downloadPath
            Invoke-Download -Urls $urls -Output $downloadPath -Stage "voice-package" -StartPercent 35 -EndPercent 78 -Label " Mio 原声音色模型包" -ExpectedSize $expectedSize -ExpectedSha256 $expectedHash
            $packagePath = $downloadPath
        }
        Write-DepsStatus -Stage "verify-package" -Percent 78 -Message ("已找到 " + $packageName + "，正在校验大小和 SHA-256") -TargetPath $packagePath
        Assert-MioModelPackage -Path $packagePath -Label "Mio 音色模型包" -ExpectedSize $expectedSize -ExpectedSha256 $expectedHash | Out-Null
        Write-DepsStatus -Stage "install-model" -Percent 80 -Message ("正在校验并安装 Mio 原声音色到 " + $voiceDir) -TargetPath $voiceDir
        $installCode = Invoke-Native -FilePath $geniePython -Arguments @(
            $packageInstaller, "--package", $packagePath, "--target", $voiceDir
        )
        if ($installCode -ne 0) { throw "Mio 原声音色模型包安装失败。" }
    }

    if (-not (Test-MioVoiceData)) { throw "Mio 原声音色文件完整性检查未通过。" }
    $runtimeLoadCode = Invoke-Native -FilePath $geniePython -Arguments @("-c", "import genie_tts, jieba, numpy, onnxruntime")
    if ($runtimeLoadCode -ne 0) { throw "Genie 完整运行时加载失败，请重试。" }

    Set-Content -LiteralPath $runtimeMarker -Value (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") -Encoding UTF8
    Set-Content -LiteralPath $installMarker -Value (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") -Encoding UTF8
    Write-DepsStatus -Stage "done" -Percent 100 -Message "模型文件已安装，正在由 Mio 自动注册、预热并试听" -Done $true -TargetPath $voiceDir
    Write-Host "=== 模型文件安装完成；返回 Mio 等待自动试听 ==="
} catch {
    Write-DepsFail -Message ("Mio 本地原声音色安装失败：" + $_.Exception.Message)
    exit 1
}
