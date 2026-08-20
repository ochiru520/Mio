# 一键安装：本地视觉（Ollama + Qwen2.5-VL 3B）
# 由后端 dependency_installer 调用，独立控制台窗口运行，进度写入 MIO_STATUS_FILE。
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "ollama_vision"

. (Join-Path $PSScriptRoot "common.ps1")

$visionDir = if ($env:MIO_LOCAL_VISION_DIR) { $env:MIO_LOCAL_VISION_DIR } else { Join-Path $env:MIO_WORKSPACE_ROOT "本地视觉" }
$ollamaDir = Join-Path $visionDir "Ollama"
$modelsDir = Join-Path $visionDir "models"
$ollamaExe = Join-Path $ollamaDir "ollama.exe"
$zipPath = Join-Path $visionDir "ollama-windows-amd64.zip"
$manifest = Join-Path $modelsDir "manifests\registry.ollama.ai\library\qwen2.5vl\3b"

function Test-OllamaModel {
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { return $false }
    try {
        $payload = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifest | ConvertFrom-Json
        $assets = @($payload.config) + @($payload.layers)
        if ($assets.Count -eq 0) { return $false }
        foreach ($asset in $assets) {
            $digest = [string]$asset.digest
            if (-not $digest.StartsWith("sha256:")) { return $false }
            $blob = Join-Path (Join-Path $modelsDir "blobs") $digest.Replace(":", "-")
            if (-not (Test-Path -LiteralPath $blob -PathType Leaf)) { return $false }
            $expectedSize = [int64]$asset.size
            if ($expectedSize -gt 0 -and (Get-Item -LiteralPath $blob).Length -ne $expectedSize) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

function Wait-OllamaReady {
    param([System.Diagnostics.Process]$Process, [string]$HostAddress)
    $url = "http://" + $HostAddress + "/api/tags"
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($Process.HasExited) {
            throw "Ollama 本地服务提前退出（退出码 $($Process.ExitCode)）。"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1
            if ($response.StatusCode -eq 200) { return }
        } catch {
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Ollama 本地服务启动超时。"
}

function Invoke-OllamaPull {
    param([string]$HostAddress, [string]$Model)
    $request = $null
    $response = $null
    $reader = $null
    try {
        $url = "http://" + $HostAddress + "/api/pull"
        $request = [System.Net.HttpWebRequest]::Create($url)
        $request.Method = "POST"
        $request.ContentType = "application/json"
        $request.Accept = "application/x-ndjson"
        $request.Timeout = 7200000
        $request.ReadWriteTimeout = 180000
        $body = [System.Text.Encoding]::UTF8.GetBytes((@{ model = $Model; stream = $true } | ConvertTo-Json -Compress))
        $request.ContentLength = $body.Length
        $requestStream = $request.GetRequestStream()
        try { $requestStream.Write($body, 0, $body.Length) } finally { $requestStream.Close() }

        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)
        $totals = @{}
        $completed = @{}
        $highestPercent = 0
        $lastBytes = 0L
        $lastAt = Get-Date
        while (-not $reader.EndOfStream) {
            $line = $reader.ReadLine()
            if (-not $line) { continue }
            Write-Host $line
            try { $item = $line | ConvertFrom-Json } catch { continue }
            if ($item.error) { throw ([string]$item.error) }
            $digest = [string]$item.digest
            $total = [int64]($item.total -as [int64])
            $done = [int64]($item.completed -as [int64])
            if ($digest -and $total -gt 0) {
                $totals[$digest] = $total
                # PowerShell 5.1 会为 [Math]::Max(0, $done) 选择 Int32 重载；
                # 单层下载超过 2 GiB 后便会溢出并终止。全程显式保持 Int64。
                $completed[$digest] = Limit-Int64 -Value $done -Minimum 0 -Maximum $total
            }
            $knownTotal = 0L
            $knownDone = 0L
            foreach ($key in $totals.Keys) {
                $knownTotal += [int64]$totals[$key]
                $knownDone += [int64]$completed[$key]
            }
            $currentPercent = if ($knownTotal -gt 0) { [int](($knownDone * 100) / $knownTotal) } else { 0 }
            $highestPercent = [Math]::Max($highestPercent, [Math]::Min(99, $currentPercent))
            $overallPercent = 35 + [int](63 * $highestPercent / 100)
            $now = Get-Date
            $elapsed = [Math]::Max(0.001, ($now - $lastAt).TotalSeconds)
            $speed = [Math]::Max(0, ($knownDone - $lastBytes) / 1MB / $elapsed)
            $lastAt = $now
            $lastBytes = $knownDone
            $status = [string]$item.status
            if (-not $status) { $status = "正在下载模型文件" }
            $doneMb = [Math]::Round($knownDone / 1MB, 1)
            $totalMb = [Math]::Round($knownTotal / 1MB, 1)
            $message = if ($knownTotal -gt 0) {
                "正在下载视觉模型：" + $status + " · " + $doneMb + " / " + $totalMb + " MB（" + $highestPercent + "%）"
            } else {
                "正在下载视觉模型：" + $status
            }
            Write-DepsStatus -Stage "pull" -Percent $overallPercent -Message $message -FileName "qwen2.5vl-3b" -DownloadedBytes $knownDone -TotalBytes $knownTotal -DownloadPercent $highestPercent -TargetPath $modelsDir -SpeedMbS $speed
        }
        return 0
    } catch {
        Write-Host ("模型流式下载失败：" + $_.Exception.Message) -ForegroundColor Yellow
        return 1
    } finally {
        if ($reader) { try { $reader.Close() } catch { } }
        if ($response) { try { $response.Close() } catch { } }
        if ($request) { try { $request.Abort() } catch { } }
    }
}

$serverProcess = $null

try {
    Write-Host "=== 本地视觉一键安装 ==="
    Write-DepsStatus -Stage "prepare" -Percent 2 -Message "正在准备安装目录"

    New-Item -ItemType Directory -Force -Path $visionDir | Out-Null
    $env:OLLAMA_MODELS = $modelsDir

    if (-not (Test-Path -LiteralPath $ollamaExe)) {
        Write-DepsStatus -Stage "download" -Percent 8 -Message "正在下载 Ollama 0.32.14 运行器（约 1.36 GiB，支持断点续传）"
        $urls = @(
            "https://gh-proxy.com/https://github.com/ollama/ollama/releases/download/v0.32.14/ollama-windows-amd64.zip",
            "https://ghfast.top/https://github.com/ollama/ollama/releases/download/v0.32.14/ollama-windows-amd64.zip",
            "https://github.com/ollama/ollama/releases/download/v0.32.14/ollama-windows-amd64.zip"
        )
        Invoke-Download -Urls $urls -Output $zipPath -Stage "download" -StartPercent 8 -EndPercent 24 -Label " Ollama 运行器" -ExpectedSize 1459874325 -ExpectedSha256 "5ae5bca5f0d297f5e35665e01db399a69a8eac3f8fad89cd9d2531fd495c9457"
        Write-DepsStatus -Stage "extract" -Percent 25 -Message "正在解压 Ollama"
        Expand-Archive -LiteralPath $zipPath -DestinationPath $ollamaDir -Force
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-OllamaModel)) {
        Write-DepsStatus -Stage "service" -Percent 30 -Message "正在启动临时 Ollama 本地服务"
        $port = Get-FreeTcpPort
        $env:OLLAMA_HOST = "127.0.0.1:" + $port
        $serverProcess = Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WorkingDirectory $ollamaDir -WindowStyle Hidden -PassThru
        Wait-OllamaReady -Process $serverProcess -HostAddress $env:OLLAMA_HOST
        Write-DepsStatus -Stage "pull" -Percent 34 -Message "正在下载视觉模型 Qwen2.5-VL 3B（约 3 GB，视网速需要几分钟）"
        $pullCode = Invoke-OllamaPull -HostAddress $env:OLLAMA_HOST -Model "qwen2.5vl:3b"
        if ($pullCode -ne 0) { throw "模型下载失败（退出码 $pullCode）。" }
    }

    if (-not (Test-OllamaModel)) {
        throw "模型文件校验失败，请重试。"
    }
    Write-DepsStatus -Stage "done" -Percent 100 -Message "本地视觉安装完成，回到应用点击「重新检查」即可使用" -Done $true
    Write-Host "=== 安装完成 ==="
    Write-Host "回到澪的界面，点击「重新检查」，屏幕观察就能使用本地视觉了。"
} catch {
    Write-DepsFail -Message ("本地视觉安装失败：" + $_.Exception.Message)
    Write-Host "按任意键关闭窗口..." 
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
} finally {
    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        try { Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue } catch { }
        try { $serverProcess.WaitForExit(5000) | Out-Null } catch { }
    }
}
