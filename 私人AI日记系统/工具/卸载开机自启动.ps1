$ErrorActionPreference = "Stop"

$LauncherBaseName = -join ([char[]](0x542f, 0x52a8, 0x6faa))
$StartupShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) ($LauncherBaseName + ".lnk")
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) ($LauncherBaseName + ".lnk")

foreach ($shortcut in @($StartupShortcut, $DesktopShortcut)) {
    if (Test-Path -LiteralPath $shortcut) {
        Remove-Item -LiteralPath $shortcut -Force
        Write-Host "Deleted: $shortcut"
    }
}
