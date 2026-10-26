import hashlib
import json
import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from django.http import FileResponse
from django.urls import reverse

from git_db.db import (
    GIT_ENV,
    GitDatabase,
    IndexNotInitialized,
    NoArtifacts,
    get_git_database,
)
from git_db.files import DuplicateIndexEntry, IndexEntryMissing, KindMismatch
from git_db.mirror import mirror_root
from git_db.models import GitArtifactModel, GitIndexConfigModel, GitReleaseModel
from packages.models import Project, Release
from packages.tests.factories import (
    AssetFactory,
    DependencyFactory,
    ProjectFactory,
    ReleaseFactory,
    quark_tar_gz,
)
from packages.views import serve_index_mirror

SHA = hashlib.sha256(quark_tar_gz()).hexdigest()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "--git-dir", str(repo), *args], text=True, env=GIT_ENV
    )


def show(repo: Path, path: str) -> str:
    return git(repo, "show", f"main:{path}")


def commits(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s", "main").splitlines()


@pytest.fixture
def index() -> GitDatabase:
    git_database = get_git_database()
    git_database.init_index()
    return git_database


def published_release(**kwargs) -> Release:
    """A release with a quark artifact, ready to be published."""
    asset = AssetFactory.create(**{f"release__{k}": v for k, v in kwargs.items()})
    return asset.release


@pytest.mark.django_db
def test_golden_index(index: GitDatabase):
    beta = ProjectFactory.create(name="Beta_Core")
    alpha_1_0 = published_release(project__name="Alpha", version="1.0.0")
    alpha_1_1 = published_release(project=alpha_1_0.project, version="1.1.0")
    DependencyFactory.create(release=alpha_1_1, dependency=beta, constraint="^1.0.0")

    index.commit_release(alpha_1_0)
    index.commit_release(alpha_1_1)

    assert show(index.repo_path, "config.json") == (
        '{"index-format": 1, "dl": "http://localhost:8000/dl/{name}/{version}/'
        '{name}-{version}-{kind}-{abi}-{platform}.tar.gz", '
        '"http": "http://localhost:8000/index/", "api": "http://localhost:8000/"}\n'
    )
    assert show(index.repo_path, "a/alpha.jsonl") == (
        '{"name": "alpha", "version": "1.0.0", "kind": "quark", "dependencies": {}, '
        '"yanked": false, "artifacts": [{"abi": "any", "platform": "any", '
        f'"sha256": "{SHA}"}}]}}\n'
        '{"name": "alpha", "version": "1.1.0", "kind": "quark", '
        '"dependencies": {"beta-core": "^1.0.0"}, '
        '"yanked": false, "artifacts": [{"abi": "any", "platform": "any", '
        f'"sha256": "{SHA}"}}]}}\n'
    )
    assert commits(index.repo_path) == [
        "publish alpha 1.1.0",
        "publish alpha 1.0.0",
        "update index config",
    ]


def test_line_round_trips_cli_fixture():
    # copied from ../magnet_cli/tests/fixtures/index/a/alpha.jsonl
    line = (
        '{"name": "alpha", "version": "1.1.0", "kind": "quark", '
        '"dependencies": {"beta": "^1.0.0"}, "yanked": false, '
        '"artifacts": [{"abi": "any", "platform": "any", '
        '"sha256": "de5baa9f2f62954446e1c2ebd5fb5a4959de4f40214ab05c8ee73a274558ade6"}]}'
    )
    assert GitReleaseModel(**json.loads(line)).to_json() == line


def test_config_with_http_mirror():
    config = GitIndexConfigModel(
        dl="https://example.org/dl/{name}.tar.gz",
        http="https://example.org/index/",
        api="https://example.org/",
    )
    assert config.to_json() == (
        '{"index-format": 1, "dl": "https://example.org/dl/{name}.tar.gz", '
        '"http": "https://example.org/index/", "api": "https://example.org/"}'
    )


def test_config_without_optional_keys():
    config = GitIndexConfigModel(dl="https://example.org/dl/{name}.tar.gz")
    assert config.to_json() == (
        '{"index-format": 1, "dl": "https://example.org/dl/{name}.tar.gz"}'
    )


def test_line_with_sclang():
    line = GitReleaseModel(
        name="alpha",
        version="1.0.0",
        kind="quark",
        sclang=">=3.13",
        dependencies={},
        artifacts=[GitArtifactModel(abi="any", platform="any", sha256=SHA)],
    )
    expected = (
        '{"name": "alpha", "version": "1.0.0", "kind": "quark", "sclang": ">=3.13", '
        '"dependencies": {}, "yanked": false, "artifacts": [{"abi": "any", '
        f'"platform": "any", "sha256": "{SHA}"}}]}}'
    )
    assert line.to_json() == expected
    assert GitReleaseModel(**json.loads(expected)).to_json() == expected


@pytest.mark.django_db
def test_commit_release_sets_published_at(index: GitDatabase):
    release = published_release()
    assert release.published_at is None

    index.commit_release(release)

    release.refresh_from_db()
    assert release.published_at is not None


@pytest.mark.django_db
def test_commits_use_the_server_identity(index: GitDatabase):
    index.commit_release(published_release())
    identity = "magnet-server <magnet-server@localhost>"
    assert git(index.repo_path, "log", "-1", "--format=%an <%ae>|%cn <%ce>") == (
        f"{identity}|{identity}\n"
    )


@pytest.mark.django_db
def test_release_without_artifacts_is_rejected(index: GitDatabase):
    release = ReleaseFactory.create()
    with pytest.raises(NoArtifacts):
        index.commit_release(release)

    release.refresh_from_db()
    assert release.published_at is None
    assert commits(index.repo_path) == ["update index config"]


@pytest.mark.django_db
def test_duplicate_version_is_rejected(index: GitDatabase):
    release = published_release()
    index.commit_release(release)
    with pytest.raises(DuplicateIndexEntry):
        index.commit_release(release)


@pytest.mark.django_db
def test_kind_is_fixed_by_first_release(index: GitDatabase):
    first = published_release(project__kind=Project.Kind.quark, version="1.0.0")
    index.commit_release(first)

    project = first.project
    project.kind = Project.Kind.lepton
    project.save()
    with pytest.raises(KindMismatch):
        index.commit_release(published_release(project=project, version="2.0.0"))


@pytest.mark.django_db
def test_yank_flips_only_that_release(index: GitDatabase):
    first = published_release(project__name="alpha", version="1.0.0")
    second = published_release(project=first.project, version="1.1.0")
    index.commit_release(first)
    index.commit_release(second)

    first.yank_git_entry()

    lines = show(index.repo_path, "a/alpha.jsonl").splitlines()
    assert [json.loads(line)["yanked"] for line in lines] == [True, False]
    first.refresh_from_db()
    assert first.yanked


@pytest.mark.django_db
def test_yank_of_unpublished_release_is_rejected(index: GitDatabase):
    release = published_release()
    with pytest.raises(IndexEntryMissing):
        release.yank_git_entry()

    release.refresh_from_db()
    assert not release.yanked


@pytest.mark.django_db
def test_artifacts_are_appended(index: GitDatabase):
    source = AssetFactory.create(
        release__project__name="gamma",
        release__project__kind=Project.Kind.lepton,
        release__version="0.3.0",
        abi="source",
    )
    index.commit_release(source.release)
    binary = AssetFactory.create(
        release=source.release, abi="api3", platform="linux_x86_64"
    )

    binary.make_git_entry()

    line = json.loads(show(index.repo_path, "g/gamma.jsonl"))
    assert [(a["abi"], a["platform"]) for a in line["artifacts"]] == [
        ("source", "any"),
        ("api3", "linux_x86_64"),
    ]
    with pytest.raises(DuplicateIndexEntry):
        binary.make_git_entry()


@pytest.mark.django_db
def test_write_without_index_is_rejected():
    with pytest.raises(IndexNotInitialized):
        get_git_database().commit_release(published_release())


def test_init_index_is_idempotent_and_updates_the_config(settings):
    git_database = get_git_database()
    git_database.init_index()
    git_database.init_index()
    assert commits(git_database.repo_path) == ["update index config"]
    assert "magnet index" in show(git_database.repo_path, "README.md")

    settings.MAGNET_DOWNLOAD_BASE_URL = "http://localhost:8080"
    git_database.init_index()

    assert len(commits(git_database.repo_path)) == 2
    config = json.loads(show(git_database.repo_path, "config.json"))
    assert config["dl"].startswith("http://localhost:8080/dl/{name}/")
    assert config["http"] == "http://localhost:8080/index/"

    settings.MAGNET_API_BASE_URL = "http://localhost:9000/"
    git_database.init_index()

    assert len(commits(git_database.repo_path)) == 3
    config = json.loads(show(git_database.repo_path, "config.json"))
    assert config["api"] == "http://localhost:9000/"


def test_init_index_keeps_an_existing_repository(tmp_path: Path):
    repo = tmp_path / "existing.git"
    work = tmp_path / "work"

    def run(*args: str, cwd: Path = tmp_path):
        subprocess.run(["git", *args], cwd=cwd, check=True, env=GIT_ENV)

    run("init", "-q", "--bare", "-b", "main", str(repo))
    run("clone", "-q", str(repo), str(work))
    (work / "README.md").write_text("# Hello\n")
    run("add", "-A", cwd=work)
    run("commit", "-qm", "init commit", cwd=work)
    run("push", "-q", "origin", "HEAD:main", cwd=work)

    GitDatabase(repo).init_index()

    assert commits(repo) == ["update index config", "init commit"]
    assert show(repo, "README.md") == "# Hello\n"


def test_concurrent_writes_all_end_up_in_the_index(index: GitDatabase):
    names = [f"package-{n}" for n in range(8)]
    start = threading.Barrier(len(names))
    errors: list[Exception] = []

    def publish(name: str):
        line = GitReleaseModel(
            name=name,
            version="1.0.0",
            kind="quark",
            dependencies={},
            artifacts=[GitArtifactModel(abi="any", platform="any", sha256=SHA)],
        )
        start.wait()
        try:
            index.add_release(line)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=publish, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    published = commits(index.repo_path)[:-1]
    assert sorted(published) == [f"publish {name} 1.0.0" for name in names]
    for name in names:
        assert json.loads(show(index.repo_path, f"p/{name}.jsonl"))["name"] == name


@pytest.mark.django_db
def test_admin_reports_index_errors(admin_client, index: GitDatabase):
    release = ReleaseFactory.create()  # a draft without artifacts

    response = admin_client.post(
        reverse("admin:packages_release_changelist") + "?state=draft",
        {"action": "add_to_git_index", "_selected_action": [release.pk]},
        follow=True,
    )

    assert response.status_code == 200
    assert "needs its any-any artifact" in response.content.decode()


def mirrored(path: str) -> Path:
    return mirror_root() / path


@pytest.mark.django_db
def test_mirror_equals_the_index(index: GitDatabase):
    index.commit_release(published_release(project__name="Alpha", version="1.0.0"))

    for path in ["config.json", "a/alpha.jsonl"]:
        assert mirrored(path).read_text() == show(index.repo_path, path)
        assert stat.S_IMODE(mirrored(path).stat().st_mode) == 0o644
    assert not mirrored("README.md").exists()
    assert sorted(p.name for p in mirror_root().rglob("*")) == [
        "a",
        "alpha.jsonl",
        "config.json",
    ]


@pytest.mark.django_db
def test_mirror_rewrites_only_changed_files(index: GitDatabase):
    alpha = published_release(project__name="Alpha", version="1.0.0")
    index.commit_release(alpha)
    os.utime(mirrored("a/alpha.jsonl"), (0, 0))
    os.utime(mirrored("config.json"), (0, 0))

    index.commit_release(published_release(project__name="Beta", version="1.0.0"))
    assert mirrored("a/alpha.jsonl").stat().st_mtime == 0
    assert mirrored("b/beta.jsonl").read_text() == show(index.repo_path, "b/beta.jsonl")

    index.yank_release(alpha)
    assert mirrored("a/alpha.jsonl").read_text() == show(
        index.repo_path, "a/alpha.jsonl"
    )
    assert '"yanked": true' in mirrored("a/alpha.jsonl").read_text()
    assert mirrored("config.json").stat().st_mtime == 0


@pytest.mark.django_db
def test_mirror_removes_deleted_packages(index: GitDatabase):
    alpha = published_release(project__name="Alpha", version="1.0.0")
    index.commit_release(alpha)
    index.commit_release(published_release(project__name="Beta", version="1.0.0"))

    index.remove_package("alpha")

    assert not mirrored("a/alpha.jsonl").exists()
    assert not mirrored("a").exists()
    assert mirrored("b/beta.jsonl").exists()


@pytest.mark.django_db
def test_failed_export_is_repaired_by_the_next_one(
    index: GitDatabase, monkeypatch, caplog
):
    def fail(checkout, target):
        raise OSError("disk full")

    monkeypatch.setattr("git_db.db.export_index", fail)
    alpha = published_release(project__name="Alpha", version="1.0.0")
    # the push happened, so the publish succeeds
    index.commit_release(alpha)
    assert "publish alpha 1.0.0" in commits(index.repo_path)
    assert alpha.published_at
    assert "Export of the index to the HTTP mirror failed" in caplog.text
    assert not mirrored("a/alpha.jsonl").exists()

    monkeypatch.undo()
    # without a change to the index, e.g. `rebuild_index`
    index.rebuild()
    assert mirrored("a/alpha.jsonl").read_text() == show(
        index.repo_path, "a/alpha.jsonl"
    )


@pytest.mark.django_db
def test_mirror_for_runserver(index: GitDatabase, rf):
    index.commit_release(published_release(project__name="Alpha", version="1.0.0"))

    response = serve_index_mirror(rf.get("/index/a/alpha.jsonl"), "a/alpha.jsonl")
    assert response.status_code == 200
    assert isinstance(response, FileResponse)
    content = b"".join(response.streaming_content)  # type: ignore[arg-type]
    assert content == mirrored("a/alpha.jsonl").read_bytes()
    assert response["Content-Type"] == "application/json"
    assert response["Cache-Control"] == "no-cache"
    assert response["Access-Control-Allow-Origin"] == "*"

    response = serve_index_mirror(rf.get("/index/u/unknown.jsonl"), "u/unknown.jsonl")
    assert response.status_code == 404
    assert response["Access-Control-Allow-Origin"] == "*"
