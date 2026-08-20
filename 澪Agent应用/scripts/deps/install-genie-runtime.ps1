# 安装 Genie ONNX CPU 运行引擎；不安装 Mio 角色音色文件。
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "genie_runtime"
. (Join-Path $PSScriptRoot "common.ps1")

$voiceDir = if ($env:MIO_VOICE_TRAINING_DIR) { $env:MIO_VOICE_TRAINING_DIR } else { Join-Path $env:MIO_DATA_DIR "音色训练" }
$genieEnvDir = Join-Path $voiceDir ".genie-env"
$geniePython = Join-Path $genieEnvDir "Scripts\python.exe"
$genieData = Join-Path $voiceDir "GenieData"
$runtimeMarker = Join-Path $genieEnvDir ".mio-genie-runtime-complete"
$packageInstaller = Join-Path $PSScriptRoot "install-mio-voice-package.py"
$patchScript = Join-Path $PSScriptRoot "patch-genie-runtime.py"
$sourceManifestPath = Join-Path $PSScriptRoot "mio-genie-runtime-source.json"

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
    Write-Host "=== Genie 本地语音运行引擎安装 ==="
    Write-Host ("安装位置：" + $voiceDir)
    Write-DepsStatus -Stage "prepare" -Percent 2 -Message ("准备 Genie 运行引擎；安装到 " + $voiceDir)
    if (-not (Test-Path -LiteralPath $sourceManifestPath -PathType Leaf)) { throw "安装包缺少 Genie 运行引擎下载清单。" }
    $source = Get-Content -LiteralPath $sourceManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($source.format -ne "mio-genie-runtime-source") { throw "Genie 下载清单格式不正确。" }
    $packageName = [string]$source.file_name
    $expectedSize = [long]$source.size_bytes
    $expectedHash = [string]$source.sha256
    $downloadDir = Join-Path $voiceDir "downloads"
    $downloadPath = Join-Path $downloadDir $packageName
    New-Item -ItemType Directory -Force -Path $voiceDir, $downloadDir | Out-Null

    $bootstrapPython = Ensure-DepsVenv
    if (-not (Test-Path -LiteralPath $geniePython)) {
        Write-DepsStatus -Stage "python" -Percent 12 -Message ("创建独立 Genie Python 环境：" + $genieEnvDir)
        $venvCode = Invoke-Native -FilePath $bootstrapPython -Arguments @("-m", "venv", $genieEnvDir)
        if ($venvCode -ne 0 -or -not (Test-Path -LiteralPath $geniePython)) { throw "Genie Python 环境创建失败。" }
    }

    $probe = Invoke-Native -FilePath $geniePython -Arguments @("-c", "import importlib.util, jieba, numpy, onnx, onnxruntime; raise SystemExit(0 if importlib.util.find_spec('genie_tts') and int(numpy.__version__.split('.')[0]) < 2 else 1)")
    if ($probe -ne 0) {
        Write-DepsStatus -Stage "pip" -Percent 18 -Message "安装 Genie 2.0.2 运行依赖"
        $pipCode = Invoke-Native -FilePath $geniePython -Arguments @("-m", "pip", "install", "onnx==1.22.0", "onnxruntime==1.22.1", "transformers==4.50.0", "tokenizers", "numpy==1.26.4", "soundfile", "soxr", "pyyaml", "sounddevice", "pydantic", "fastapi", "uvicorn[standard]", "pyopenjtalk-plus", "nltk", "pypinyin", "g2pM", "jieba", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
        if ($pipCode -ne 0) { throw "Genie 运行依赖安装失败。" }
        $genieCode = Invoke-Native -FilePath $geniePython -Arguments @("-m", "pip", "install", "--no-deps", "genie-tts==2.0.2", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
        if ($genieCode -ne 0) { throw "Genie 2.0.2 主程序安装失败。" }
    }
    $sitePackages = Join-Path $genieEnvDir "Lib\site-packages"
    $patchCode = Invoke-Native -FilePath $geniePython -Arguments @($patchScript, "--site-packages", $sitePackages)
    if ($patchCode -ne 0) { throw "Genie 中文分词兼容修补失败。" }

    if (-not (Test-GenieRuntimeData)) {
        $packagePath = Find-MioModelPackage -FileName $packageName -ExplicitPath $env:MIO_GENIE_PACKAGE
        if (-not $packagePath) {
            $urls = @($source.urls | Where-Object { $_ -and ([string]$_).StartsWith("https://") })
            if ($urls.Count -eq 0) { throw ("没有找到固定模型包 " + $packageName + "。请放到 Mio\模型、安装器旁\模型或系统下载\模型目录。") }
            Write-DepsStatus -Stage "engine-package" -Percent 35 -Message ("下载 " + $packageName) -TargetPath $downloadPath
            Invoke-Download -Urls $urls -Output $downloadPath -Stage "engine-package" -StartPercent 35 -EndPercent 82 -Label " Genie 运行引擎" -ExpectedSize $expectedSize -ExpectedSha256 $expectedHash
            $packagePath = $downloadPath
        }
        Write-DepsStatus -Stage "verify-package" -Percent 82 -Message ("已找到 " + $packageName + "，正在校验大小和 SHA-256") -TargetPath $packagePath
        Assert-MioModelPackage -Path $packagePath -Label "Genie 运行引擎包" -ExpectedSize $expectedSize -ExpectedSha256 $expectedHash | Out-Null
        Write-DepsStatus -Stage "install-engine" -Percent 85 -Message ("安装 GenieData 到 " + $voiceDir) -TargetPath $voiceDir
        $installCode = Invoke-Native -FilePath $geniePython -Arguments @($packageInstaller, "--package", $packagePath, "--target", $voiceDir)
        if ($installCode -ne 0) { throw "Genie 运行引擎包安装失败。" }
    }
    if (-not (Test-GenieRuntimeData)) { throw "GenieData 完整性检查未通过。" }
    $probe = Invoke-Native -FilePath $geniePython -Arguments @("-c", "import genie_tts, jieba, numpy, onnxruntime")
    if ($probe -ne 0) { throw "Genie 运行引擎自检失败。" }
    Set-Content -LiteralPath $runtimeMarker -Value (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") -Encoding UTF8
    Write-DepsStatus -Stage "done" -Percent 100 -Message "Genie 本地语音运行引擎已安装；现在可以单独安装 Mio 音色包" -Done $true -TargetPath $voiceDir
    Write-Host "=== Genie 运行引擎安装完成 ==="
} catch {
    Write-DepsFail -Message ("Genie 本地语音运行引擎安装失败：" + $_.Exception.Message)
    exit 1
}
