param(
    [string]$MioDirectory = "",
    [switch]$Restore
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
function Get-MioFixHash {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace("-", "") }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function Set-MioFixBytes {
    param([string]$Path, [AllowNull()][byte[]]$Bytes)
    if ($null -eq $Bytes) { [IO.File]::Delete($Path); return }
    $temporary = $Path + "." + [guid]::NewGuid().ToString("N") + ".tmp"
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temporary, $Path, [Management.Automation.Language.NullString]::Value)
        } else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
    }
}
try {
    if (-not $MioDirectory) {
        $MioDirectory = Read-Host "请粘贴 Mio.exe 所在的文件夹路径，然后按回车"
    }
    $MioDirectory = [IO.Path]::GetFullPath($MioDirectory.Trim().Trim('"'))
    $exe = Join-Path $MioDirectory "Mio.exe"
    $scriptRoot = Join-Path $MioDirectory "_internal\agent_scripts"
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $scriptRoot "napcat-control.ps1") -PathType Leaf)) {
        throw "这不是 Mio 安装目录，应包含 Mio.exe 和 _internal/agent_scripts/napcat-control.ps1。"
    }
    $files = @("napcat-control.ps1", "napcat-loader.ps1")
    $hashes = Get-Content -LiteralPath (Join-Path $PSScriptRoot "SHA256.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($name in $files) {
        $source = Join-Path $PSScriptRoot $name
        if ((Get-MioFixHash $source) -ne $hashes.$name) {
            throw "修复包校验失败，请重新解压完整压缩包：$name"
        }
    }
    . (Join-Path $PSScriptRoot "napcat-loader.ps1")
    Assert-MioNapCatPlainPath $scriptRoot
    Assert-MioNapCatPlainPath $exe
    foreach ($name in $files) { Assert-MioNapCatPlainPath (Join-Path $scriptRoot $name) }
    $prefix = $MioDirectory.TrimEnd("\") + "\"
    $running = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and
        ($_.Name -ieq "Mio.exe" -or $_.Name -ieq "澪.exe")
    })
    if ($running) { throw "Mio 仍在运行，请先从系统托盘退出 Mio，再运行修复。" }
    $backupRoot = Join-Path $MioDirectory "备份\QQ加载修复"
    Assert-MioNapCatPlainPath $backupRoot
    if ($Restore) {
        $backup = $null
        foreach ($candidate in Get-ChildItem -LiteralPath $backupRoot -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending) {
            Assert-MioNapCatPlainPath $candidate.FullName
            $candidateReceipt = Join-Path $candidate.FullName "receipt.json"
            Assert-MioNapCatPlainPath $candidateReceipt
            try { $candidateData = Get-Content -LiteralPath $candidateReceipt -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop }
            catch { continue }
            if ($candidateData.state -eq "applied") { $backup = $candidate; break }
        }
        if (-not $backup) { throw "没有找到修复前备份。" }
        Assert-MioNapCatPlainPath $backup.FullName
        $receiptPath = Join-Path $backup.FullName "receipt.json"
        Assert-MioNapCatPlainPath $receiptPath
        $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($receipt.installRoot -ne $MioDirectory) { throw "备份与所选安装目录不符。" }
        foreach ($name in $files) {
            $entry = $receipt.files.$name
            if (-not $entry) { throw "备份记录不完整：$name" }
            $target = Join-Path $scriptRoot $name
            if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or
                (Get-MioFixHash $target) -ne $entry.installedHash) {
                throw "程序文件在修复后已有变化，停止自动恢复以免覆盖新版本：$name"
            }
            if ($entry.existed) {
                $saved = Join-Path $backup.FullName $name
                Assert-MioNapCatPlainPath $saved
                if ((Get-MioFixHash $saved) -ne $entry.originalHash) {
                    throw "备份文件校验失败：$name"
                }
            }
        }
        $restoreBefore = @{}
        $restored = [Collections.Generic.List[string]]::new()
        foreach ($name in $files) { $restoreBefore[$name] = [IO.File]::ReadAllBytes((Join-Path $scriptRoot $name)) }
        try {
            foreach ($name in $files) {
                $target = Join-Path $scriptRoot $name
                $bytes = $null
                if ($receipt.files.$name.existed) { $bytes = [IO.File]::ReadAllBytes((Join-Path $backup.FullName $name)) }
                Set-MioFixBytes -Path $target -Bytes $bytes
                $restored.Add($name)
            }
            $receipt.state = "restored"
            Set-MioFixBytes -Path $receiptPath -Bytes ([Text.UTF8Encoding]::new($false).GetBytes(($receipt | ConvertTo-Json -Depth 5)))
        } catch {
            $restoreFailure = $_
            $undoErrors = [Collections.Generic.List[string]]::new()
            foreach ($name in $restored) {
                try { Set-MioFixBytes -Path (Join-Path $scriptRoot $name) -Bytes $restoreBefore[$name] }
                catch { $undoErrors.Add($name) }
            }
            if ($undoErrors.Count) { throw "撤销失败且部分文件未能回滚：$($undoErrors -join '；')。备份：$($backup.FullName)" }
            throw "撤销失败，已回滚到撤销前的状态。$($restoreFailure.Exception.Message)"
        }
        Write-Host "已恢复修复前的 Mio 启动脚本。"
        Write-Host "QQ 目录内的专用加载文件不会自动删除；不被启动配置引用时不会执行。"
        exit 0
    }
    $upToDate = $true
    foreach ($name in $files) {
        $target = Join-Path $scriptRoot $name
        if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or
            (Get-MioFixHash $target) -ne $hashes.$name) { $upToDate = $false }
    }
    if ($upToDate) { Write-Host "修复脚本已安装，无需重复覆盖。"; exit 0 }
    $backupPath = Join-Path $backupRoot ((Get-Date).ToString("yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
    $receipt = [ordered]@{ installRoot = $MioDirectory; state = "prepared"; files = [ordered]@{} }
    foreach ($name in $files) {
        $target = Join-Path $scriptRoot $name
        $existed = Test-Path -LiteralPath $target -PathType Leaf
        $originalHash = ""
        if ($existed) {
            [IO.File]::Copy($target, (Join-Path $backupPath $name), $false)
            $originalHash = Get-MioFixHash $target
        }
        $receipt.files[$name] = @{ existed = $existed; originalHash = $originalHash; installedHash = $hashes.$name }
    }
    [IO.File]::WriteAllText((Join-Path $backupPath "receipt.json"), ($receipt | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    $changed = [Collections.Generic.List[string]]::new()
    try {
        foreach ($name in $files) {
            $target = Join-Path $scriptRoot $name
            Set-MioFixBytes -Path $target -Bytes ([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot $name)))
            $changed.Add($name)
            if ((Get-MioFixHash $target) -ne $hashes.$name) { throw "写入校验失败：$name" }
        }
        $receipt.state = "applied"
        Set-MioFixBytes -Path (Join-Path $backupPath "receipt.json") -Bytes ([Text.UTF8Encoding]::new($false).GetBytes(($receipt | ConvertTo-Json -Depth 5)))
    } catch {
        $installFailure = $_
        $restoreErrors = [Collections.Generic.List[string]]::new()
        foreach ($name in $changed) {
            try {
                $target = Join-Path $scriptRoot $name
                if ($receipt.files[$name].existed) { Set-MioFixBytes -Path $target -Bytes ([IO.File]::ReadAllBytes((Join-Path $backupPath $name))) }
                elseif (Test-Path -LiteralPath $target) { [IO.File]::Delete($target) }
            } catch { $restoreErrors.Add($name) }
        }
        if ($restoreErrors.Count) { throw "修复失败，部分文件未恢复：$($restoreErrors -join '；')。原件备份：$backupPath" }
        throw "修复失败，已恢复原脚本。$($installFailure.Exception.Message)"
    }
    Write-Host "QQ 加载路径修复已安装。原脚本备份：$backupPath"
    Write-Host "请彻底退出普通 QQ，再打开 Mio，在微信与QQ设置中启动通道并获取二维码。"
    Write-Host "首次启动会在 QQ 的 resources/app 内增加专用加载文件，不修改 QQ 的 package.json。"
    Write-Host "若提示目录无写入权限，可退出 Mio 后，以管理员身份运行本修复和首次通道启动。"
    Write-Host "本工具不验证真人扫码或消息送达；请完成扫码后检查 OneBot 连接。"
} catch {
    Write-Host ("处理失败：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
