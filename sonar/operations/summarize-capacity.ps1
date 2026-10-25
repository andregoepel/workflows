param(
    [Parameter(Mandatory)][string]$SamplesPath,
    [Parameter(Mandatory)][string]$TasksPath,
    [Parameter(Mandatory)][string]$Project,
    [Parameter(Mandatory)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Capacity.psm1') -Force
foreach ($path in @($SamplesPath, $TasksPath)) {
    if ((Get-Item -LiteralPath $path).Length -gt 32MB) { throw 'Evidence exceeds the bounded input limit.' }
}
if (Test-Path -LiteralPath $OutputPath) { throw 'Existing evidence must not be overwritten.' }

$samples = @(Get-Content -LiteralPath $SamplesPath | ForEach-Object { $_ | ConvertFrom-Json })
$tasks = @(Get-Content -LiteralPath $TasksPath -Raw | ConvertFrom-Json)
$summary = Get-CapacitySummary -Samples $samples -Tasks $tasks -Project $Project
$summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8NoBOM
if (!$summary.withinProposedEnvelope) { exit 1 }
