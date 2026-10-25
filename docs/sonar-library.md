# Opt-in Sonar library analysis

This addition extracts the analysis/readiness subset accepted in
[app-foundation PR #289](https://github.com/andregoepel/app-foundation/pull/289)
at commit `9e93efe1058637ea9bd781a33f9ff32e03104b09`. Its offline tests retain
the established failure cases. The shared runner uses the official SonarScanner
for .NET and Microsoft `dotnet-coverage`; Octokit.NET handles GitHub requests.
The small Sonar HTTP boundary covers server APIs without an unsupported .NET SDK.

Tracking: [workflows #27](https://github.com/andregoepel/workflows/issues/27),
[core #24](https://github.com/andregoepel/core/issues/24),
[rollout epic #238](https://github.com/andregoepel/app-foundation/issues/238).

## Scope and boundaries

The first consumer is `core`. Existing reusable workflows, their consumers,
and app-foundation's implementation remain unchanged. This runner supports the
current ecosystem's .NET 10 library layout: a `.slnx` solution, `src/` projects,
`tests/<project>/<project>.csproj`, xUnit v3/Microsoft.Testing.Platform test names
under `AndreGoepel.*`, and no external services or browser setup. Host-app/E2E
onboarding needs a separately reviewed extension. E2E is not applicable to core.

`sonar/` is an isolated non-packable .NET tool, not a library for consumer
applications. Its `AndreGoepel.Core` dependency supplies the existing Result
abstraction. It adds no dependency to the Core library. The legacy Python image
producers are unrelated and unchanged. App-foundation can migrate to this shared
implementation in a later reviewed change; its publishing receipts are not changed.

The output is **analysis readiness only**: `kind: library-analysis-readiness`,
`analysisReady: true`, `releaseEligible: false`. It does not authorize package
uploads or deployments, and cannot substitute for app-foundation's release receipt.
No OIDC access, package credentials, NuGet login or push operation is provided.

## Consumer contract

Each consumer commits these reviewed files:

- `scripts/sonar/contract.json`: SDK/server versions, central server identity,
  gate name, complete solution/test inventory with minimum discovered counts,
  executed source anchors and generated-source exclusions. Authored coverage
  exclusions are prohibited. Core's example is in its onboarding PR.
- `scripts/sonar/main-policy.json`: repository and workflow paths, HTTPS Sonar
  target, project/server identity, immutable tool revision, gate conditions,
  frozen quality profiles with rule counts/timestamps, approved
  `SPECIFIC_ANALYSIS` baseline, evidence age limits, and mandatory CI jobs/steps.
- An immutable new `sonar-main.yml` caller. Its reusable-workflow SHA,
  `tool-revision` input and policy `toolRevision` must match. Never use a branch
  or tag. The runner verifies the actual referenced workflow and tool checkout.
- Pinned local tools: SonarScanner 12.0.0, dotnet-coverage 18.12.0, CSharpier 1.3.0.
  The runner SDK is exactly 10.0.402. NuGet lock files are mandatory.
- Ignore `artifacts/`, `.sonarqube/` and `.sonar-analysis.lock`.

Standalone CI uses empty `sharedPath`/`sharedSha`; unexpected reusable references
are rejected. CI using one reusable workflow must record its exact path and SHA.
Multiple/nested reusable CI chains need an explicit policy-model extension.
Only listed required jobs/steps count; an intentionally skipped tag publisher
on a main push is not treated as a failed mandatory job.

## Central workflow and initial activation

The new `.github/workflows/sonar-library.yml` is callable only. Its job requires
the consumer variable `SONAR_MAIN_ENABLED=true` and a completed main-push CI event.
It runs in the **consumer's** `sonar-main` environment with `contents: read` and
`actions: read`. Configure branch restrictions to main and the agreed reviewer
before adding `SONAR_SCAN_TOKEN` and `SONAR_VERIFY_TOKEN` environment secrets.
Use a project-analysis token and a separate browse-only verification identity.
No token belongs in a file, CLI argument supplied by an operator, PR or artifact.

Review/merge shared support first, then the consumer. Provision the consumer's
separate private Sonar project, copied/frozen profiles, dedicated gate and scoped
credentials. Capture actual profile rule revisions into its reviewed policy;
do not copy app-foundation's project, baseline, tokens or profile timestamps.
Keep the opt-in variable unset until these prerequisites are reviewed.

A null baseline is allowed only to retain the initial complete analysis. It
deliberately fails readiness and emits no `readiness.json`. Select that actual
main analysis as the reviewed baseline, commit its ID through review, and only
then collect successful readiness evidence. Empty profile inventory fails before
analysis. There is no bootstrap switch that silently grants readiness.

GitHub's caller context is retained inside a reusable workflow; the runner binds
that context to the current main SHA and triggering CI attempt, then rechecks
the GitHub API before, during and after analysis. It also verifies successful
required jobs/steps, freshness, source hashes, unchanged policy, the submitted CE
task, latest analysis, revision/build string, gate and exact baseline. A newer
main commit, rerun, missing evidence, stale evidence or changed policy fails closed.
See [GitHub's reusable workflow context](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

Analysis is serialized per consumer. During wave 1 only core is activated; do not
enable a further consumer concurrently until staging capacity has been measured.
See the [bounded capacity evidence and operating limits](sonar-capacity.md).
Per-repository concurrency groups do not serialize different repositories.
Successful final jobs upload one short-lived readiness record; failures may upload
only the sanitized summary. Private logs, coverage XML and scanner files are never
uploaded. Scanner scratch state is removed; an existing lock/state is preserved
and requires inspection instead of automatic deletion.

## Local Windows and Linux verification

Use PowerShell 7 and .NET SDK 10.0.402 on either platform. Start a separate
loopback Sonar server on port 9000 at the contract version, provision a
`<repository>-local` project and matching dedicated gate/profiles, and use
short-lived local scan/verification credentials. The main project and central
server cannot be used for local branch scans. Local results never create readiness.
The core wrapper fetches an exact shared commit, builds outside the analyzed
solution, strips credentials during bootstrap and invokes the same runner.

The full pipeline checks CSharpier, locked restore, vulnerabilities, ordinary
build, scanner build, every solution project, complete test discovery/execution,
fresh xUnit/TRX reports, coverage source checksums/anchors, merged coverage and
Sonar's actual coverage import. It uses the documented
[dotnet-coverage integration](https://docs.sonarsource.com/sonarqube-community-build/analyzing-source-code/dotnet-environments/dotnet-test-coverage).

To develop the shared tool, from `sonar/` run:

```text
dotnet restore --locked-mode
dotnet tool restore
dotnet csharpier format .
dotnet build -c Release --no-restore
dotnet test -c Release --no-build --no-restore
dotnet list package --vulnerable --include-transitive --format json --no-restore
```

The additive `validate-sonar.yml` runs these checks on Windows and Linux without
Sonar credentials or publishing permissions. Existing validation remains in place.
The [maintenance procedure](sonar-maintenance.md) defines dependency-update coverage,
coordinated pin changes, credential rotation and the proposed named-secret handoff.
Consumer adoption and hosted validation remain separate reviewed changes.

## Rollout acceptance and rollback

PR checks/local scans are preparation evidence. After reviewed activation, retain
two fresh exact-main CI/Sonar sets, controlled local gate failure/recovery,
missing/stale/wrong-revision rejection tests, and staged resource/task-processing
measurements. No main-branch failure drill is implied; obtain explicit approval
for one if needed. Keep the wave issue open until this evidence is accepted.

Rollback: unset `SONAR_MAIN_ENABLED`, wait for the in-flight analysis to finish,
revoke only this consumer's scoped credentials, and revert the additive caller
through review if needed. Retain analysis evidence. Do not reset another project's
baseline or change existing CI/publishing. A stopped readiness producer makes no
claim that an existing publisher is protected.
