"""What builds or removes docs (`docs.tasks.update_docs`, `rebuild`), the admin action,
`build_docs` and the DEBUG view."""

import pytest
from django.core.management import CommandError, call_command
from django.http import Http404, HttpResponseNotFound
from django.test import RequestFactory
from django.urls import reverse

from docs import publish
from docs.models import DocBuild
from docs.tests.builds_tests import FakeRunner
from docs.views import serve_docs
from packages.deletion import delete_project_permanently, delete_release_permanently
from packages.models import Release
from packages.services import create_draft, finalise, unyank, yank
from packages.tests.factories import ProjectFactory, ReleaseFactory
from packages.tests.services_tests import upload_quark
from users.tests.factories import UserFactory


@pytest.fixture
def docs_on(settings, monkeypatch, tmp_path) -> FakeRunner:
    settings.MAGNET_DOCS_BUILDS = True
    settings.MAGNET_DOCS_WORK_DIR = tmp_path / "docs_work"
    fake = FakeRunner()
    monkeypatch.setattr("docs.tasks.get_runner", lambda: fake)
    return fake


@pytest.fixture
def index():
    call_command("init_index")


@pytest.fixture
def user():
    return UserFactory.create()


def release(user, version: str) -> Release:
    draft, _ = create_draft(user, "Foo_Bar", version)
    upload_quark(user, draft, version=version)
    return finalise(user, draft)


def queued() -> list[str]:
    """Versions with a queued build."""
    return sorted(
        DocBuild.objects.filter(status=DocBuild.Status.queued).values_list(
            "release__version", flat=True
        )
    )


def built() -> None:
    """The queued builds are done, with their docs published."""
    DocBuild.objects.update(status=DocBuild.Status.built)
    if publish.published_dir("foo-bar") is None:
        published_docs()


def published_docs() -> None:
    """Docs of foo-bar on disk, as a previous build left them."""
    folder = publish.build_dir("foo-bar", 99)
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("docs")
    publish.publish("foo-bar", folder)


@pytest.mark.django_db
def test_finalising_a_new_current_version_builds_its_docs(docs_on, index, user):
    release(user, "1.0.0")
    assert queued() == ["1.0.0"]

    # an older patch release doesn't change the current version
    built()
    release(user, "0.9.1")
    assert queued() == []

    release(user, "1.1.0")
    assert queued() == ["1.1.0"]


@pytest.mark.django_db
def test_a_new_version_replaces_a_queued_build(docs_on, index, user):
    release(user, "1.0.0")
    release(user, "1.1.0")

    assert queued() == ["1.1.0"]
    assert DocBuild.objects.count() == 1


@pytest.mark.django_db
def test_yanking_the_documented_version_builds_the_previous(docs_on, index, user):
    older = release(user, "1.0.0")
    newer = release(user, "1.1.0")
    built()

    yank(user, newer)
    assert queued() == ["1.0.0"]

    # un-yanking makes 1.1.0 current again
    built()
    unyank(user, newer)
    assert queued() == ["1.1.0"]

    # yanking an older version changes nothing
    built()
    yank(user, older)
    assert queued() == []


@pytest.mark.django_db
def test_yanking_the_last_version_removes_the_docs(
    docs_on, index, user, django_capture_on_commit_callbacks
):
    only = release(user, "1.0.0")
    published_docs()

    with django_capture_on_commit_callbacks(execute=True):
        yank(user, only)

    assert publish.published_dir("foo-bar") is None
    assert not (publish.docs_root() / "foo-bar").exists()


@pytest.mark.django_db
def test_unyanking_the_last_version_builds_its_docs_again(
    docs_on, index, user, django_capture_on_commit_callbacks
):
    only = release(user, "1.0.0")
    built()
    with django_capture_on_commit_callbacks(execute=True):
        yank(user, only)

    unyank(user, only)

    assert queued() == ["1.0.0"]


@pytest.mark.django_db
def test_a_failed_build_isnt_retried_by_other_changes(docs_on, index, user):
    current = release(user, "1.1.0")
    DocBuild.objects.update(status=DocBuild.Status.failed)
    older = release(user, "1.0.0")

    yank(user, older)

    assert queued() == []
    assert current.doc_builds.count() == 1


@pytest.mark.django_db
def test_deleting_versions_and_packages(
    docs_on, index, user, django_capture_on_commit_callbacks
):
    older = release(user, "1.0.0")
    newer = release(user, "1.1.0")
    built()

    delete_release_permanently(newer, "broken")
    assert queued() == ["1.0.0"]

    with django_capture_on_commit_callbacks(execute=True):
        delete_project_permanently(older.project, "spam")
    assert publish.published_dir("foo-bar") is None
    assert not DocBuild.objects.exists()


@pytest.mark.django_db
def test_nothing_happens_without_docs_builds(index, user):
    release(user, "1.0.0")

    assert not DocBuild.objects.exists()


@pytest.mark.django_db
def test_admin_action_rebuilds_the_current_version(docs_on, admin_client):
    current = ReleaseFactory.create(final=True, version="1.0.0")
    without_versions = ProjectFactory.create()

    response = admin_client.post(
        reverse("admin:packages_project_changelist"),
        {
            "action": "rebuild_docs",
            "_selected_action": [current.project.pk, without_versions.pk],
        },
        follow=True,
    )

    assert "Queued the docs of 1 of 2 packages." in response.content.decode()
    assert queued() == ["1.0.0"]


@pytest.mark.django_db
def test_build_docs_command(docs_on):
    foo = ReleaseFactory.create(final=True, project__name="Foo_Bar", version="1.0.0")
    ReleaseFactory.create(final=True, project__name="Other", version="2.0.0")

    call_command("build_docs", "FOO_bar")
    assert [b.release for b in DocBuild.objects.all()] == [foo]

    call_command("build_docs", "--all")
    assert queued() == ["1.0.0", "2.0.0"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "args, message",
    [
        ([], "Name packages or pass --all"),
        (["foo", "--all"], "Name packages or pass --all"),
        (["missing"], "Unknown packages: missing"),
    ],
)
def test_build_docs_command_errors(docs_on, args, message):
    with pytest.raises(CommandError, match=message):
        call_command("build_docs", *args)


@pytest.mark.django_db
def test_build_docs_command_without_docs_builds():
    with pytest.raises(CommandError, match="Docs builds are off"):
        call_command("build_docs", "--all")


def get(path: str):
    """`serve_docs`, which runserver serves at /docs/ (DEBUG)."""
    try:
        return serve_docs(RequestFactory().get(f"/docs/{path}"), path)
    except Http404:
        return HttpResponseNotFound()


def test_debug_view_serves_the_docs():
    published_docs()
    (publish.docs_root() / ".builds" / "secret.html").write_text("no")

    page = get("foo-bar/index.html")
    assert page.status_code == 200
    assert b"".join(page.streaming_content) == b"docs"
    assert "script-src 'self' 'unsafe-inline'" in page["Content-Security-Policy"]

    assert get("foo-bar/").status_code == 200
    redirect = get("foo-bar")
    assert (redirect.status_code, redirect["Location"]) == (302, "/docs/foo-bar/")
    assert get(".builds/secret.html").status_code == 404
    assert get("missing/").status_code == 404
