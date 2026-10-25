$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'Capacity.psm1') -Force
$project = 'workflows-capacity-29-' + ('a' * 32)
$start = [DateTimeOffset]'2026-01-01T00:00:00Z'
$passed = 0

function New-Evidence {
    $samples = @(0..120 | ForEach-Object {
        $index = $_
        [pscustomobject]@{
            schemaVersion = 1; at = $start.AddSeconds($index).ToString('o'); uptime = 1000 + $index; boot = 'private-host-id'
            health = 'GREEN'; databaseReady = $true
            cpu = [pscustomobject]@{ total = 10000 + 400 * $index; idle = 8000 + 300 * $index; ioWait = $index; steal = 0 }
            memory = [pscustomobject]@{ MemTotal = 1000000; MemAvailable = 600000 }
            disk = [pscustomobject]@{ readBytes = 1000 * $index; writeBytes = 2000 * $index; busyMs = 10 * $index }
            space = [pscustomobject]@{ total = 1000000; available = 700000 }
            pressureUs = [pscustomobject]@{ cpu = 100 * $index; memory = 0; io = 0 }
            queue = [pscustomobject]@{ total = 0; tasks = @() }
            containers = @('sonar', 'database' | ForEach-Object {
                [pscustomobject]@{
                    role = $_; id = "private-$_"; startedAt = 'private-start'; status = 'running'; health = 'healthy'
                    restarts = 0; oomKilled = $false; memoryLimitBytes = 1000000; cpuLimit = 2; memoryBytes = 500000
                    cpuUsageUs = 100000 * $index; throttledUs = 0
                    memoryEvents = [pscustomobject]@{ oom = 0; oom_kill = 0 }
                    io = [pscustomobject]@{ readBytes = 10 * $index; writeBytes = 20 * $index }
                }
            })
        }
    })
    $tasks = @([pscustomobject]@{
        id = 'task-1'; componentKey = $project; status = 'SUCCESS'; executionTimeMs = 10000
        startedAt = $start.AddSeconds(50).ToString('o'); executedAt = $start.AddSeconds(60).ToString('o')
    })
    [pscustomobject]@{ Samples = $samples; Tasks = $tasks }
}

function Test-Case([string]$Name, [scriptblock]$Change, [string]$Expected = 'throw') {
    # Arrange
    $evidence = New-Evidence
    & $Change $evidence
    $errorMessage = $null
    $report = $null

    # Act
    try { $report = Get-CapacitySummary -Samples $evidence.Samples -Tasks $evidence.Tasks -Project $project }
    catch { $errorMessage = $_.Exception.Message }

    # Assert
    switch ($Expected) {
        'throw' { if (!$errorMessage) { throw "$Name accepted incomplete evidence." } }
        'outside' { if ($errorMessage -or $report.withinProposedEnvelope) { throw "$Name failed to report the exceeded envelope: $errorMessage" } }
        'pass' {
            if ($errorMessage -or !$report.withinProposedEnvelope -or $report.releaseEligible -or
                $report.tasks[0].processingSamples -ne 11 -or $report.hostCpuPeakPercent -ne 24.75 -or
                $report.containerCpuQuotaPeakPercent.sonar -ne 5 -or
                ($report | ConvertTo-Json -Depth 10) -match 'private-|task-1|workflows-capacity-29-') {
                throw "$Name produced an invalid or unsanitized summary: $errorMessage"
            }
        }
        default { throw 'Unknown test expectation.' }
    }
    $script:passed++
    Write-Output "PASS $Name"
}

Test-Case 'complete sequential evidence' {} 'pass'
Test-Case 'missing field' { param($e) $e.Samples[55].PSObject.Properties.Remove('health') }
Test-Case 'sampling gap' { param($e) $e.Samples = @($e.Samples | Where-Object { $_.uptime -lt 1045 -or $_.uptime -gt 1049 }) }
Test-Case 'clock jump' { param($e) $e.Samples[55].at = $start.AddSeconds(65).ToString('o') }
Test-Case 'changed host' { param($e) $e.Samples[55].boot = 'different' }
Test-Case 'CPU counter reset' { param($e) $e.Samples[55].cpu.total = 1 }
Test-Case 'disk counter reset' { param($e) $e.Samples[55].disk.writeBytes = 0 }
Test-Case 'pressure counter reset' { param($e) $e.Samples[55].pressureUs.cpu = 0 }
Test-Case 'container counter reset' { param($e) $e.Samples[55].containers[0].cpuUsageUs = 0 }
Test-Case 'unhealthy database' { param($e) $e.Samples[55].databaseReady = $false }
Test-Case 'unhealthy service' { param($e) $e.Samples[55].health = 'YELLOW' }
Test-Case 'container restart' { param($e) $e.Samples[55].containers[0].restarts = 1 }
Test-Case 'OOM event' { param($e) $e.Samples[55].containers[0].memoryEvents.oom_kill = 1 }
Test-Case 'changed limit' { param($e) $e.Samples[55].containers[0].memoryLimitBytes = 2000000 }
Test-Case 'duplicate container role' { param($e) $e.Samples[55].containers[1].role = 'sonar' }
Test-Case 'failed task' { param($e) $e.Tasks[0].status = 'FAILED' }
Test-Case 'duplicate task' { param($e) $e.Tasks += $e.Tasks[0] }
Test-Case 'short task lacks processing samples' { param($e) $e.Tasks[0].executedAt = $start.AddSeconds(51).ToString('o'); $e.Tasks[0].executionTimeMs = 1000 }
Test-Case 'missing baseline' { param($e) $e.Samples = $e.Samples[40..120] }
Test-Case 'missing cooldown' { param($e) $e.Samples = $e.Samples[0..70] }
Test-Case 'inconsistent task duration' { param($e) $e.Tasks[0].executionTimeMs = 100000 }
Test-Case 'host memory limit' { param($e) $e.Samples[55].memory.MemAvailable = 240000 } 'outside'
Test-Case 'disk free limit' { param($e) $e.Samples[55].space.available = 190000 } 'outside'
Test-Case 'container memory limit' { param($e) $e.Samples[55].containers[0].memoryBytes = 850000 } 'outside'
Test-Case 'incomplete queue inventory' { param($e) $e.Samples[55].queue.total = 2 } 'outside'
Test-Case 'foreign task' { param($e) $e.Samples[55].queue.total = 1; $e.Samples[55].queue.tasks = @([pscustomobject]@{ id = 'task-1'; componentKey = 'foreign' }) } 'outside'
Test-Case 'unknown task' { param($e) $e.Samples[55].queue.total = 1; $e.Samples[55].queue.tasks = @([pscustomobject]@{ id = 'unknown'; componentKey = $project }) } 'outside'
Test-Case 'CPU saturation' { param($e) foreach ($s in $e.Samples) { $s.cpu.idle = 8000 } } 'outside'
Test-Case 'memory pressure' { param($e) foreach ($s in $e.Samples) { $s.pressureUs.memory = ($s.uptime - 1000) * 50000 } } 'outside'
Test-Case 'I/O pressure' { param($e) foreach ($s in $e.Samples) { $s.pressureUs.io = ($s.uptime - 1000) * 200000 } } 'outside'
Test-Case 'disk saturation' { param($e) foreach ($s in $e.Samples) { $s.disk.busyMs = ($s.uptime - 1000) * 900 } } 'outside'

Write-Output "$passed capacity evidence cases passed. No network access or credentials used."
