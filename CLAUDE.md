# workflows

Reusable GitHub Actions `workflow_call` workflows and canonical config
templates shared by every repo under the ecosystem root's `CLAUDE.md`
(`C:\Users\andre\source\repos\CLAUDE.md`) and its `.claude-shared/`
includes. The `ci-library` → `ci-hostapp` → `e2e` / `docker-image` shape
referenced by that root CLAUDE.md's "Commands" section lives here, not
duplicated per-repo.

This repo has no application code and no `Directory.Build.props` — the
`.NET repos` command/warning rules from the shared conventions don't apply
here. What applies instead:

## What lives here

- `.github/workflows/ci-library.yml` — NuGet library CI + tag-triggered
  publish (marten-configuration, marten-identity, app-foundation)
- `.github/workflows/ci-hostapp.yml` — Blazor/Aspire host app CI
  (finance-app, andregoepel-dev, customer-portal)
- `.github/workflows/e2e.yml` — Aspire + Playwright E2E, with mandatory
  trace-capture-on-failure
- `.github/workflows/docker-image.yml` — GHCR image build + push
- `.github/workflows/validate.yml` — lints the four workflows above with
  `actionlint` on every push/PR to this repo, so a broken reusable workflow
  never reaches `@main` for a consumer to pull
- `templates/` — canonical config boilerplate (`Directory.Build.props`,
  `.editorconfig`, `.gitattributes`, `dependabot.yml`,
  `dependabot-lockfile-sync.yml`, `NuGet.config`,
  `tests/Directory.Build.props`) copied into each consumer repo, not
  referenced remotely — see `templates/README.md` for the placeholder
  convention and per-file adoption notes

See [README.md](README.md) for each workflow's full input/secret contract
and a copy-pasteable `uses:` example.

## Rules specific to this repo

- Every third-party `uses:` (actions, not just this repo's own workflows)
  is pinned to a commit SHA with a trailing version comment — never a
  mutable tag. This repo is a supply-chain choke point for six other repos;
  a compromised action here compromises all of them at once.
- A change to an existing input's name, type, or required-ness is a
  breaking change for every consumer still on `@main`. Prefer adding a new
  optional input with a default that preserves current behavior over
  renaming or repurposing one.
- Don't add a fifth reusable workflow speculatively — wait until a second
  consumer actually needs the shape before extracting it.
- Consumer repos are migrated to these workflows in their own PRs, tracked
  by the per-repo issues listed in README.md's "Migration status" — don't
  bundle a consumer migration into a change made here.
