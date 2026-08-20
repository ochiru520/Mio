param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
$AgentRoot = Join-Path (Split-Path -Parent $BackendRoot) "澪Agent应用"
$BackendPython = Join-Path $BackendRoot "backend\.venv\Scripts\python.exe"
$Failures = [System.Collections.Generic.List[string]]::new()

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "`n[$Name]" -ForegroundColor Cyan
    try {
        & $Action
    }
    catch {
        $Failures.Add("$Name：$($_.Exception.Message)")
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
}

function Find-TrackedSecrets {
    param([string]$Repository)
    $pattern = 'sk-[A-Za-z0-9_-]{24,}|(?im)^[ \t]*(OPENAI_API_KEY|QQ_ONEBOT_TOKEN|OBS_WEBSOCKET_PASSWORD)[ \t]*=[ \t]*(?!replace-with|your-|$)\S+'
    $hits = [System.Collections.Generic.HashSet[string]]::new()
    Push-Location $Repository
    try {
        if (Test-Path -LiteralPath (Join-Path $Repository ".git")) {
            $files = @(git -c core.quotepath=false ls-files --cached --others --exclude-standard)
        }
        else {
            $files = @(Get-ChildItem -LiteralPath $Repository -Recurse -File -Force | Where-Object {
                $_.FullName -notmatch '[\\/](node_modules|dist|release|build|__pycache__|\.venv|\.pytest_cache)[\\/]'
            } | Select-Object -ExpandProperty FullName)
        }
        foreach ($file in $files) {
            if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
            try {
                $content = Get-Content -LiteralPath $file -Raw -Encoding UTF8
            }
            catch {
                continue
            }
            if ($content -match $pattern) { [void]$hits.Add($file) }
        }
    }
    finally {
        Pop-Location
    }
    return @($hits)
}

Invoke-Step "当前工作树密钥扫描" {
    foreach ($repo in @($BackendRoot, $AgentRoot)) {
        $hits = Find-TrackedSecrets -Repository $repo
        if ($hits.Count) {
            throw "$(Split-Path $repo -Leaf) 命中高风险文件：$($hits -join ', ')"
        }
        Write-Host "$(Split-Path $repo -Leaf)：未发现高置信度密钥"
    }
}

Invoke-Step "Git 历史提示" {
    $historyHits = [System.Collections.Generic.List[string]]::new()
    foreach ($repo in @($BackendRoot, $AgentRoot)) {
        if (-not (Test-Path -LiteralPath (Join-Path $repo ".git"))) {
            Write-Host "$(Split-Path $repo -Leaf)：无 Git 历史，按干净源码包处理"
            continue
        }
        Push-Location $repo
        try {
            foreach ($hit in @(git log --all -G 'sk-[A-Za-z0-9_-]{24,}|QQ_ONEBOT_TOKEN\s*=' --oneline -- .)) {
                $historyHits.Add("$(Split-Path $repo -Leaf)：$hit")
            }
        }
        finally {
            Pop-Location
        }
    }
    if ($historyHits.Count) {
        Write-Host "历史存在需要人工确认的提交：" -ForegroundColor Yellow
        $historyHits | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        throw "公开前需要建立干净仓库或经授权后重写历史"
    }
}

Invoke-Step "敏感运行文件忽略规则" {
    $targets = @(
        'backend/.env',
        '数据/personal_ai.db',
        '数据/运行设置.json',
        '数据/模型供应商.json',
        '数据/日记/示例.md',
        '数据/桌宠/设置.json'
    )
    if (-not (Test-Path -LiteralPath (Join-Path $BackendRoot ".git"))) {
        foreach ($target in $targets) {
            if (Test-Path -LiteralPath (Join-Path $BackendRoot $target)) {
                throw "干净源码包仍包含敏感运行文件：$target"
            }
        }
        Write-Host "无 Git 历史；敏感运行文件均不存在"
    }
    else {
        Push-Location $BackendRoot
        try {
            foreach ($target in $targets) {
                git check-ignore --quiet -- $target
                if ($LASTEXITCODE -ne 0) { throw "$target 未被 .gitignore 覆盖" }
            }
        }
        finally {
            Pop-Location
        }
    }
}

Invoke-Step "设置可配置性映射" {
    & (Join-Path $PSScriptRoot "检查设置覆盖.ps1")
}

if (-not $SkipTests) {
    Invoke-Step "后端测试" {
        if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
            throw "后端虚拟环境不存在：$BackendPython"
        }
        Push-Location (Join-Path $BackendRoot "backend")
        try { & $BackendPython -m unittest discover -s tests; if ($LASTEXITCODE -ne 0) { throw "后端测试失败" } }
        finally { Pop-Location }
    }
    Invoke-Step "Agent 前端" {
        Push-Location $AgentRoot
        try { npm test; if ($LASTEXITCODE -ne 0) { throw "前端测试失败" }; npm run build; if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }; npm audit --audit-level=high; if ($LASTEXITCODE -ne 0) { throw "前端依赖审计失败" } }
        finally { Pop-Location }
    }
    Invoke-Step "Windows 启动器与本地 Worker" {
        Push-Location $AgentRoot
        try {
            & $BackendPython -m unittest discover -s desktop -p "test_*.py"; if ($LASTEXITCODE -ne 0) { throw "桌面启动器测试失败" }
            & $BackendPython -m unittest discover -s scripts -p "test_*.py"; if ($LASTEXITCODE -ne 0) { throw "本地 Worker 测试失败" }
            & $BackendPython -m unittest discover -s scripts/deps -p "test_*.py"; if ($LASTEXITCODE -ne 0) { throw "依赖下载测试失败" }
        }
        finally { Pop-Location }
    }
    Invoke-Step "Live2D 桌宠" {
        Push-Location (Join-Path $AgentRoot "live2d-desktop")
        try { npm run test:model; if ($LASTEXITCODE -ne 0) { throw "桌宠测试失败" }; npm audit --audit-level=high; if ($LASTEXITCODE -ne 0) { throw "桌宠依赖审计失败" } }
        finally { Pop-Location }
    }
}

if ($Failures.Count) {
    Write-Host "`n发布检查未通过：" -ForegroundColor Red
    $Failures | ForEach-Object { Write-Host "- $_" -ForegroundColor Red }
    exit 1
}

Write-Host "`n发布检查通过。" -ForegroundColor Green
