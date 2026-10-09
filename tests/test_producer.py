import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_evidence as evidence
import verified_build as producer

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
CONFIG = {
    "app_id": "atlas",
    "repository": {"name": "example-org/atlas", "id": 1001},
    "sha": SHA,
    "build": {"path": ".github/workflows/build-main.yml", "id": 3002},
    "gates": {"ci": {"path": ".github/workflows/ci.yml", "id": 3001}},
    "image": "ghcr.io/example-org/atlas",
    "dockerfile": "Dockerfile",
    "context": ".",
}
BUILD = {"run_id": 5002, "attempt": 1, "number": 42}
ORIGIN = {"run_id": 5000, "attempt": 1, "number": 41}


def run(identity, current, *, status="completed", conclusion="success"):
    return {
        "id": current["run_id"],
        "run_attempt": current["attempt"],
        "run_number": current["number"],
        "workflow_id": identity["id"],
        "path": identity["path"],
        "repository": {"full_name": "example-org/atlas", "id": 1001},
        "head_repository": {"full_name": "example-org/atlas", "id": 1001},
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": status,
        "conclusion": conclusion,
    }


class API:
    def __init__(self):
        self.build = run(CONFIG["build"], BUILD, status="in_progress", conclusion=None)
        self.gate = run(
            CONFIG["gates"]["ci"], {"run_id": 5001, "attempt": 1, "number": 40}
        )
        self.builds = [self.build]
        self.gate_runs = [self.gate]
        self.identities = {3002: CONFIG["build"], 3001: CONFIG["gates"]["ci"]}
        self.history = {5000: run(CONFIG["build"], ORIGIN), 5001: self.gate}
        self.calls = []
        self.artifacts = []
        self.archive_bytes = b""

    def __call__(self, path):
        self.calls.append(path)
        if not path:
            return {
                "full_name": "example-org/atlas",
                "id": 1001,
                "default_branch": "main",
            }
        parts = path.split("?")[0].split("/")
        identity = int(parts[3])
        if parts[2] == "workflows":
            if len(parts) == 4:
                return {**self.identities[identity], "state": "active"}
            items = self.builds if identity == 3002 else self.gate_runs
            return copy.deepcopy({"total_count": len(items), "workflow_runs": items})
        if len(parts) == 5 and parts[4] == "artifacts":
            return {
                "total_count": len(self.artifacts),
                "artifacts": copy.deepcopy(self.artifacts),
            }
        if len(parts) == 6:
            result = self.history[identity]
            return copy.deepcopy(result)
        return copy.deepcopy(self.build if identity == 5002 else self.gate)

    def archive(self, identity):
        assert identity == 6001
        return self.archive_bytes

    def artifact(self, value=None, *, entries=None):
        if value is None:
            value = evidence.manifest(
                CONFIG, ORIGIN, {"ci": evidence.ticket(self.gate)}, DIGEST
            )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in (
                entries or {"deployment-manifest.json": evidence.canonical(value)}
            ).items():
                archive.writestr(name, data)
        self.archive_bytes = buffer.getvalue()
        self.artifacts = [
            {
                "id": 6001,
                "name": "deployment-manifest-v2",
                "size_in_bytes": len(self.archive_bytes),
                "digest": "sha256:" + hashlib.sha256(self.archive_bytes).hexdigest(),
                "expired": False,
                "created_at": "2020-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "workflow_run": {
                    "id": 5000,
                    "repository_id": 1001,
                    "head_repository_id": 1001,
                    "head_branch": "main",
                    "head_sha": SHA,
                },
            }
        ]


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.api = API()

    def test_manifest_has_exact_canonical_contract(self):
        gates = evidence.wait_for_gates(self.api, CONFIG, BUILD)
        raw = evidence.canonical(evidence.manifest(CONFIG, BUILD, gates, DIGEST))
        value = evidence.parse(raw)
        self.assertEqual(
            {"kind", "schema", "app_id", "source", "build", "gates", "image"},
            set(value),
        )
        self.assertEqual({"workflow", "run_id", "attempt"}, set(value["gates"]["ci"]))
        self.assertEqual(CONFIG["image"] + "@" + DIGEST, value["image"]["reference"])
        self.assertEqual(raw, evidence.canonical(value))
        self.assertLess(len(raw), 65536)

    def test_invalid_inputs_are_rejected(self):
        changes = [
            ("gates", {}),
            ("gates", {"ci": CONFIG["build"]}),
            ("sha", "main"),
            ("dockerfile", "../Dockerfile"),
            ("dockerfile", "Dockerfile\n--secret=x"),
            ("context", "https://example.com/context"),
            ("image", "ghcr.io/other/atlas"),
            ("app_id", "atlas\nimage=evil"),
        ]
        for key, value in changes:
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.configuration(dict(CONFIG, **{key: value}))
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.workflow({"path": ".github/workflows/ci.yml", "id": True})

    def test_duplicate_json_keys_and_oversize_are_rejected(self):
        for raw in ('{"ci":1,"ci":2}', '{"id":NaN}', " " * 65537):
            with (
                self.subTest(raw=raw[:30]),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.parse(raw)

    def test_separate_e2e_gate_is_mandatory(self):
        config = copy.deepcopy(CONFIG)
        config["gates"]["e2e"] = {"path": ".github/workflows/e2e.yml", "id": 3003}
        e2e = run(config["gates"]["e2e"], {"run_id": 5003, "number": 25, "attempt": 2})

        def api(path):
            if path == "/actions/workflows/3003":
                return {**config["gates"]["e2e"], "state": "active"}
            if path.startswith("/actions/workflows/3003/runs?"):
                return {"total_count": 1, "workflow_runs": [copy.deepcopy(e2e)]}
            if path == "/actions/runs/5003":
                return copy.deepcopy(e2e)
            return self.api(path)

        pinned = evidence.wait_for_gates(api, config, BUILD)
        self.assertEqual({"ci", "e2e"}, set(pinned))
        self.assertEqual(2, pinned["e2e"]["attempt"])
        e2e["conclusion"] = "skipped"
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.recheck(api, config, BUILD, pinned)

    def test_wrong_gate_provenance_never_passes(self):
        changes = [
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("head_branch", "feature"),
            ("head_sha", "c" * 40),
            ("path", ".github/workflows/other.yml"),
            ("workflow_id", 3009),
            ("run_attempt", True),
            ("repository", {"full_name": "example-org/atlas", "id": 1002}),
            ("head_repository", {"full_name": "fork/atlas", "id": 1002}),
        ]
        for key, value in changes:
            api = API()
            api.gate[key] = value
            with self.subTest(key=key), self.assertRaises(evidence.InvalidEvidence):
                evidence.wait_for_gates(api, CONFIG, BUILD)

    def test_all_unsuccessful_conclusions_block(self):
        for conclusion in (
            "failure",
            "cancelled",
            "skipped",
            "neutral",
            "timed_out",
            "action_required",
            None,
        ):
            api = API()
            api.gate["conclusion"] = conclusion
            with (
                self.subTest(conclusion=conclusion),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.wait_for_gates(api, CONFIG, BUILD)

    def test_newest_gate_blocks_fallback_to_success(self):
        older = copy.deepcopy(self.api.gate)
        older.update(id=4999, run_number=39)
        self.api.gate_runs.insert(0, older)
        self.api.gate["conclusion"] = "failure"
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.wait_for_gates(self.api, CONFIG, BUILD)

    def test_missing_and_running_gates_time_out_without_evidence(self):
        for missing in (True, False):
            api = API()
            if missing:
                api.gate_runs = []
            else:
                api.gate.update(status="in_progress", conclusion=None)
            with (
                self.subTest(missing=missing),
                self.assertRaisesRegex(evidence.InvalidEvidence, "Timed out"),
            ):
                evidence.wait_for_gates(api, CONFIG, BUILD, seconds=0)

    def test_waits_for_exact_attempt_then_succeeds(self):
        self.api.gate.update(status="in_progress", conclusion=None)

        def finish(_):
            self.api.gate.update(status="completed", conclusion="success")

        selected = evidence.wait_for_gates(self.api, CONFIG, BUILD, sleep=finish)
        self.assertEqual(1, selected["ci"]["attempt"])

    def test_rerun_during_wait_or_after_success_is_rejected(self):
        self.api.gate.update(status="in_progress", conclusion=None)

        def rerun(_):
            self.api.gate.update(
                status="completed", conclusion="success", run_attempt=2
            )

        with self.assertRaisesRegex(evidence.InvalidEvidence, "attempt changed"):
            evidence.wait_for_gates(self.api, CONFIG, BUILD, sleep=rerun)
        api = API()
        pinned = evidence.wait_for_gates(api, CONFIG, BUILD)
        api.gate["run_attempt"] = 2
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.recheck(api, CONFIG, BUILD, pinned)

    def test_build_rerun_and_newer_build_are_rejected(self):
        self.api.build["run_attempt"] = 2
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.current_build(self.api, CONFIG, BUILD)
        api = API()
        api.builds.append(
            run(CONFIG["build"], {"run_id": 5010, "number": 43, "attempt": 1})
        )
        with self.assertRaisesRegex(evidence.InvalidEvidence, "superseded"):
            evidence.current_build(api, CONFIG, BUILD)

    def test_workflow_identity_drift_is_rejected(self):
        self.api.identities[3001] = {
            "id": 3001,
            "path": ".github/workflows/renamed.yml",
        }
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.wait_for_gates(self.api, CONFIG, BUILD)

    def test_truncated_or_ambiguous_history_is_rejected(self):
        for response in (
            {"total_count": 101, "workflow_runs": []},
            {"total_count": 1, "workflow_runs": []},
            {"total_count": 2, "workflow_runs": [self.api.gate, self.api.gate]},
        ):
            with (
                self.subTest(response=response["total_count"]),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.listing(lambda _: response, CONFIG, CONFIG["gates"]["ci"])

    def test_registry_image_requires_source_recipe_platform_and_successful_origin(self):
        value = [
            {
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {"Labels": evidence.labels(CONFIG, ORIGIN)},
            }
        ]
        self.assertEqual(
            ORIGIN, evidence.verify_image(value, CONFIG, BUILD, self.api, fresh=False)
        )
        for key in (
            "org.opencontainers.image.source",
            "org.opencontainers.image.revision",
            evidence.LABEL + "dockerfile",
            evidence.LABEL + "app",
        ):
            changed = copy.deepcopy(value)
            changed[0]["Config"]["Labels"][key] = "wrong"
            with self.subTest(key=key), self.assertRaises(evidence.InvalidEvidence):
                evidence.verify_image(changed, CONFIG, BUILD, self.api, fresh=False)
        self.api.history[5000]["conclusion"] = "failure"
        with self.assertRaises(evidence.InvalidEvidence):
            evidence.verify_image(value, CONFIG, BUILD, self.api, fresh=False)

    def test_reuse_artifact_binds_actual_registry_digest(self):
        self.api.artifact()
        evidence.reuse_evidence(self.api, CONFIG, DIGEST, ORIGIN)
        with self.assertRaisesRegex(evidence.InvalidEvidence, "prove this image"):
            evidence.reuse_evidence(self.api, CONFIG, "sha256:" + "c" * 64, ORIGIN)

    def test_retained_artifact_from_successful_previous_attempt_supports_rerun(self):
        api = API()
        api.build["run_attempt"] = 2
        api.history[5002] = run(CONFIG["build"], BUILD)
        api.artifact(
            evidence.manifest(CONFIG, BUILD, {"ci": evidence.ticket(api.gate)}, DIGEST)
        )
        api.artifacts[0]["workflow_run"]["id"] = 5002
        current = dict(BUILD, attempt=2)
        pinned = evidence.wait_for_gates(api, CONFIG, current)
        image = [
            {
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {"Labels": evidence.labels(CONFIG, BUILD)},
            }
        ]
        origin = evidence.verify_image(image, CONFIG, current, api, fresh=False)
        evidence.reuse_evidence(api, CONFIG, DIGEST, origin)
        value = evidence.manifest(CONFIG, current, pinned, DIGEST)
        self.assertEqual(2, value["build"]["attempt"])
        self.assertEqual(CONFIG["image"] + "@" + DIGEST, value["image"]["reference"])

    def test_unavailable_forged_and_expired_artifacts_block_reuse(self):
        for change in ("missing", "duplicate", "checksum", "expired", "fork", "failed"):
            api = API()
            api.artifact()
            if change == "missing":
                api.artifacts = []
            if change == "duplicate":
                api.artifacts *= 2
            if change == "checksum":
                api.artifacts[0]["digest"] = DIGEST
            if change == "expired":
                api.artifacts[0]["expired"] = True
            if change == "fork":
                api.artifacts[0]["workflow_run"]["head_repository_id"] = 1002
            if change == "failed":
                api.history[5000]["conclusion"] = "failure"
            with (
                self.subTest(change=change),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.reuse_evidence(api, CONFIG, DIGEST, ORIGIN)

    def test_archive_traversal_extra_files_and_noncanonical_json_are_rejected(self):
        for entries in (
            {"../deployment-manifest.json": b"{}"},
            {"deployment-manifest.json": b"{}", "extra": b"x"},
            {"deployment-manifest.json": b'{"kind":"wrong"}'},
            {"deployment-manifest.json": b"x" * 65537},
        ):
            self.api.artifact(entries=entries)
            with (
                self.subTest(entries=list(entries)),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                evidence.reuse_evidence(self.api, CONFIG, DIGEST, ORIGIN)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.api = API()
        self.api.artifact()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        self.events = []
        self.build = patch.object(
            producer,
            "build_image",
            side_effect=lambda *args: self.events.append("build"),
        ).start()
        self.command = patch.object(
            producer,
            "command",
            side_effect=lambda *args, **kwargs: self.events.append("push"),
        ).start()
        self.inspect = patch.object(
            producer, "inspect_image", return_value=ORIGIN
        ).start()
        self.addCleanup(patch.stopall)

    def registry(self, values):
        parent = self

        class Registry:
            def digest(self, reference):
                parent.events.append("lookup")
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value

        return Registry()

    def test_fresh_build_rechecks_then_pushes_once(self):
        digest = producer.produce(
            CONFIG,
            BUILD,
            self.api,
            self.registry([None, None, DIGEST, DIGEST, DIGEST]),
            self.root,
            self.output,
        )
        self.assertEqual(DIGEST, digest)
        self.assertEqual(
            ["lookup", "build", "lookup", "push", "lookup", "lookup", "lookup"],
            self.events,
        )
        self.assertTrue((self.output / "deployment-manifest.json").exists())

    def test_reuse_does_not_build_or_push(self):
        producer.produce(
            CONFIG,
            BUILD,
            self.api,
            self.registry([DIGEST, DIGEST, DIGEST]),
            self.root,
            self.output,
        )
        self.build.assert_not_called()
        self.command.assert_not_called()

    def test_unknown_registry_state_never_triggers_build(self):
        with self.assertRaises(evidence.InvalidEvidence):
            producer.produce(
                CONFIG,
                BUILD,
                self.api,
                self.registry([evidence.InvalidEvidence("401")]),
                self.root,
                self.output,
            )
        self.build.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_gate_rerun_after_build_blocks_push(self):
        self.build.side_effect = lambda *args: self.api.gate.update(run_attempt=2)
        with self.assertRaises(evidence.InvalidEvidence):
            producer.produce(
                CONFIG, BUILD, self.api, self.registry([None]), self.root, self.output
            )
        self.command.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_tag_appearing_during_build_is_not_overwritten(self):
        with self.assertRaises(evidence.InvalidEvidence):
            producer.produce(
                CONFIG,
                BUILD,
                self.api,
                self.registry([None, DIGEST]),
                self.root,
                self.output,
            )
        self.command.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_gate_rerun_after_pull_blocks_artifact(self):
        def inspected(*args, **kwargs):
            self.api.gate["run_attempt"] = 2
            return ORIGIN

        self.inspect.side_effect = inspected
        # Preserve historical gate evidence; the final latest-attempt check must fail.
        self.api.history[5001] = copy.deepcopy(self.api.gate)
        with self.assertRaises(evidence.InvalidEvidence):
            producer.produce(
                CONFIG,
                BUILD,
                self.api,
                self.registry([DIGEST, DIGEST, DIGEST]),
                self.root,
                self.output,
            )
        self.assertFalse(self.output.exists())

    def test_moved_tag_blocks_artifact(self):
        with self.assertRaises(evidence.InvalidEvidence):
            producer.produce(
                CONFIG,
                BUILD,
                self.api,
                self.registry([DIGEST, "sha256:" + "c" * 64]),
                self.root,
                self.output,
            )
        self.assertFalse(self.output.exists())


class TransportTests(unittest.TestCase):
    def test_only_main_push_in_exact_caller_on_hosted_runner_is_accepted(self):
        env = {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SERVER_URL": "https://github.com",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "INPUT_APP_ID": "atlas",
            "INPUT_SOURCE_REPOSITORY": "example-org/atlas",
            "INPUT_SOURCE_REPOSITORY_ID": "1001",
            "GITHUB_SHA": SHA,
            "INPUT_BUILD_WORKFLOW_PATH": CONFIG["build"]["path"],
            "INPUT_BUILD_WORKFLOW_ID": "3002",
            "INPUT_GATES": json.dumps(CONFIG["gates"]),
            "INPUT_IMAGE": CONFIG["image"],
            "INPUT_DOCKERFILE": "Dockerfile",
            "INPUT_CONTEXT": ".",
            "GITHUB_REPOSITORY": "example-org/atlas",
            "GITHUB_REPOSITORY_ID": "1001",
            "GITHUB_RUN_ID": "5002",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_NUMBER": "42",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual((CONFIG, BUILD), producer.environment())
        for key, value in (
            ("GITHUB_EVENT_NAME", "workflow_run"),
            ("GITHUB_EVENT_NAME", "pull_request"),
            ("GITHUB_REF", "refs/tags/v1"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
            ("GITHUB_REPOSITORY_ID", "1002"),
            ("GITHUB_REPOSITORY", "fork/atlas"),
        ):
            with (
                self.subTest(key=key),
                patch.dict(os.environ, dict(env, **{key: value}), clear=True),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                producer.environment()

    def test_build_subprocess_has_no_github_tokens(self):
        with (
            patch.dict(
                os.environ,
                {
                    "GH_TOKEN": "secret",
                    "GITHUB_TOKEN": "secret",
                    "INPUT_GATES": "private",
                },
            ),
            patch.object(producer.subprocess, "run") as process,
        ):
            producer.command(["docker", "buildx", "version"])
            env = process.call_args.kwargs["env"]
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("INPUT_GATES", env)

    def registry_response(self, response):
        return patch.object(
            producer, "http", side_effect=[(200, {}, b'{"token":"test"}'), response]
        )

    def test_only_explicit_registry_absence_allows_build(self):
        registry = producer.Registry(CONFIG["image"], "actor", "test")
        with self.registry_response(
            (404, {}, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
        ):
            self.assertIsNone(registry.digest("verified-v2-" + SHA))
        for response in (
            (401, {}, b"denied"),
            (403, {}, b"denied"),
            (429, {}, b"slow"),
            (500, {}, b"failed"),
            (404, {}, b'{"errors":[{"code":"DENIED"}]}'),
        ):
            with (
                self.subTest(status=response[0]),
                self.registry_response(response),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                registry.digest("verified-v2-" + SHA)

    def test_registry_digest_is_computed_from_bytes_and_indices_are_rejected(self):
        registry = producer.Registry(CONFIG["image"], "actor", "test")
        raw = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        with self.registry_response((200, {"Docker-Content-Digest": digest}, raw)):
            self.assertEqual(digest, registry.digest(digest))
        for content, header in (
            (raw, DIGEST),
            (
                b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[]}',
                DIGEST,
            ),
        ):
            with (
                self.registry_response(
                    (200, {"Docker-Content-Digest": header}, content)
                ),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                registry.digest("tag")

    def test_download_credentials_never_follow_redirect(self):
        api = producer.GitHub(CONFIG["repository"]["name"], "secret")
        url = "https://results.blob.core.windows.net/artifact?signature=test"
        with patch.object(
            producer,
            "http",
            side_effect=[(302, {"Location": url}, b""), (200, {}, b"archive")],
        ) as request:
            self.assertEqual(b"archive", api.archive(6001))
            self.assertEqual((url, {}), request.call_args_list[1].args)
        for url in (
            "http://results.blob.core.windows.net/a",
            "https://evil.example/a",
            "https://user:secret@results.blob.core.windows.net/a",
        ):
            with (
                patch.object(
                    producer, "http", return_value=(302, {"Location": url}, b"")
                ),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                api.archive(6001)

    def test_http_refuses_oversized_responses_and_redirects(self):
        self.assertIsNone(
            producer.NoRedirect().redirect_request(
                None, None, 302, None, None, "https://evil.example"
            )
        )

        class Response:
            code, headers = 200, {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return b"x" * size

        class Opener:
            def open(self, *args, **kwargs):
                return Response()

        with (
            patch.object(
                producer.urllib.request, "build_opener", return_value=Opener()
            ),
            self.assertRaises(evidence.InvalidEvidence),
        ):
            producer.http("https://api.github.com/test", {})
