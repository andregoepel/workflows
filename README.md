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
`.editorconfig`, `.gitattributes`, `dependabot.yml`,
`dependabot-lockfile-sync.yml`, `NuGet.config`,
`tests/Directory.Build.props`). Unlike the reusable workflows below, these
are copied into a consumer repo rather than referenced with `uses:`. See
[`templates/README.md`](templates/README.md) for what each file is, which
variant applies when, and the `{{PLACEHOLDER}}` convention.

## Reusable workflows

All four live in [`.github/workflows/`](.github/workflows/) and are called
with `uses: andregoepel/workflows/.github/workflows/<file>.yml@main`. Pin to
`@main` for now (see [Versioning](#versioning) below).

### `ci-library.yml`

For NuGet library repos: restore (locked) → format check → build → test →
vulnerability scan, then pack + publish to NuGet on a `vX.Y.Z` tag push via
OIDC trusted publishing.

| Input | Required | Default | Description |
|---|---|---|---|
| `dotnet-version` | no | `10.0.x` | SDK version, `setup-dotnet` syntax |
| `test-filter` | no | `""` | Optional `dotnet test --filter` expression |
| `pack-projects` | **yes** | — | Newline-separated `.csproj` paths to pack |
| `nuget-source` | no | `https://api.nuget.org/v3/index.json` | Push target |

| Secret | Required | Description |
|---|---|---|
| `NUGET_USER` | **yes** | NuGet trusted-publishing user |

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
    uses: andregoepel/workflows/.github/workflows/ci-library.yml@main
    with:
      pack-projects: |
        src/AndreGoepel.Marten.Configuration/AndreGoepel.Marten.Configuration.csproj
    secrets:
      NUGET_USER: ${{ secrets.NUGET_USER }}
    permissions:
      id-token: write # NuGet OIDC trusted publishing
      contents: read
```

Multi-package repos (e.g. marten-identity, app-foundation) list every
project on its own line under `pack-projects`.

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
    uses: andregoepel/workflows/.github/workflows/ci-hostapp.yml@main
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
    uses: andregoepel/workflows/.github/workflows/e2e.yml@main
    with:
      e2e-project: tests/AndreGoepel.FinanceApp.E2ETests/AndreGoepel.FinanceApp.E2ETests.csproj
      global-json-file: global.json
```

A repo pinned only via SDK version (e.g. marten-identity) omits
`global-json-file` and relies on the `dotnet-version` default.

### `docker-image.yml`

Builds the deployed app's Docker image on every push/PR; pushes to GHCR
(tagged `:<sha>` and `:latest`) only on push to `main`.

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
    uses: andregoepel/workflows/.github/workflows/docker-image.yml@main
    with:
      image: ghcr.io/${{ github.repository }}
      dockerfile: ./src/AndreGoepel.FinanceApp/Dockerfile
    permissions:
      contents: read
      packages: write # push to GHCR via the built-in GITHUB_TOKEN
```

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

Every workflow here is pinned to a commit SHA internally for every third-party
action it uses (never a mutable tag), so a compromised upstream action can't
silently enter any consumer's pipeline. Consumers currently pin their `uses:`
to `@main` — there is no tagged-release scheme yet for this repo itself. If
that becomes a problem (a breaking change to an input contract landing on
`main` without warning), switch consumers to a `vX` tag instead; not needed
for the initial bootstrap.

## Validation

[`validate.yml`](.github/workflows/validate.yml) runs `actionlint` on every
push/PR to this repo, so a broken reusable workflow is caught here before
any consumer pulls it from `@main`.

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
