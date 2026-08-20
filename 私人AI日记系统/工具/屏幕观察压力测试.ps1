[CmdletBinding()]
param(
    [ValidateRange(1, 1440)]
    [int]$DurationMinutes = 240,

    [ValidateRange(2, 60)]
    [int]$SampleSeconds = 10,

    [ValidateSet("primary", "all")]
    [string]$Scope = "primary",

    [string]$ApiBase = "http://127.0.0.1:8000",

    [string]$ReportDirectory = "D:\澪Agent数据\压力测试"
)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
$deadline = $startedAt.AddMinutes($DurationMinutes)
$errors = [System.Collections.Generic.List[string]]::new()
$frameSamples = [System.Collections.Generic.List[object]]::new()
$healthFailures = 0
$statusFailures = 0
$frameStalls = 0
$maxObserverRestarts = 0
$maxAgentWorkingSetMb = 0.0
$maxObserverWorkingSetMb = 0.0
$firstFrameId = 0
$lastFrameId = 0
$previousFrameId = -1
$captureStarted = $false
$fatalError = ""

New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null
Add-Type -AssemblyName System.Net.Http
$httpClient = [System.Net.Http.HttpClient]::new()
$httpClient.Timeout = [TimeSpan]::FromSeconds(15)

function Invoke-MioJson {
    param(
        [string]$Uri,
        [ValidateSet("GET", "POST")]
        [string]$Method = "GET",
        [string]$Body = ""
    )
    $request = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::new($Method),
        $Uri
    )
    try {
        if ($Body) {
            $request.Content = [System.Net.Http.StringContent]::new(
                $Body,
                [System.Text.Encoding]::UTF8,
                "application/json"
            )
        }
        $response = $httpClient.SendAsync($request).GetAwaiter().GetResult()
        try {
            $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            $text = [System.Text.Encoding]::UTF8.GetString($bytes)
            if (-not $response.IsSuccessStatusCode) {
                throw "HTTP $([int]$response.StatusCode)：$text"
            }
            return $text | ConvertFrom-Json
        } finally {
            $response.Dispose()
        }
    } finally {
        $request.Dispose()
    }
}

function Get-MioHealth {
    try {
        return Invoke-MioJson -Uri "$ApiBase/health"
    } catch {
        return $null
    }
}

function Wait-MioHealth {
    $health = Get-MioHealth
    if ($health) {
        return $health
    }
    $agentPath = "D:\澪Agent\澪.exe"
    if (-not (Test-Path -LiteralPath $agentPath)) {
        throw "澪 Agent 未运行，并且没有找到 $agentPath"
    }
    Start-Process -FilePath $agentPath | Out-Null
    $healthDeadline = (Get-Date).AddSeconds(35)
    do {
        Start-Sleep -Milliseconds 500
        $health = Get-MioHealth
    } until ($health -or (Get-Date) -ge $healthDeadline)
    if (-not $health) {
        throw "澪 Agent 在 35 秒内没有进入健康状态"
    }
    return $health
}

try {
    $health = Wait-MioHealth
    $payload = @{
        scope = $Scope
        interval_ms = 1000
        capture_only = $true
    } | ConvertTo-Json
    Invoke-MioJson -Method POST -Uri "$ApiBase/api/companion/screen/start" -Body $payload | Out-Null
    $captureStarted = $true

    while ((Get-Date) -lt $deadline) {
        $sampledAt = Get-Date
        $sampleError = ""
        $screen = $null
        try {
            if (-not (Get-MioHealth)) {
                $healthFailures += 1
                $sampleError = "健康检查失败"
            }
            $status = Invoke-MioJson -Uri "$ApiBase/api/companion/status"
            $screen = $status.screen
            if (-not $screen.running -or -not $screen.process_isolated -or -not $screen.process_alive) {
                $statusFailures += 1
                $sampleError = "观察器没有保持隔离运行"
            }
            if (-not $status.screen_analysis.capture_only) {
                $statusFailures += 1
                $sampleError = "仅捕获模式意外关闭"
            }
            $frameId = [int]$screen.frame_id
            if ($firstFrameId -eq 0) {
                $firstFrameId = $frameId
            }
            if ($previousFrameId -ge 0 -and $frameId -le $previousFrameId) {
                $frameStalls += 1
                $sampleError = "帧编号没有增长"
            }
            $previousFrameId = $frameId
            $lastFrameId = $frameId
            $maxObserverRestarts = [Math]::Max($maxObserverRestarts, [int]$screen.process_restarts)

            $observerProcess = Get-Process -Id ([int]$screen.process_pid) -ErrorAction SilentlyContinue
            if ($observerProcess) {
                $maxObserverWorkingSetMb = [Math]::Max(
                    $maxObserverWorkingSetMb,
                    [Math]::Round($observerProcess.WorkingSet64 / 1MB, 2)
                )
            }
            $backendPort = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($backendPort) {
                $agentProcess = Get-Process -Id $backendPort.OwningProcess -ErrorAction SilentlyContinue
                if ($agentProcess) {
                    $maxAgentWorkingSetMb = [Math]::Max(
                        $maxAgentWorkingSetMb,
                        [Math]::Round($agentProcess.WorkingSet64 / 1MB, 2)
                    )
                }
            }
            if ($screen.error) {
                $sampleError = [string]$screen.error
            }
        } catch {
            $statusFailures += 1
            $sampleError = $_.Exception.Message
        }

        if ($sampleError) {
            $errors.Add("$($sampledAt.ToString('s')) $sampleError")
        }
        $frameSamples.Add([pscustomobject]@{
            sampled_at = $sampledAt.ToString("o")
            frame_id = if ($screen) { [int]$screen.frame_id } else { 0 }
            backend = if ($screen) { [string]$screen.capture_backend } else { "" }
            observer_pid = if ($screen) { [int]$screen.process_pid } else { 0 }
            restarts = if ($screen) { [int]$screen.process_restarts } else { 0 }
            error = $sampleError
        })
        Start-Sleep -Seconds $SampleSeconds
    }
} catch {
    $fatalError = $_.Exception.Message
    $errors.Add("$((Get-Date).ToString('s')) $fatalError")
} finally {
    if ($captureStarted) {
        try {
            Invoke-MioJson -Method POST -Uri "$ApiBase/api/companion/screen/stop" | Out-Null
        } catch {
            $errors.Add("$((Get-Date).ToString('s')) 停止观察失败：$($_.Exception.Message)")
        }
    }
}

$endedAt = Get-Date
$report = [ordered]@{
    test = "澪 Agent 屏幕观察压力测试"
    capture_only = $true
    requested_duration_minutes = $DurationMinutes
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    actual_duration_seconds = [Math]::Round(($endedAt - $startedAt).TotalSeconds, 1)
    scope = $Scope
    sample_seconds = $SampleSeconds
    sample_count = $frameSamples.Count
    first_frame_id = $firstFrameId
    last_frame_id = $lastFrameId
    frame_stalls = $frameStalls
    health_failures = $healthFailures
    status_failures = $statusFailures
    observer_restarts = $maxObserverRestarts
    max_agent_working_set_mb = $maxAgentWorkingSetMb
    max_observer_working_set_mb = $maxObserverWorkingSetMb
    fatal_error = $fatalError
    passed = (-not $fatalError -and $healthFailures -eq 0 -and $statusFailures -eq 0 -and $frameStalls -eq 0)
    errors = @($errors)
    samples = @($frameSamples)
}

$reportPath = Join-Path $ReportDirectory "屏幕观察压力测试-$($startedAt.ToString('yyyyMMdd-HHmmss')).json"
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$httpClient.Dispose()
Write-Host "压力测试报告：$reportPath"
Write-Host "通过：$($report.passed)；帧：$firstFrameId -> $lastFrameId；重启：$maxObserverRestarts；错误：$($errors.Count)"
if (-not $report.passed) {
    exit 1
}
