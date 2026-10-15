"""Read-only GitHub artifact retention observations; never starts or deletes runs."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

REPOSITORY = "andregoepel/workflows"
WORKFLOW = ".github/workflows/artifact-retention-probe.yml"
NAME = "artifact-retention-probe"
RUN_KEYS = ("id", "run_attempt", "head_sha", "event", "path", "status", "conclusion")
ARTIFACT_KEYS = ("id", "name", "size_in_bytes", "digest", "expired", "created_at", "expires_at")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def get(suffix):
    result = subprocess.run(
        ["gh", "api", "--method", "GET", f"repos/{REPOSITORY}/{suffix}"],
        capture_output=True, timeout=40, check=False,
    )
    require(result.returncode == 0, "GitHub GET failed; no state change requested")
    return json.loads(result.stdout)


def preflight(sha):
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid source SHA")
    repository = get("")
    require(repository["full_name"] == REPOSITORY and repository["default_branch"] == "main",
            "Wrong diagnostic repository/default branch")
    policy = get("actions/permissions")
    require(policy["enabled"] is True and policy["allowed_actions"] == "all",
            "Diagnostic actions are not covered by the reviewed policy; do not relax repository permissions")
    branch = get("branches/main")
    require(branch["commit"]["sha"] == sha, "Reviewed diagnostic revision is not current main")
    workflow = get("actions/workflows/artifact-retention-probe.yml")
    require(workflow["path"] == WORKFLOW and workflow["state"] == "active", "Diagnostic workflow is unavailable")
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        runs = get(f"actions/runs?status={status}&per_page=1")
        require(runs["total_count"] == 0, "Wait for other workflow runs before diagnosis")
    return {"repository": REPOSITORY, "repository_id": repository["id"], "sha": sha,
            "allowed_actions": policy["allowed_actions"], "workflow_id": workflow["id"],
            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "dispatch_performed": False}


def observe(run_id, attempt, sha):
    require(run_id > 0 and attempt > 0, "Invalid run identity")
    require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid source SHA")
    before = get(f"actions/runs/{run_id}")
    listing = get(f"actions/runs/{run_id}/artifacts?per_page=100")
    require(listing["total_count"] == len(listing["artifacts"]) <= 100, "Incomplete artifact listing")
    artifacts = []
    for item in listing["artifacts"]:
        require(item["name"] == NAME, "Unexpected artifact in diagnostic run")
        direct = get(f"actions/artifacts/{item['id']}")
        require(direct == item, "Artifact metadata changed during observation")
        artifacts.append({**{k: item[k] for k in ARTIFACT_KEYS}, "workflow_run": item["workflow_run"]})
    after = get(f"actions/runs/{run_id}")
    require(all(before[k] == after[k] for k in RUN_KEYS), "Run changed during observation")
    require(after["id"] == run_id and after["run_attempt"] == attempt and after["head_sha"] == sha,
            "Run identity drift")
    require(after["repository"]["full_name"] == REPOSITORY, "Wrong repository")
    return {"schema": 1, "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "repository": REPOSITORY, "repository_id": after["repository"]["id"],
            "run": {k: after[k] for k in RUN_KEYS}, "artifacts": artifacts}


def validate(snapshot, completed=False):
    require(snapshot["schema"] == 1 and snapshot["repository"] == REPOSITORY, "Wrong snapshot contract")
    run = snapshot["run"]
    require(type(run["id"]) is int and run["id"] > 0 and type(run["run_attempt"]) is int
            and run["run_attempt"] > 0, "Invalid run identity")
    require(re.fullmatch(r"[0-9a-f]{40}", run["head_sha"]) is not None, "Invalid source SHA")
    require(run["event"] == "workflow_dispatch" and run["path"] == WORKFLOW, "Wrong diagnostic workflow")
    if completed:
        require(run["status"] == "completed" and run["conclusion"] == "success", "Run is not successful")
    require(len(snapshot["artifacts"]) == 1, "Expected one retained diagnostic artifact")
    artifact = snapshot["artifacts"][0]
    require(artifact["name"] == NAME and artifact["expired"] is False, "Missing or expired diagnostic artifact")
    require(type(artifact["id"]) is int and artifact["id"] > 0, "Invalid artifact ID")
    require(type(artifact["size_in_bytes"]) is int and 0 < artifact["size_in_bytes"] <= 65536,
            "Unexpected diagnostic artifact size")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"]) is not None, "Missing archive digest")
    owner = artifact["workflow_run"]
    require(owner["id"] == run["id"] and owner["head_sha"] == run["head_sha"]
            and owner["repository_id"] == snapshot["repository_id"]
            and owner["head_repository_id"] == snapshot["repository_id"], "Artifact ownership mismatch")
    require(datetime.datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
            > datetime.datetime.fromisoformat(snapshot["observed_at"]), "Artifact retention elapsed")
    return artifact


def compare(first, repeated):
    old = validate(first, completed=True)
    new = validate(repeated, completed=True)
    require(first["run"]["run_attempt"] == 1 and repeated["run"]["run_attempt"] == 2,
            "Require original attempt and exactly one rerun")
    for field in ("id", "head_sha", "path", "event"):
        require(first["run"][field] == repeated["run"][field], "Different diagnostic runs")
    require(first["repository_id"] == repeated["repository_id"], "Repository identity drift")
    require(old == new, "Original artifact identity or metadata changed")
    require(datetime.datetime.fromisoformat(repeated["observed_at"])
            > datetime.datetime.fromisoformat(first["observed_at"]), "Observation order reversed")
    return {"artifact_retained": True, "run_id": first["run"]["id"], "artifact_id": old["id"],
            "deployment_evidence": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("preflight")
    check.add_argument("--sha", required=True)
    read = sub.add_parser("observe")
    read.add_argument("--run", type=int, required=True)
    read.add_argument("--attempt", type=int, required=True)
    read.add_argument("--sha", required=True)
    read.add_argument("--out", type=Path, required=True)
    read.add_argument("--completed", action="store_true")
    diff = sub.add_parser("compare")
    diff.add_argument("first", type=Path)
    diff.add_argument("repeated", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(preflight(args.sha)))
    elif args.command == "observe":
        result = observe(args.run, args.attempt, args.sha)
        # Preserve missing-artifact evidence before failing, never overwrite a prior observation.
        with args.out.open("x", encoding="utf-8") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
        print(json.dumps(result))
        validate(result, completed=args.completed)
    else:
        print(json.dumps(compare(json.loads(args.first.read_bytes()), json.loads(args.repeated.read_bytes()))))


if __name__ == "__main__":
    main()
