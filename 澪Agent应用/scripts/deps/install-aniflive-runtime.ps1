# UTF-8 BOM is added when this file is packaged for Windows PowerShell 5.1.
param([Parameter(Mandatory=$true)][string]$JobFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$job = Get-Content -LiteralPath $JobFile -Raw -Encoding UTF8 | ConvertFrom-Json
$env:MIO_STATUS_FILE = ''
. (Join-Path $PSScriptRoot 'common.ps1')
function Write-SetupState([string]$Stage, [string]$Message) {
    $state = @{ schema=1; job_id=$job.job_id; stage=$Stage; message=$Message; percent=1; ready=$false }
    $temp = [string]$job.state_path + '.bootstrap.tmp'
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination $job.state_path -Force
}
$setupLease = $null
try {
    $leasePath = Join-Path (Split-Path ([string]$job.root) -Parent) '.mio-anif-run.lock'
    $setupLease = [System.IO.File]::Open($leasePath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    Write-SetupState 'python' '正在准备独立语音运行环境，可稍后返回查看进度。'
    if (Test-Path -LiteralPath $job.cancel_path) { exit 2 }
    $setupPython = ''
    foreach ($candidateCommand in @('py', 'python')) {
        try {
            $candidateArgs = if ($candidateCommand -eq 'py') { @('-3.10') } else { @() }
            $candidate = & $candidateCommand @candidateArgs -X utf8 -c 'import sys; print(sys.executable) if sys.version_info[:2]==(3,10) and sys.maxsize>2**32 else sys.exit(1)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath ([string]$candidate))) { $setupPython = [string]$candidate; break }
        } catch {}
    }
    if (-not $setupPython) {
        $baseDir = Join-Path $env:LOCALAPPDATA 'MioAgentRuntime\Python310'
        $setupPython = Join-Path $baseDir 'python.exe'
        if (-not (Test-Path -LiteralPath $setupPython)) {
            $downloadDir = Join-Path $job.root 'downloads'
            New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
            $installer = Join-Path $downloadDir 'python-3.10.11-amd64.exe'
            Invoke-Download -Urls @('https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe', 'https://mirrors.huaweicloud.com/python/3.10.11/python-3.10.11-amd64.exe') -Output $installer -ExpectedSize 29037240 -ExpectedSha256 'd8dede5005564b408ba50317108b765ed9c3c510342a598f9fd42681cbe0648b'
            if (Test-Path -LiteralPath $job.cancel_path) { exit 2 }
            $signature = Get-AuthenticodeSignature -LiteralPath $installer
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'Python 安装器签名不符，已停止安装。' }
            $proc = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', ('TargetDir="' + $baseDir + '"'), 'PrependPath=0', 'Include_pip=1', 'Include_launcher=0', 'Include_test=0', 'AssociateFiles=0', 'Shortcuts=0') -WindowStyle Hidden -Wait -PassThru
            if ($proc.ExitCode -ne 0) { throw 'Python 运行环境安装失败，请重试。' }
            if (-not (Test-Path -LiteralPath $setupPython)) { throw 'Python 安装器未生成运行程序，请修复 Python 3.10（64 位）后重试。' }
        }
    }
    if (Test-Path -LiteralPath $job.cancel_path) { exit 2 }
    $worker = Join-Path (Split-Path $PSScriptRoot -Parent) 'aniflive_setup_worker.py'
    & $setupPython -X utf8 $worker --job $JobFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch {
    Write-SetupState 'failed' ('运行环境准备失败：' + $_.Exception.Message)
    exit 1
} finally {
    if ($setupLease) { $setupLease.Dispose() }
}
