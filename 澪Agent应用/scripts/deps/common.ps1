# deps 公共函数库：状态写入、下载（多源重试+进度）、venv 准备、原生命令执行。
# 注意：本文件必须保持 UTF-8（带 BOM），且字符串统一使用「」引号，兼容 PowerShell 5.1。
# PowerShell 5.1 在 ErrorActionPreference=Stop 时会把原生命令的 stderr 当作异常中断，
# 因此原生命令 stderr 不参与进度主循环；下载进度通过轮询输出文件大小计算并写入状态文件。

function Write-DepsStatus {
    param(
        [string]$Stage,
        [int]$Percent,
        [string]$Message,
        [string]$ErrorText = "",
        [bool]$Done = $false,
        [string]$FileName = "",
        [long]$DownloadedBytes = 0,
        [long]$TotalBytes = 0,
        [int]$DownloadPercent = 0,
        [string]$TargetPath = "",
        [double]$SpeedMbS = 0
    )
    $payload = [ordered]@{
        id         = $env:MIO_DEP_ID
        stage      = $Stage
        percent    = $Percent
        message    = $Message
        error      = $ErrorText
        done       = $Done
        file_name  = $FileName
        downloaded_bytes = $DownloadedBytes
        total_bytes = $TotalBytes
        download_percent = $DownloadPercent
        target_path = $TargetPath
        speed_mb_s = [Math]::Round([Math]::Max(0, $SpeedMbS), 2)
        updated_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    }
    try {
        $payload | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:MIO_STATUS_FILE -Encoding UTF8
    } catch {
    }
}

function Write-DepsFail {
    param([string]$Message)
    Write-Host ("[失败] " + $Message) -ForegroundColor Red
    Write-DepsStatus -Stage "error" -Percent 0 -Message $Message -ErrorText $Message -Done $true
}

function Find-MioModelPackage {
    param(
        [Parameter(Mandatory = $true)][string]$FileName,
        [string]$ExplicitPath = ""
    )

    $candidates = New-Object System.Collections.Generic.List[string]
    if ($ExplicitPath) { $candidates.Add($ExplicitPath) }

    $programRoots = @()
    if ($env:MIO_PROGRAM_DIR) { $programRoots += $env:MIO_PROGRAM_DIR }
    try {
        # release: Mio\_internal\agent_scripts\deps -> Mio
        $programRoots += (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..\..")).Path
    } catch {
    }
    foreach ($root in @($programRoots | Select-Object -Unique)) {
        if (-not $root) { continue }
        $candidates.Add((Join-Path $root ("模型\" + $FileName)))
        $candidates.Add((Join-Path $root $FileName))
        $parent = Split-Path -Parent $root
        if ($parent) { $candidates.Add((Join-Path $parent ("模型\" + $FileName))) }
    }

    if ($env:MIO_DATA_DIR) {
        $installSourceFile = Join-Path $env:MIO_DATA_DIR "安装来源目录.txt"
        if (Test-Path -LiteralPath $installSourceFile -PathType Leaf) {
            $installSource = ([string](Get-Content -LiteralPath $installSourceFile -Raw -Encoding UTF8)).Trim()
            if ($installSource) {
                $candidates.Add((Join-Path $installSource ("模型\" + $FileName)))
                $candidates.Add((Join-Path $installSource $FileName))
            }
        }
    }
    if ($env:MIO_VOICE_TRAINING_DIR) {
        $candidates.Add((Join-Path $env:MIO_VOICE_TRAINING_DIR ("downloads\" + $FileName)))
    }
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE ("Downloads\模型\" + $FileName)))
        $candidates.Add((Join-Path $env:USERPROFILE ("Downloads\" + $FileName)))
        $candidates.Add((Join-Path $env:USERPROFILE ("Desktop\模型\" + $FileName)))
        $candidates.Add((Join-Path $env:USERPROFILE ("Desktop\" + $FileName)))
    }

    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

function Assert-MioModelPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label,
        [long]$ExpectedSize = 0,
        [string]$ExpectedSha256 = ""
    )
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($ExpectedSize -gt 0 -and $item.Length -ne $ExpectedSize) {
        throw ($Label + "大小不符：实际 " + $item.Length + " 字节，要求 " + $ExpectedSize + " 字节。请删除旧包并重新取得当前版本。")
    }
    if ($ExpectedSha256) {
        $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedHash = $ExpectedSha256.Trim().ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw ($Label + " SHA-256 不符：实际 " + $actualHash + "，要求 " + $expectedHash + "。文件可能是旧版或下载不完整。")
        }
    }
    return $item
}

function Limit-Int64 {
    param(
        [long]$Value,
        [long]$Minimum = 0,
        [long]$Maximum = [long]::MaxValue
    )
    if ($Maximum -lt $Minimum) { throw "64 位数值范围无效。" }
    if ($Value -lt $Minimum) { return [long]$Minimum }
    if ($Value -gt $Maximum) { return [long]$Maximum }
    return [long]$Value
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $FilePath @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                Write-Host ([string]$_.Exception.Message)
            } else {
                Write-Host ([string]$_)
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $exitCode
}

function Resolve-UrlContentLength {
    param([Parameter(Mandatory = $true)][string]$Url)
    # HEAD 请求尽量拿 Content-Length；拿不到返回 -1（下载时按已下载 MB 显示，不显示百分比）。
    try {
        $lengthText = & curl.exe -sIL --connect-timeout 15 --max-time 30 -o NUL -w "%{content_length}" $Url 2>$null
        $all = [string]::Join("`n", $lengthText)
        $last = ($all -split "`n")[-1].Trim()
        $parsed = 0L
        if ([long]::TryParse($last.Trim(), [ref]$parsed) -and $parsed -gt 0) {
            return $parsed
        }
    } catch {
    }
    return -1
}

function Invoke-Download {
    param(
        [string[]]$Urls,
        [string]$Output,
        [string]$Stage = "downloading",
        [int]$StartPercent = 0,
        [int]$EndPercent = 99,
        [string]$Label = "文件",
        [long]$ExpectedSize = 0,
        [string]$ExpectedSha256 = ""
    )
    $partial = $Output + ".part"
    $fileName = Split-Path -Leaf $Output
    $expectedHash = $ExpectedSha256.Trim().ToLowerInvariant()

    function Test-DownloadFile {
        param([string]$Path)
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
        $size = (Get-Item -LiteralPath $Path).Length
        if ($size -le 0) { return $false }
        if ($ExpectedSize -gt 0 -and $size -ne $ExpectedSize) { return $false }
        if ($expectedHash) {
            $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualHash -ne $expectedHash) { return $false }
        }
        return $true
    }

    if (Test-DownloadFile -Path $Output) {
        $existingSize = (Get-Item -LiteralPath $Output).Length
        Write-DepsStatus -Stage $Stage -Percent $EndPercent -Message ($Label + "已存在且校验通过") -FileName $fileName -DownloadedBytes $existingSize -TotalBytes $existingSize -DownloadPercent 100 -TargetPath $Output
        return
    }
    if (Test-Path -LiteralPath $Output) {
        Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue
    }
    if ($ExpectedSize -gt 0 -and (Test-Path -LiteralPath $partial)) {
        if ((Get-Item -LiteralPath $partial).Length -gt $ExpectedSize) {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
    }

    $lastError = ""
    foreach ($url in $Urls) {
        $totalBytes = if ($ExpectedSize -gt 0) { $ExpectedSize } else { Resolve-UrlContentLength -Url $url }
        Write-Host ("正在下载 " + $url)
        $quotedUrl = [char]34 + $url + [char]34
        $argLine = "-sS -L --fail --retry 3 --retry-all-errors --connect-timeout 30 --max-time 7200 --speed-limit 1024 --speed-time 120 -C - -o " + [char]34 + $partial + [char]34 + " " + $quotedUrl
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "curl.exe"
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardError = $false
        $psi.RedirectStandardOutput = $false
        $psi.Arguments = $argLine
        $proc = New-Object System.Diagnostics.Process
        $proc.StartInfo = $psi
        try {
            if (-not $proc.Start()) { throw "下载进程启动失败。" }
        } catch {
            $lastError = $_.Exception.Message
            Write-Host ("该下载源启动失败：" + $lastError) -ForegroundColor Yellow
            continue
        }
        try {
            $sampleAt = Get-Date
            $sampleBytes = if (Test-Path -LiteralPath $partial) { (Get-Item -LiteralPath $partial).Length } else { 0L }
            while (-not $proc.HasExited) {
                Start-Sleep -Milliseconds 500
                $size = 0L
                if (Test-Path -LiteralPath $partial) {
                    try { $size = (Get-Item -LiteralPath $partial -ErrorAction Stop).Length } catch { $size = 0 }
                }
                if ($totalBytes -gt 0) {
                    $downloadPct = [Math]::Min(99, [int](($size * 100) / $totalBytes))
                    $pct = $StartPercent + [int](($EndPercent - $StartPercent) * $downloadPct / 100)
                    $mb = [Math]::Round($size / 1MB, 1)
                    $tmb = [Math]::Round($totalBytes / 1MB, 1)
                    $now = Get-Date
                    $elapsed = [Math]::Max(0.001, ($now - $sampleAt).TotalSeconds)
                    $speed = [Math]::Max(0, ($size - $sampleBytes) / 1MB / $elapsed)
                    Write-DepsStatus -Stage $Stage -Percent $pct -Message ("正在下载" + $Label + "：" + $mb + " / " + $tmb + " MB（" + $downloadPct + "%）") -FileName $fileName -DownloadedBytes $size -TotalBytes $totalBytes -DownloadPercent $downloadPct -TargetPath $Output -SpeedMbS $speed
                    $sampleAt = $now
                    $sampleBytes = $size
                } else {
                    $mb = [Math]::Round($size / 1MB, 1)
                    Write-DepsStatus -Stage $Stage -Percent $StartPercent -Message ("正在下载" + $Label + "：已下载 " + $mb + " MB") -FileName $fileName -DownloadedBytes $size -TargetPath $Output
                }
            }
            $proc.WaitForExit()
            Start-Sleep -Milliseconds 300
            if ($proc.ExitCode -eq 0) {
                $finalSize = (Get-Item -LiteralPath $partial -ErrorAction SilentlyContinue).Length
                if ($finalSize -gt 0 -and (Test-DownloadFile -Path $partial)) {
                    Move-Item -LiteralPath $partial -Destination $Output -Force
                    Write-DepsStatus -Stage $Stage -Percent $EndPercent -Message ($Label + "下载完成并校验通过") -FileName $fileName -DownloadedBytes $finalSize -TotalBytes $(if ($totalBytes -gt 0) { $totalBytes } else { $finalSize }) -DownloadPercent 100 -TargetPath $Output
                    return
                }
                if ($ExpectedSize -gt 0 -and $finalSize -ne $ExpectedSize) {
                    throw ("下载文件大小不正确：" + $finalSize + " / " + $ExpectedSize + " 字节")
                }
                if ($expectedHash) { throw "下载文件 SHA-256 校验失败。" }
                throw "下载完成但文件为空或不完整，可能被拦截。"
            }
            throw ("下载失败，退出码 " + $proc.ExitCode)
        } catch {
            $lastError = $_.Exception.Message
            Write-Host ("该下载源失败：" + $lastError) -ForegroundColor Yellow
            if (-not $proc.HasExited) { try { $proc.Kill() } catch { } }
        }
    }
    Write-DepsFail -Message ("所有下载源都失败：" + $lastError)
    throw ("所有下载源都失败：" + $lastError)
}


function Ensure-DepsVenv {
    # 确保「工作区\音色训练\.voice-env\Scripts\python.exe」存在。
    # 优先使用系统已安装的 Python 3.10-3.12 创建 venv；没有才下载安装。
    # base Python 装在纯英文路径（%LOCALAPPDATA%），Python 安装器对中文 TargetDir 支持不好。
    $voiceDir = if ($env:MIO_VOICE_TRAINING_DIR) { $env:MIO_VOICE_TRAINING_DIR } else { Join-Path $env:MIO_WORKSPACE_ROOT "音色训练" }
    $venvDir = Join-Path $voiceDir ".voice-env"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) { return $venvPython }

    New-Item -ItemType Directory -Force -Path $voiceDir | Out-Null

    # 1) 优先用系统已有的 Python（py launcher 或 PATH）
    $systemPython = ""
    try {
        $candidate = & py -3.10 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate) { $systemPython = [string]$candidate }
    } catch {
    }
    if (-not $systemPython) {
        try {
            $candidate = & python -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                $versionLine = & python --version 2>$null
                if ($versionLine -match "3\.(10|11|12)") { $systemPython = [string]$candidate }
            }
        } catch {
        }
    }
    if ($systemPython -and (Test-Path -LiteralPath $systemPython)) {
        Write-Host ("正在使用系统 Python： " + $systemPython)
        $venvCode = Invoke-Native -FilePath $systemPython -Arguments @("-m", "venv", $venvDir)
        if ($venvCode -eq 0 -and (Test-Path -LiteralPath $venvPython)) {
            return $venvPython
        }
        Write-Host "系统 Python 创建独立环境失败，改用自带安装包。" -ForegroundColor Yellow
    }

    # 2) 没有合适的系统 Python 时，下载 Python 3.10.11 静默安装
    $baseDir = Join-Path $env:LOCALAPPDATA "MioAgentRuntime\Python310"
    $basePython = Join-Path $baseDir "python.exe"
    if (-not (Test-Path -LiteralPath $basePython)) {
        Write-Host "正在下载 Python 运行时（国内镜像）..."
        $installer = Join-Path $env:TEMP "mio-python-3.10.11-setup.exe"
        $urls = @(
            "https://mirrors.huaweicloud.com/python/3.10.11/python-3.10.11-amd64.exe",
            "https://registry.npmmirror.com/-/binary/python/3.10.11/python-3.10.11-amd64.exe",
            "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe"
        )
        Invoke-Download -Urls $urls -Output $installer -Stage "downloading_python" -StartPercent 2 -EndPercent 14 -Label " Python 运行环境"
        Write-Host "正在静默安装 Python（只装给澪使用，不影响系统）..."
        $proc = Start-Process -FilePath $installer -ArgumentList @("/quiet", "InstallAllUsers=0", "TargetDir=$baseDir", "PrependPath=0", "Include_pip=1", "Include_launcher=0", "Include_test=0", "AssociateFiles=0", "Shortcuts=0", "SimpleInstall=0") -Wait -PassThru
        if ($proc.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $basePython)) {
            throw "Python 安装失败（退出码 $($proc.ExitCode)）。如果杀毒软件拦截，请允许后重试。"
        }
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    Write-Host "正在创建独立 Python 环境..."
    $venvCode = Invoke-Native -FilePath $basePython -Arguments @("-m", "venv", $venvDir)
    if ($venvCode -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "独立 Python 环境创建失败。"
    }
    return $venvPython
}

function Invoke-DepsPip {
    param(
        [string]$Python,
        [string[]]$Packages,
        [string]$Message
    )
    Write-Host $Message
    $upgradeCode = Invoke-Native -FilePath $Python -Arguments @("-m", "pip", "install", "--upgrade", "pip", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
    $installArgs = @("-m", "pip", "install") + $Packages + @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple")
    $installCode = Invoke-Native -FilePath $Python -Arguments $installArgs
    if ($installCode -ne 0) {
        throw ("依赖安装失败：" + ($Packages -join ", "))
    }
}
