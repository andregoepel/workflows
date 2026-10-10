"""Hosted build producer. No operator credentials, dispatches or deployment access."""

import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

# -I excludes the working directory; import only helpers beside this exact script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_evidence as evidence


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(url, headers):
    """Bound responses and never forward credentials through redirects."""
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(2 * 1024 * 1024 + 1)
        evidence.require(len(raw) <= 2 * 1024 * 1024, "HTTP response exceeds limit")
        return response.code, response.headers, raw


class GitHub:
    def __init__(self, repository, token):
        self.base = "https://api.github.com/repos/" + repository
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.remaining = 1000

    def _get(self, path):
        evidence.require(self.remaining > 0, "GitHub API budget exhausted")
        status, headers, raw = http(self.base + path, self.headers)
        self.remaining -= 1
        reported = headers.get("x-ratelimit-remaining")
        if reported is not None:
            evidence.match(reported, r"[0-9]{1,9}")
            # Headers can fluctuate between regions. Never increase this job's
            # allowance on a larger observation or assume a reset has occurred.
            self.remaining = min(self.remaining, int(reported))
        return status, headers, raw

    def __call__(self, path):
        status, _, raw = self._get(path)
        evidence.require(status == 200, "GitHub request failed (status %s)" % status)
        return evidence.parse(raw, 2 * 1024 * 1024)

    def archive(self, artifact_id):
        status, headers, _ = self._get(f"/actions/artifacts/{artifact_id}/zip")
        evidence.require(status == 302, "Artifact download redirect required")
        url = headers["Location"]
        parsed = urllib.parse.urlsplit(url)
        evidence.require(
            parsed.scheme == "https"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.hostname is not None
            and parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com")
            ),
            "Unapproved artifact download origin",
        )
        status, _, raw = http(url, {})
        evidence.require(
            status == 200 and len(raw) <= 256 * 1024,
            "Artifact download failed or oversized",
        )
        return raw


class Registry:
    def __init__(self, image, actor, token):
        self.name = image.removeprefix("ghcr.io/")
        self.actor, self.token = actor, token

    def authenticate(self):
        # Gate waits and builds can outlive a registry bearer token. Obtain a new
        # pull token for each bounded lookup instead of interpreting expiry as absence.
        query = urllib.parse.urlencode(
            {"service": "ghcr.io", "scope": "repository:" + self.name + ":pull"}
        )
        auth = base64.b64encode((self.actor + ":" + self.token).encode()).decode()
        status, _, raw = http(
            "https://ghcr.io/token?" + query, {"Authorization": "Basic " + auth}
        )
        evidence.require(status == 200, "Registry authentication failed")
        bearer = evidence.parse(raw)["token"]
        evidence.require(
            type(bearer) is str and 0 < len(bearer) <= 16384, "Invalid registry token"
        )
        return {
            "Authorization": "Bearer " + bearer,
            "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
        }

    def digest(self, reference):
        status, headers, raw = http(
            "https://ghcr.io/v2/" + self.name + "/manifests/" + reference,
            self.authenticate(),
        )
        if status == 404:
            errors = evidence.parse(raw)["errors"]
            evidence.require(
                type(errors) is list
                and errors
                and all(
                    error["code"] in ("MANIFEST_UNKNOWN", "NAME_UNKNOWN")
                    for error in errors
                ),
                "Ambiguous registry absence",
            )
            return None
        evidence.require(
            status == 200,
            "Registry lookup failed (status %s); refusing rebuild" % status,
        )
        value = evidence.parse(raw, 2 * 1024 * 1024)
        evidence.require(
            type(value["schemaVersion"]) is int
            and value["schemaVersion"] == 2
            and value["mediaType"]
            in (
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            )
            and "manifests" not in value,
            "Expected one image manifest, not an index",
        )
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        evidence.require(
            headers.get("Docker-Content-Digest") == digest,
            "Registry manifest digest mismatch",
        )
        if reference.startswith("sha256:"):
            evidence.require(reference == digest, "Registry returned another digest")
        return digest


def command(arguments, *, cwd=None, capture=False):
    # Do not expose GitHub tokens to Docker build tools or their subprocesses.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in (
            "PATH",
            "HOME",
            "DOCKER_CONFIG",
            "DOCKER_HOST",
            "DOCKER_CERT_PATH",
            "DOCKER_TLS_VERIFY",
            "BUILDX_BUILDER",
            "LANG",
            # Windows Docker Desktop plugin discovery for local validation.
            "SYSTEMROOT",
            "PROGRAMFILES",
            "PROGRAMDATA",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
        )
    }
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=capture,
        text=True,
        timeout=2400 if not capture else 120,
        check=True,
    )
    return result.stdout if capture else None


def inspect_image(image, digest, config, build, api, *, fresh):
    reference = image + "@" + digest
    command(["docker", "pull", "--platform", "linux/amd64", reference])
    raw = command(["docker", "image", "inspect", reference], capture=True)
    value = evidence.parse(raw, 2 * 1024 * 1024)
    origin = evidence.verify_image(value, config, build, api, fresh=fresh)
    evidence.require(
        reference in value[0]["RepoDigests"], "Pulled image digest mismatch"
    )
    return origin


def build_image(source, config, build, tag):
    # Resolve both paths inside the source checkout before sending any context.
    source = source.resolve(strict=True)
    context = (source / config["context"]).resolve(strict=True)
    dockerfile = (source / config["dockerfile"]).resolve(strict=True)
    evidence.require(
        context.is_relative_to(source)
        and context.is_dir()
        and dockerfile.is_relative_to(source)
        and dockerfile.is_file(),
        "Build path escapes source checkout",
    )
    arguments = [
        "docker",
        "buildx",
        "build",
        "--platform",
        "linux/amd64",
        "--provenance=false",
        "--sbom=false",
        "--load",
        "--file",
        str(dockerfile),
        "--tag",
        tag,
    ]
    for name, value in sorted(evidence.labels(config, build).items()):
        arguments.extend(["--label", name + "=" + value])
    command([*arguments, str(context)], cwd=source)
    value = evidence.parse(
        command(["docker", "image", "inspect", tag], capture=True), 2 * 1024 * 1024
    )
    evidence.verify_image(value, config, build, None, fresh=True)


def produce(config, build, api, registry, source, output, *, wait_seconds=1200):
    config = evidence.configuration(config)
    evidence.fields(build, "run_id attempt number")
    for value in build.values():
        evidence.number(value)
    pinned = evidence.wait_for_gates(api, config, build, seconds=wait_seconds)
    tag = "verified-v2-" + config["sha"]
    target = config["image"] + ":" + tag
    digest = registry.digest(tag)
    fresh = digest is None
    if fresh:
        build_image(source, config, build, target)
        evidence.recheck(api, config, build, pinned)
        evidence.require(
            registry.digest(tag) is None,
            "Commit tag appeared during build; refusing overwrite",
        )
        command(["docker", "push", target])
        digest = registry.digest(tag)
        evidence.require(digest is not None, "Published image is unavailable")
    origin = inspect_image(config["image"], digest, config, build, api, fresh=fresh)
    if not fresh:
        evidence.reuse_evidence(api, config, digest, origin)
    # A moving tag never becomes evidence. Check its bytes and all latest run
    # attempts again immediately before emitting the digest-pinned document.
    evidence.require(
        registry.digest(tag) == digest and registry.digest(digest) == digest,
        "Commit tag or image changed",
    )
    evidence.recheck(api, config, build, pinned)
    raw = evidence.canonical(evidence.manifest(config, build, pinned, digest))
    output.mkdir(parents=True, exist_ok=False)
    (output / "deployment-manifest.json").write_bytes(raw)
    return digest


def environment():
    env = os.environ
    evidence.require(
        env["GITHUB_EVENT_NAME"] == "push"
        and env["GITHUB_REF"] == "refs/heads/main"
        and env["GITHUB_SERVER_URL"] == "https://github.com"
        and env["RUNNER_ENVIRONMENT"] == "github-hosted",
        "Only GitHub-hosted main pushes are supported",
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
    build = {
        "run_id": int(env["GITHUB_RUN_ID"]),
        "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "number": int(env["GITHUB_RUN_NUMBER"]),
    }
    return config, build


def main():
    config, build = environment()
    token = os.environ["GH_TOKEN"]
    api = GitHub(config["repository"]["name"], token)
    registry = Registry(config["image"], os.environ["GITHUB_ACTOR"], token)
    source = Path(os.environ["GITHUB_WORKSPACE"]) / "source"
    checked_out = command(
        ["git", "rev-parse", "HEAD"], cwd=source, capture=True
    ).strip()
    evidence.require(checked_out == config["sha"], "Checkout SHA mismatch")
    digest = produce(
        config,
        build,
        api,
        registry,
        source,
        Path(os.environ["RUNNER_TEMP"]) / "deployment-manifest-v2",
    )
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(
            "digest=" + digest + "\nimage=" + config["image"] + "@" + digest + "\n"
        )
    print(
        "Verified deployment manifest created; image " + config["image"] + "@" + digest
    )


if __name__ == "__main__":
    try:
        main()
    except evidence.InvalidEvidence as error:
        # These messages are fixed producer diagnostics, never raw HTTP content.
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
        # Network exceptions may carry authenticated URLs and registry responses;
        # do not print exception objects, raw headers or credential-bearing bodies.
        print(
            "::error::Verified build refused. Check source, gate attempts, image provenance and service availability.",
            file=sys.stderr,
        )
        raise SystemExit(1)
