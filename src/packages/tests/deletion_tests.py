import json

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import ProtectedError
from django.test import RequestFactory
from django.urls import reverse

from git_db.db import GitDatabase, get_git_database
from git_db.tests.git_db_tests import commits, show
from packages.deletion import (
    delete_project_permanently,
    delete_release_permanently,
    gone_marker,
)
from packages.models import Asset, DeletedRelease, Project, Release, ReservedName
from packages.services import create_draft, finalise
from packages.tests.services_tests import quark, upload
from packages.views import serve_artifact
from users.tests.factories import UserFactory

ARTIFACT = "dl/foo-bar/1.0.0/foo-bar-1.0.0-quark-any-any.tar.gz"


def artifact_files() -> list[str]:
    """The stored artifacts (without the gone markers, which have the same names)."""
    root = settings.MAGNET_ARTIFACTS_ROOT
    return sorted(str(p.relative_to(root)) for p in (root / "dl").rglob("*.tar.gz"))


@pytest.fixture
def index() -> GitDatabase:
    call_command("init_index")
    return get_git_database()


@pytest.fixture
def owner():
    return UserFactory.create()


def publish(owner, version, name="foo-bar", final=True, **kwargs) -> Release:
    release, _ = create_draft(owner, name, version)
    upload(owner, release, quark(name=name, version=version, **kwargs))
    return finalise(owner, release) if final else release


def index_versions(index: GitDatabase, name="foo-bar") -> list[str]:
    try:
        content = show(index.repo_path, f"{name[0]}/{name}.jsonl")
    except Exception:
        return []
    return [json.loads(line)["version"] for line in content.splitlines()]


@pytest.mark.django_db
def test_delete_a_version(index, owner, django_capture_on_commit_callbacks):
    first = publish(owner, "1.0.0")
    publish(owner, "1.1.0")

    with django_capture_on_commit_callbacks(execute=True):
        delete_release_permanently(first, "leaked secret")

    assert index_versions(index) == ["1.1.0"]
    assert commits(index.repo_path)[0] == "delete foo-bar 1.0.0"
    assert ARTIFACT not in artifact_files()
    assert gone_marker(ARTIFACT).is_file()
    tombstone = DeletedRelease.objects.get()
    assert (tombstone.normalized_name, tombstone.version, tombstone.reason) == (
        "foo-bar",
        "1.0.0",
        "leaked secret",
    )
    assert list(Release.objects.values_list("version", flat=True)) == ["1.1.0"]
    with pytest.raises(ValidationError, match="was deleted"):
        create_draft(owner, "foo-bar", "1.0.0")

    # a rebuild doesn't bring it back
    before = commits(index.repo_path)
    call_command("rebuild_index")
    assert commits(index.repo_path) == before


@pytest.mark.django_db
def test_delete_the_last_version(index, owner, django_capture_on_commit_callbacks):
    release = publish(owner, "1.0.0")

    with django_capture_on_commit_callbacks(execute=True):
        delete_release_permanently(release, "malware")

    assert index_versions(index) == []
    # the package and its name stay, an admin may delete it too
    assert Project.objects.get().normalized_name == "foo-bar"


@pytest.mark.django_db
def test_delete_a_draft(index, owner, django_capture_on_commit_callbacks):
    draft = publish(owner, "1.0.0", final=False)
    before = commits(index.repo_path)

    with django_capture_on_commit_callbacks(execute=True):
        delete_release_permanently(draft, "malware")

    assert commits(index.repo_path) == before
    assert gone_marker(ARTIFACT).is_file() and artifact_files() == []
    assert DeletedRelease.objects.filter(version="1.0.0").exists()


@pytest.mark.django_db
def test_failed_index_write_changes_nothing(
    index, owner, monkeypatch, django_capture_on_commit_callbacks
):
    release = publish(owner, "1.0.0")

    def fail(self, name, version):
        raise RuntimeError("push failed")

    monkeypatch.setattr(GitDatabase, "remove_release", fail)
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
    ):
        delete_release_permanently(release, "malware")

    assert Release.objects.exists() and Asset.objects.exists()
    assert not DeletedRelease.objects.exists()
    assert artifact_files() == [ARTIFACT] and not gone_marker(ARTIFACT).exists()
    assert index_versions(index) == ["1.0.0"]


@pytest.mark.django_db
def test_delete_a_package(index, owner, django_capture_on_commit_callbacks):
    publish(owner, "1.0.0")
    publish(owner, "1.1.0", final=False)
    project = Project.objects.get()

    with django_capture_on_commit_callbacks(execute=True):
        delete_project_permanently(project, "malware")

    assert index_versions(index) == []
    assert commits(index.repo_path)[0] == "delete foo-bar"
    assert not Project.objects.exists() and not Asset.objects.exists()
    assert artifact_files() == []
    assert gone_marker(ARTIFACT).is_file()
    assert sorted(DeletedRelease.objects.values_list("version", flat=True)) == [
        "1.0.0",
        "1.1.0",
    ]
    reservation = ReservedName.objects.get(normalized_name="foo-bar")
    assert (reservation.reason, reservation.note) == ("deleted", "malware")
    # also for its former owner
    with pytest.raises(ValidationError, match="reserved"):
        create_draft(owner, "Foo_Bar", "2.0.0")


@pytest.mark.django_db
def test_delete_a_package_with_a_legacy_name(index, owner):
    ReservedName.objects.filter(normalized_name="wslib").update(claimed_by=owner)
    publish(owner, "1.0.0", name="wslib")

    delete_project_permanently(Project.objects.get(), "malware")

    reservation = ReservedName.objects.get(normalized_name="wslib")
    assert (reservation.reason, reservation.claimed_by) == ("deleted", None)


@pytest.mark.django_db
def test_package_with_dependents_cant_be_deleted(index, owner):
    publish(owner, "1.0.0")
    publish(owner, "0.1.0", name="baz", dependencies='foo-bar = "^1"')
    project = Project.objects.get(normalized_name="foo-bar")

    with pytest.raises(ValidationError, match="other versions depend on it: baz 0.1.0"):
        delete_project_permanently(project, "malware")
    # also not by accident, e.g. via the shell
    with pytest.raises(ProtectedError):
        project.delete()
    assert index_versions(index) == ["1.0.0"]


@pytest.mark.django_db
def test_debug_downloads_answer_410_for_deleted_artifacts(index, owner):
    publish(owner, "1.0.0")
    path = ARTIFACT.removeprefix("dl/")
    request = RequestFactory().get(f"/dl/{path}")

    assert serve_artifact(request, path).status_code == 200
    gone_marker(ARTIFACT).parent.mkdir(parents=True)
    gone_marker(ARTIFACT).touch()
    response = serve_artifact(request, path)
    assert response.status_code == 410
    # CORS like nginx, also on 410/404, for the browser client
    assert response["Access-Control-Allow-Origin"] == "*"
    response = serve_artifact(request, "unknown/1.0.0/unknown.tar.gz")
    assert response.status_code == 404
    assert response["Access-Control-Allow-Origin"] == "*"


@pytest.mark.django_db
def test_admin_deletes_after_confirmation(
    admin_client, index, owner, django_capture_on_commit_callbacks
):
    release = publish(owner, "1.0.0")
    url = reverse("admin:packages_release_changelist")
    data = {"action": "delete_permanently", "_selected_action": [release.pk]}

    page = admin_client.post(url, data)
    assert page.status_code == 200
    assert "can never be published again" in page.text
    assert Release.objects.exists()

    # the reason is required
    page = admin_client.post(url, {**data, "confirm": "yes", "reason": ""})
    assert "This field is required" in page.text
    assert Release.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(
            url, {**data, "confirm": "yes", "reason": "malware"}, follow=True
        )
    assert "deleted permanently" in response.text
    assert not Release.objects.exists()
    assert DeletedRelease.objects.get().reason == "malware"


@pytest.mark.django_db
def test_admin_reports_dependents(admin_client, index, owner):
    publish(owner, "1.0.0")
    publish(owner, "0.1.0", name="baz", dependencies='foo-bar = "^1"')
    project = Project.objects.get(normalized_name="foo-bar")

    response = admin_client.post(
        reverse("admin:packages_project_changelist"),
        {
            "action": "delete_permanently",
            "_selected_action": [project.pk],
            "confirm": "yes",
            "reason": "malware",
        },
        follow=True,
    )

    assert "other versions depend on it: baz 0.1.0" in response.text
    assert Project.objects.filter(pk=project.pk).exists()


@pytest.mark.django_db
def test_only_superusers_delete_permanently(client):
    staff = UserFactory.create(is_staff=True)
    staff.user_permissions.set(
        Permission.objects.filter(content_type__app_label="packages")
    )
    client.force_login(staff)

    for model in ["release", "project"]:
        page = client.get(reverse(f"admin:packages_{model}_changelist")).text
        assert "delete_permanently" not in page
        # Django's delete would bypass the index, the tombstones and the markers
        assert "delete_selected" not in page
