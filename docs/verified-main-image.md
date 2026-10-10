# Verified main-image producer

`verified-main-image.yml` is an opt-in GitHub.com reusable workflow for hosted
`push` builds on `main`. The existing `docker-image.yml` interface is unchanged.
The new producer emits generation-2 deployment evidence and has no operator
credentials, deployment dispatches or dependency on a private repository.

## Caller contract

Create a **separate** main-push build workflow. Each gate is a separate main-push
workflow, identified by its exact path **and numeric GitHub workflow ID**. Do not
make the caller one of its own gates: the complete caller run cannot succeed until
this job and artifact upload finish. A caller driven by `workflow_run`, a tag, a
manual dispatch or a pull request is not supported. CI/E2E must not depend on this
build, or they would wait on each other.

Replace the commit placeholder and synthetic IDs before adoption. Obtain the
repository ID and workflow IDs from the GitHub REST API after registering the
caller workflow. Pin the reusable workflow to a reviewed full commit.

```yaml
name: Build verified main image
on:
  push:
    branches: [main]

permissions: {}

jobs:
  image:
    uses: andregoepel/workflows/.github/workflows/verified-main-image.yml@{{WORKFLOWS_COMMIT_SHA}}
    permissions:
      contents: read
      actions: read
      packages: write
    with:
      app-id: atlas
      source-repository: example-org/atlas
      source-repository-id: "1001"
      build-workflow-path: .github/workflows/build-main.yml
      build-workflow-id: "3002"
      gates: >-
        {"ci":{"path":".github/workflows/ci.yml","id":3001},
         "e2e":{"path":".github/workflows/e2e.yml","id":3003}}
      image: ghcr.io/example-org/atlas
      dockerfile: src/Atlas/Dockerfile
      context: .
```

All inputs above except `context` are required. `context` defaults to `.`; paths
are local and source-relative, without `..`, a leading `./` or remote contexts.
The lowercase GHCR image must belong to the source repository's owner. Grant the
source repository Actions access to that package. No PAT or inherited secrets are
needed: the built-in `GITHUB_TOKEN` is used for read-only repository/Actions queries
and the image push. `digest` and `image` outputs expose the immutable digest and
`repository@sha256:...` reference.

The producer checks out its helper code at the **called workflow's exact SHA**,
using GitHub's `job.workflow_*` identity through `toJSON(job)`. It fails if that
identity is absent; it never falls back to the app checkout or mutable `main`.
The app checkout is a separate directory at the caller's exact push SHA.
GitHub Enterprise Server does not expose these job identity fields and is not
supported by this workflow. See the official [job context reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#job-context).

## Checks and publication order

1. Verify the caller repository name/ID, default branch, build workflow path/ID,
   run ID/attempt/number and same-repository main-push SHA through the GitHub API.
   The current build must still be running at its original attempt and be the
   newest build run for that SHA.
2. Wait at most 20 minutes for one to eight configured gates. Every gate must be a
   successful completed main-push run in the same repository at the exact SHA.
   Pin the newest observed run and attempt. Failed, cancelled, skipped, missing,
   superseded or changed attempts cannot fall back to an older success. Listings
   above 100 entries or inconsistent counts fail closed.
   During this wait, cache repository/workflow identity lookups, but continue
   checking live run listings and attempts for every gate, including successful
   gates. Space regular polls at least one minute apart and slow down when response
   headers report a smaller remaining API budget. Leave 96 requests for later
   verification and headroom; if polling cannot preserve that allowance, stop
   before building. This is a local allowance, not a reservation against other
   jobs sharing the repository's token budget. All pre-push and pre-manifest
   provenance rechecks bypass the wait cache. The eight-gate, 20-minute regression
   simulation with 100 ms responses uses 388 polling requests, below GitHub's
   standard [1,000 requests/hour/repository limit](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#primary-rate-limit-for-github_token-in-github-actions).
   Spread affordable polls across the entire remaining wait window, retaining
   the last affordable poll until the deadline. That terminal poll runs before
   declaring timeout; its bounded HTTP requests can finish after the 20-minute
   wait. No further poll is scheduled after a pending terminal result.
3. Inspect the reserved tag `verified-v2-<full-sha>`. Only an explicit registry
   `MANIFEST_UNKNOWN`/`NAME_UNKNOWN` 404 permits a fresh build. Authentication,
   permission, throttling, network and malformed-response errors stop the job.
4. If absent, build and load one `linux/amd64` image with fixed OCI source/revision
   and producer identity/recipe labels. Recheck gate attempts and tag absence
   before pushing. BuildKit attestations and SBOM indices are disabled here so
   the reference identifies one image manifest; this is not a signed attestation.
5. If present, pull by the computed, registry-confirmed digest and verify its
   platform, source, revision, app, repository/workflow IDs, build origin and recipe.
   Reuse additionally requires the original successful build attempt and one
   retained manifest artifact binding those exact image bytes. Verify the artifact
   archive checksum, origin, lifetime, closed canonical document and successful
   historical build/gate attempts. Labels alone do not authorize reuse.
6. Recheck the tag, digest and latest current build/gate attempts before writing
   the document. Upload exactly one `deployment-manifest-v2` artifact containing
   `deployment-manifest.json`. A rerun replaces its own previous artifact with
   evidence for the new build attempt; consumers must recheck current attempts.

Jobs are serialized per image/commit within the caller repository without cancelling
the running build. Reserve that tag namespace for this workflow, retain the commit
tags, and restrict other package writers. GitHub concurrency does not serialize
unrelated repositories or external publishers, and GHCR tags are not immutable.
The image reference in the document is always a digest. The producer does not
publish `latest`, release tags or deployment approvals.

## Manifest and retries

The canonical UTF-8 JSON is at most 64 KiB, with sorted keys, two-space indentation,
ASCII escapes and a final LF. Its exact fields are:

| Section | Fields |
| --- | --- |
| Root | `kind: deployment-manifest`, `schema: 2`, `app_id`, `source`, `build`, `gates`, `image` |
| `source` | `repository: {name,id}`, `branch: main`, `sha` |
| `build` | `workflow: {path,id}`, `run_id`, `attempt`, `number` |
| Each named gate | `workflow: {path,id}`, `run_id`, `attempt` |
| `image` | `reference: repository@sha256:...`, `platform: linux/amd64`, `revision: source SHA` |

On a successful-build rerun, retain the image and publish fresh evidence for the
current attempt after all gates pass again. Changing the app, recipe or workflow
identity while selecting an existing commit tag fails; it never silently rebuilds
or overwrites that image. Artifacts are retained for 90 days subject to repository
policy. Expired/deleted origin artifacts block reuse, even if the image still exists.

A crash before a push can cause a later attempt to repeat local build work. A crash
after pushing but before the original run succeeds leaves an orphan image: the
next attempt refuses to claim a successful origin or overwrite it. Such cases
require investigation and a separately reviewed recovery decision. This implements
reuse of verified published images, not a distributed exactly-once execution
guarantee across GitHub and GHCR.

The current build is necessarily still `in_progress` when its manifest is produced.
Deployment consumers must independently verify the **completed successful build**,
latest attempts, same-SHA gates, artifact provenance and image digest afterwards.
Producer checks and artifact upload cannot form an atomic transaction with GitHub
reruns. A late rerun therefore invalidates old evidence at the consumer boundary.
Main-source authors, workflow administrators and package writers remain trust roots.

## Validation and adoption

`python3 -I -B tests/run.py` runs offline provenance, retry, archive and transport
tests. `python3 -I -B tests/docker_build.py` builds, inspects and removes a neutral
local scratch image; it does not publish an image or run application code. Both run
in the repository's validation workflow alongside actionlint. HTTP tests use
synthetic responses and do not need credentials or network access.

Each application adopts this workflow in its own PR, with its actual public
repository/workflow identities, appropriate CI/E2E gates and a verified caller run.
That hosted adoption run is still required to verify package permissions and the
complete GitHub/GHCR exchange. Existing consumers are not changed by this feature.
Release association without rebuilding uses the separate
[verified release workflow](verified-release-image.md).

Relevant upstream contracts: [workflow-run API](https://docs.github.com/en/rest/actions/workflow-runs),
[artifact API](https://docs.github.com/en/rest/actions/artifacts), and
[immutable artifact replacement](https://github.com/actions/upload-artifact#overwriting-an-artifact).
