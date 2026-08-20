$ErrorActionPreference = "Stop"

$BackendRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $BackendRoot
$AgentRoot = Get-ChildItem -LiteralPath $WorkspaceRoot -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "src\components\SettingsPage.vue") } |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $AgentRoot) {
    throw "The sibling Agent application repository was not found."
}
$ConfigPath = Join-Path $BackendRoot "backend\app\config.py"
$CompanionPath = Join-Path $BackendRoot "backend\app\companion_service.py"
$SettingsPagePath = Join-Path $AgentRoot "src\components\SettingsPage.vue"
$AppPath = Join-Path $AgentRoot "src\App.vue"

foreach ($path in @($ConfigPath, $CompanionPath, $SettingsPagePath, $AppPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Settings coverage input is missing: $path"
    }
}

function Get-NamedKeys {
    param(
        [string]$Content,
        [string]$StartPattern,
        [string]$EndPattern
    )
    $pattern = "(?s)$StartPattern(?<body>.*?)$EndPattern"
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        throw "Unable to locate settings block: $StartPattern"
    }
    return @(
        [regex]::Matches($match.Groups['body'].Value, '(?m)^\s*["''](?<key>[a-z0-9_]+)["'']\s*:') |
            ForEach-Object { $_.Groups['key'].Value } |
            Sort-Object -Unique
    )
}

$configContent = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
$companionContent = Get-Content -LiteralPath $CompanionPath -Raw -Encoding UTF8
$settingsPageContent = Get-Content -LiteralPath $SettingsPagePath -Raw -Encoding UTF8
$appContent = Get-Content -LiteralPath $AppPath -Raw -Encoding UTF8

$runtimeParams = @{
    Content = $configContent
    StartPattern = 'RUNTIME_SETTING_SPECS[^=]*=\s*\{'
    EndPattern = '\}\s*RUNTIME_PATH_FIELDS'
}
$runtimeKeys = Get-NamedKeys @runtimeParams
$runtimeUiKeys = @(
    [regex]::Matches($settingsPageContent, 'runtimeSettingsDraft\.(?<key>[a-z0-9_]+)') |
        ForEach-Object { $_.Groups['key'].Value } |
        Sort-Object -Unique
)
$privateRuntimePathKeys = @(
    [regex]::Match(
        $appContent,
        '(?s)const privateRuntimePathKeys\s*=\s*\[(?<body>.*?)\]'
    ).Groups['body'].Value |
        ForEach-Object { [regex]::Matches($_, "'(?<key>[a-z0-9_]+)'") } |
        ForEach-Object { $_.Groups['key'].Value } |
        Sort-Object -Unique
)
$missingRuntime = @(
    $runtimeKeys | Where-Object { $_ -notin $runtimeUiKeys -and $_ -notin $privateRuntimePathKeys }
)
$unknownRuntime = @($runtimeUiKeys | Where-Object { $_ -notin $runtimeKeys })
$stalePrivateRuntime = @($privateRuntimePathKeys | Where-Object { $_ -notin $runtimeKeys })
if ($missingRuntime.Count -or $unknownRuntime.Count -or $stalePrivateRuntime.Count) {
    throw "Runtime settings mismatch. Missing: $($missingRuntime -join ', '); unknown: $($unknownRuntime -join ', '); stale private: $($stalePrivateRuntime -join ', ')"
}

$companionParams = @{
    Content = $companionContent
    StartPattern = 'DEFAULT_CONFIG[^=]*=\s*\{'
    EndPattern = '\}\s*def _migrate_config'
}
$companionKeys = Get-NamedKeys @companionParams
$sectionKeys = @(
    [regex]::Match(
        $appContent,
        '(?s)const companionSettingKeys\s*=\s*\{(?<body>.*?)\}\s*const privateRuntimePathKeys'
    ).Groups['body'].Value |
        ForEach-Object { [regex]::Matches($_, "'(?<key>[a-z0-9_]+)'") } |
        ForEach-Object { $_.Groups['key'].Value } |
        Sort-Object -Unique
)

# These values are controlled by dedicated UI/actions or maintained internally.
# Model/reasoning use the conversation selector, greeting uses General settings,
# position comes from dragging, and the voice internals are locked to Mio's one profile.
$indirectCompanionKeys = @(
    'config_schema_version',
    'chat_model_id',
    'chat_reasoning_level',
    'default_voice_profile_id',
    'gpt_sovits_gpt_weights',
    'gpt_sovits_prompt_language',
    'gpt_sovits_prompt_text',
    'gpt_sovits_ref_audio',
    'gpt_sovits_sovits_weights',
    'position_x',
    'position_y',
    'qq_startup_enabled',
    'startup_greeting_enabled',
    'voice_profiles'
)
$missingCompanion = @(
    $companionKeys | Where-Object { $_ -notin $sectionKeys -and $_ -notin $indirectCompanionKeys }
)
$staleIndirect = @($indirectCompanionKeys | Where-Object { $_ -notin $companionKeys })
if ($missingCompanion.Count -or $staleIndirect.Count) {
    throw "Companion settings mismatch. Unclassified: $($missingCompanion -join ', '); stale indirect keys: $($staleIndirect -join ', ')"
}

Write-Host "Runtime settings: $($runtimeUiKeys.Count) bound to the Advanced settings UI, $($privateRuntimePathKeys.Count) private-only paths excluded" -ForegroundColor Green
Write-Host "Companion settings: $($sectionKeys.Count) section-saved, $($indirectCompanionKeys.Count) managed by dedicated controls or runtime" -ForegroundColor Green
