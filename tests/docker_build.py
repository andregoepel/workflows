"""Build a neutral scratch image locally; never log in, push or run app containers."""

from pathlib import Path
import os
import sys
import tempfile
import uuid

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / "tools"), str(root / "tests")]
import verified_build as producer
from test_producer import CONFIG, BUILD

tag = "workflow-producer-test:" + uuid.uuid4().hex
os.environ.setdefault("DOCKER_CONFIG", str(Path.home() / ".docker"))
try:
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        (source / "Dockerfile").write_text(
            "FROM scratch\nUSER 1654\n", encoding="utf-8"
        )
        producer.build_image(source, CONFIG, BUILD, tag)
        print(
            "PASS: actual linux/amd64 build, load, OCI labels and recipe/run identity checks"
        )
finally:
    producer.command(["docker", "image", "rm", tag])
