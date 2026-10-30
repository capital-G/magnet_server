import hashlib
import json
import os
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from git_db.db import GitDatabase, get_git_database
from git_db.tests.git_db_tests import commits, show
from packages.models import Project, Release, artifact_filename
from packages.tests.cli import fixtures_have_kinds
from packages.tests.factories import quark_tar_gz
from packages.validators import normalize_name
from users.tests.factories import UserFactory

CLI_FIXTURES = Path(
    os.environ.get(
        "MAGNET_CLI_FIXTURES",
        Path(__file__).resolve().parents[4] / "magnet_cli" / "tests" / "fixtures",
    )
)


@pytest.fixture
def index() -> GitDatabase:
    call_command("init_index")
    return get_git_database()


def index_lines(index: GitDatabase, name: str) -> list[dict]:
    path = f"{name[0]}/{name}.jsonl"
    return [json.loads(line) for line in show(index.repo_path, path).splitlines()]


def artifact_files() -> list[str]:
    root = settings.MAGNET_ARTIFACTS_ROOT
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.tar.gz"))


def publish(tmp_path: Path, *args: str, content: bytes = b"Foo {}\n") -> None:
    file = tmp_path / "upload.tar.gz"
    file.write_bytes(quark_tar_gz({"Classes/Foo.sc": content}))
    call_command("publish", "--file", str(file), *args)


def test_init_index_command_is_idempotent(index: GitDatabase):
    call_command("init_index")
    assert commits(index.repo_path) == ["update index config"]


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
def test_publish_command(tmp_path: Path, index: GitDatabase):
    publish(
        tmp_path,
        "--name",
        "DDW_Common",
        "--version",
        "0.1.0",
        "--kinds",
        "quark",
        "--kind",
        "quark",
    )
    publish(
        tmp_path,
        *(
            "--name",
            "beta",
            "--version",
            "1.2.0",
            "--kinds",
            "quark",
            "--kind",
            "quark",
        ),
        *("--dependency", "DDW_Common=>=0.1.0"),
        content=b"Beta {}\n",
    )

    [ddw_common] = index_lines(index, "ddw-common")
    [beta] = index_lines(index, "beta")
    assert beta["dependencies"] == {"ddw-common": ">=0.1.0"}
    assert (
        ddw_common["artifacts"][0]["sha256"]
        == hashlib.sha256(quark_tar_gz({"Classes/Foo.sc": b"Foo {}\n"})).hexdigest()
    )
    assert artifact_files() == [
        "dl/beta/1.2.0/beta-1.2.0-quark-any-any.tar.gz",
        "dl/ddw-common/0.1.0/ddw-common-0.1.0-quark-any-any.tar.gz",
    ]
    assert Project.objects.get(normalized_name="ddw-common").name == "DDW_Common"


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
def test_publish_command_with_kinds(tmp_path: Path, index: GitDatabase):
    publish(
        tmp_path,
        *("--name", "alpha", "--version", "1.0.0"),
        "--kinds",
        "lepton,quark",
        "--kind",
        "quark",
    )
    publish(
        tmp_path,
        *("--name", "beta", "--version", "1.0.0"),
        "--kinds",
        "lepton",
        "--kind",
        "source",
    )

    [alpha] = index_lines(index, "alpha")
    assert (alpha["kinds"], alpha["artifacts"][0]["kind"]) == (
        ["quark", "lepton"],
        "quark",
    )
    [beta] = index_lines(index, "beta")
    assert beta["kinds"] == ["lepton"]
    assert [(a["kind"], a["abi"], a["platform"]) for a in beta["artifacts"]] == [
        ("source", "any", "any")
    ]
    assert "dl/beta/1.0.0/beta-1.0.0-source-any-any.tar.gz" in artifact_files()


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user", "index")
@pytest.mark.parametrize(
    "args, error",
    [
        (["--kinds", "quark,gluon", "--kind", "quark"], "unknown kinds gluon"),
        (["--kinds", "quark", "--kind", "gluon"], "invalid choice"),
        (
            [
                "--kinds",
                "quark",
                "--kind",
                "lepton",
                "--abi",
                "api3_0",
                "--platform",
                "macos_arm64",
            ],
            "doesn't declare the kind lepton",
        ),
    ],
)
def test_publish_command_checks_the_kinds(tmp_path: Path, args, error):
    with pytest.raises(CommandError, match=error):
        publish(tmp_path, "--name", "alpha", "--version", "1.0.0", *args)

    assert not Project.objects.exists()
    assert artifact_files() == []


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user", "index")
@pytest.mark.parametrize(
    "args, error",
    [
        (["--dependency", "unknown=^1"], "not a known package"),
        (["--dependency", "alpha=~>1"], "version requirement"),
        (
            ["--abi", "api3", "--platform", "linux_x86_64"],
            "has the abi and platform 'any'",
        ),
    ],
)
def test_publish_command_rolls_back_invalid_releases(tmp_path: Path, args, error):
    with pytest.raises(CommandError, match=error):
        publish(
            tmp_path,
            "--name",
            "alpha",
            "--version",
            "1.0.0",
            "--kinds",
            "quark",
            "--kind",
            "quark",
            *args,
        )

    assert not Project.objects.exists()
    assert artifact_files() == []


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
def test_publish_command_removes_the_artifact_if_the_index_write_fails(tmp_path: Path):
    # no index
    with pytest.raises(CommandError, match="init_index"):
        publish(
            tmp_path,
            "--name",
            "alpha",
            "--version",
            "1.0.0",
            "--kinds",
            "quark",
            "--kind",
            "quark",
        )

    assert not Release.objects.exists()
    assert artifact_files() == []


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_publish_command_needs_an_owner(tmp_path: Path):
    with pytest.raises(CommandError, match="createsuperuser"):
        publish(
            tmp_path,
            "--name",
            "alpha",
            "--version",
            "1.0.0",
            "--kinds",
            "quark",
            "--kind",
            "quark",
        )


def write_fixtures(root: Path, lines: list[dict]) -> None:
    """Fixtures in the layout of the CLI's `tests/fixtures`, with a tarball for each
    artifact (`kind`, `abi`, `platform`) of a line."""
    (root / "dl").mkdir(parents=True)
    for line in lines:
        for artifact in line["artifacts"]:
            tags = (artifact["kind"], artifact["abi"], artifact["platform"])
            data = quark_tar_gz(
                {"Classes/A.sc": f"{line['name']} {line['version']} {tags}".encode()}
            )
            name = f"{line['name']}-{line['version']}-{'-'.join(tags)}.tar.gz"
            (root / "dl" / name).write_bytes(data)
            artifact["sha256"] = hashlib.sha256(data).hexdigest()
        path = root / "index" / line["name"][0] / f"{line['name']}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(line) + "\n")


def artifact(kind="quark", abi="any", platform="any"):
    return {"kind": kind, "abi": abi, "platform": platform}


def line(name, version, dependencies=None, kinds=None, yanked=False, artifacts=None):
    return {
        "name": name,
        "version": version,
        "kinds": kinds or ["quark"],
        "dependencies": dependencies or {},
        "yanked": yanked,
        "artifacts": artifacts or [artifact()],
    }


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
def test_seed_fixtures(tmp_path: Path, index: GitDatabase):
    legacy = {
        "legacy-quark": True,
        "git": {"url": "https://example.com/x.git", "ref": "a" * 40},
    }
    gamma = [
        artifact("source"),
        artifact("quark"),
        artifact("lepton", "api3_0", "macos_arm64"),
    ]
    old_format = line("theta", "1.0.0")
    old_format["kind"] = old_format.pop("kinds")[0]
    write_fixtures(
        tmp_path,
        [
            line("alpha", "1.0.0"),
            line("alpha", "1.1.0", {"Zeta_Lib": "^1.0.0"}, yanked=True),
            line(
                "gamma",
                "0.3.0",
                {"alpha": "^1"},
                kinds=["quark", "lepton"],
                artifacts=gamma,
            ),
            line("zeta-lib", "1.0.0"),
            line("zeta-lib", "1.1.0", {"ddwCommon": legacy}),
            line("eta", "1.0.0", artifacts=[artifact("source"), artifact("quark")]),
            old_format,
        ],
    )
    # a resolver-only fixture with a missing tarball
    (tmp_path / "dl" / "eta-1.0.0-quark-any-any.tar.gz").unlink()

    call_command("seed_cli_fixtures", str(tmp_path))

    alpha = index_lines(index, "alpha")
    assert [(a["version"], a["yanked"]) for a in alpha] == [
        ("1.0.0", False),
        ("1.1.0", True),
    ]
    assert alpha[1]["dependencies"] == {"zeta-lib": "^1.0.0"}
    zeta = index_lines(index, "zeta-lib")
    assert [z["version"] for z in zeta] == ["1.0.0", "1.1.0"]
    assert zeta[1]["dependencies"] == {"ddwcommon": legacy}
    [gamma_line] = index_lines(index, "gamma")
    assert gamma_line["kinds"] == ["quark", "lepton"]
    assert gamma_line["artifacts"] == gamma
    assert [path for path in artifact_files() if "gamma" in path] == [
        "dl/gamma/0.3.0/gamma-0.3.0-lepton-api3_0-macos_arm64.tar.gz",
        "dl/gamma/0.3.0/gamma-0.3.0-quark-any-any.tar.gz",
        "dl/gamma/0.3.0/gamma-0.3.0-source-any-any.tar.gz",
    ]
    assert not Project.objects.filter(normalized_name="eta").exists()
    assert not Project.objects.filter(normalized_name="theta").exists()

    # idempotent, and the index can be regenerated from the database without a diff
    log = commits(index.repo_path)
    call_command("seed_cli_fixtures", str(tmp_path))
    call_command("rebuild_index")
    assert commits(index.repo_path) == log


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user", "index")
def test_seed_fixtures_checks_the_sha256(tmp_path: Path):
    write_fixtures(tmp_path, [line("alpha", "1.0.0")])
    (tmp_path / "dl" / "alpha-1.0.0-quark-any-any.tar.gz").write_bytes(quark_tar_gz())

    with pytest.raises(CommandError, match="doesn't match the sha256"):
        call_command("seed_cli_fixtures", str(tmp_path))


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
def test_rebuild_index_restores_package_files(tmp_path: Path, index: GitDatabase):
    write_fixtures(tmp_path, [line("alpha", "1.0.0"), line("beta", "1.0.0")])
    call_command("seed_cli_fixtures", str(tmp_path))
    expected = show(index.repo_path, "a/alpha.jsonl")

    with index.transaction("break the index") as checkout:
        (checkout / "a" / "alpha.jsonl").unlink()
        (checkout / "x").mkdir()
        (checkout / "x" / "stale.jsonl").write_text("{}\n")

    call_command("rebuild_index")

    assert show(index.repo_path, "a/alpha.jsonl") == expected
    files = index._run("ls-tree", "-r", "--name-only", "main").split()
    assert files == ["README.md", "a/alpha.jsonl", "b/beta.jsonl", "config.json"]


@pytest.mark.django_db
@pytest.mark.usefixtures("admin_user")
@pytest.mark.skipif(not CLI_FIXTURES.is_dir(), reason="needs ../magnet_cli")
@pytest.mark.skipif(
    not fixtures_have_kinds(CLI_FIXTURES),
    reason="the CLI fixtures are in the index format before artifact kinds (C8.7)",
)
def test_seed_real_cli_fixtures(index: GitDatabase):
    call_command("seed_cli_fixtures", str(CLI_FIXTURES))

    seeded = 0
    for path in sorted((CLI_FIXTURES / "index").glob("*/*.jsonl")):
        for raw in path.read_text().splitlines():
            expected = json.loads(raw)
            name, version = expected["name"], expected["version"]
            tarballs = [
                CLI_FIXTURES
                / "dl"
                / artifact_filename(name, version, a["kind"], a["abi"], a["platform"])
                for a in expected["artifacts"]
            ]
            # resolver-only fixtures (e.g. epsilon) have no tarball
            if not tarballs or not all(tarball.is_file() for tarball in tarballs):
                continue
            expected["dependencies"] = {
                normalize_name(name): requirement
                for name, requirement in expected["dependencies"].items()
            }
            actual = [
                line
                for line in index_lines(index, expected["name"])
                if line["version"] == expected["version"]
            ]
            assert actual == [expected]
            seeded += 1
    assert seeded > 0

    log = commits(index.repo_path)
    call_command("rebuild_index")
    assert commits(index.repo_path) == log


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_publish_command_checks_the_owner(tmp_path: Path, admin_user):
    other = UserFactory.create()
    publish(
        tmp_path,
        *(
            "--name",
            "alpha",
            "--version",
            "1.0.0",
            "--kinds",
            "quark",
            "--kind",
            "quark",
        ),
    )

    with pytest.raises(CommandError, match="not an owner or maintainer of alpha"):
        publish(
            tmp_path,
            *(
                "--name",
                "alpha",
                "--version",
                "1.0.1",
                "--kinds",
                "quark",
                "--kind",
                "quark",
            ),
            *("--owner", other.username),
        )
