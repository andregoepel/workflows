"""Associate a published release with existing verified bytes; never build code."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_evidence as evidence
import verified_build as producer


def version(tag):
    """Bounded SemVer parser; '+' maps injectively to '_' for registry tags."""
    evidence.require(type(tag) is str and 1 < len(tag) <= 128, "Invalid release tag")
    evidence.require(tag.startswith("v"), "Release tag must start with v")
    parts = tag[1:].split("+")
    evidence.require(len(parts) <= 2, "Invalid build metadata")
    core, separator, pre = parts[0].partition("-")
    numbers = core.split(".")
    evidence.require(len(numbers) == 3, "Expected SemVer major.minor.patch")
    for item in numbers:
        evidence.match(item, r"0|[1-9][0-9]*")
    for identifiers, numeric in ([(pre, True)] if separator else []) + (
        [(parts[1], False)] if len(parts) == 2 else []
    ):
        for item in identifiers.split("."):
            evidence.match(item, r"[0-9A-Za-z-]+")
            evidence.require(
                not numeric
                or not item.isdigit()
                or item == "0"
                or not item.startswith("0"),
                "Leading zero in prerelease identifier",
            )
    return tag.replace("+", "_"), bool(separator)


def release_identity(value):
    evidence.number(value["id"])
    _, prerelease = version(value["tag_name"])
    evidence.require(
        value["draft"] is False
        and type(value["prerelease"]) is bool
        and value["prerelease"] == prerelease
        and type(value["published_at"]) is str
        and value["published_at"],
        "Release must be published with matching prerelease status",
    )
    return {
        "id": value["id"],
        "tag": value["tag_name"],
        "prerelease": prerelease,
        "published_at": value["published_at"],
    }


def release_source(api, config, release):
    """Resolve the live tag, including annotated tags, rather than target_commitish."""
    evidence.require(
        release_identity(api(f'/releases/{release["id"]}')) == release,
        "Release changed since publication",
    )
    ref = api("/git/ref/tags/" + urllib.parse.quote(release["tag"], safe=""))
    evidence.require(ref["ref"] == "refs/tags/" + release["tag"], "Tag ref mismatch")
    target = ref["object"]
    for _ in range(8):
        evidence.match(target["sha"], r"[0-9a-f]{40}")
        if target["type"] == "commit":
            break
        evidence.require(target["type"] == "tag", "Release tag is not a commit")
        annotated = api("/git/tags/" + target["sha"])
        evidence.require(annotated["sha"] == target["sha"], "Tag object mismatch")
        target = annotated["object"]
    evidence.require(
        target["type"] == "commit" and target["sha"] == config["sha"],
        "Release tag moved or annotation chain exceeds limit",
    )
    comparison = api(f'/compare/{config["sha"]}...main?per_page=1')
    evidence.require(
        comparison["status"] in ("ahead", "identical")
        and comparison["merge_base_commit"]["sha"] == config["sha"]
        and comparison["base_commit"]["sha"] == config["sha"],
        "Release commit is not on main",
    )


def successful_build(api, config, expected=None):
    repository = api("")
    evidence.repo(repository, config["repository"])
    evidence.require(
        repository["default_branch"] == "main", "Expected main default branch"
    )
    evidence.active(api, config["build"])
    items = evidence.listing(api, config, config["build"])
    evidence.require(items, "No same-commit build exists")
    listed = max(items, key=lambda item: item["run_number"])
    selected = evidence.run(
        api(f'/actions/runs/{listed["id"]}'), config, config["build"], successful=True
    )
    build = evidence.ticket(selected)
    evidence.require(build == evidence.ticket(listed), "Build changed during lookup")
    evidence.require(
        expected is None or build == expected, "Build run or attempt changed"
    )
    return build


def recheck(api, config, release, build, digest, document, artifact):
    release_source(api, config, release)
    successful_build(api, config, build)
    pinned = {}
    evidence.require(
        evidence.gates(api, config, pinned), "Mandatory gates are not complete"
    )
    evidence.require(
        document == evidence.manifest(config, build, pinned, digest),
        "Current attempts differ from build manifest",
    )
    # Upload-artifact replacement creates a new immutable artifact ID. Do not
    # accept a replacement or disappearance during this association attempt.
    evidence.require(
        evidence.reuse_evidence(api, config, digest, build) == (document, artifact),
        "Build artifact changed during association",
    )


class ReleaseRegistry(producer.Registry):
    def associate(self, tag, digest):
        source = self.manifest(digest)
        evidence.require(
            source is not None and source[0] == digest, "Source image missing"
        )
        raw = source[1]
        existing = self.digest(tag)
        evidence.require(
            existing in (None, digest), "Release image tag already differs"
        )
        if existing == digest:
            return
        headers = self.authenticate(actions="pull,push")
        headers["Content-Type"] = evidence.parse(raw, 2 * 1024 * 1024)["mediaType"]
        # Copy the exact manifest bytes inside the same image repository. No
        # Docker retag/build operation may reserialize the manifest or create an index.
        status, response, _ = producer.http(
            "https://ghcr.io/v2/" + self.name + "/manifests/" + tag,
            headers,
            data=raw,
            method="PUT",
        )
        evidence.require(
            status == 201 and response.get("Docker-Content-Digest") == digest,
            "Registry did not confirm the exact release digest",
        )
        evidence.require(
            self.digest(tag) == digest, "Release image tag changed after copy"
        )


def associate(config, release, api, registry, output):
    config = evidence.configuration(config)
    tag, _ = version(release["tag"])
    release_source(api, config, release)
    build = successful_build(api, config)
    commit_tag = "verified-v2-" + config["sha"]
    digest = registry.digest(commit_tag)
    evidence.require(
        digest is not None, "Verified source image missing; refusing rebuild"
    )
    document, artifact = evidence.reuse_evidence(api, config, digest, build)
    origin = producer.inspect_image(
        config["image"], digest, config, build, api, fresh=False
    )
    if origin != build:
        evidence.reuse_evidence(api, config, digest, origin)
    recheck(api, config, release, build, digest, document, artifact)
    evidence.require(registry.digest(commit_tag) == digest, "Source image tag changed")
    registry.associate(tag, digest)
    evidence.require(
        registry.digest(commit_tag) == digest
        and registry.digest(tag) == digest
        and registry.digest(digest) == digest,
        "Image association changed",
    )
    recheck(api, config, release, build, digest, document, artifact)
    record = {
        "kind": "release-association",
        "schema": 1,
        "app_id": config["app_id"],
        "release": release,
        "manifest": {
            "artifact": artifact,
            "sha256": hashlib.sha256(evidence.canonical(document)).hexdigest(),
            "document": document,
        },
        "image": {"reference": config["image"] + "@" + digest, "tag": tag},
    }
    raw = evidence.canonical(record)
    output.mkdir(parents=True, exist_ok=False)
    (output / "release-association.json").write_bytes(raw)
    (output / "release-association.md").write_text(
        f'# Release {release["tag"]}\n\n'
        f'- Source: `{config["repository"]["name"]}@{config["sha"]}`\n'
        f'- Image: `{config["image"]}@{digest}`\n'
        f'- Readable tag: `{config["image"]}:{tag}`\n'
        f'- Build: {build["run_id"]}, attempt {build["attempt"]}\n'
        f'- Manifest artifact: {artifact["id"]} (`{artifact["digest"]}`)\n\n'
        "This association is not a production approval. Deploy by verified digest.\n",
        encoding="utf-8",
    )
    return record


def environment():
    env = os.environ
    evidence.require(
        env["GITHUB_EVENT_NAME"] == "release"
        and env["GITHUB_SERVER_URL"] == "https://github.com"
        and env["RUNNER_ENVIRONMENT"] == "github-hosted",
        "Only GitHub-hosted published releases are supported",
    )
    with open(env["GITHUB_EVENT_PATH"], "rb") as stream:
        event = evidence.parse(stream.read(2 * 1024 * 1024 + 1), 2 * 1024 * 1024)
    evidence.require(event["action"] == "published", "Expected release.published")
    release = release_identity(event["release"])
    evidence.require(
        env["GITHUB_REF"] == "refs/tags/" + release["tag"], "Release ref mismatch"
    )
    config = evidence.configuration(
        {
            "app_id": env["INPUT_APP_ID"],
            "repository": {
                "name": env["INPUT_SOURCE_REPOSITORY"],
                "id": int(env["INPUT_SOURCE_REPOSITORY_ID"]),
            },
            "sha": env["GITHUB_SHA"],
            "build": {
                "path": env["INPUT_BUILD_WORKFLOW_PATH"],
                "id": int(env["INPUT_BUILD_WORKFLOW_ID"]),
            },
            "gates": evidence.parse(env["INPUT_GATES"]),
            "image": env["INPUT_IMAGE"],
            "dockerfile": env["INPUT_DOCKERFILE"],
            "context": env["INPUT_CONTEXT"],
        }
    )
    evidence.require(
        config["repository"]
        == {"name": env["GITHUB_REPOSITORY"], "id": int(env["GITHUB_REPOSITORY_ID"])},
        "Inputs differ from caller repository",
    )
    evidence.repo(event["repository"], config["repository"])
    return config, release


def main():
    config, release = environment()
    token = os.environ["GH_TOKEN"]
    record = associate(
        config,
        release,
        producer.GitHub(config["repository"]["name"], token),
        ReleaseRegistry(config["image"], os.environ["GITHUB_ACTOR"], token),
        Path(os.environ["RUNNER_TEMP"]) / "release-association-v1",
    )
    reference = record["image"]["reference"]
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(
            "image="
            + reference
            + "\ndigest="
            + reference.split("@")[1]
            + "\nrelease-tag="
            + record["image"]["tag"]
            + "\n"
        )
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write(f'Release `{release["tag"]}` → `{reference}`\n')


if __name__ == "__main__":
    try:
        main()
    except evidence.InvalidEvidence as error:
        print("::error::" + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        RecursionError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        RuntimeError,
    ):
        print(
            "::error::Release association refused. Check release, build evidence and registry availability.",
            file=sys.stderr,
        )
        raise SystemExit(1)
