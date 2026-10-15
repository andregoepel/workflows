import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retention", ROOT / "tools/artifact_retention.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def snapshot(attempt=1):
    return {"schema": 1, "repository": probe.REPOSITORY, "repository_id": 42,
            "observed_at": f"2026-10-15T07:0{attempt}:00+00:00",
            "run": {"id": 123, "run_attempt": attempt, "head_sha": "a" * 40,
                    "event": "workflow_dispatch", "path": probe.WORKFLOW,
                    "status": "completed", "conclusion": "success"},
            "artifacts": [{"id": 456, "name": probe.NAME, "size_in_bytes": 250,
                           "digest": "sha256:" + "b" * 64, "expired": False,
                           "created_at": "2026-10-15T07:00:00Z", "expires_at": "2027-01-13T07:00:00Z",
                           "workflow_run": {"id": 123, "head_sha": "a" * 40,
                                            "repository_id": 42, "head_repository_id": 42}}]}


class RetentionTests(unittest.TestCase):
    def preflight_responses(self):
        return [{"full_name": probe.REPOSITORY, "default_branch": "main", "id": 42},
                {"enabled": True, "allowed_actions": "all"},
                {"commit": {"sha": "a" * 40}},
                {"path": probe.WORKFLOW, "state": "active", "id": 56},
                *[{"total_count": 0} for _ in range(5)]]

    def test_preflight_reads_without_dispatch(self):
        with patch.object(probe, "get", side_effect=self.preflight_responses()):
            self.assertFalse(probe.preflight("a" * 40)["dispatch_performed"])

    def test_preflight_rejects_restricted_or_disabled_actions(self):
        for policy in ({"enabled": True, "allowed_actions": "local_only"},
                       {"enabled": True, "allowed_actions": "selected"},
                       {"enabled": False, "allowed_actions": "all"}):
            values = self.preflight_responses()
            values[1] = policy
            with patch.object(probe, "get", side_effect=values):
                with self.assertRaises(ValueError):
                    probe.preflight("a" * 40)

    def test_preflight_rejects_drift_and_running_jobs(self):
        for index, value in ((0, {"full_name": "other/repository", "default_branch": "main"}),
                             (2, {"commit": {"sha": "b" * 40}}),
                             (3, {"path": probe.WORKFLOW, "state": "disabled_manually"}),
                             (4, {"total_count": 1})):
            values = self.preflight_responses()
            values[index] = value
            with patch.object(probe, "get", side_effect=values):
                with self.assertRaises(ValueError):
                    probe.preflight("a" * 40)

    def test_retained_artifact(self):
        result = probe.compare(snapshot(), snapshot(2))
        self.assertTrue(result["artifact_retained"])
        self.assertFalse(result["deployment_evidence"])

    def test_missing_or_duplicate_artifact(self):
        for artifacts in ([], snapshot()["artifacts"] * 2):
            item = snapshot(2)
            item["artifacts"] = artifacts
            with self.assertRaises(ValueError):
                probe.compare(snapshot(), item)

    def test_replacement_and_digest_drift(self):
        for field, value in (("id", 789), ("digest", "sha256:" + "c" * 64),
                             ("created_at", "2026-10-15T07:01:30Z")):
            item = snapshot(2)
            item["artifacts"][0][field] = value
            with self.assertRaises(ValueError):
                probe.compare(snapshot(), item)

    def test_expiration(self):
        for field, value in (("expired", True), ("expires_at", "2026-10-14T00:00:00Z")):
            item = snapshot(2)
            item["artifacts"][0][field] = value
            with self.assertRaises(ValueError):
                probe.compare(snapshot(), item)

    def test_owner_mismatch(self):
        for field, value in (("id", 124), ("head_sha", "c" * 40), ("repository_id", 43),
                             ("head_repository_id", 43)):
            item = snapshot(2)
            item["artifacts"][0]["workflow_run"][field] = value
            with self.assertRaises(ValueError):
                probe.validate(item)

    def test_terminal_success_required(self):
        for field, value in (("status", "in_progress"), ("conclusion", "failure"), ("conclusion", None)):
            item = snapshot(2)
            item["run"][field] = value
            with self.assertRaises(ValueError):
                probe.compare(snapshot(), item)

    def test_diagnostic_identity_and_attempts(self):
        for field, value in (("event", "push"), ("path", ".github/workflows/staging-deploy.yml"),
                             ("run_attempt", 3), ("head_sha", "invalid")):
            item = snapshot(2)
            item["run"][field] = value
            with self.assertRaises(ValueError):
                probe.compare(snapshot(), item)

    def test_observation_order(self):
        item = snapshot(2)
        item["observed_at"] = snapshot()["observed_at"]
        with self.assertRaises(ValueError):
            probe.compare(snapshot(), item)

    def test_read_transport_uses_get_only(self):
        with patch.object(probe.subprocess, "run") as command:
            command.return_value.returncode = 0
            command.return_value.stdout = b'{}'
            probe.get("actions/runs/123")
            self.assertEqual(command.call_args.args[0],
                             ["gh", "api", "--method", "GET", f"repos/{probe.REPOSITORY}/actions/runs/123"])

    def test_capture_and_detect_run_race(self):
        source = snapshot()
        run = {**source["run"], "repository": {"id": 42, "full_name": probe.REPOSITORY}}
        listing = {"total_count": 1, "artifacts": source["artifacts"]}
        values = [run, listing, source["artifacts"][0], run]
        with patch.object(probe, "get", side_effect=values):
            self.assertEqual(probe.observe(123, 1, "a" * 40)["artifacts"], source["artifacts"])
        changed = copy.deepcopy(run)
        changed["run_attempt"] = 2
        with patch.object(probe, "get", side_effect=values[:3] + [changed]):
            with self.assertRaises(ValueError):
                probe.observe(123, 1, "a" * 40)

    def test_incomplete_listing(self):
        run = {**snapshot()["run"], "repository": {"id": 42, "full_name": probe.REPOSITORY}}
        with patch.object(probe, "get", side_effect=[run, {"total_count": 1, "artifacts": []}]):
            with self.assertRaises(ValueError):
                probe.observe(123, 1, "a" * 40)

    def test_missing_observation_preserved_as_data(self):
        run = {**snapshot(2)["run"], "repository": {"id": 42, "full_name": probe.REPOSITORY}}
        with patch.object(probe, "get", side_effect=[run, {"total_count": 0, "artifacts": []}, run]):
            result = probe.observe(123, 2, "a" * 40)
        self.assertEqual(result["artifacts"], [])
        with self.assertRaises(ValueError):
            probe.validate(result)


if __name__ == "__main__":
    unittest.main()
