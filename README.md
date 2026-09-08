# workflows

Reusable GitHub Actions workflows and canonical config templates for
AndreGoepel and Nerdventures-Studio repositories.

This repo exists so the CI shape (restore → format → build → test →
vulnerability scan → [pack/publish | docker build/push]) is defined once and
consumed everywhere, instead of hand-maintained copies drifting apart across
repos. See [CLAUDE.md](CLAUDE.md) for how this repo fits into the wider
ecosystem.

## Canonical config templates

[`templates/`](templates/) holds the config boilerplate that's 90–100%
identical across every .NET repo in the ecosystem (`Directory.Build.props`,
`.editorconfig`, `.gitattributes`, Dependabot variants, a digest-pinned host-app
Dockerfile,
`dependabot-lockfile-sync.yml`, `NuGet.config`,
`tests/Directory.Build.props`). Unlike the reusable workflows below, these
are copied into a consumer repo rather than referenced with `uses:`. See
[`templates/README.md`](templates/README.md) for what each file is, which
variant applies when, and the `{{PLACEHOLDER}}` convention.

## Reusable workflows

All four live in [`.github/workflows/`](.github/workflows/) and are called at a
reviewed full commit SHA. In the examples below, replace
`{{WORKFLOWS_COMMIT_SHA}}` with the 40-character commit selected from this
repository. Never replace it with `main` or another mutable ref. The consumer's
`github-actions` Dependabot entry keeps the SHA current afterward.

### `ci-library.yml`

For NuGet library repos: restore (locked) → format check → build → test →
vulnerability scan. Does **not** pack or publish — see
[`nuget-publish` action](#nuget-publish-action) below for why that step
can't live in a called reusable workflow.

| Input | Required | Default | Description |
|---|---|---|---|
| `dotnet-version` | no | `10.0.x` | SDK version, `setup-dotnet` syntax |
| `test-filter` | no | `""` | Optional `dotnet test --filter` expression |

```yaml
# .github/workflows/ci.yml in a library repo, e.g. marten-configuration
name: CI
on:
  push:
    branches: ["main"]
    tags: ["v*"]
  pull_request:
    branches: ["main"]

jobs:
  ci:
    uses: andregoepel/workflows/.github/workflows/ci-library.yml@{{WORKFLOWS_COMMIT_SHA}}

  pack-and-publish:
    needs: ci
    runs-on: ubuntu-latest
    if: startsWith(github.ref, 'refs/tags/v')
    permissions:
      id-token: write # NuGet OIDC trusted publishing
      contents: read
    steps:
      - uses: andregoepel/workflows/.github/actions/nuget-publish@{{WORKFLOWS_COMMIT_SHA}}
        with:
          pack-projects: |
            src/AndreGoepel.Marten.Configuration/AndreGoepel.Marten.Configuration.csproj
          nuget-user: ${{ secrets.NUGET_USER }}
```

Multi-package repos (e.g. marten-identity, app-foundation) list every
project on its own line under `pack-projects`.

### `nuget-publish` action

Not a `workflow_call` workflow but a composite action, called with `uses:
andregoepel/workflows/.github/actions/nuget-publish@<full-commit-sha>` from a job defined
**directly in the consumer repo's own `ci.yml`** (see example above) — never
from inside another called reusable workflow.

Reason: NuGet.org's Trusted Publishing validates the OIDC token's
`job_workflow_ref` claim against the workflow file that defines the job
requesting the token. For a job inside a called `workflow_call` workflow,
that claim points at the *called* workflow (this repo), not the consumer —
so a policy scoped to the consumer repo can never match, no matter how the
policy is configured on nuget.org's end (confirmed as a structural
limitation, not a config mistake, in
[NuGet/login#6](https://github.com/NuGet/login/issues/6) and
[github/community#179952](https://github.com/orgs/community/discussions/179952)).
A composite action runs as steps inside the *caller's* own job, so
`job_workflow_ref` still points at the consumer's own workflow file and its
trusted-publishing policy matches correctly.

Checks out the tag, installs the SDK, packs every project listed, then
exchanges the OIDC token for a short-lived NuGet API key and pushes.

| Input | Required | Default | Description |
|---|---|---|---|
| `pack-projects` | **yes** | — | Newline-separated `.csproj` paths to pack |
| `nuget-user` | **yes** | — | NuGet trusted-publishing user |
| `nuget-source` | no | `https://api.nuget.org/v3/index.json` | Push target |
| `dotnet-version` | no | `10.0.x` | SDK version, `setup-dotnet` syntax |

The calling job must set `permissions: id-token: write` (to request the OIDC
token) and needs no other setup — the action does its own checkout and
`setup-dotnet`.

### `ci-hostapp.yml`

For Blazor/Aspire host apps: restore (locked) the deployed app + tests,
restore the AppHost + E2E project plainly (both dev-only, never shipped) →
format check → build → test (E2E excluded via `test-filter`) → vulnerability
scan. No publish step — host apps ship via `docker-image.yml`.

| Input | Required | Default | Description |
|---|---|---|---|
| `global-json-file` | no | `global.json` | Pins the SDK to match lockfiles |
| `restore-projects` | **yes** | — | Newline-separated deployed-app + test `.csproj` paths |
| `apphost-project` | **yes** | — | Aspire AppHost `.csproj`, restored plainly |
| `e2e-project` | no | `""` | E2E test `.csproj`, restored plainly if set |
| `test-filter` | no | `""` | Optional `dotnet test --filter` expression |

```yaml
# .github/workflows/ci.yml in finance-app
name: CI
on:
  push:
    branches: ["main"]
  pull_request:
    branches: ["main"]

jobs:
  ci:
    uses: andregoepel/workflows/.github/workflows/ci-hostapp.yml@{{WORKFLOWS_COMMIT_SHA}}
    with:
      restore-projects: |
        src/AndreGoepel.FinanceApp/AndreGoepel.FinanceApp.csproj
        src/AndreGoepel.FinanceApp.Domain/AndreGoepel.FinanceApp.Domain.csproj
        src/AndreGoepel.FinanceApp.Connectors/AndreGoepel.FinanceApp.Connectors.csproj
        src/AndreGoepel.FinanceApp.Categorization/AndreGoepel.FinanceApp.Categorization.csproj
        tests/AndreGoepel.FinanceApp.Tests/AndreGoepel.FinanceApp.Tests.csproj
        tests/AndreGoepel.FinanceApp.Domain.Tests/AndreGoepel.FinanceApp.Domain.Tests.csproj
        tests/AndreGoepel.FinanceApp.Connectors.Tests/AndreGoepel.FinanceApp.Connectors.Tests.csproj
        tests/AndreGoepel.FinanceApp.Categorization.Tests/AndreGoepel.FinanceApp.Categorization.Tests.csproj
      apphost-project: src/AndreGoepel.FinanceApp.AppHost/AndreGoepel.FinanceApp.AppHost.csproj
      e2e-project: tests/AndreGoepel.FinanceApp.E2ETests/AndreGoepel.FinanceApp.E2ETests.csproj
      test-filter: "FullyQualifiedName!~E2ETests"
```

A repo with no E2E suite (e.g. andregoepel-dev, customer-portal) simply
omits `e2e-project` and `test-filter`.

### `e2e.yml`

For the Aspire + Playwright end-to-end suite, run as its own workflow since
it needs Docker and browser binaries. **Trace-capture-on-failure is a
standard, non-optional feature of this workflow** — on failure it always
uploads whatever the test host wrote to `PLAYWRIGHT_TRACE_DIR` (14-day
retention). Consumer test hosts must write traces there on failure (see
marten-identity's `E2ETestBase`) to get a replayable trace instead of a bare
log.

| Input | Required | Default | Description |
|---|---|---|---|
| `e2e-project` | **yes** | — | E2E test `.csproj` |
| `dotnet-version` | no | `10.0.x` | SDK version; ignored if `global-json-file` is set |
| `global-json-file` | no | `""` | Pins the SDK; takes precedence over `dotnet-version` |
| `target-framework` | no | `net10.0` | TFM of the build output, used to locate `playwright.ps1` |

```yaml
# .github/workflows/e2e.yml in finance-app
name: E2E
on:
  push:
    branches: ["main"]
  pull_request:
    branches: ["main"]
  workflow_dispatch:

jobs:
  e2e:
    uses: andregoepel/workflows/.github/workflows/e2e.yml@{{WORKFLOWS_COMMIT_SHA}}
    with:
      e2e-project: tests/AndreGoepel.FinanceApp.E2ETests/AndreGoepel.FinanceApp.E2ETests.csproj
      global-json-file: global.json
```

A repo pinned only via SDK version (e.g. marten-identity) omits
`global-json-file` and relies on the `dotnet-version` default.

### `docker-image.yml`

Builds the deployed app's Docker image on every push/PR; pushes to GHCR
(tagged `:<sha>` and `:latest`) only on push to `main`. The called workflow
exposes `jobs.<job-id>.outputs.digest`, which is the immutable manifest digest
that a downstream deployment must consume.

| Input | Required | Default | Description |
|---|---|---|---|
| `image` | **yes** | — | Fully-qualified image name, e.g. `ghcr.io/andregoepel/finance-app` |
| `dockerfile` | **yes** | — | Path to the Dockerfile |
| `context` | no | `.` | Docker build context |

`image` is required rather than defaulted to `ghcr.io/${{ github.repository
}}`, because GHCR image names must be lowercase and at least one consumer
(`Nerdventures-Studio/customer-portal`) has an uppercase org slug and must
override it explicitly.

```yaml
# .github/workflows/docker-image.yml in finance-app
name: Docker Image CI
on:
  push:
    branches: ["main"]
  pull_request:
    branches: ["main"]

jobs:
  docker:
    uses: andregoepel/workflows/.github/workflows/docker-image.yml@{{WORKFLOWS_COMMIT_SHA}}
    with:
      image: ghcr.io/${{ github.repository }}
      dockerfile: ./src/AndreGoepel.FinanceApp/Dockerfile
    permissions:
      contents: read
      packages: write # push to GHCR via the built-in GITHUB_TOKEN
```

For a caller job named `docker`, the pushed image is selected immutably as:

```yaml
jobs:
  deploy:
    needs: docker
    runs-on: ubuntu-latest
    env:
      APP_IMAGE: ghcr.io/andregoepel/finance-app@${{ needs.docker.outputs.digest }}
    steps:
      # Pass APP_IMAGE to the deployment mechanism and verify the running digest.
      - run: ./deploy.sh "$APP_IMAGE"
```

The deployment must pass that reference to its orchestrator, verify the
running container against the expected digest, and record both values for
audit and rollback. `latest` remains a convenience/discovery tag only.

`Nerdventures-Studio/customer-portal` passes `image:
ghcr.io/nerdventures-studio/customer-portal` explicitly instead.

## Permissions

`GITHUB_TOKEN` permissions granted to a called workflow are capped by the
permissions the **caller** grants at the job (or workflow) level — set
`permissions:` on the calling job as shown above (`id-token: write` +
`contents: read` for `ci-library.yml`'s publish step; `packages: write` +
`contents: read` for `docker-image.yml`), otherwise those steps fail even
though the reusable workflow itself declares the permissions it needs.

## Versioning

Every workflow here pins third-party actions to full commit SHAs, and every
consumer must pin this repository the same way. Select a reviewed commit from
`main`, place its 40-character SHA in the consumer workflow with a same-line
reference comment, and let Dependabot propose later revisions. A moving branch
or major-version tag is not an acceptable pipeline identity.

## Validation

[`validate.yml`](.github/workflows/validate.yml) runs `actionlint` and checks
the immutable container baseline on every push/PR to this repo.

## Migration status

No consumer repo has been switched over to these reusable workflows yet.
Each repo's own `ci.yml` / `e2e.yml` / `docker-image.yml` continues to run
its current copy-pasted version until it's migrated in its own PR, tracked
per-repo:

- app-foundation#151
- marten-identity#156
- marten-configuration#12
- finance-app#65
- customer-portal#89
- andregoepel-dev#56
