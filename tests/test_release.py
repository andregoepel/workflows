"""Offline release association checks; no app code, registry or GitHub writes."""

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_evidence as evidence
import verified_build as producer
import verified_release as release
from test_producer import API, BUILD, CONFIG, DIGEST, ORIGIN, SHA


class ReleaseAPI(API):
    def __init__(self, tag="v1.2.3"):
        super().__init__()
        self.build.update(status="completed", conclusion="success")
        self.history[5002] = copy.deepcopy(self.build)
        self.release = {
            "id": 7001,
            "tag_name": tag,
            "draft": False,
            "prerelease": release.version(tag)[1],
            "published_at": "2026-10-10T00:00:00Z",
            # This field is deliberately not used to resolve the released commit.
            "target_commitish": "main",
        }
        self.target = {"type": "commit", "sha": SHA}
        self.tags = {}
        self.comparison = {
            "status": "ahead",
            "base_commit": {"sha": SHA},
            "merge_base_commit": {"sha": SHA},
        }
        self.document = evidence.manifest(
            CONFIG, BUILD, {"ci": evidence.ticket(self.gate)}, DIGEST
        )
        self.origin_api = None
        self.save_artifact()

    def save_artifact(self):
        self.artifact(self.document)
        self.artifacts[0]["workflow_run"]["id"] = BUILD["run_id"]

    def __call__(self, path):
        if (
            self.origin_api is not None
            and path == "/actions/runs/5000/artifacts?per_page=100"
        ):
            return self.origin_api(path)
        if path == "/releases/7001":
            return copy.deepcopy(self.release)
        if path.startswith("/git/ref/tags/"):
            return {
                "ref": "refs/tags/" + self.release["tag_name"],
                "object": copy.deepcopy(self.target),
            }
        if path.startswith("/git/tags/"):
            sha = path.rsplit("/", 1)[1]
            return {"sha": sha, "object": copy.deepcopy(self.tags[sha])}
        if path.startswith("/compare/"):
            return copy.deepcopy(self.comparison)
        return super().__call__(path)

    def archive(self, identity):
        if identity == 6002 and self.origin_api is not None:
            return self.origin_api.archive_bytes
        return super().archive(identity)


class Registry:
    def __init__(self):
        self.tags = {"verified-v2-" + SHA: DIGEST, DIGEST: DIGEST}
        self.copies = []
        self.after_copy = lambda: None

    def digest(self, ref):
        return self.tags.get(ref)

    def associate(self, tag, digest):
        evidence.require(
            self.tags.get(tag) in (None, digest), "Release image tag already differs"
        )
        if tag not in self.tags:
            self.tags[tag] = digest
            self.copies.append((tag, digest))
        self.after_copy()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api, self.registry = ReleaseAPI(), Registry()
        self.identity = release.release_identity(self.api.release)
        self.real_inspect = producer.inspect_image
        self.inspection = patch.object(
            producer, "inspect_image", return_value=BUILD
        ).start()
        self.build = patch.object(
            producer, "build_image", side_effect=AssertionError("No build allowed")
        ).start()
        self.command = patch.object(
            producer,
            "command",
            side_effect=AssertionError("No build/push commands allowed"),
        ).start()
        self.addCleanup(patch.stopall)

    def associate(self, directory="out"):
        return release.associate(
            CONFIG, self.identity, self.api, self.registry, self.root / directory
        )

    def assert_refused(self):
        with self.assertRaises(evidence.InvalidEvidence):
            self.associate()
        self.assertFalse((self.root / "out").exists())

    def test_success_binds_release_exact_manifest_and_digest(self):
        record = self.associate()
        raw = (self.root / "out/release-association.json").read_bytes()
        self.assertEqual(record, evidence.parse(raw))
        self.assertEqual(raw, evidence.canonical(record))
        self.assertEqual(self.api.document, record["manifest"]["document"])
        self.assertEqual(
            hashlib.sha256(evidence.canonical(self.api.document)).hexdigest(),
            record["manifest"]["sha256"],
        )
        self.assertEqual(CONFIG["image"] + "@" + DIGEST, record["image"]["reference"])
        self.assertEqual([("v1.2.3", DIGEST)], self.registry.copies)
        self.build.assert_not_called()
        self.command.assert_not_called()

    def test_retry_is_idempotent_and_does_not_select_new_main(self):
        first = self.associate()
        # A newer main commit is irrelevant: the event SHA and live tag stay pinned.
        self.api.release["target_commitish"] = "c" * 40
        self.registry.tags["verified-v2-" + "c" * 40] = "sha256:" + "d" * 64
        self.assertEqual(first, self.associate("retry"))
        self.assertEqual(1, len(self.registry.copies))

    def test_reused_image_verifies_original_labels_and_both_build_artifacts(self):
        # Exercise the real inspection helper, mocking only the Docker boundary.
        with patch.object(producer, "inspect_image", new=self.real_inspect):
            self.api.origin_api = API()
            self.api.origin_api.artifact()
            self.api.origin_api.artifacts[0]["id"] = 6002
            image = [
                {
                    "Os": "linux",
                    "Architecture": "amd64",
                    "Config": {"Labels": evidence.labels(CONFIG, ORIGIN)},
                    "RepoDigests": [CONFIG["image"] + "@" + DIGEST],
                }
            ]
            self.command.side_effect = [None, json.dumps(image)]
            record = self.associate()
            self.assertEqual(
                BUILD["run_id"], record["manifest"]["document"]["build"]["run_id"]
            )
            self.assertIn("/actions/runs/5000/attempts/1", self.api.calls)
            self.api.origin_api.artifacts = []
            self.command.side_effect = [None, json.dumps(image)]
            with self.assertRaisesRegex(
                evidence.InvalidEvidence, "retained producer manifest"
            ):
                self.associate("missing-origin")

    def test_real_inspection_rejects_revision_and_digest_mismatch(self):
        for mismatch in ("revision", "digest"):
            with (
                self.subTest(mismatch=mismatch),
                patch.object(producer, "inspect_image", new=self.real_inspect),
            ):
                image = [
                    {
                        "Os": "linux",
                        "Architecture": "amd64",
                        "Config": {"Labels": evidence.labels(CONFIG, BUILD)},
                        "RepoDigests": [CONFIG["image"] + "@" + DIGEST],
                    }
                ]
                if mismatch == "revision":
                    image[0]["Config"]["Labels"][
                        "org.opencontainers.image.revision"
                    ] = ("c" * 40)
                else:
                    image[0]["RepoDigests"] = []
                self.command.side_effect = [None, json.dumps(image)]
                self.assert_refused()
                self.assertFalse(self.registry.copies)

    def test_prerelease_and_build_metadata_have_collision_free_readable_tags(self):
        self.api = ReleaseAPI("v2.0.0-rc.1+build.001")
        self.identity = release.release_identity(self.api.release)
        record = self.associate()
        self.assertEqual("v2.0.0-rc.1_build.001", record["image"]["tag"])
        self.assertTrue(record["release"]["prerelease"])

    def test_annotated_tag_chain_resolves_to_exact_commit(self):
        self.api.target = {"type": "tag", "sha": "c" * 40}
        self.api.tags["c" * 40] = {"type": "tag", "sha": "d" * 40}
        self.api.tags["d" * 40] = {"type": "commit", "sha": SHA}
        self.associate()

    def test_tag_loop_and_non_commit_target_are_rejected(self):
        for kind in ("tag", "tree"):
            with self.subTest(kind=kind):
                self.api.target = {"type": kind, "sha": "c" * 40}
                self.api.tags["c" * 40] = self.api.target
                self.assert_refused()
        self.assertFalse(self.registry.copies)

    def test_missing_source_never_builds(self):
        del self.registry.tags["verified-v2-" + SHA]
        self.assert_refused()
        self.build.assert_not_called()
        self.inspection.assert_not_called()

    def test_conflicting_release_tag_never_overwrites(self):
        other = "sha256:" + "c" * 64
        self.registry.tags["v1.2.3"] = other
        self.assert_refused()
        self.assertEqual(other, self.registry.tags["v1.2.3"])
        self.assertFalse(self.registry.copies)

    def test_release_mutation_before_copy_blocks_publication(self):
        changes = [
            lambda: self.api.target.update(sha="c" * 40),
            lambda: self.api.release.update(tag_name="v1.2.4"),
            lambda: self.api.release.update(draft=True),
            lambda: self.api.release.update(prerelease=True),
            lambda: self.api.comparison.update(status="diverged"),
        ]
        for mutate in changes:
            with self.subTest(mutation=changes.index(mutate)):
                self.api = ReleaseAPI()
                mutate()
                self.assert_refused()
                self.assertFalse(self.registry.copies)

    def test_missing_expired_replaced_or_mismatched_manifest_blocks_copy(self):
        for change in ("missing", "expired", "checksum", "digest", "attempt", "gate"):
            with self.subTest(change=change):
                self.api = ReleaseAPI()
                if change == "missing":
                    self.api.artifacts = []
                elif change == "expired":
                    self.api.artifacts[0]["expired"] = True
                elif change == "checksum":
                    self.api.artifacts[0]["digest"] = DIGEST
                else:
                    if change == "digest":
                        self.api.document["image"]["reference"] = (
                            CONFIG["image"] + "@sha256:" + "c" * 64
                        )
                    elif change == "attempt":
                        self.api.document["build"]["attempt"] = 2
                    else:
                        self.api.document["gates"]["ci"]["attempt"] = 2
                    self.api.save_artifact()
                self.assert_refused()
                self.assertFalse(self.registry.copies)

    def test_latest_failed_or_running_build_and_gate_cannot_fall_back(self):
        for subject in ("build", "gate"):
            for status, conclusion in (
                ("completed", "failure"),
                ("completed", "skipped"),
                ("in_progress", None),
            ):
                with self.subTest(
                    subject=subject, status=status, conclusion=conclusion
                ):
                    self.api = ReleaseAPI()
                    getattr(self.api, subject).update(
                        status=status, conclusion=conclusion
                    )
                    self.assert_refused()
                    self.assertFalse(self.registry.copies)

    def test_current_attempts_must_match_retained_build_manifest(self):
        for subject in ("build", "gate"):
            with self.subTest(subject=subject):
                self.api = ReleaseAPI()
                getattr(self.api, subject)["run_attempt"] = 2
                self.assert_refused()
                self.assertFalse(self.registry.copies)

    def test_changed_evidence_during_inspection_blocks_copy(self):
        for subject in ("build", "gate", "tag", "artifact", "source"):
            with self.subTest(subject=subject):
                self.api = ReleaseAPI()
                self.registry = Registry()

                def inspect(*args, **kwargs):
                    if subject in ("build", "gate"):
                        getattr(self.api, subject)["run_attempt"] = 2
                    elif subject == "tag":
                        self.api.target["sha"] = "c" * 40
                    elif subject == "artifact":
                        self.api.artifacts = []
                    else:
                        self.registry.tags["verified-v2-" + SHA] = "sha256:" + "c" * 64
                    return BUILD

                self.inspection.side_effect = inspect
                self.assert_refused()
                self.assertFalse(self.registry.copies)

    def test_post_copy_races_emit_no_success_record(self):
        for subject in (
            "release",
            "build",
            "gate",
            "artifact",
            "tag",
            "source",
            "digest",
        ):
            with self.subTest(subject=subject):
                self.api, self.registry = ReleaseAPI(), Registry()

                def mutate():
                    if subject in ("build", "gate"):
                        getattr(self.api, subject)["run_attempt"] = 2
                    elif subject == "release":
                        self.api.target["sha"] = "c" * 40
                    elif subject == "artifact":
                        self.api.artifacts = []
                    else:
                        ref = {
                            "tag": "v1.2.3",
                            "source": "verified-v2-" + SHA,
                            "digest": DIGEST,
                        }[subject]
                        self.registry.tags[ref] = "sha256:" + "c" * 64

                self.registry.after_copy = mutate
                self.assert_refused()

    def test_semver_validation_is_bounded_and_strict(self):
        for tag in ("v0.0.0", "v1.2.3", "v1.2.3-0", "v1.2.3-x-y.9+001", "v1.2.3+001"):
            with self.subTest(tag=tag):
                self.assertEqual(tag.replace("+", "_"), release.version(tag)[0])
        for tag in (
            "1.2.3",
            "v01.2.3",
            "v1.2",
            "v1.2.3-01",
            "v1.2.3-",
            "v1.2.3+",
            "v1.2.3+x+y",
            "v1.2.3-a..b",
            "v1.2.3_x",
            "v1.2.3\n",
            "v" + "1" * 10000,
            "v1.2.3-ä",
        ):
            with (
                self.subTest(tag=tag[:40]),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                release.version(tag)

    def test_environment_accepts_only_published_release_in_exact_hosted_caller(self):
        event = self.root / "event.json"
        event.write_text(
            json.dumps(
                {
                    "action": "published",
                    "release": self.api.release,
                    "repository": {
                        "full_name": CONFIG["repository"]["name"],
                        "id": 1001,
                    },
                }
            ),
            encoding="utf-8",
        )
        env = {
            "GITHUB_EVENT_NAME": "release",
            "GITHUB_REF": "refs/tags/v1.2.3",
            "GITHUB_SERVER_URL": "https://github.com",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_SHA": SHA,
            "GITHUB_REPOSITORY": CONFIG["repository"]["name"],
            "GITHUB_REPOSITORY_ID": "1001",
            "INPUT_APP_ID": "atlas",
            "INPUT_SOURCE_REPOSITORY": CONFIG["repository"]["name"],
            "INPUT_SOURCE_REPOSITORY_ID": "1001",
            "INPUT_BUILD_WORKFLOW_PATH": CONFIG["build"]["path"],
            "INPUT_BUILD_WORKFLOW_ID": "3002",
            "INPUT_GATES": json.dumps(CONFIG["gates"]),
            "INPUT_IMAGE": CONFIG["image"],
            "INPUT_DOCKERFILE": "Dockerfile",
            "INPUT_CONTEXT": ".",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual((CONFIG, self.identity), release.environment())
        for key, value in (
            ("GITHUB_EVENT_NAME", "push"),
            ("GITHUB_REF", "refs/heads/main"),
            ("GITHUB_REPOSITORY_ID", "1002"),
            ("GITHUB_SHA", "main"),
            ("RUNNER_ENVIRONMENT", "self-hosted"),
        ):
            with (
                self.subTest(key=key),
                patch.dict(os.environ, dict(env, **{key: value}), clear=True),
                self.assertRaises(evidence.InvalidEvidence),
            ):
                release.environment()


class ReleaseTransportTests(unittest.TestCase):
    def setUp(self):
        self.raw = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
        self.digest = "sha256:" + hashlib.sha256(self.raw).hexdigest()
        self.registry = release.ReleaseRegistry(CONFIG["image"], "actor", "secret")
        self.missing = (404, {}, b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}')
        self.found = (200, {"Docker-Content-Digest": self.digest}, self.raw)
        self.token = (200, {}, b'{"token":"test"}')

    def test_copy_puts_exact_original_bytes_without_reserialization(self):
        with patch.object(
            producer,
            "http",
            side_effect=[
                self.token,
                self.found,
                self.token,
                self.missing,
                self.token,
                (201, {"Docker-Content-Digest": self.digest}, b""),
                self.token,
                self.found,
            ],
        ) as http:
            self.registry.associate("v1.2.3", self.digest)
        put = http.call_args_list[5]
        self.assertEqual({"method": "PUT", "data": self.raw}, put.kwargs)
        self.assertEqual(
            "https://ghcr.io/v2/example-org/atlas/manifests/v1.2.3", put.args[0]
        )
        self.assertIn("pull%2Cpush", http.call_args_list[4].args[0])
        self.assertEqual(
            "application/vnd.oci.image.manifest.v1+json", put.args[1]["Content-Type"]
        )

    def test_existing_identical_tag_does_not_put(self):
        with patch.object(
            producer,
            "http",
            side_effect=[self.token, self.found, self.token, self.found],
        ) as http:
            self.registry.associate("v1.2.3", self.digest)
        self.assertFalse(
            any(call.kwargs.get("method") == "PUT" for call in http.call_args_list)
        )

    def test_tag_appearing_with_another_digest_blocks_put(self):
        other = self.raw + b" "
        other_digest = "sha256:" + hashlib.sha256(other).hexdigest()
        with patch.object(
            producer,
            "http",
            side_effect=[
                self.token,
                self.found,
                self.token,
                (200, {"Docker-Content-Digest": other_digest}, other),
            ],
        ) as http:
            with self.assertRaisesRegex(evidence.InvalidEvidence, "already differs"):
                self.registry.associate("v1.2.3", self.digest)
        self.assertFalse(
            any(call.kwargs.get("method") == "PUT" for call in http.call_args_list)
        )

    def test_failed_or_digest_changing_put_never_succeeds(self):
        for status, headers in (
            (401, {}),
            (403, {}),
            (429, {}),
            (500, {}),
            (307, {"Location": "https://other.example"}),
            (201, {"Docker-Content-Digest": DIGEST}),
        ):
            with (
                self.subTest(status=status),
                patch.object(
                    producer,
                    "http",
                    side_effect=[
                        self.token,
                        self.found,
                        self.token,
                        self.missing,
                        self.token,
                        (status, headers, b""),
                    ],
                ),
            ):
                with self.assertRaisesRegex(
                    evidence.InvalidEvidence, "exact release digest"
                ):
                    self.registry.associate("v1.2.3", self.digest)

    def test_successful_put_requires_independent_readback(self):
        with patch.object(
            producer,
            "http",
            side_effect=[
                self.token,
                self.found,
                self.token,
                self.missing,
                self.token,
                (201, {"Docker-Content-Digest": self.digest}, b""),
                self.token,
                self.missing,
            ],
        ):
            with self.assertRaisesRegex(evidence.InvalidEvidence, "changed after copy"):
                self.registry.associate("v1.2.3", self.digest)
