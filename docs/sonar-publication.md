# Shared NuGet verification support

Companion support for [Core #29](https://github.com/andregoepel/core/issues/29).
No workflow, publishing action or existing consumer pin changes here.

The non-packable `sonar` console tool now exposes the accepted preparation,
handoff, rehearsal, stored-intent and outcome-verification commands previously
implemented for app-foundation. The implementation uses its existing Octokit
client and Sonar HTTP boundary. There is no NuGet upload adapter, login operation
or persistent publishing credential in the tool.

## Consumer contract

Consumers supply `scripts/sonar/publication-target.json`:

```json
{
  "repository": "owner/library",
  "readinessWorkflowId": 123,
  "packages": ["Example.Library"],
  "verifierRevision": "<reviewed immutable workflows commit>"
}
```

The placeholder above is deliberately invalid. A nonzero lower-case 40-character
commit SHA is mandatory. Repository and workflow identities must be reviewed;
package IDs are unique, bounded and cannot contain path separators. Each package
currently maps to `src/<ID>/<ID>.csproj` and .NET 10 assembly/symbol payloads.
Supporting other layouts or target frameworks requires an explicit extension.

`WORKFLOWS_SONAR_TOOL_SHA` must match this verifier pin. The existing main-policy
tool pin still identifies the producer of analysis evidence; these revisions may
differ. Build the pinned verifier without publication credentials, then supply
`WORKFLOWS_GITHUB_TOKEN`, `WORKFLOWS_SONAR_TOKEN` and
`WORKFLOWS_SONAR_VERIFY_TOKEN` only for verification. Child build/test/pack commands
strip them. All NuGet CLI modes reject OIDC and NuGet publication credentials.

The consumer's existing contract/main policy binds exact tests, source, central
project, gate, baseline, frozen profiles, CI definitions and fresh run attempts.
The additional `nuget-policy.json` registers the preparation workflow ID, content
hash and mandatory steps. Empty IDs, missing policies and configuration drift
fail closed. Source hashing includes these consumer configuration files.

## Commands and trust

Use `--root ROOT --nuget-dry-run --sha SHA --version X.Y.Z --readiness-run ID`
for read-only evidence verification and local package preparation. A tagged
preparation adds `--tag vX.Y.Z` and uses `--nuget-prepare`; it requires the original
trusted tag-push context. Handoff modes include `--nuget-rehearse`,
`--nuget-verify-handoff`, `--nuget-verify-intent`, and the separate acceptance and
outcome inspection modes. Exact arguments are validated by `NuGetHandoffOptions`.
No mode authorizes or performs an upload.

Library analysis receipts remain `kind: library-analysis-readiness`,
`analysisReady: true`, `releaseEligible: false`. They are inputs, never publication
capabilities. Live source, CI, Sonar, preparation provenance, package hashes,
stored intent and freshness are rechecked independently. The consumer must also
enforce protected approval, immutable tags and a scoped publisher with no bypass.
All tool receipts preserve `releaseEligible: false`; the future reviewed workflow
uses official NuGet/login and dotnet push only after those checks.

## Validation

1,086 offline tests passed on both Windows and Linux. Release builds, CSharpier
and the current dependency vulnerability audit passed. Hosted CI for this change
must still run after the review branch is pushed.

The adapted offline suite covers positive handoff and negative/race cases for
pending/failed/missing gates, reruns and supersession, expired or mismatched
receipts, altered source/policy, archive authentication, package/symbol substitution,
tag retargeting, partial outcomes and retry restrictions. Additional tests prove
single-package inventory handling and isolation between consumer repositories.

App-foundation remains unchanged. Its policy, tokens, package identities and
enabled/disabled workflows are not copied into a live consumer. A shared-tool
change does not itself close Core's production-enforcement issue.
