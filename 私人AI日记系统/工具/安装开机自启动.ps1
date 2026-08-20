$ErrorActionPreference = "Stop"

$StartupDir = [Environment]::GetFolderPath("Startup")
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$LauncherBaseName = -join ([char[]](0x542f, 0x52a8, 0x6faa))
$ScriptPath = Join-Path $PSScriptRoot ($LauncherBaseName + ".ps1")
$StartupShortcut = Join-Path $StartupDir ($LauncherBaseName + ".lnk")
$DesktopShortcut = Join-Path $DesktopDir ($LauncherBaseName + ".lnk")

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Startup script not found: $ScriptPath"
}

function New-LauncherShortcut {
    param(
        [string]$ShortcutPath,
        [string]$WindowStyle
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = "powershell.exe"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = "powershell.exe,0"
    if ($WindowStyle -eq "hidden") {
        $shortcut.WindowStyle = 7
    } else {
        $shortcut.WindowStyle = 1
    }
    $shortcut.Save()
}

New-LauncherShortcut -ShortcutPath $StartupShortcut -WindowStyle "hidden"
New-LauncherShortcut -ShortcutPath $DesktopShortcut -WindowStyle "normal"

Write-Host "Created startup shortcut: $StartupShortcut"
Write-Host "Created desktop shortcut: $DesktopShortcut"
