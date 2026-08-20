$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "napcat-control.ps1") -Action restart
