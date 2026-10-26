"""`magnet publish` of the real CLI against a live test server."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from django.core.management import call_command

from git_db.db import get_git_database
from git_db.tests.git_db_tests import show
from packages.models import Release
from users.oauth import create_access_token
from users.tests.factories import UserFactory

MAGNET = (
    Path(__file__).resolve().parents[4] / "magnet_cli" / "target" / "debug" / "magnet"
)

MANIFEST = """\
magnet-version = 1

[package]
name = "Foo_Bar"
version = "1.0.0"
kind = "quark"
sclang = ">=3.13"

[metadata]
summary = "Published by the real CLI"

[artifact]
global-paths = ["Classes/**.sc"]
"""


def magnet(project: Path, *args: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [MAGNET, *args],
        cwd=project,
        env={"HOME": str(project.parent), "PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
# a live server needs committed data; restore the reserved names afterwards
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_magnet_publish(live_server, tmp_path):
    call_command("init_index")
    user = UserFactory.create()
    token = create_access_token(user, days=1, scope="publish").token
    project = tmp_path / "foo"
    (project / "Classes").mkdir(parents=True)
    (project / "Classes" / "Foo.sc").write_text("Foo {}\n")
    (project / "README.md").write_text("# Foo\n")
    (project / "magnet.toml").write_text(MANIFEST)
    env = {"MAGNET_TOKEN": token, "MAGNET_API_URL": f"{live_server.url}/"}

    magnet(project, "package", env=env)
    first = magnet(project, "publish", env=env)
    second = magnet(project, "publish", env=env)

    release = Release.objects.select_related("project").get()
    assert release.is_final and release.project.name == "Foo_Bar"
    assert release.project.owner == user
    [line] = show(get_git_database().repo_path, "f/foo-bar.jsonl").splitlines()
    artifact = project / "dist" / "foo-bar-1.0.0-quark-any-any.tar.gz"
    assert json.loads(line)["artifacts"] == [
        {
            "abi": "any",
            "platform": "any",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    ]
    assert json.loads(line)["sclang"] == ">=3.13"
    assert "already uploaded" not in first
    assert "already uploaded" in second


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_magnet_publish_lepton(live_server, tmp_path):
    call_command("init_index")
    token = create_access_token(UserFactory.create(), days=1, scope="publish").token
    project = tmp_path / "foo"
    for path, content in {
        "Classes/Foo.sc": "Foo {}\n",
        "src/Foo.cpp": "// Foo\n",
        "build/Foo.scx": "binary\n",
        "magnet.toml": MANIFEST.replace('kind = "quark"', 'kind = "lepton"')
        + 'source-paths = ["Classes/**", "src/**"]\n'
        + 'platform-paths = { macos_arm64 = ["build/*.scx"] }\n',
    }.items():
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(content)
    env = {
        "MAGNET_TOKEN": token,
        "MAGNET_API_URL": f"{live_server.url}/",
        "MAGNET_ABI": "api3_0",
        "MAGNET_PLATFORM": "macos_arm64",
    }

    magnet(project, "package", env=env)
    magnet(project, "publish", env=env)

    [line] = show(get_git_database().repo_path, "f/foo-bar.jsonl").splitlines()
    # the CLI uploads the source first
    assert [(a["abi"], a["platform"]) for a in json.loads(line)["artifacts"]] == [
        ("source", "any"),
        ("api3_0", "macos_arm64"),
    ]
