# Artifact retention control experiment

This manual workflow investigates whether the original artifact survives a
whole-run or single-job rerun. It runs only in `andregoepel/workflows` on a
GitHub-hosted Ubuntu runner. It does not build images, publish packages, use
consumer secrets, or access deployment hosts.

A repository that permits only local actions must not have its policy relaxed
just to run this experiment. The read-only `preflight` command checks the
reviewed repository policy, exact main revision, workflow availability and empty
run queues before dispatch. It currently accepts only this repository's existing
`allowed_actions=all` policy; a selected-actions policy needs a separately reviewed
check. If the caller cannot read the policy, stop rather than assuming permission.

## Workflow behavior

[artifact-retention-probe.yml](../.github/workflows/artifact-retention-probe.yml)
has only `workflow_dispatch`, requires the default branch, and has a five-minute
job limit. Its token permissions are `contents: read` and `actions: read`.
Checkout and upload-artifact are pinned. Attempt 1 uploads one small text file
containing only the run ID and source SHA, with `overwrite: false`. Later attempts
skip the upload entirely and observe the original artifact before any replacement
could hide its disappearance.

[artifact_retention.py](../tools/artifact_retention.py) uses GET requests only.
It checks the artifact listing and direct artifact metadata, run identity,
expiration and observation consistency. Output files use exclusive creation so
an earlier observation cannot be overwritten. Missing-artifact data is written
before validation fails. This checks metadata retention, not ZIP content or
application provenance; it is never deployment evidence.

## Bounded execution

After review/merge, select the exact diagnostic main SHA. Avoid simultaneous
merges, artifact cleanup and unrelated reruns during observation. Use at most two
fresh dispatches and one rerun per dispatch, within a 30-minute observation window.
Do not retry an ambiguous mutation response without checking the current run.
The observer itself never dispatches, reruns, cancels or deletes anything.

1. From the checked-out reviewed revision, run:

   ```text
   python -I -B tools/artifact_retention.py preflight --sha REVIEWED_SHA
   gh workflow run artifact-retention-probe.yml --ref main -R andregoepel/workflows
   ```

   Unambiguously identify the new run by workflow path, `workflow_dispatch`, SHA
   and dispatch time. Wait for attempt 1 to complete successfully.
2. Save an independent local observation outside the checkout:

   ```text
   python -I -B tools/artifact_retention.py observe --run RUN_A --attempt 1 --sha REVIEWED_SHA --completed --out A-before.json
   ```

   Stop on failure. Record the exact run ID and artifact ID/digest.
3. Request exactly one whole-run rerun, retaining response headers for its
   GitHub request ID, without request/token debug logging:

   ```text
   gh api --method POST --include repos/andregoepel/workflows/actions/runs/RUN_A/rerun
   ```

   Once attempt 2 is terminal, observe it using `--attempt 2 --completed
   --out A-after.json`. Preserve failed observations and logs as failures.
4. Start a separate run B on the same reviewed SHA after preflight and obtain
   `B-before.json`. Read `gh run view RUN_B -R andregoepel/workflows --json jobs`
   and select the `databaseId` of its only job, `retention`. Independently read
   `GET /repos/andregoepel/workflows/actions/jobs/JOB_B` and verify run ownership.
   Rerun that job exactly once with `POST /repos/andregoepel/workflows/actions/jobs/JOB_B/rerun`;
   do not use the browser job number or the run ID in place of the API job ID.
   Save the terminal observation as `B-after.json`.
5. For each successful pair, compare:

   ```text
   python -I -B tools/artifact_retention.py compare A-before.json A-after.json
   python -I -B tools/artifact_retention.py compare B-before.json B-after.json
   ```

   Success requires the same run and source, attempts 1 and 2, successful terminal
   results, and unchanged original artifact ID, digest, ownership and retention.
   Leave the test artifacts in place; do not repair a failed pair with a new upload.

Stop on policy/source drift, ambiguous identity, queue delay or window expiry.
Already running jobs retain their five-minute timeout. Document differences from
any consumer case (push versus dispatch, reusable workflow, permissions,
retention policy). Neither success nor failure here alone establishes a consumer
incident's cause; actual consumer provenance verification remains mandatory.

## Tests

`python -I -B tests/run.py` includes positive and negative cases for restricted
policy, source drift, active queues, missing/replaced/expired artifacts, wrong
ownership, incomplete lists, run races and failed attempts. Existing CI also
lints the workflow; the manual retention experiment never runs on PR or push.
