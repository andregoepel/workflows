# Canonical config templates

Literal, copy-into-your-repo templates for the config boilerplate that's
90–100% identical across every .NET repo in the ecosystem (andregoepel-dev,
finance-app, customer-portal, marten-configuration, marten-identity,
app-foundation). These extract the shape that already converged across
those repos' own consolidation work — they aren't a new standard invented
here, they're the existing standard written down once so the next repo (or
the next drift-fix) starts from a copy instead of a blank page.

This repo has no `.NET code` of its own — nothing here is built or restored.
These files are consumed by copying, not by reference (`git submodule`, an
MSBuild `<Import>` across repos, etc. — not worth the coupling for files
this small).

## Placeholder convention

Every value that legitimately varies per consumer is written as
`{{PLACEHOLDER_NAME}}`. Search-and-replace these after copying a template
in; don't leave a `{{...}}` token committed. The placeholders used across
these templates:

| Placeholder | Meaning | Example |
|---|---|---|
| `{{AUTHOR}}` | NuGet `Authors` / copyright holder | `André Göpel` |
| `{{COPYRIGHT}}` | Full `Copyright` property value | `© André Göpel` |
| `{{REPO_URL}}` | This repo's GitHub URL | `https://github.com/andregoepel/finance-app` |
| `{{PROJECT_PATH}}` | App project directory relative to the repo root | `src/AndreGoepel.FinanceApp` |
| `{{PROJECT_NAME}}` | App project and output assembly name | `AndreGoepel.FinanceApp` |
| `{{DOCKERFILE_DIRECTORY}}` | Dockerfile directory without leading slash | `src/AndreGoepel.FinanceApp` |

Files with no placeholder table entry below are byte-identical across all
six repos today — copy them as-is, no substitution needed.

## Files

### `Directory.Build.props.library` / `Directory.Build.props.hostapp`

Root `Directory.Build.props`, two variants:

- **`.library`** — for packable NuGet library repos (marten-configuration,
  marten-identity, app-foundation's `AndreGoepel.Marten.Identity*`
  projects). Carries the packaging block (`PackageProjectUrl`,
  `PackageLicenseExpression`, `PackageRequireLicenseAcceptance`,
  `PublishRepositoryUrl`, `IncludeSymbols`, `SymbolPackageFormat`) on top
  of the shared block.
- **`.hostapp`** — for Blazor/Aspire host apps (finance-app,
  andregoepel-dev, customer-portal). No packaging block — host apps never
  ship a `.nupkg`.

Both share: `RestorePackagesWithLockFile=true`, `TreatWarningsAsErrors=true`,
`WarningsNotAsErrors=NU1901;NU1902` (low/moderate NuGet audit findings stay
warnings; NU1903/NU1904 high/critical stay errors), and the MIT
`Authors`/`Copyright`/`RepositoryUrl` block.

Adopt as the repo-root `Directory.Build.props`, drop the `.library` /
`.hostapp` suffix, fill in the placeholders.

Uses `Placeholder` for author/copyright rather than hardcoding
"André Göpel" — `Nerdventures-Studio/customer-portal` has a different
author/copyright, so this stays a real per-consumer variable, not just a
formality.

### `tests.Directory.Build.props`

Adopt as `tests/Directory.Build.props`. Solves an MSBuild footgun:
`Directory.Build.props` discovery only picks up the *nearest* file walking
up from a project, so a naive `tests/Directory.Build.props` would silently
shadow the root file — every test project would quietly lose
`TreatWarningsAsErrors`, `RestorePackagesWithLockFile`, and the audit
warning exceptions, with no build error to flag it. The explicit
`<Import Project="$([MSBuild]::GetPathOfFileAbove(...))" />` chains back to
the root file instead of replacing it.

Centralizes `TargetFramework`, `ImplicitUsings`, `Nullable`, `IsPackable`
for every test project — remove those four properties from each
`tests/**/*.csproj`'s own `<PropertyGroup>` once this is in place (they're
now inherited). A test project with its own genuinely special properties
(e.g. an E2E project opting out of lock files because
`Aspire.Hosting.Testing` pulls RID-specific packages) keeps those in its
own `.csproj` — this file only owns what's common to *every* test project.

This pattern is new — only finance-app has adopted it so far. No
placeholders; it's already generic.

### `.editorconfig.web` / `.editorconfig.pure-csharp`

Two variants, not a single forced standard — **which one applies depends
on whether the repo has web-facing content**, not on repo size or
maturity:

- **`.web`** (45 lines) — includes `[*.{razor,cshtml}]`, `[*.{css,scss}]`,
  and `[*.js]` sections. Use for any repo with Blazor/Razor/CSS/JS files:
  marten-identity (ships a Blazor UI library), andregoepel-dev,
  finance-app, customer-portal, app-foundation.
- **`.pure-csharp`** (26 lines) — drops the Razor/CSS/JS sections. Use for
  a repo that is plain C# with no web-facing files at all —
  marten-configuration is the current example (a settings-store library
  with no ASP.NET Core dependency; see its own `CLAUDE.md` "Conventions
  Audit" section for the judgment call in full).

Don't default to `.web` "to match everyone else" — an editorconfig section
with nothing to apply to is dead weight, and it's a false signal that the
repo has content it doesn't. Re-evaluate a repo's choice only when its
actual file content changes (e.g. marten-configuration would move to
`.web` if it ever grew a Razor component), not to chase uniformity.

Adopt as `.editorconfig` (drop the `.web` / `.pure-csharp` suffix — the
leading dot is kept here, unlike the other suffixed templates, specifically
so an editor that only recognizes an exact `.editorconfig` filename can
still be pointed at it directly if needed). No placeholders — both variants
are content-only, nothing repo-identifying in either.

### `.gitattributes`

Canonical LF-normalization + binary-declaration set. The `* text=auto
eol=lf` line and its rationale comment are load-bearing — without it a
Windows checkout with `core.autocrlf=true` converts files to CRLF, which
then disagrees with csharpier/`.editorconfig` (`end_of_line = lf`) and
produces phantom whitespace-only diffs.

`*.png` / `*.ico` are included active (generically useful — most repos
have at least a favicon or a design-system asset). A genuinely
repo-specific binary type (finance-app's `*.xlsx` for spreadsheet import
fixtures) is left as a commented-out example showing the pattern, not
forced into every repo's file. Uncomment / add lines for whatever binary
types your repo actually carries.

No placeholders — adopt verbatim, then add repo-specific binary lines as
needed.

### `dependabot.yml` / `dependabot.hostapp.yml`

Adopt the appropriate variant as `.github/dependabot.yml`:

- `dependabot.yml` is for library repositories and covers NuGet plus
  SHA-pinned GitHub Actions.
- `dependabot.hostapp.yml` adds the Docker ecosystem. Replace
  `{{DOCKERFILE_DIRECTORY}}` with the directory containing the host's
  Dockerfile so Dependabot can update its `tag@sha256:<digest>` references.

If the repository contains a real Docker Compose manifest, add a
`docker-compose` entry for its directory as well. Do not use that mechanism to
move a production application image automatically: the application digest is
the controlled output of an approved build/release.

### `Dockerfile.hostapp`

Adopt as the host application's `Dockerfile`. Replace `{{PROJECT_PATH}}` and
`{{PROJECT_NAME}}`, then add any additional in-repo project references required
before restore. The .NET runtime and SDK retain the readable `10.0` tag but are
pinned to reviewed multi-architecture manifest digests. The host-app Dependabot
template keeps those digests current through pull requests.

Never remove the digest, replace it with a tag-only `FROM`, or deploy a moving
application tag as the production identity. The reusable Docker workflow emits
the pushed manifest digest; the deployment must consume and verify it.

### `dependabot-lockfile-sync.yml`

Adopt as `.github/workflows/dependabot-lockfile-sync.yml`. Regenerates
`packages.lock.json` on Dependabot PRs (Dependabot bumps
`Directory.Packages.props` but can't regenerate lock files under Central
Package Management, so its PRs would otherwise fail CI's locked-mode
restore). Requires a repo-specific `DEPENDABOT_LOCKFILE_PAT` secret to be
useful (falls back to `GITHUB_TOKEN`, which works but won't re-trigger CI
on its own push) — that secret is a per-repo setup step this template
can't automate.

Action versions are pinned to commit SHAs, matching this repo's own
supply-chain rule. Dependabot's `github-actions` ecosystem (see
`dependabot.yml` above) keeps those pins current once adopted — no manual
upkeep needed after the initial copy.

No placeholders.

### `NuGet.config`

Adopt as repo-root `NuGet.config`. Byte-identical across all six repos —
clears any inherited/machine-level package sources and pins to
`nuget.org` only. No placeholders.

## How to adopt in a repo

1. Copy the relevant file(s) from this directory to the destination path
   noted above (dropping any `.library` / `.hostapp` / `.web` /
   `.pure-csharp` suffix).
2. Fill in every `{{PLACEHOLDER}}` — see the table above.
3. For a host app, confirm every external `FROM` uses `tag@sha256:<digest>`,
   the Dependabot Docker directory matches the Dockerfile, and the consumer
   workflow is pinned to a full `andregoepel/workflows` commit SHA.
4. For `tests.Directory.Build.props`: remove `TargetFramework`,
   `ImplicitUsings`, `Nullable`, `IsPackable` from each existing
   `tests/**/*.csproj`'s `<PropertyGroup>` (now inherited); keep anything
   genuinely project-specific.
5. Run the repo's normal build/format/test cycle and fix anything the
   newly-strict `TreatWarningsAsErrors` (if not already set) surfaces.
6. Diff the result against a sibling repo that's already aligned (e.g.
   marten-identity for a library, finance-app for a host app) as a sanity
   check before opening the PR.
