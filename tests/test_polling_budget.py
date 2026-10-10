"""Simulated GitHub rate limits and clock; a full wait takes no real time."""

from collections import Counter
import copy
import json
import unittest
from unittest.mock import patch

import build_evidence as evidence
import verified_build as producer
from test_producer import API, BUILD, CONFIG, run


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class GitHubServer:
    def __init__(self, *, budget=1000, completes_at=None):
        self.config = copy.deepcopy(CONFIG)
        self.config["gates"] = {
            f"gate{i}": {"path": f".github/workflows/gate{i}.yml", "id": 3100 + i}
            for i in range(8)
        }
        self.clock, self.budget = Clock(), budget
        self.completes_at = completes_at
        self.calls = Counter()
        self.disabled = False
        self.rerun_at = None
        self.base = API()

    def http(self, url, headers):
        path = url.removeprefix("https://api.github.com/repos/example-org/atlas")
        self.calls[path] += 1
        self.clock.now += 0.1
        if self.budget <= 0:
            return 403, {"x-ratelimit-remaining": "0"}, b"{}"
        self.budget -= 1
        for index, identity in enumerate(self.config["gates"].values()):
            flow = f'/actions/workflows/{identity["id"]}'
            gate = run(identity, {"run_id": 5100 + index, "attempt": 1, "number": 40})
            if index == 7 and (
                self.completes_at is None or self.clock() < self.completes_at
            ):
                gate.update(status="in_progress", conclusion=None)
            if (
                index == 0
                and self.rerun_at is not None
                and self.clock() >= self.rerun_at
            ):
                gate["run_attempt"] = 2
            if path == flow:
                result = {
                    **identity,
                    "state": "disabled_manually" if self.disabled else "active",
                }
                break
            if path.startswith(flow + "/runs?"):
                result = {"total_count": 1, "workflow_runs": [gate]}
                break
            if path == f'/actions/runs/{gate["id"]}':
                result = gate
                break
        else:
            result = self.base(path)
        return (
            200,
            {"x-ratelimit-remaining": str(self.budget)},
            json.dumps(result).encode(),
        )

    def wait(self, api):
        return evidence.wait_for_gates(
            api, self.config, BUILD, clock=self.clock, sleep=self.clock.sleep
        )


class PollingBudgetTests(unittest.TestCase):
    def test_eight_gates_wait_full_twenty_minutes_inside_repository_budget(self):
        server = GitHubServer()
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            with self.assertRaisesRegex(evidence.InvalidEvidence, "Timed out"):
                server.wait(api)
        # One terminal poll may finish just after the wait deadline; the fake
        # transport takes 100 ms for each of its 18 live lookups.
        self.assertAlmostEqual(1201.8, server.clock())
        self.assertLessEqual(server.calls.total(), 400)
        self.assertGreaterEqual(server.budget, 96)
        # Stable lookups occur once; successful run attempts are still monitored.
        self.assertEqual(1, server.calls[""])
        self.assertEqual(1, server.calls["/actions/workflows/3002"])
        for index in range(8):
            self.assertEqual(1, server.calls[f"/actions/workflows/{3100 + index}"])
            self.assertGreater(server.calls[f"/actions/runs/{5100 + index}"], 1)

    def test_success_and_both_final_rechecks_fit_the_same_budget(self):
        server = GitHubServer(completes_at=1100)
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            pinned = server.wait(api)
            before = server.calls.total()
            evidence.recheck(api, server.config, BUILD, pinned)
            evidence.recheck(api, server.config, BUILD, pinned)
        self.assertEqual(56, server.calls.total() - before)
        self.assertLess(server.calls.total(), 460)
        self.assertEqual(3, server.calls[""])
        self.assertEqual(3, server.calls["/actions/workflows/3100"])

    def test_last_affordable_poll_runs_before_timeout_with_150_requests(self):
        server = GitHubServer(budget=150, completes_at=60)
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            pinned = server.wait(api)
            self.assertEqual(46, server.calls.total())
            self.assertGreaterEqual(server.budget, 96)
            self.assertLess(server.clock(), 1200)
            evidence.recheck(api, server.config, BUILD, pinned)
            evidence.recheck(api, server.config, BUILD, pinned)
        self.assertEqual(102, server.calls.total())
        self.assertEqual(48, server.budget)

    def test_short_wait_executes_terminal_poll_before_declaring_timeout(self):
        for completes_at in (10, None):
            with self.subTest(completes_at=completes_at):
                server = GitHubServer(budget=150, completes_at=completes_at)
                api = producer.GitHub(CONFIG["repository"]["name"], "test")
                with patch.object(producer, "http", side_effect=server.http):

                    def wait():
                        return evidence.wait_for_gates(
                            api,
                            server.config,
                            BUILD,
                            seconds=30,
                            clock=server.clock,
                            sleep=server.clock.sleep,
                        )

                    if completes_at is None:
                        with self.assertRaisesRegex(
                            evidence.InvalidEvidence, "Timed out"
                        ):
                            wait()
                    else:
                        self.assertEqual(8, len(wait()))
                self.assertEqual(46, server.calls.total())
                self.assertAlmostEqual(31.8, server.clock())

    def test_partly_used_repository_budget_slows_polls_and_reserves_rechecks(self):
        server = GitHubServer(budget=200, completes_at=850)
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            pinned = server.wait(api)
            self.assertGreaterEqual(server.budget, 96)
            evidence.recheck(api, server.config, BUILD, pinned)
            evidence.recheck(api, server.config, BUILD, pinned)
        self.assertLess(server.calls.total(), 200)
        self.assertLess(server.clock(), 1200)

    def test_low_budget_stops_polling_before_spending_verification_reserve(self):
        server = GitHubServer(budget=120)
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            with self.assertRaisesRegex(
                evidence.InvalidEvidence, "Insufficient GitHub API budget"
            ):
                server.wait(api)
        self.assertEqual(96, server.budget)
        self.assertEqual(24, server.calls.total())

    def test_cached_identity_does_not_hide_change_from_final_recheck(self):
        server = GitHubServer(completes_at=65)
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            pinned = server.wait(api)
            server.disabled = True
            with self.assertRaisesRegex(evidence.InvalidEvidence, "not active"):
                evidence.recheck(api, server.config, BUILD, pinned)

    def test_successful_gate_rerun_is_detected_during_budgeted_wait(self):
        server = GitHubServer()
        server.rerun_at = 60
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(producer, "http", side_effect=server.http):
            with self.assertRaisesRegex(evidence.InvalidEvidence, "attempt changed"):
                server.wait(api)

    def test_larger_or_absent_rate_header_does_not_replenish_job_allowance(self):
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        responses = [
            (200, {"x-ratelimit-remaining": "200"}, b"{}"),
            (200, {"x-ratelimit-remaining": "300"}, b"{}"),
            (200, {}, b"{}"),
        ]
        with patch.object(producer, "http", side_effect=responses):
            api("")
            self.assertEqual(200, api.remaining)
            api("")
            self.assertEqual(199, api.remaining)
            api("")
            self.assertEqual(198, api.remaining)

    def test_exhausted_api_allowance_is_not_retried(self):
        api = producer.GitHub(CONFIG["repository"]["name"], "test")
        with patch.object(
            producer, "http", return_value=(403, {"x-ratelimit-remaining": "0"}, b"{}")
        ) as request:
            with self.assertRaises(evidence.InvalidEvidence):
                api("")
            with self.assertRaisesRegex(evidence.InvalidEvidence, "budget exhausted"):
                api("")
            self.assertEqual(1, request.call_count)
