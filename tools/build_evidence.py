"""Generation-2 producer contract and GitHub provenance checks (standard library)."""

import json
import hashlib
import io
import re
import stat
import time
import zipfile
from datetime import datetime, timezone


class InvalidEvidence(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def match(value, pattern):
    require(type(value) is str and re.fullmatch(pattern, value), "Invalid identity")
    return value


def number(value):
    require(type(value) is int and 0 < value <= 9007199254740991, "Invalid number")
    return value


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "Invalid fields")


def workflow(value):
    fields(value, "path id")
    match(value["path"], r"\.github/workflows/[A-Za-z0-9_-]+\.ya?ml")
    number(value["id"])
    return value


def parse(raw, limit=65536):
    require(len(raw) <= limit, "JSON exceeds limit")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    def deny(value):
        raise InvalidEvidence("Non-integer JSON number")

    return json.loads(
        raw, object_pairs_hook=pairs, parse_float=deny, parse_constant=deny
    )


def canonical(value):
    raw = (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
        + "\n"
    ).encode()
    require(len(raw) <= 65536, "Manifest exceeds 64 KiB")
    return raw


def configuration(value):
    fields(value, "app_id repository sha build gates image dockerfile context")
    match(value["app_id"], r"[a-z][a-z0-9-]{0,62}")
    fields(value["repository"], "name id")
    match(
        value["repository"]["name"],
        r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*",
    )
    number(value["repository"]["id"])
    match(value["sha"], r"[0-9a-f]{40}")
    workflow(value["build"])
    gates = value["gates"]
    require(
        type(gates) is dict and 1 <= len(gates) <= 8,
        "One to eight mandatory gates required",
    )
    for name, identity in gates.items():
        match(name, r"[a-z][a-z0-9-]{0,62}")
        workflow(identity)
    identities = [value["build"], *gates.values()]
    require(
        len({v["id"] for v in identities}) == len(identities)
        and len({v["path"] for v in identities}) == len(identities),
        "Workflow identities must be distinct",
    )
    match(value["image"], r"ghcr\.io/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)+")
    require(
        value["image"].split("/")[1]
        == value["repository"]["name"].split("/")[0].lower(),
        "Image must belong to source owner",
    )
    for key in ("dockerfile", "context"):
        path = value[key]
        require(type(path) is str and len(path) <= 512, "Invalid build path")
        if key == "context" and path == ".":
            continue
        require(
            all(
                re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", part)
                for part in path.split("/")
            ),
            "Build paths must be relative local paths",
        )
    return value


def repo(value, expected):
    require(type(value) is dict, "Missing run repository")
    number(value["id"])
    require(
        value["id"] == expected["id"] and value["full_name"] == expected["name"],
        "Fork or repository mismatch",
    )


def run(value, config, identity, *, successful=False):
    for key in ("id", "workflow_id", "run_attempt", "run_number"):
        number(value[key])
    require(
        value["workflow_id"] == identity["id"] and value["path"] == identity["path"],
        "Run workflow mismatch",
    )
    repo(value["repository"], config["repository"])
    repo(value["head_repository"], config["repository"])
    require(
        value["event"] == "push"
        and value["head_branch"] == "main"
        and value["head_sha"] == config["sha"],
        "Run is not a same-commit main push",
    )
    if successful:
        require(
            value["status"] == "completed" and value["conclusion"] == "success",
            "Run did not succeed",
        )
    return value


def active(api, identity):
    value = api(f'/actions/workflows/{identity["id"]}')
    require(
        type(value["id"]) is int
        and value["id"] == identity["id"]
        and value["path"] == identity["path"]
        and value["state"] == "active",
        "Workflow identity is not active",
    )


def listing(api, config, identity):
    value = api(
        f'/actions/workflows/{identity["id"]}/runs?branch=main&event=push&head_sha={config["sha"]}&per_page=100'
    )
    items = value["workflow_runs"]
    require(
        type(value["total_count"]) is int
        and type(items) is list
        and 0 <= value["total_count"] <= 100
        and len(items) == value["total_count"],
        "Incomplete run history",
    )
    for item in items:
        run(item, config, identity)
    require(
        len({v["id"] for v in items}) == len(items)
        and len({v["run_number"] for v in items}) == len(items),
        "Ambiguous run history",
    )
    return items


def ticket(value):
    return {
        "run_id": value["id"],
        "attempt": value["run_attempt"],
        "number": value["run_number"],
    }


def current_build(api, config, expected):
    value = api("")
    repo(value, config["repository"])
    require(value["default_branch"] == "main", "Expected main default branch")
    active(api, config["build"])
    current = run(api(f'/actions/runs/{expected["run_id"]}'), config, config["build"])
    require(
        ticket(current) == expected
        and current["status"] == "in_progress"
        and current["conclusion"] is None,
        "Current build attempt changed or stopped",
    )
    items = listing(api, config, config["build"])
    require(
        items and ticket(max(items, key=lambda v: v["run_number"])) == expected,
        "Newer build superseded this run",
    )
    return current


def gates(api, config, pinned):
    """Pin the first observed latest attempt; never fall back to an older success."""
    ready = True
    for name, identity in config["gates"].items():
        active(api, identity)
        items = listing(api, config, identity)
        if not items:
            require(name not in pinned, "Previously observed gate disappeared")
            ready = False
            continue
        listed = max(items, key=lambda v: v["run_number"])
        selected = run(api(f'/actions/runs/{listed["id"]}'), config, identity)
        require(ticket(selected) == ticket(listed), "Gate run changed during lookup")
        observed = ticket(selected)
        if name in pinned:
            require(pinned[name] == observed, "Gate run or attempt changed")
        else:
            pinned[name] = observed
        if selected["status"] == "completed":
            run(selected, config, identity, successful=True)
        else:
            require(
                selected["status"]
                in ("queued", "in_progress", "waiting", "pending", "requested")
                and selected["conclusion"] is None,
                "Unsupported gate state",
            )
            ready = False
    return ready


def wait_for_gates(
    api, config, build, *, seconds=1200, clock=time.monotonic, sleep=time.sleep
):
    deadline, pinned, identities = clock() + seconds, {}, {}
    # At eight gates two full fresh rechecks cost 56 requests. Keep additional
    # room for retained artifact/origin checks and concurrent repository traffic.
    reserve, requests = 96, 0
    stable = {
        "",
        *(
            f'/actions/workflows/{item["id"]}'
            for item in [config["build"], *config["gates"].values()]
        ),
    }

    def remaining():
        return min(1000 - requests, getattr(api, "remaining", 1000))

    def polling_api(path):
        nonlocal requests
        if path in identities:
            return identities[path]
        require(
            remaining() > reserve, "Insufficient GitHub API budget for gate polling"
        )
        value = api(path)
        requests += 1
        if path in stable:
            identities[path] = value
        return value

    # Only stable repository/workflow metadata is cached. Run listings and
    # attempts, including already successful gates, are checked on every poll.
    batch = 2 + 2 * len(config["gates"])
    while True:
        current_build(polling_api, config, build)
        if gates(polling_api, config, pinned):
            return pinned
        left = deadline - clock()
        require(left > 0, "Timed out waiting for mandatory gates")
        polls = (remaining() - reserve) // batch
        require(polls > 0, "Insufficient GitHub API budget for gate polling")
        # At least one minute between rounds; spread a smaller available budget
        # over the rest of the wait instead of consuming it in an early burst.
        sleep(min(left, max(60, left / polls)))
        require(clock() < deadline, "Timed out waiting for mandatory gates")


def recheck(api, config, build, pinned):
    current_build(api, config, build)
    require(
        set(pinned) == set(config["gates"]) and gates(api, config, pinned),
        "Mandatory gate no longer successful",
    )


def manifest(config, build, pinned, digest):
    match(digest, r"sha256:[0-9a-f]{64}")
    return {
        "kind": "deployment-manifest",
        "schema": 2,
        "app_id": config["app_id"],
        "source": {
            "repository": config["repository"],
            "branch": "main",
            "sha": config["sha"],
        },
        "build": {"workflow": config["build"], **build},
        "gates": {
            name: {
                "workflow": config["gates"][name],
                "run_id": item["run_id"],
                "attempt": item["attempt"],
            }
            for name, item in pinned.items()
        },
        "image": {
            "reference": config["image"] + "@" + digest,
            "platform": "linux/amd64",
            "revision": config["sha"],
        },
    }


LABEL = "io.github.deployment-producer."


def labels(config, build):
    return {
        "org.opencontainers.image.source": "https://github.com/"
        + config["repository"]["name"],
        "org.opencontainers.image.revision": config["sha"],
        LABEL + "contract": "2",
        LABEL + "app": config["app_id"],
        LABEL + "repository-id": str(config["repository"]["id"]),
        LABEL + "workflow-id": str(config["build"]["id"]),
        LABEL + "workflow-path": config["build"]["path"],
        LABEL + "dockerfile": config["dockerfile"],
        LABEL + "context": config["context"],
        **{LABEL + key: str(value) for key, value in build.items()},
    }


def verify_image(value, config, build, api, *, fresh):
    require(type(value) is list and len(value) == 1, "Ambiguous image inspection")
    value = value[0]
    require(
        value["Os"] == "linux" and value["Architecture"] == "amd64",
        "Image platform mismatch",
    )
    actual = value["Config"]["Labels"]
    origin = {
        key: number(int(match(actual[LABEL + key], r"[1-9][0-9]{0,15}")))
        for key in build
    }
    require(
        all(
            actual.get(key) == expected
            for key, expected in labels(config, origin).items()
        ),
        "Image provenance or build recipe mismatch",
    )
    if fresh:
        require(origin == build, "New image has wrong build identity")
    else:
        # A failed post-push run is deliberately not relabelled as a successful
        # original build. Keep that orphan for investigation; never overwrite it.
        historical = run(
            api(f'/actions/runs/{origin["run_id"]}/attempts/{origin["attempt"]}'),
            config,
            config["build"],
            successful=True,
        )
        require(ticket(historical) == origin, "Original image build identity changed")
    return origin


def reuse_evidence(api, config, digest, origin):
    """Bind reused registry bytes to a successful producer artifact, not labels alone."""
    response = api(f'/actions/runs/{origin["run_id"]}/artifacts?per_page=100')
    items = response["artifacts"]
    require(
        type(response["total_count"]) is int
        and type(items) is list
        and 0 <= response["total_count"] <= 100
        and len(items) == response["total_count"],
        "Incomplete artifact listing",
    )
    matches = [item for item in items if item["name"] == "deployment-manifest-v2"]
    require(len(matches) == 1, "Reuse requires one retained producer manifest")
    artifact = matches[0]
    number(artifact["id"])
    require(
        type(artifact["size_in_bytes"]) is int
        and 0 < artifact["size_in_bytes"] <= 256 * 1024
        and artifact["expired"] is False,
        "Artifact expired or oversized",
    )

    def moment(value):
        match(value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )

    require(
        moment(artifact["created_at"])
        <= datetime.now(timezone.utc)
        < moment(artifact["expires_at"]),
        "Artifact outside validity period",
    )
    owner = artifact["workflow_run"]
    for key in ("id", "repository_id", "head_repository_id"):
        number(owner[key])
    require(
        owner["id"] == origin["run_id"]
        and owner["repository_id"]
        == owner["head_repository_id"]
        == config["repository"]["id"]
        and owner["head_sha"] == config["sha"]
        and owner["head_branch"] == "main",
        "Artifact provenance mismatch",
    )
    raw = api.archive(artifact["id"])
    require(
        0 < len(raw) <= 256 * 1024
        and "sha256:" + hashlib.sha256(raw).hexdigest() == artifact["digest"],
        "Artifact checksum mismatch",
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1, "Unexpected artifact entries")
        entry = entries[0]
        require(
            entry.filename == "deployment-manifest.json"
            and not entry.is_dir()
            and not entry.flag_bits & 1
            and not entry.external_attr & 0x10
            and stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
            and 0 < entry.file_size <= 65536
            and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
            "Unsafe manifest archive",
        )
        with archive.open(entry) as stream:
            content = stream.read(65537)
        require(len(content) == entry.file_size, "Artifact entry length mismatch")
    value = parse(content)
    require(canonical(value) == content, "Noncanonical manifest")
    fields(value, "kind schema app_id source build gates image")
    fields(value["build"], "workflow run_id attempt number")
    build = {
        key: number(value["build"][key]) for key in ("run_id", "attempt", "number")
    }
    require(
        build["run_id"] == origin["run_id"] and build["attempt"] >= origin["attempt"],
        "Manifest build differs from original image build",
    )
    require(
        type(value["gates"]) is dict and set(value["gates"]) == set(config["gates"]),
        "Manifest gate set changed",
    )
    pinned = {}
    for name, gate in value["gates"].items():
        fields(gate, "workflow run_id attempt")
        pinned[name] = {key: number(gate[key]) for key in ("run_id", "attempt")}
        previous = run(
            api(f'/actions/runs/{gate["run_id"]}/attempts/{gate["attempt"]}'),
            config,
            config["gates"][name],
            successful=True,
        )
        require(
            previous["id"] == gate["run_id"]
            and previous["run_attempt"] == gate["attempt"],
            "Historical gate attempt mismatch",
        )
    require(
        value == manifest(config, build, pinned, digest)
        and type(value["schema"]) is int,
        "Existing manifest does not prove this image",
    )
    previous = run(
        api(f'/actions/runs/{build["run_id"]}/attempts/{build["attempt"]}'),
        config,
        config["build"],
        successful=True,
    )
    require(ticket(previous) == build, "Manifest build attempt mismatch")
