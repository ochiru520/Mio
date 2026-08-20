# 一键安装：QQ 通道（NapCat）
# 多源尝试下载 NapCat.Shell.zip（官方 Release 资产），解压到 Mio 的 NapCat 目录。
# Shell 包不包含完整 QQ；运行时复用电脑上已安装的官方 NT QQ。
param()

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:MIO_DEP_ID = "napcat"

. (Join-Path $PSScriptRoot "common.ps1")

$root = $env:MIO_WORKSPACE_ROOT
$napcatDir = if ($env:MIO_NAPCAT_DIR) { $env:MIO_NAPCAT_DIR } else { Join-Path $root "NapCat" }
$zipPath = Join-Path $root "napcat-shell.zip"
$stagingDir = Join-Path $root "napcat-shell.installing"

function Test-NapCatShellReady {
    if (-not (Test-Path -LiteralPath $napcatDir -PathType Container)) { return $false }
    foreach ($name in @("NapCatWinBootMain.exe", "NapCatWinBootHook.dll", "napcat.mjs", "qqnt.json")) {
        $found = Get-ChildItem -LiteralPath $napcatDir -Recurse -File -Filter $name -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $found) { return $false }
    }
    return $true
}

try {
    Write-Host "=== QQ 通道（NapCat）一键安装 ==="
    Write-Host "提示：NapCat Shell 不包含 QQ，需要电脑上已安装官方 NT QQ。"
    Write-DepsStatus -Stage "prepare" -Percent 3 -Message "正在准备安装目录"

    New-Item -ItemType Directory -Force -Path $root | Out-Null

    if (-not (Test-NapCatShellReady)) {
        Write-DepsStatus -Stage "download" -Percent 15 -Message "正在下载 NapCat（约 50 MB，优先国内通道）"
        $urls = @(
            "https://ghfast.top/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip",
            "https://github.moeyy.xyz/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip",
            "https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip"
        )
        Invoke-Download -Urls $urls -Output $zipPath -Stage "download" -StartPercent 15 -EndPercent 44 -Label " NapCat"
        Write-DepsStatus -Stage "extract" -Percent 45 -Message "正在解压 NapCat"
        if (Test-Path -LiteralPath $stagingDir) { Remove-Item -LiteralPath $stagingDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
        Expand-Archive -LiteralPath $zipPath -DestinationPath $stagingDir -Force
        New-Item -ItemType Directory -Force -Path $napcatDir | Out-Null
        Get-ChildItem -LiteralPath $stagingDir -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $napcatDir -Recurse -Force
        }
        Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-NapCatShellReady)) {
        throw "NapCat 下载或解压不完整，缺少 BootMain、Hook 或 Shell 主程序。"
    }

    Write-DepsStatus -Stage "done" -Percent 100 -Message "NapCat Shell 已就绪。回到 Mio，由 Mio 启动机器人 QQ" -Done $true
    Write-Host "=== 安装完成 ==="
    Write-Host "下一步：彻底退出普通 QQ，回到 Mio 的 QQ 设置点击「安装并配置」。"
} catch {
    Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-DepsFail -Message ("NapCat 自动下载失败：" + $_.Exception.Message + "。可以打开官方文档页手动安装：https://napneko.pages.dev")
    Write-Host "=== 自动下载失败，改用官方文档页手动安装 ==="
    Write-Host "国内可访问的官方文档：https://napneko.pages.dev"
    Write-Host "按文档「NapCat.Shell - Win 手动启动教程」下载 NapCat.Shell.zip，解压后放到：$napcatDir"
    Write-Host "放好后回到 Mio 点击「重新检查」。"
    Write-Host "按任意键关闭窗口..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
