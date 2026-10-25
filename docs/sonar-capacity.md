# Bounded Sonar capacity measurement

Tracking: [#29](https://github.com/andregoepel/workflows/issues/29).
This is a capacity record for the existing shared service. It does not activate
a repository, authorize a release, install monitoring or change service limits.

## Plan and operating envelope

Use a disposable private project with a random `workflows-capacity-29-<guid>` key,
a project-analysis token expiring the next day, and the already frozen C# quality
profile/gate. Associate these with the temporary project without editing them.
The administering identity needs Execute Analysis on that temporary project to
issue its project token; global administration alone was insufficient. Delete the
project and revoke its token after its queue is empty. Never grant a global scan
permission or reuse a consumer's project key or token for the fixture.

The [fixture generator](../sonar/operations/new-capacity-fixture.ps1) creates
non-packable C# libraries of 250 and 1,000 classes, four methods and 16 conditional
branches per class. It pins SDK 10.0.402 and official SonarScanner 12.0.0. There are
no package dependencies, database/browser tests, coverage or publishing steps.
Run official scanner begin/build/end sequentially on the operator's computer;
only normal report upload and compute-engine processing occur on the VPS. Follow
the [official .NET scanner sequence](https://docs.sonarsource.com/sonarqube-community-build/analyzing-source-code/scanners/dotnet/using).
Keep token values out of transcripts and redact process output before saving it;
remove owned scanner scratch after execution. No production-readiness record is
created by this diagnostic exercise.

The [read-only collector](../sonar/operations/collect-capacity.sh) requires Linux
cgroup v2, Docker access and a previously reviewed root-owned helper exposing
`api ENDPOINT OUTPUT_FILE [curl arguments]`. The helper supplies authentication
privately. Review its provenance before sourcing it; this repository does not
install it or contain credentials. Stream the script over the existing authorized
SSH connection with four arguments: duration (30–900 seconds), exact Sonar and
PostgreSQL container identities, and the protected helper path. Redirect stdout
to a private local JSONL file. Never upload that file as a public CI artifact.

The executed plan bounded sampling to 600 seconds. Each sample records host CPU,
memory, PSI pressure, root disk capacity/I/O, both containers' cgroup CPU/memory/I/O,
configured ceilings, restart/OOM counters, database readiness, Sonar health and
the complete active task queue. It sleeps one second between collections; actual
cadence includes API/Docker overhead. Samples are sequential observations, not
atomic or instantaneous measurements. Record all completed task IDs, timestamps
and durations from Sonar, and retain setup/cleanup results alongside the samples.

Before upload and before starting another fixture, require an empty queue,
GREEN health, a ready database, samples no older than eight seconds, at least 25%
host memory available, 20% root disk free, and less than 85% of each container's
memory limit. Refuse a subsequent upload on restart/OOM or failed processing.
Bound each scanner/build stage to three minutes; retain diagnostics on timeout.
If a CE task exceeds three minutes, submit no more work and inspect it. Do not
restart the service to abort a task; wait for completion before deleting its
temporary project. An unresolved active task prevents cleanup and acceptance.

The offline [summarizer](../sonar/operations/summarize-capacity.ps1) requires at
least 30 seconds of baseline and cooldown, at least three observations during
each evaluated task, no gaps above three seconds, monotonic counters and stable
host/container identities and limits. Incomplete/failed evidence throws. Its
proposed-envelope flag is false for foreign/concurrent work or observed host CPU
or disk utilization reaching 90%, memory PSI reaching 5%, I/O PSI reaching 20%,
or the headroom limits above. Even a passing capacity summary always has
`releaseEligible: false`. Those ceilings are conservative operating decisions,
not newly configured service limits or a general performance guarantee.

```powershell
pwsh -File sonar/operations/summarize-capacity.ps1 `
  -SamplesPath <private-jsonl> -TasksPath <private-task-array-json> `
  -Project <owned-fixture-project-key> -OutputPath <new-local-summary-json>
```

Inputs and output stay local until the summary is inspected for publication.
The collector does not submit analyses. There is deliberately no unattended load
generator or workflow trigger. An operator owns submission, live abort decisions,
token revocation, project cleanup and final configuration comparisons.

## Measurement record

The 25 October 2026 measurement used two sequential synthetic uploads. The small
fixture contained 22,000 non-comment lines and completed CE processing in 2.889
seconds. It received only two processing samples, below the three-sample criterion;
its task success is established, but it is not a qualified peak measurement.

The larger fixture contained 88,000 non-comment lines in 1,000 files/classes,
4,000 functions and complexity 20,000. Its CE task succeeded in 6.727 seconds,
with four processing observations. Both fixtures had 96.6% duplicated lines:
they exercise C# upload/processing but do not represent the semantic diversity,
coverage or database integration of a real application.

The full private stream includes both uploads. The qualified larger-fixture
window begins 30 seconds after the small task completed and retains the entire
larger task plus cooldown. The small task's insufficient sampling is retained
as an explicit limitation; it is not silently counted as a passing measurement.
The full stream has 378 observations from 03:35:44 to 03:45:42 UTC. The qualified
window runs from 03:37:32 to 03:45:42 UTC (489.7 seconds, 310 observations); its
largest sample gap is 1.73 seconds. The service was already warm, including the
preceding small fixture; cold-start capacity is not established.

| Measurement in the qualified window | Result |
|---|---|
| Host available memory, first sample / minimum | 59.73% / 56.73% |
| Sonar cgroup memory, first sample / maximum of configured limit | 44.38% / 48.49% |
| PostgreSQL cgroup memory, first sample / maximum of configured limit | 14.22% / 15.90% |
| Host CPU / I/O wait / steal maximum interval percentage | 31.50% / 0.30% / 0.00% |
| Sonar / PostgreSQL CPU maximum relative to configured quota | 86.98% / 53.60% |
| Sonar / PostgreSQL cgroup throttled CPU counter increase | 1.024 s / 0.203 s |
| CPU / memory / I/O PSI maximum interval percentage | 4.41% / 0.00% / 0.80% |
| Root disk free minimum / busy maximum interval percentage | 74.17% / 7.60% |
| Root disk read / write counter increase | 69,632 / 97,009,664 bytes |
| Sonar health / database readiness | GREEN / ready at every observation |
| Active queue / unexpected work | At most one task / none |
| Container identity, limits, restarts and OOM | Stable; no new restart or OOM |

The qualified summary passes the proposed envelope. CPU throttling occurred even
though host headroom remained; do not infer spare concurrent-task capacity from
host CPU alone. Interval percentages cannot recover peaks between observations.
Disk counters include other existing host activity and are not attributed solely
to Sonar.

Cleanup verified that all owned diagnostic projects and tokens were absent, no
task remained active, and final service health was GREEN. Compose and service-unit
fingerprints matched preflight; the two existing consumer projects' latest CE
task identities/statuses were unchanged. No consumer policy, baseline, credential,
service limit or repository was changed by the exercise. The raw streams, task
records, rejected full-window assessment, qualified subset, summary, checksum
manifest and cleanup state remain in ignored local evidence.

Earlier diagnostic attempts are also retained privately: collector smoke tests
exposed root-device whitespace parsing (fixed before measurement), setup stopped
before scanning on a missing project-level analysis permission, and a desktop
interruption stopped a subsequent attempt during baseline sampling. No upload
occurred in those attempts. Their owned resources were removed and verified before
the fresh run. They are not continuous-health evidence for the completed window.

## Rollout recommendation and limits

Retain **one analysis submission at a time across the shared service** and an
empty active queue before starting the next planned analysis. The temporary
fixture ceiling is 1,000 classes/88,000 non-comment C# lines of this construction;
it does not establish that an arbitrary repository of that size is safe. Do not
increase memory/CPU limits on the basis of this run. Measure each real consumer's
first representative analysis and test needs before admitting it to the envelope.

The reusable workflow's concurrency group serializes only one repository. It is
not a cross-repository scheduler. Coordinate planned analyses operationally during
onboarding. If automatic triggers across more repositories could overlap, obtain
a separately reviewed central queue/admission mechanism or concurrent-load test
before claiming guaranteed global serialization. No such mechanism is added here.

Concurrent uploads, larger/diverse real workloads, long-duration saturation,
backup contention and unsampled instantaneous peaks remain untested. Existing
monitoring/backup evidence is separate from this short capacity measurement.
Marten repositories needing PostgreSQL/Testcontainers require a separately
reviewed runner extension; capacity evidence does not add that capability.

Cleanup/rollback removes only the diagnostic project's token, project and owned
scanner scratch. Compare original service/container identities, OOM/restart
counters, composition/service fingerprints, consumer analyses and policies. Record
any mismatch instead of resetting an existing baseline or modifying a consumer.
Permanent monitoring remains [app-foundation #287](https://github.com/andregoepel/app-foundation/issues/287).
