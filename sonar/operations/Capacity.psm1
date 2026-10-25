Set-StrictMode -Version Latest

function Get-CapacitySummary {
    param([object[]]$Samples, [object[]]$Tasks, [string]$Project)

    if ($Samples.Count -lt 3 -or $Tasks.Count -eq 0 -or $Project -notmatch '^workflows-capacity-29-[a-f0-9]{32}$') {
        throw 'Complete samples, completed tasks and the isolated project identity are required.'
    }

    $first = $Samples[0]
    $last = $Samples[-1]
    $maximumGap = 0.0
    $cpuPeak = 0.0
    $diskPeak = 0.0
    $memoryPressurePeak = 0.0
    $ioPressurePeak = 0.0
    $cpuPressurePeak = 0.0
    $ioWaitPeak = 0.0
    $stealPeak = 0.0
    $minimumAvailable = 100.0
    $minimumDisk = 100.0
    $containerPeaks = @{ sonar = 0.0; database = 0.0 }
    $containerCpuPeaks = @{ sonar = 0.0; database = 0.0 }
    $failures = [Collections.Generic.HashSet[string]]::new()
    $taskIds = @($Tasks | ForEach-Object { $_.id })
    if (@($taskIds | Sort-Object -Unique).Count -ne $Tasks.Count) { throw 'Duplicate task identity.' }

    for ($index = 0; $index -lt $Samples.Count; $index++) {
        $sample = $Samples[$index]
        if ($sample.schemaVersion -ne 1 -or $sample.boot -cne $first.boot -or
            $sample.health -cne 'GREEN' -or !$sample.databaseReady -or $sample.containers.Count -ne 2) {
            throw 'Missing sample fields, changed host identity or unhealthy service/database.'
        }

        if ($sample.memory.MemTotal -le 0 -or $sample.memory.MemAvailable -lt 0 -or
            $sample.memory.MemAvailable -gt $sample.memory.MemTotal -or
            $sample.memory.MemTotal -ne $first.memory.MemTotal -or $sample.space.total -ne $first.space.total -or $sample.space.total -le 0 -or
            $sample.space.available -lt 0 -or $sample.space.available -gt $sample.space.total) {
            throw 'Invalid memory or disk measurement.'
        }

        $available = 100.0 * $sample.memory.MemAvailable / $sample.memory.MemTotal
        $diskAvailable = 100.0 * $sample.space.available / $sample.space.total
        $minimumAvailable = [Math]::Min($minimumAvailable, $available)
        $minimumDisk = [Math]::Min($minimumDisk, $diskAvailable)
        if ($available -lt 25) { [void]$failures.Add('Host available memory below 25%.') }
        if ($diskAvailable -lt 20) { [void]$failures.Add('Root disk free space below 20%.') }
        if ($sample.queue.total -ne $sample.queue.tasks.Count -or $sample.queue.total -gt 1) {
            [void]$failures.Add('Incomplete queue inventory or concurrent work.')
        }
        foreach ($active in $sample.queue.tasks) {
            if ($active.componentKey -cne $Project -or $active.id -notin $taskIds) {
                [void]$failures.Add('Unrelated or unaccounted task overlapped the measurement.')
            }
        }

        $seconds = 0.0
        if ($index -gt 0) {
            $previous = $Samples[$index - 1]
            $seconds = $sample.uptime - $previous.uptime
            $wallSeconds = ([DateTimeOffset]$sample.at - [DateTimeOffset]$previous.at).TotalSeconds
            if ($seconds -le 0 -or $seconds -gt 3 -or [Math]::Abs($wallSeconds - $seconds) -gt 0.5) {
                throw 'Missing samples, non-monotonic time or clock jump.'
            }
            $maximumGap = [Math]::Max($maximumGap, $seconds)
            $total = $sample.cpu.total - $previous.cpu.total
            $idle = $sample.cpu.idle - $previous.cpu.idle
            $wait = $sample.cpu.ioWait - $previous.cpu.ioWait
            $steal = $sample.cpu.steal - $previous.cpu.steal
            if ($total -le 0 -or $idle -lt 0 -or $wait -lt 0 -or $steal -lt 0 -or ($idle + $wait + $steal) -gt $total) {
                throw 'Host CPU counters reset or are invalid.'
            }
            $cpuPeak = [Math]::Max($cpuPeak, 100.0 * ($total - $idle - $wait) / $total)
            $ioWaitPeak = [Math]::Max($ioWaitPeak, 100.0 * $wait / $total)
            $stealPeak = [Math]::Max($stealPeak, 100.0 * $steal / $total)
            foreach ($field in @('readBytes', 'writeBytes', 'busyMs')) {
                if ($sample.disk.$field -lt $previous.disk.$field) { throw 'Disk counters reset.' }
            }
            foreach ($field in @('cpu', 'memory', 'io')) {
                if ($sample.pressureUs.$field -lt $previous.pressureUs.$field) { throw 'Pressure counters reset.' }
            }
            $diskPeak = [Math]::Max($diskPeak, 100.0 * ($sample.disk.busyMs - $previous.disk.busyMs) / ($seconds * 1000))
            $memoryPressurePeak = [Math]::Max($memoryPressurePeak, 100.0 * ($sample.pressureUs.memory - $previous.pressureUs.memory) / ($seconds * 1000000))
            $ioPressurePeak = [Math]::Max($ioPressurePeak, 100.0 * ($sample.pressureUs.io - $previous.pressureUs.io) / ($seconds * 1000000))
            $cpuPressurePeak = [Math]::Max($cpuPressurePeak, 100.0 * ($sample.pressureUs.cpu - $previous.pressureUs.cpu) / ($seconds * 1000000))
        }

        foreach ($role in @('sonar', 'database')) {
            $matches = @($sample.containers | Where-Object role -CEQ $role)
            $original = @($first.containers | Where-Object role -CEQ $role)
            if ($matches.Count -ne 1 -or $original.Count -ne 1) { throw 'Missing or duplicate container role.' }
            $container = $matches[0]
            $baseline = $original[0]
            if ($container.id -cne $baseline.id -or $container.startedAt -cne $baseline.startedAt -or
                $container.status -cne 'running' -or $container.health -cne 'healthy' -or
                $container.restarts -ne $baseline.restarts -or $container.oomKilled -or
                $container.memoryEvents.oom -ne $baseline.memoryEvents.oom -or
                $container.memoryEvents.oom_kill -ne $baseline.memoryEvents.oom_kill -or
                $container.memoryLimitBytes -le 0 -or $container.cpuLimit -le 0 -or
                $container.memoryLimitBytes -ne $baseline.memoryLimitBytes -or $container.cpuLimit -ne $baseline.cpuLimit -or
                $container.memoryBytes -lt 0 -or $container.memoryBytes -gt $container.memoryLimitBytes) {
                throw 'Container health, limits, identity, restart or OOM changed.'
            }
            $percent = 100.0 * $container.memoryBytes / $container.memoryLimitBytes
            $containerPeaks[$role] = [Math]::Max($containerPeaks[$role], $percent)
            if ($percent -ge 85) { [void]$failures.Add("$role cgroup memory reached 85% of its limit.") }
            if ($index -gt 0) {
                $before = @($previous.containers | Where-Object role -CEQ $role)[0]
                if ($container.cpuUsageUs -lt $before.cpuUsageUs -or $container.throttledUs -lt $before.throttledUs -or
                    $container.io.readBytes -lt $before.io.readBytes -or $container.io.writeBytes -lt $before.io.writeBytes) {
                    throw 'Container counters reset.'
                }
                $containerCpuPeaks[$role] = [Math]::Max($containerCpuPeaks[$role], 100.0 * ($container.cpuUsageUs - $before.cpuUsageUs) / ($seconds * 1000000 * $container.cpuLimit))
            }
        }
    }

    $completed = @()
    foreach ($task in $Tasks) {
        $start = [DateTimeOffset]$task.startedAt
        $end = [DateTimeOffset]$task.executedAt
        $during = @($Samples | Where-Object { [DateTimeOffset]$_.at -ge $start -and [DateTimeOffset]$_.at -le $end })
        if ($task.componentKey -cne $Project -or $task.status -cne 'SUCCESS' -or $end -le $start -or
            $task.executionTimeMs -le 0 -or [Math]::Abs(($end - $start).TotalMilliseconds - $task.executionTimeMs) -gt 2000 -or
            [DateTimeOffset]$first.at -gt $start.AddSeconds(-30) -or
            [DateTimeOffset]$last.at -lt $end.AddSeconds(30) -or $during.Count -lt 3) {
            throw 'Task failed or lacks baseline, processing samples or cooldown coverage.'
        }
        $completed += [ordered]@{ durationSeconds = $task.executionTimeMs / 1000.0; processingSamples = $during.Count }
    }

    if ($cpuPeak -ge 90) { [void]$failures.Add('Observed host CPU reached 90%.') }
    if ($diskPeak -ge 90) { [void]$failures.Add('Observed disk utilization reached 90%.') }
    if ($memoryPressurePeak -ge 5) { [void]$failures.Add('Observed memory pressure reached 5%.') }
    if ($ioPressurePeak -ge 20) { [void]$failures.Add('Observed I/O pressure reached 20%.') }

    [ordered]@{
        schemaVersion = 1
        withinProposedEnvelope = $failures.Count -eq 0
        releaseEligible = $false
        sampleCount = $Samples.Count
        measuredSeconds = $last.uptime - $first.uptime
        maximumSampleGapSeconds = [Math]::Round($maximumGap, 3)
        hostCpuPeakPercent = [Math]::Round($cpuPeak, 2)
        hostIoWaitPeakPercent = [Math]::Round($ioWaitPeak, 2)
        hostStealPeakPercent = [Math]::Round($stealPeak, 2)
        minimumHostAvailableMemoryPercent = [Math]::Round($minimumAvailable, 2)
        minimumDiskFreePercent = [Math]::Round($minimumDisk, 2)
        diskUtilizationPeakPercent = [Math]::Round($diskPeak, 2)
        memoryPressurePeakPercent = [Math]::Round($memoryPressurePeak, 2)
        ioPressurePeakPercent = [Math]::Round($ioPressurePeak, 2)
        cpuPressurePeakPercent = [Math]::Round($cpuPressurePeak, 2)
        containerMemoryPeakPercent = $containerPeaks
        containerCpuQuotaPeakPercent = $containerCpuPeaks
        containerThrottledMicroseconds = @{
            sonar = ($last.containers | Where-Object role -CEQ 'sonar').throttledUs - ($first.containers | Where-Object role -CEQ 'sonar').throttledUs
            database = ($last.containers | Where-Object role -CEQ 'database').throttledUs - ($first.containers | Where-Object role -CEQ 'database').throttledUs
        }
        diskReadBytes = $last.disk.readBytes - $first.disk.readBytes
        diskWriteBytes = $last.disk.writeBytes - $first.disk.writeBytes
        tasks = $completed
        findings = @($failures | Sort-Object)
        limits = 'Observed interval peaks for these sequential fixtures only; no concurrency, larger-repository capacity or unsampled instantaneous peak claim.'
    }
}

Export-ModuleMember -Function Get-CapacitySummary
