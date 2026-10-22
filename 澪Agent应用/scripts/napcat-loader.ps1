# Mio-managed QQ entry points. Dot-source this file; it does not start QQ.
function Assert-MioNapCatPlainPath {
    param([string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "NapCat 加载修复不处理符号链接或目录联接：$current"
            }
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Initialize-MioNapCatLoader {
    param(
        [Parameter(Mandatory = $true)][string]$ShellRoot,
        [Parameter(Mandatory = $true)][string]$QQPath
    )
    $ShellRoot = [IO.Path]::GetFullPath($ShellRoot)
    $QQPath = [IO.Path]::GetFullPath($QQPath)
    Assert-MioNapCatPlainPath $ShellRoot
    Assert-MioNapCatPlainPath $QQPath
    $mainPath = Join-Path $ShellRoot "napcat.mjs"
    $sourcePatch = Join-Path $ShellRoot "qqnt.json"
    foreach ($required in @($QQPath, $mainPath, $sourcePatch)) {
        Assert-MioNapCatPlainPath $required
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "NapCat 加载所需文件不存在：$required"
        }
    }
    try {
        $patch = [IO.File]::ReadAllText($sourcePatch) | ConvertFrom-Json -ErrorAction Stop
        if (-not ($patch -is [pscustomobject]) -or $patch.name -ne "qq-chat" -or -not $patch.main) {
            throw "应为包含 main 入口的 QQ 配置。"
        }
    } catch {
        throw "NapCat 的 qqnt.json 无效，请补齐完整 Shell 包。$($_.Exception.Message)"
    }

    # Electron joins main onto resources/app, even when main looks absolute.
    # Install a small, namespaced entry there; never change QQ's package.json.
    $qqRoot = Split-Path -Parent $QQPath
    $appRoots = [Collections.Generic.List[string]]::new()
    $directApp = Join-Path $qqRoot "resources\app"
    if (Test-Path -LiteralPath $directApp -PathType Container) { $appRoots.Add($directApp) }
    $versionsRoot = Join-Path $qqRoot "versions"
    Assert-MioNapCatPlainPath $versionsRoot
    if (Test-Path -LiteralPath $versionsRoot -PathType Container) {
        foreach ($version in Get-ChildItem -LiteralPath $versionsRoot -Directory) {
            Assert-MioNapCatPlainPath $version.FullName
            $app = Join-Path $version.FullName "resources\app"
            if (Test-Path -LiteralPath $app -PathType Container) { $appRoots.Add($app) }
        }
    }
    $verifiedApps = [Collections.Generic.List[string]]::new()
    foreach ($app in $appRoots) {
        Assert-MioNapCatPlainPath $app
        $packagePath = Join-Path $app "package.json"
        Assert-MioNapCatPlainPath $packagePath
        if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) { continue }
        try { $package = [IO.File]::ReadAllText($packagePath) | ConvertFrom-Json -ErrorAction Stop }
        catch { throw "QQ 的 package.json 无法解析：$packagePath" }
        if ($package.name -eq "qq-chat") { $verifiedApps.Add($app) }
    }
    if (-not $verifiedApps.Count) {
        throw "所选 QQ.exe 目录下没有找到有效的 resources/app/package.json，请检查官方 NT QQ 安装目录。"
    }

    $loaderName = "mio-napcat-loader.cjs"
    $marker = "// Mio NapCat loader v1"
    # Let Node encode the URL, including spaces, Chinese, #, %, and apostrophes.
    $mainLiteral = ConvertTo-Json -InputObject $mainPath -Compress
    $loader = $marker + "`n" + 'const { pathToFileURL } = require("node:url");' + "`n" +
        'import(pathToFileURL(' + $mainLiteral + ').href).catch(error => { console.error("Mio NapCat 加载失败：", error); process.exitCode = 1; });' + "`n"
    $patch.main = "./$loaderName"
    $patch | Add-Member -NotePropertyName "mioNapCatLoaderVersion" -NotePropertyValue 1 -Force
    $patchText = ConvertTo-Json -InputObject $patch -Depth 100
    $patchPath = Join-Path $ShellRoot "qqnt.mio.json"
    $loadPath = Join-Path $ShellRoot $loaderName
    $writes = [Collections.Generic.List[object]]::new()
    $writes.Add([pscustomobject]@{ Path = $loadPath; Text = $loader; Kind = "loader" })
    foreach ($app in $verifiedApps) {
        $writes.Add([pscustomobject]@{ Path = (Join-Path $app $loaderName); Text = $loader; Kind = "loader" })
    }
    # Switch to the new package only after every entry point is in place.
    $writes.Add([pscustomobject]@{ Path = $patchPath; Text = $patchText; Kind = "patch" })
    foreach ($write in $writes) {
        Assert-MioNapCatPlainPath $write.Path
        if (Test-Path -LiteralPath $write.Path) {
            $existing = [IO.File]::ReadAllText($write.Path)
            $owned = $existing.StartsWith($marker)
            if ($write.Kind -eq "patch") {
                try { $owned = (($existing | ConvertFrom-Json).mioNapCatLoaderVersion -eq 1) }
                catch { $owned = $false }
            }
            if (-not $owned) { throw "发现非 Mio 受管的同名文件，已停止覆盖：$($write.Path)" }
        }
    }
    $changed = [Collections.Generic.List[object]]::new()
    try {
        foreach ($write in $writes) {
            $before = $null
            if (Test-Path -LiteralPath $write.Path) {
                $before = [IO.File]::ReadAllBytes($write.Path)
                if ([IO.File]::ReadAllText($write.Path) -eq $write.Text) { continue }
            }
            $temporary = $write.Path + "." + [guid]::NewGuid().ToString("N") + ".tmp"
            try {
                [IO.File]::WriteAllText($temporary, $write.Text, [Text.UTF8Encoding]::new($false))
                if ($null -eq $before) { [IO.File]::Move($temporary, $write.Path) }
                else { [IO.File]::Replace($temporary, $write.Path, [Management.Automation.Language.NullString]::Value) }
                $changed.Add([pscustomobject]@{ Path = $write.Path; Before = $before })
            } finally {
                if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) }
            }
        }
    } catch {
        $failure = $_
        $rollbackErrors = [Collections.Generic.List[string]]::new()
        for ($index = $changed.Count - 1; $index -ge 0; $index--) {
            $entry = $changed[$index]
            try {
                if ($null -eq $entry.Before) {
                    if (Test-Path -LiteralPath $entry.Path) { [IO.File]::Delete($entry.Path) }
                } else { [IO.File]::WriteAllBytes($entry.Path, $entry.Before) }
            } catch { $rollbackErrors.Add($entry.Path) }
        }
        if ($rollbackErrors.Count) {
            throw "NapCat 加载准备失败，部分文件未能恢复：$($rollbackErrors -join '；')。$($failure.Exception.Message)"
        }
        throw "NapCat 加载准备失败，已撤回本次修改。请检查目录写入权限；Program Files 可能需要管理员权限。$($failure.Exception.Message)"
    }
    return [pscustomobject]@{ Patch = $patchPath; Load = $loadPath; AppRoots = @($verifiedApps.ToArray()) }
}
