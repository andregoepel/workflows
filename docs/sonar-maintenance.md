# Sonar maintenance and credential boundaries

Tracking: [#30](https://github.com/andregoepel/workflows/issues/30).
Owner and reviewer: André Goepel. This procedure covers the shared runner and its
Core consumer. Applying a consumer change requires a separate Core review; merging
the shared changes does not update an immutable consumer pin.

## Cadence and update coverage

| When | Review | Evidence and response |
|---|---|---|
| Weekly | Dependency advisories, Dependabot proposals, token expiry metadata | Review on Monday; address known vulnerabilities before accepting a build; record the next credential action privately |
| Monthly | SDK, scanner, coverage, formatter, packages and workflow pins | Review on the first Monday; open one compatible update batch with the affected consumer changes listed |
| 30 days before token expiry | Replacement identities, token type/scope, environment destinations | Prepare rotation; complete validation and revocation at least 14 days before expiry |
| Before each new consumer or material workload change | Capacity, test requirements, secret scope | Apply the [capacity procedure](sonar-capacity.md); no assumed concurrency allowance |
| Every workflow pin, secret, permission, server or quality-profile change | Credential exposure and policy compatibility | Review the complete trust boundary and fresh CI/Sonar evidence |

These are operator responsibilities, not newly installed timers or reminders.
The protected operational record contains actual token names, creation/expiry
dates, destinations, last verification and next review dates. Never commit token
values, host inventory or private credential records. The initial read-only audit
recorded both Core token expiries and confirmed that renewal is not overdue.

The shared `.github/dependabot.yml` retains weekly Action updates and adds weekly
NuGet proposals for `/sonar`, grouping minor/patch changes with three open NuGet
PRs maximum. This becomes effective only after review and merge. It neither merges
updates nor changes any consumer. Review every lockfile diff and advisory result.
SDK pins, scanner/coverage contracts, PowerShell fixtures and coordinated consumer
policy hashes remain manual responsibilities even if Dependabot also proposes a
tool-manifest change. See the [Dependabot options](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference).

Current compatibility set (an inventory, not a claim that these are latest):

| Component | Reviewed version/source of truth |
|---|---|
| .NET SDK | `10.0.402`, `sonar/global.json`; consumer `global.json` and contract must agree |
| SonarScanner for .NET | `12.0.0`, consumer tool manifest and contract; capacity fixture pins it explicitly |
| Microsoft dotnet-coverage | `18.12.0`, consumer manifest and contract |
| CSharpier | `1.3.0`, shared and consumer tool manifests |
| Runner packages | `sonar/Directory.Packages.props` and both `packages.lock.json` files |
| Actions and shared workflow | Reviewed full commit SHAs; consumer caller, tool revision and policy must match |
| Sonar server, frozen profiles and gate | Exact consumer contract/policy, independently verified against the service |

## Coordinated update and rollback

1. Record the current shared SHA, consumer main SHA, SDK/tool versions, contract,
   policy hashes, baseline ID, gate/profile inventory and successful CI/Sonar run
   IDs and attempts. Do not retain credentials in this record.
2. Prepare a shared PR first. Update relevant pins and lockfiles together; preserve
   source/coverage checks, fail-closed behavior and the BCL-only Core boundary.
   Run locked restore, formatting, build, the full offline suite and vulnerability
   audit on Windows and Linux. Check Action syntax and capacity regression tests.
3. Prepare the consumer PR against the merged shared commit. Align its SDK selection,
   tool manifest, contract, caller `uses` SHA, `tool-revision`, policy `toolRevision`
   and any publishing policy hashes that bind changed files. Check all consumers of
   the changed interface before approving it. No wildcard SDK selection or mutable
   workflow ref is acceptable.
4. Test local Windows/Linux analysis compatibility first. Server/analyzer upgrades
   need their own isolated restore/upgrade validation and policy review; never hide
   changed rule inventories by silently updating expectations. Baseline replacement
   requires a separate explicit decision.
5. After the reviewed consumer merge, retain two fresh exact-main successful CI and
   Sonar readiness sets, correct coverage, unchanged baseline and approved gate,
   plus negative stale/wrong-policy checks. For a publisher consumer, validate the
   no-upload handoff separately without dispatching an actual release. Record run
   attempts and avoid duplicate analyses.
6. If validation fails, stop the rollout. Restore the last reviewed compatible pins,
   contract and policy together through a revert PR, then obtain fresh CI/Sonar
   evidence. Do not reuse pre-revert readiness, weaken a gate, reset a baseline,
   bypass a publishing guard or downgrade a migrated Sonar database in place.

## Core credential rotation: prepared, not executed

Core's scan credential is a project-analysis token. Its separate verifier is a
user token owned by a dedicated Browse-only account. User tokens inherit their
owner's permissions; a descriptive token name does not impose a scope. The admin
operator separately retains Browse and See Source Code on the private project.
See [Sonar token types and expiry](https://docs.sonarsource.com/sonarqube-community-build/user-guide/managing-tokens).

The current work leaves other repositories unchanged. **#30 remains open until a
separately authorized Core rotation and its post-rotation checks pass.** The exact
destinations to review are Core's `sonar-main`, `sonar-nuget-prepare` and
`nuget-publish` environments, each with `SONAR_SCAN_TOKEN` and `SONAR_VERIFY_TOKEN`,
plus the protected VPS credential source. Do not create repository-level fallback
copies. Publishing permissions and NuGet credentials are outside this rotation.

1. Read current expiry metadata and enumerate every existing copy without reading
   secrets into logs. Snapshot project visibility, direct/group permissions,
   environment reviewers, branch/tag restrictions, self-approval setting and
   administrator-bypass setting. Confirm André can approve his own runs and bypass
   remains disabled. Check scan identity cannot inherit broader group permissions.
2. Create replacement tokens with the same least privilege and bounded expiry.
   Give them unique names; keep old tokens available for rollback temporarily.
   Transfer values only through protected memory/stdin from the authorized VPS
   source to the six explicitly approved GitHub environment destinations. Never
   use issue comments, command history, artifacts or plaintext temporary files.
3. Validate a protected main analysis with the replacement scan token and readiness
   with the replacement verifier. Validate preparation and publisher-environment
   copies with a reviewed no-upload harness. Do not trigger a release or use the
   live publishing job as a credential test.
4. Record this access matrix using read-only requests, permission inspection and
   the intended analysis. Do not prove a denial by attempting destructive changes
   against another live project; use an isolated fixture if an HTTP denial needs
   demonstration. Compare all protections with step 1.

   | Identity | Required positive checks | Required negative checks |
   |---|---|---|
   | Scanner | Submit analysis to Core only | No Browse/source/admin permissions; no scan/access grant on another project |
   | Verifier | Read Core measures, tasks, gate and baseline | No source, Execute Analysis, project/system administration or other private project access |
   | Operator | Browse and See Source Code on private Core | Project remains private; no new public/anonymous access |

5. Only after all destinations and the access matrix pass, revoke superseded tokens.
   Confirm old authentication is rejected and the new verifier still reads the
   current analysis. Retain sanitized run/task IDs and private expiry metadata.
   If any destination fails before revocation, restore the old copies and investigate;
   if old tokens are expired/revoked, provision a replacement, never revive a bypass.

## Narrower reusable-workflow handoff

`sonar-library.yml` now declares two optional named secrets. Existing approved
`secrets: inherit` callers remain compatible; missing tokens still fail in the
runner. Optional declarations alone **do not reduce Core's current exposure**.

After this shared PR is merged, a separately reviewed Core pin/policy update can
replace inheritance with:

```yaml
secrets:
  SONAR_SCAN_TOKEN: ${{ secrets.SONAR_SCAN_TOKEN }}
  SONAR_VERIFY_TOKEN: ${{ secrets.SONAR_VERIFY_TOKEN }}
```

GitHub documents named forwarding and the called job's environment-secret
resolution in [reusing workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
Keep `environment: sonar-main` in the called job, and preserve required reviewers
and deployment restrictions. Verify this exact handoff on the protected hosted
runner before declaring migration complete; actionlint alone does not prove
environment resolution. Do not work around an empty value by copying secrets to
repository scope or broadening inheritance.

Core currently exposes caller-accessible secrets, including its existing
`NUGET_USER`, to the pinned reusable workflow through inheritance. The analysis
job does not reference that value. Review all inherited names and every changed
shared revision while this remains in place. Named forwarding limits caller
secrets, but secrets stored directly on `sonar-main` remain available to that job;
keep that environment dedicated to Sonar. The automatic `GITHUB_TOKEN` remains
limited to `contents: read` and `actions: read`. No OIDC permission is introduced.
