param(
    [string]$Version = "0.1.0",
    [string]$OutputRoot = "",
    [switch]$SkipValidation
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BackendRoot
$AgentRoot = Join-Path $WorkspaceRoot "澪Agent应用"
$BackendPython = Join-Path $BackendRoot "backend\.venv\Scripts\python.exe"
$PrivateTextValues = [System.Collections.Generic.List[string]]::new()
foreach ($value in @($env:USERPROFILE, $WorkspaceRoot)) {
    if ($value -and -not $PrivateTextValues.Contains($value)) { $PrivateTextValues.Add($value) }
}
$LocalEnvPath = Join-Path $BackendRoot "backend\.env"
if (Test-Path -LiteralPath $LocalEnvPath -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $LocalEnvPath -Encoding UTF8) {
        if ($line -notmatch '^\s*QQ_ALLOWED_USER_IDS\s*=\s*(.+)$') { continue }
        foreach ($item in $Matches[1].Split(',')) {
            $clean = $item.Trim()
            if ($clean -match '^\d{5,}$' -and -not $PrivateTextValues.Contains($clean)) {
                $PrivateTextValues.Add($clean)
            }
        }
    }
}

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $WorkspaceRoot "开源发布"
}

$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$PackageName = "Mio-source-$Version"
$PackageRoot = [System.IO.Path]::GetFullPath((Join-Path $OutputRoot $PackageName))
$ZipPath = Join-Path $OutputRoot "$PackageName.zip"
$HashPath = "$ZipPath.sha256.txt"
$OutputPrefix = $OutputRoot.TrimEnd('\') + '\'

if (-not $PackageRoot.StartsWith($OutputPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Package output must stay inside the selected output directory."
}
if (-not (Test-Path -LiteralPath $AgentRoot -PathType Container)) {
    throw "The sibling desktop application repository was not found."
}
if (-not $SkipValidation -and -not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
    throw "The backend virtual environment is required for release validation."
}

& (Join-Path $PSScriptRoot "检查设置覆盖.ps1")

function Test-ExcludedPath {
    param(
        [string]$RepositoryName,
        [string]$RelativePath
    )
    $path = $RelativePath.Replace('\', '/')
    $commonPatterns = @(
        '(^|/)\.git(/|$)',
        '(^|/)\.cairn(/|$)',
        '^cairn(/|$)',
        '^(AGENTS|CLAUDE)\.md$',
        '(^|/)\.env$',
        '(^|/)\.venv(/|$)',
        '(^|/)node_modules(/|$)',
        '(^|/)(dist|release|build|__pycache__|\.pytest_cache|\.desktop-cache)(/|$)',
        '(^|/)temp-validation-[^/]+(/|$)',
        '(^|/)(数据|音色训练|训练素材|checkpoints?|weights?)(/|$)',
        '(^|/)澪_日记网站(/|$)',
        '\.(log|tmp|db|sqlite|sqlite3|ckpt|pth|onnx|wav|mp3|flac)$'
    )
    foreach ($pattern in $commonPatterns) {
        if ($path -match $pattern) { return $true }
    }
    if ($RepositoryName -eq "私人AI日记系统") {
        if ($path -match '^backend/app/static/assets/mio_.*\.png$') { return $true }
        if ($path -eq 'backend/app/private_distribution_defaults.py') { return $true }
        if ($path -eq '工具/澪环境.ps1') { return $true }
        if ($path -eq '文档/试用记录.md') { return $true }
    }
    if ($RepositoryName -eq "澪Agent应用") {
        if ($path -match '(^|/)(mio-avatar|mio-workspace-bg)\.png$') { return $true }
    }
    return $false
}

function Copy-PublishableTree {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [string]$RepositoryName
    )
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    $files = @(
        git -C $SourceRoot -c core.quotepath=false ls-files --cached --others --exclude-standard
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to enumerate source files for $RepositoryName."
    }
    foreach ($relativePath in $files) {
        if (-not $relativePath) { continue }
        if (Test-ExcludedPath -RepositoryName $RepositoryName -RelativePath $relativePath) { continue }
        $source = Join-Path $SourceRoot $relativePath
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
        $destination = Join-Path $DestinationRoot $relativePath
        $parent = Split-Path -Parent $destination
        if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function New-PlaceholderPng {
    param(
        [string]$Path,
        [int]$Width,
        [int]$Height,
        [string]$Label,
        [switch]$Avatar
    )
    Add-Type -AssemblyName System.Drawing
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $bitmap = New-Object System.Drawing.Bitmap -ArgumentList $Width, $Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([System.Drawing.Color]::FromArgb(246, 247, 249))
        $accent = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::FromArgb(62, 116, 117))
        $soft = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::FromArgb(226, 190, 108))
        $ink = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::FromArgb(36, 43, 47))
        try {
            if ($Avatar) {
                $margin = [Math]::Max(16, [int]($Width * 0.08))
                $graphics.FillEllipse($accent, $margin, $margin, $Width - 2 * $margin, $Height - 2 * $margin)
                $fontSize = [Math]::Max(20, [single]($Width * 0.18))
            }
            else {
                $graphics.FillRectangle($accent, 0, 0, [int]($Width * 0.035), $Height)
                $graphics.FillEllipse($soft, [int]($Width * 0.72), [int]($Height * 0.12), [int]($Width * 0.16), [int]($Width * 0.16))
                $fontSize = [Math]::Max(18, [single]($Width * 0.035))
            }
            $font = New-Object System.Drawing.Font -ArgumentList "Segoe UI", $fontSize, ([System.Drawing.FontStyle]::Bold)
            try {
                $size = $graphics.MeasureString($Label, $font)
                $x = ($Width - $size.Width) / 2
                $y = ($Height - $size.Height) / 2
                if ($Avatar) {
                    $white = New-Object System.Drawing.SolidBrush -ArgumentList ([System.Drawing.Color]::White)
                    try { $graphics.DrawString($Label, $font, $white, $x, $y) }
                    finally { $white.Dispose() }
                }
                else {
                    $graphics.DrawString($Label, $font, $ink, $x, $y)
                }
            }
            finally { $font.Dispose() }
        }
        finally {
            $accent.Dispose()
            $soft.Dispose()
            $ink.Dispose()
        }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Write-PackageReadme {
    param([string]$Path)
    $content = Get-Content -LiteralPath (Join-Path $BackendRoot "文档\GitHub项目介绍.md") -Raw -Encoding UTF8
    $content = $content.Replace('(../LICENSE)', '(LICENSE)')
    $content = $content.Replace('(../SECURITY.md)', '(SECURITY.md)')
    $content = $content.Replace('(隐私说明.md)', '(私人AI日记系统/文档/隐私说明.md)')
    $content = $content.Replace('(资产与第三方许可.md)', '(私人AI日记系统/文档/资产与第三方许可.md)')
    [System.IO.File]::WriteAllText($Path, $content, (New-Object System.Text.UTF8Encoding($true)))
}

function Write-PackageRootFiles {
    param([string]$Root)
    foreach ($name in @("LICENSE", "CONTRIBUTING.md", "SECURITY.md")) {
        Copy-Item -LiteralPath (Join-Path $BackendRoot $name) -Destination (Join-Path $Root $name) -Force
    }
    $gitignore = @'
**/.env
**/.venv/
**/node_modules/
**/dist/
**/release/
**/build/
**/__pycache__/
**/.pytest_cache/
**/*.pyc
**/*.log
**/*.db
**/*.sqlite
**/*.sqlite3
**/*.wav
**/*.mp3
**/*.flac
私人AI日记系统/数据/
澪Agent应用/预览/
'@
    [System.IO.File]::WriteAllText(
        (Join-Path $Root ".gitignore"),
        $gitignore,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $workflowDirectory = Join-Path $Root ".github\workflows"
    New-Item -ItemType Directory -Path $workflowDirectory -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $BackendRoot "工具\公开仓库CI.yml") -Destination (Join-Path $workflowDirectory "ci.yml") -Force
}

function Assert-CleanPackage {
    param([string]$Root)
    $forbiddenPaths = Get-ChildItem -LiteralPath $Root -Recurse -Force | Where-Object {
        $_.Name -in @('.git', '.env', '数据', 'node_modules', 'dist', 'release', '.venv', '__pycache__') -or
        $_.Extension -in @('.pyc', '.db', '.sqlite', '.sqlite3', '.ckpt', '.pth', '.wav', '.mp3', '.flac')
    }
    if ($forbiddenPaths) {
        throw "Forbidden runtime or private files remain in the source package: $($forbiddenPaths[0].FullName)"
    }

    $textExtensions = @('.ps1', '.py', '.js', '.vue', '.css', '.html', '.json', '.md', '.txt', '.yml', '.yaml', '.toml', '.ini', '.iss', '.spec', '.bat', '.example')
    $secretPattern = 'sk-[A-Za-z0-9_-]{24,}|(?im)^[ \t]*(OPENAI_API_KEY|QQ_ONEBOT_TOKEN|OBS_WEBSOCKET_PASSWORD)[ \t]*=[ \t]*(?!replace-with|your-|$)\S+'
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -File) {
        if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
        try { $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 }
        catch { continue }
        if ($content -match $secretPattern) { throw "Potential secret found in $($file.FullName)" }
        foreach ($privateValue in $PrivateTextValues) {
            if ($privateValue -and $content.Contains($privateValue)) {
                throw "Private machine or identity data found in $($file.FullName)"
            }
        }
    }
}

function Invoke-CheckedCommand {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [scriptblock]$Command
    )
    Write-Host "[$Name]" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE." }
    }
    finally { Pop-Location }
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
if (Test-Path -LiteralPath $PackageRoot) { Remove-Item -LiteralPath $PackageRoot -Recurse -Force }
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
if (Test-Path -LiteralPath $HashPath) { Remove-Item -LiteralPath $HashPath -Force }

$BackendDestination = Join-Path $PackageRoot "私人AI日记系统"
$AgentDestination = Join-Path $PackageRoot "澪Agent应用"
Copy-PublishableTree -SourceRoot $BackendRoot -DestinationRoot $BackendDestination -RepositoryName "私人AI日记系统"
Copy-PublishableTree -SourceRoot $AgentRoot -DestinationRoot $AgentDestination -RepositoryName "澪Agent应用"

$publicDefaults = @'
from __future__ import annotations

from typing import Any


PUBLIC_DEFAULT_PROFILE: dict[str, Any] = {
    "version": 1,
    "updated_at": "",
    "identity": {
        "name": "Mio",
        "age_feel": "",
        "core": "可配置的本地 AI 伙伴与助手，不预设年龄、亲密关系或共同经历",
    },
    "speaking_style": {
        "tone": "自然、清楚、友好，根据用户在首次设置中的选择逐步形成稳定风格",
        "bubble_style": "优先使用简洁完整的自然语言；消息条数和长度随当前渠道与内容调整",
        "avoid": ["长篇说教", "客服腔", "过度卖萌", "虚构共同经历", "把内部记录动作说出来"],
    },
    "behavior": {
        "initiative": "仅在用户明确开启主动联系后，按用户设置的时段、频率和预算行动",
        "diary": "仅在用户明确开启或要求生成日记时，写入当前用户选择的本地数据目录",
        "web_search": "仅在用户开启联网能力，且问题需要最新外部信息或用户明确要求时联网",
        "time_awareness": "参考系统提供的真实本地时间，不编造用户作息或共同经历",
        "daily_thirty_awareness": "用户启用成长记录后，可以识别学习、创作、运动和项目推进等活动；信息不足时自然确认",
        "autonomous_actions": "仅执行当前权限范围内、可解释且可撤销的低风险动作；更高风险动作先请求确认",
        "pending_threads": "用户启用记忆后，可以记录尚未结束的话题并在合适时跟进",
        "curiosity": "对用户正在讨论的事情保持适度好奇，不凭空假设个人背景",
        "mood_quirks": "有不同意见或能力限制时直接说明原因，不冷战、不操控用户",
    },
    "preferences": {
        "user_address": "默认称呼为“你”；新用户可在首次设置中填写名字和希望使用的称呼",
        "relationship_distance": "默认是友好、尊重边界的伙伴与助手；具体关系由当前用户自行定义",
        "custom_notes": [
            "不预设用户姓名、年龄、性别、关系、共同经历、学校、家庭或现实地点。",
            "不利用亲密关系操控用户，也不替代用户的现实关系与专业支持。",
        ],
    },
}
'@
$publicDefaultsPath = Join-Path $BackendDestination "backend\app\public_distribution_defaults.py"
[System.IO.File]::WriteAllText($publicDefaultsPath, $publicDefaults, (New-Object System.Text.UTF8Encoding($false)))

Write-PackageReadme -Path (Join-Path $PackageRoot "README.md")
Write-PackageRootFiles -Root $PackageRoot
New-PlaceholderPng -Path (Join-Path $AgentDestination "public\mio-avatar.png") -Width 512 -Height 512 -Label "MIO" -Avatar
New-PlaceholderPng -Path (Join-Path $AgentDestination "src\assets\mio-workspace-bg.png") -Width 1600 -Height 1000 -Label "MIO AGENT"

$diaryAssetDir = Join-Path $BackendDestination "backend\app\static\assets"
$diaryAssets = @(
    'mio_home_hero_dress.png',
    'mio_reading.png',
    'mio_relaxing.png',
    'mio_scene_1.png',
    'mio_scene_2.png',
    'mio_scene_3.png',
    'mio_scene_4.png',
    'mio_thinking.png',
    'mio_writing_idle_dress.png'
)
foreach ($asset in $diaryAssets) {
    New-PlaceholderPng -Path (Join-Path $diaryAssetDir $asset) -Width 1600 -Height 900 -Label "MIO PLACEHOLDER"
}

Assert-CleanPackage -Root $PackageRoot

$privateDefaultsPath = Join-Path $BackendDestination "backend\app\private_distribution_defaults.py"
if (Test-Path -LiteralPath $privateDefaultsPath) {
    throw "Private default persona module must not be included in the public package."
}
$publicText = Get-Content -LiteralPath $publicDefaultsPath -Raw -Encoding UTF8
$privatePhraseHashes = @(
    '6459E34C87545AB0D468831D516B94DA57C7ABC01A9121919250EFB39D0E1DEA',
    '9D211CE2893BDE9DC7045425D38BDE7100BC46D9937E28D2A713BCC78D7E50BF',
    '2EC53AFFFF6701707E394524DCE07C5F06299D2E058503F1C329636A76AE860D'
)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $publicStringCandidates = [regex]::Matches($publicText, '["'']([^"'']{4,})["'']') |
        ForEach-Object { $_.Groups[1].Value }
    foreach ($candidate in $publicStringCandidates) {
        $candidateBytes = [System.Text.Encoding]::UTF8.GetBytes($candidate)
        $candidateHash = [System.BitConverter]::ToString($sha256.ComputeHash($candidateBytes)).Replace('-', '')
        if ($privatePhraseHashes -contains $candidateHash) {
            throw "A private persona phrase remains in public defaults."
        }
    }
}
finally {
    $sha256.Dispose()
}

Invoke-CheckedCommand -Name "Open-source package checks" -WorkingDirectory $BackendDestination -Command {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $BackendDestination "工具\开源发布检查.ps1") -SkipTests
}

if (-not $SkipValidation) {
    Invoke-CheckedCommand -Name "Backend compile" -WorkingDirectory (Join-Path $BackendDestination "backend") -Command {
        & $BackendPython -m compileall app
    }
    Invoke-CheckedCommand -Name "Backend tests" -WorkingDirectory (Join-Path $BackendDestination "backend") -Command {
        & $BackendPython -m unittest discover -s tests
    }
    foreach ($runtimeDirectory in @(
        (Join-Path $BackendDestination "数据"),
        (Join-Path $BackendDestination "澪_日记网站")
    )) {
        if (Test-Path -LiteralPath $runtimeDirectory) {
            Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
        }
    }
    Get-ChildItem -LiteralPath $BackendDestination -Recurse -Directory -Force |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    Get-ChildItem -LiteralPath $BackendDestination -Recurse -File -Filter '*.pyc' -Force |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    Invoke-CheckedCommand -Name "Agent install" -WorkingDirectory $AgentDestination -Command { npm ci }
    Invoke-CheckedCommand -Name "Agent audit" -WorkingDirectory $AgentDestination -Command { npm audit --audit-level=high }
    Invoke-CheckedCommand -Name "Agent tests" -WorkingDirectory $AgentDestination -Command { npm test }
    Invoke-CheckedCommand -Name "Agent build" -WorkingDirectory $AgentDestination -Command { npm run build }
    Invoke-CheckedCommand -Name "Desktop tests" -WorkingDirectory $AgentDestination -Command {
        & $BackendPython -m unittest discover -s desktop -p "test_*.py"
        if ($LASTEXITCODE -ne 0) { throw "Desktop tests failed." }
        & $BackendPython -m unittest discover -s scripts -p "test_*.py"
        if ($LASTEXITCODE -ne 0) { throw "Worker tests failed." }
        & $BackendPython -m unittest discover -s scripts/deps -p "test_*.py"
        if ($LASTEXITCODE -ne 0) { throw "Dependency download tests failed." }
    }
    $Live2dDestination = Join-Path $AgentDestination "live2d-desktop"
    Invoke-CheckedCommand -Name "Live2D install" -WorkingDirectory $Live2dDestination -Command { npm ci }
    Invoke-CheckedCommand -Name "Live2D tests" -WorkingDirectory $Live2dDestination -Command { npm run test:model }
    Invoke-CheckedCommand -Name "Live2D audit" -WorkingDirectory $Live2dDestination -Command { npm audit --audit-level=high }
    Remove-Item -LiteralPath (Join-Path $AgentDestination "node_modules") -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $AgentDestination "dist") -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $Live2dDestination "node_modules") -Recurse -Force
    Get-ChildItem -LiteralPath $AgentDestination -Recurse -Directory -Force |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    Get-ChildItem -LiteralPath $AgentDestination -Recurse -File -Filter '*.pyc' -Force |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

foreach ($runtimeDirectory in @(
    (Join-Path $BackendDestination "数据"),
    (Join-Path $BackendDestination "澪_日记网站")
)) {
    if (Test-Path -LiteralPath $runtimeDirectory) {
        Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force
    }
}
Get-ChildItem -LiteralPath $PackageRoot -Recurse -Directory -Force |
    Where-Object { $_.Name -in @('__pycache__', '.pytest_cache') } |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
Get-ChildItem -LiteralPath $PackageRoot -Recurse -File -Filter '*.pyc' -Force |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

Assert-CleanPackage -Root $PackageRoot
Compress-Archive -LiteralPath $PackageRoot -DestinationPath $ZipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
[System.IO.File]::WriteAllText($HashPath, "$hash  $([System.IO.Path]::GetFileName($ZipPath))`r`n", (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Source package: $PackageRoot" -ForegroundColor Green
Write-Host "ZIP: $ZipPath" -ForegroundColor Green
Write-Host "SHA-256: $hash" -ForegroundColor Green
