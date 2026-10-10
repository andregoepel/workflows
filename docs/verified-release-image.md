# Verified release-image association

`verified-release-image.yml` implements issue #19. It associates a published
SemVer release with an existing image from the [main-image producer](verified-main-image.md).
It never builds an image, executes app code, dispatches deployment or grants
production approval. No application checkout is needed.

## Caller contract

Use a separate caller triggered by `release: types: [published]`. This covers
stable releases and prereleases. GitHub.com hosted runners only; tag pushes,
manual dispatch, draft releases and release edits are not supported.
Publish only after the verified main build and all mandatory gates have completed
successfully. The job does not wait for unfinished builds; retry after completion.

Replace the synthetic IDs and commit placeholder with reviewed real identities.
The build path/ID, gates, image, app, Dockerfile and context must match the main
producer. `build-workflow-*` identifies the **main build**, not this release caller.

```yaml
name: Associate release image
on:
  release:
    types: [published]

permissions: {}

jobs:
  image:
    uses: andregoepel/workflows/.github/workflows/verified-release-image.yml@{{WORKFLOWS_COMMIT_SHA}}
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

Only `context` is optional (default `.`). The built-in token needs read access to
repository metadata and Actions artifacts, and write access to the existing GHCR
package. No PAT, release-write permission or operator repository credential is
used. The helper checkout uses the called workflow's exact `job.workflow_sha`,
never the app checkout or a moving helper ref.

Outputs are `digest`, `image` (`repository@sha256:...`) and `release-tag` (the readable
registry tag). Consumers must deploy using the verified digest, not the tag.

## Release identity and SemVer

Tags use `v` followed by [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html), with
at most 128 ASCII characters including `v`. Numeric core/prerelease identifiers
cannot have leading zeroes. GitHub's `prerelease` flag must agree with the version:
`v1.2.3-rc.1` is a prerelease, whereas `v1.2.3+build.001` is stable.

Registry tags cannot contain `+`. Map it to `_` while preserving every other
character: `v1.2.3-rc.1+build.001` becomes `v1.2.3-rc.1_build.001`. SemVer cannot
contain `_`, making this mapping unambiguous. The record retains the original Git
tag. No major/minor/`latest` aliases or version labels inside the image are changed.
Applications that embed the release version at build time need their own
build-once version-display strategy before adoption.

## Verification and publication

1. Bind the event and live release by repository name/ID, release ID, original tag,
   publication timestamp and prerelease status. Resolve lightweight or annotated
   Git tags (at most eight annotation objects) to the event's exact commit.
   `target_commitish` and the current main tip never select the image. Independently
   require that the selected commit remains an ancestor of `main`.
2. Select the latest main-push build run for that SHA and require its current
   attempt to have completed successfully. Download its one retained generation-2
   manifest; check the archive checksum, repository/run identity, expiry, canonical
   closed document and successful historical attempts. More than 100 results or
   incomplete/ambiguous run or artifact listings fail closed.
3. Resolve `verified-v2-<event-sha>` and inspect the image by its computed digest.
   Verify platform, revision, source, app, recipe and successful original build.
   Reused images with an older build origin additionally need that origin's
   retained manifest. The selected main-build manifest must bind these exact bytes.
4. Recheck the live release/tag, current successful build/gates and unchanged
   artifact ID/checksum. Every current attempt must match the selected manifest.
   A successful newer gate attempt does not silently repair stale build evidence.
5. Require the commit tag to remain unchanged. A release tag already pointing to
   the same digest is an idempotent no-op; a different digest is a conflict. For a
   missing release tag, copy the exact original manifest bytes with a registry
   `PUT` inside the same image repository. No layers, config or manifest JSON are
   rewritten and no index is created. Require the response and independent
   readback to confirm the original digest.
6. Recheck the source tag, release tag, digest, live release and all current
   build/gate/artifact evidence before emitting the association. Upload
   `release-association-v1-<run-id>-<attempt>` without overwrite. The artifact
   contains `release-association.json` and a readable `release-association.md`.

The JSON document is canonical UTF-8, sorted/indented with a final LF, at most
64 KiB. Its closed schema-1 shape is:

| Field | Value |
| --- | --- |
| `kind`, `schema` | `release-association`, `1` |
| `app_id` | Main-producer application identity |
| `release` | `id`, original `tag`, `prerelease`, `published_at` |
| `manifest.artifact` | Original Actions artifact `id` and archive `digest` |
| `manifest.sha256` | Bare lowercase SHA-256 of the canonical main-manifest JSON |
| `manifest.document` | Complete original generation-2 main manifest |
| `image` | Immutable `reference` and readable registry `tag` |

The association does not replace the main manifest, staging receipt or manual
production approval. Its uploader's run/attempt is identified by the Actions
artifact provenance and attempt-specific artifact name. Each rerun creates a new
record rather than replacing a previous record. Artifacts expire after 90 days
subject to repository policy; retain required audit evidence separately.

## Races, retries and adoption limits

Jobs serialize per image/release tag within the caller repository, without
cancelling running jobs. Reserve these tags for this workflow and restrict other
package writers. GitHub concurrency does not cover unrelated repositories or
external publishers, and GHCR does not provide an atomic create-only tag contract
used by this workflow. The final absence check and `PUT` are not a transaction:
an external writer can race between them. Rechecks detect observed changes;
they cannot guarantee tag immutability or undo an already published tag.
The immutable image digest and per-attempt artifact are the evidence, not the tag.

A failure after copying can leave a tag without a successful association artifact.
Retry revalidates everything and accepts that tag only at the same verified digest.
A failure after artifact upload can leave an artifact belonging to a failed run;
consumers must independently verify the completed successful association run and
current release/build/gate attempts before relying on it. Late reruns invalidate
old evidence. Do not delete conflicting tags or artifacts to force a retry through.

Offline tests cover identities, prereleases, artifacts, attempts, retries and
observed races, including byte-for-byte registry writes. The first application
adoption must still validate a real hosted `release.published` caller, package
permissions and the complete GitHub/GHCR exchange. No app adoption or live
release/image publication is part of this change.

Upstream contracts: [GitHub release events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#release),
[Git refs](https://docs.github.com/en/rest/git/refs#get-a-reference),
[commit comparison](https://docs.github.com/en/rest/commits/commits#compare-two-commits),
[OCI manifest publication](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#pushing-manifests),
and [immutable Actions artifacts](https://github.com/actions/upload-artifact#not-uploading-to-the-same-artifact).
