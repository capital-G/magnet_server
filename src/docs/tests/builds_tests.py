"""Docs builds (`docs.tasks`) with a fake runner instead of containers."""

import json
from collections.abc import Callable

import pytest

from docs import publish
from docs.models import DocBuild
from docs.runner import Container, Result
from docs.tasks import build_docs, request_build, shorten_log
from packages.tests.factories import ProjectFactory, ReleaseFactory


class FakeRunner:
    """Does what the builder image would: `install` creates the package's folder,
    `render` writes a page and docmap.json. `results` replaces the result of a step."""

    def __init__(self, page: str = "<p>docs</p>") -> None:
        self.page = page
        self.containers: list[Container] = []
        self.results: dict[str, Result] = {}

    def run(self, container: Container, timeout: float) -> Result:
        self.containers.append(container)
        step = container.name.rsplit("-", 1)[1]
        if step in self.results:
            return self.results[step]
        mounts = {mount.target: mount.source for mount in container.mounts}
        if step == "install":
            (mounts["/work"] / "magnet_packages" / "foo").mkdir(parents=True)
        else:
            out = mounts["/out"]
            (out / "Classes").mkdir()
            (out / "Classes" / "Foo.html").write_text(self.page)
            (out / "docmap.json").write_text(
                json.dumps({"sclang_version": "3.14.1", "documents": {}, "errors": []})
            )
        return Result(0, f"{step} output\n")


@pytest.fixture
def runner(settings, monkeypatch, tmp_path) -> FakeRunner:
    settings.MAGNET_DOCS_BUILDS = True
    settings.MAGNET_DOCS_WORK_DIR = tmp_path / "docs_work"
    settings.MAGNET_DOCS_GIT_INDEX_URL = "git://index.example/magnet_index.git"
    fake = FakeRunner()
    monkeypatch.setattr("docs.tasks.get_runner", lambda: fake)
    return fake


def version(number: str = "1.0.0", **kwargs):
    project = kwargs.pop("project", None) or ProjectFactory.create(name="Foo")
    return ReleaseFactory.create(project=project, version=number, final=True, **kwargs)


def build(release) -> DocBuild:
    built = DocBuild.objects.create(release=release)
    build_docs(built)
    built.refresh_from_db()
    return built


def page() -> str:
    return (publish.docs_root() / "foo" / "Classes" / "Foo.html").read_text()


@pytest.mark.django_db
def test_a_build_installs_renders_and_publishes(
    runner, settings, django_capture_on_commit_callbacks
):
    release = version()

    with django_capture_on_commit_callbacks(execute=True):
        queued = request_build(release)

    assert queued is not None
    queued.refresh_from_db()
    assert queued.status == DocBuild.Status.built
    assert (queued.sclang_version, queued.sclang_mismatch, queued.error) == (
        "3.14.1",
        False,
        "",
    )
    assert queued.log == "$ install\ninstall output\n$ render\nrender output\n"
    assert queued.started_at <= queued.finished_at
    assert page() == "<p>docs</p>"
    assert publish.published_dir("foo") == publish.build_dir("foo", queued.pk)
    # the work dir is gone
    assert not (settings.MAGNET_DOCS_WORK_DIR / str(queued.pk)).exists()

    install, render = runner.containers
    work = settings.MAGNET_DOCS_WORK_DIR / str(queued.pk)
    assert install.command == ["/usr/local/bin/magnet", "install"]
    # no sclang on the PATH, the index of the settings
    assert install.env == {
        "PATH": "/usr/bin:/bin",
        "MAGNET_GIT_INDEX_URL": "git://index.example/magnet_index.git",
    }
    assert [(m.source, m.target, m.read_only) for m in install.mounts] == [
        (work, "/work", False)
    ]
    assert render.network == "none"
    assert render.command[-3:] == ["/packages", "foo", "/out"]
    assert [(m.source, m.target, m.read_only) for m in render.mounts] == [
        (work / "magnet_packages", "/packages", True),
        (work / "out", "/out", False),
    ]


@pytest.mark.django_db
def test_the_temporary_project_pins_the_version(runner):
    manifests = []

    def install(container: Container, timeout: float) -> Result:
        work = container.mounts[0].source
        manifests.append((work / "magnet.toml").read_text())
        return Result(1, "")

    runner.run = wrap(runner.run, install, "install")
    build(version("1.2.3", project=ProjectFactory.create(name="Foo_Bar")))

    assert manifests == [
        "magnet-version = 1\n\n"
        '[package]\nname = "magnet-docs-build"\nversion = "0.0.0"\n\n'
        '[dependencies]\n"foo-bar" = "=1.2.3"\n'
    ]


def wrap(run: Callable, replacement: Callable, step: str) -> Callable:
    """`run`, except for the container of `step`."""

    def wrapped(container: Container, timeout: float) -> Result:
        if container.name.endswith(f"-{step}"):
            return replacement(container, timeout)
        return run(container, timeout)

    return wrapped


@pytest.mark.django_db
def test_a_failed_build_keeps_the_previous_docs(runner):
    release = version()
    first = build(release)
    runner.results["render"] = Result(1, "ERROR: Library has not been compiled\n")

    failed = build(release)

    assert failed.status == DocBuild.Status.failed
    assert failed.error == "render failed (exit code 1)"
    assert "ERROR: Library has not been compiled" in failed.log
    assert publish.published_dir("foo") == publish.build_dir("foo", first.pk)
    assert page() == "<p>docs</p>"
    assert not publish.build_dir("foo", failed.pk).exists()


@pytest.mark.django_db
def test_a_failed_install_doesnt_render(runner):
    runner.results["install"] = Result(1, "error: no such version\n")

    failed = build(version())

    assert failed.error == "install failed (exit code 1)"
    assert [c.name.rsplit("-", 1)[1] for c in runner.containers] == ["install"]
    assert publish.published_dir("foo") is None


@pytest.mark.django_db
def test_a_build_that_times_out_fails(runner, settings):
    settings.MAGNET_DOCS_TIMEOUT = 42
    runner.results["render"] = Result(None, "compiling class library...\n")

    failed = build(version())

    assert failed.status == DocBuild.Status.failed
    assert failed.error == "render timed out after 42 seconds"
    assert "compiling class library" in failed.log


@pytest.mark.django_db
def test_a_render_without_docmap_fails(runner):
    def render(container: Container, timeout: float) -> Result:
        return Result(0, "")

    runner.run = wrap(runner.run, render, "render")

    failed = build(version())

    assert failed.error == "the render step wrote no docmap.json"
    assert publish.published_dir("foo") is None


@pytest.mark.django_db
def test_a_runner_that_cant_start_fails_the_build(runner):
    def missing(container: Container, timeout: float) -> Result:
        raise FileNotFoundError("No such file or directory: 'podman'")

    runner.run = missing

    failed = build(version())

    assert failed.status == DocBuild.Status.failed
    assert failed.error.startswith("the install container could not be started")


@pytest.mark.django_db
def test_versions_that_arent_current_anymore_are_skipped(runner):
    old = version("1.0.0")
    version("1.1.0", project=old.project)

    skipped = build(old)

    assert skipped.status == DocBuild.Status.skipped
    assert "isn't the current version" in skipped.log
    assert runner.containers == []


@pytest.mark.django_db
def test_a_new_build_replaces_the_docs_atomically(runner):
    release = version()
    first = build(release)
    runner.page = "<p>new docs</p>"

    second = build(release)

    assert page() == "<p>new docs</p>"
    assert (publish.docs_root() / "foo").is_symlink()
    assert publish.published_dir("foo") == publish.build_dir("foo", second.pk)
    # the old build's folder is gone, no temporary links are left
    assert not publish.build_dir("foo", first.pk).exists()
    assert sorted(p.name for p in publish.docs_root().iterdir()) == [".builds", "foo"]


@pytest.mark.django_db
def test_unpublish(runner):
    first = build(version())

    publish.unpublish("foo")

    assert not (publish.docs_root() / "foo").exists()
    assert not publish.build_dir("foo", first.pk).exists()


@pytest.mark.django_db
def test_a_package_has_one_queued_build(runner, django_capture_on_commit_callbacks):
    old = version("1.0.0")
    new = version("1.1.0", project=old.project)
    other = version("1.0.0", project=ProjectFactory.create(name="Other"))

    with django_capture_on_commit_callbacks() as callbacks:
        first = request_build(old)
        second = request_build(new)
        request_build(other)

    assert first == second
    assert second is not None
    second.refresh_from_db()
    assert second.release == new
    assert DocBuild.objects.count() == 2
    # one task per queued build
    assert len(callbacks) == 2


@pytest.mark.django_db
def test_builds_are_off_by_default(settings, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks() as callbacks:
        assert request_build(version()) is None
    assert callbacks == []
    assert not DocBuild.objects.exists()


@pytest.mark.django_db
def test_an_unmet_sclang_requirement_still_builds(runner):
    built = build(version(sclang=">=3.15"))

    assert built.status == DocBuild.Status.built
    assert built.sclang_mismatch


def test_long_logs_lose_their_middle():
    log = "a" * 10 + "b" * 100 + "c" * 10

    shortened = shorten_log(log, limit=20)

    assert shortened == "a" * 10 + "\n[… 100 characters …]\n" + "c" * 10
    assert shorten_log("short", limit=20) == "short"
