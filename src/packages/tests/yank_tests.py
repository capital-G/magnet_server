import json

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import CommandError, call_command
from django.urls import reverse

from git_db.db import GitDatabase, get_git_database
from git_db.tests.git_db_tests import commits, show
from packages.models import Release
from packages.services import create_draft, finalise, unyank, yank
from packages.tests.services_tests import quark, upload
from users.tests.factories import UserFactory


@pytest.fixture
def index() -> GitDatabase:
    call_command("init_index")
    return get_git_database()


@pytest.fixture
def release(index) -> Release:
    """foo-bar 1.0.0 and 1.1.0, both final."""
    owner = UserFactory.create()
    for version in ["1.0.0", "1.1.0"]:
        draft, _ = create_draft(owner, "foo-bar", version)
        upload(owner, draft, quark(version=version))
        finalise(owner, draft)
    return Release.objects.get(version="1.0.0")


def yanked_in_index(index: GitDatabase) -> list[bool]:
    lines = show(index.repo_path, "f/foo-bar.jsonl").splitlines()
    return [json.loads(line)["yanked"] for line in lines]


@pytest.mark.django_db
def test_yank_and_unyank(index, release):
    owner = release.project.owner

    yank(owner, release, "broken on Windows")
    release.refresh_from_db()
    assert (release.yanked, release.yanked_reason) == (True, "broken on Windows")
    assert yanked_in_index(index) == [True, False]
    # the artifact stays downloadable
    asset = release.assets.get()
    assert asset.file.storage.exists(asset.file.name)

    unyank(owner, release)
    release.refresh_from_db()
    assert (release.yanked, release.yanked_reason) == (False, "")
    assert yanked_in_index(index) == [False, False]
    assert commits(index.repo_path)[:2] == [
        "unyank foo-bar 1.0.0",
        "yank foo-bar 1.0.0",
    ]


@pytest.mark.django_db
def test_yank_twice_changes_nothing(index, release):
    owner = release.project.owner
    yank(owner, release)
    before = commits(index.repo_path)

    yank(owner, release)
    unyank(owner, Release.objects.get(pk=release.pk))
    unyank(owner, Release.objects.get(pk=release.pk))

    assert commits(index.repo_path) == ["unyank foo-bar 1.0.0", *before]


@pytest.mark.django_db
def test_only_owners_and_maintainers_yank(release):
    other = UserFactory.create()
    with pytest.raises(PermissionDenied):
        yank(other, release)

    release.project.maintainers.add(other)
    yank(other, release)
    unyank(other, Release.objects.get(pk=release.pk))


@pytest.mark.django_db
def test_drafts_cant_be_yanked(release):
    draft, _ = create_draft(release.project.owner, "foo-bar", "2.0.0")
    with pytest.raises(ValidationError, match="only final versions can be yanked"):
        yank(release.project.owner, draft)


@pytest.mark.django_db
def test_yank_on_the_website(client, index, release):
    url = reverse("yank", args=["foo-bar", "1.0.0"])
    page = reverse("project-detail", args=["foo-bar"])

    assert client.post(url).status_code == 302  # to the login
    client.force_login(UserFactory.create())
    assert client.post(url).status_code == 403
    assert yanked_in_index(index) == [False, False]

    client.force_login(release.project.owner)
    content = client.get(page).text
    for version in ["1.0.0", "1.1.0"]:
        assert reverse("yank", args=["foo-bar", version]) in content
    response = client.post(
        url, headers={"HX-Request": "true", "HX-Prompt": "broken on Windows"}
    )
    assert response.headers["HX-Redirect"] == f"{page}?version=1.0.0"
    assert yanked_in_index(index) == [True, False]

    # everyone sees the state, owners can undo it
    content = client.get(f"{page}?version=1.0.0").text
    assert "yanked" in content and "Yanked: broken on Windows" in content
    assert reverse("unyank", args=["foo-bar", "1.0.0"]) in content
    client.logout()
    content = client.get(f"{page}?version=1.0.0").text
    assert "Yanked: broken on Windows" in content
    assert reverse("unyank", args=["foo-bar", "1.0.0"]) not in content

    client.force_login(release.project.owner)
    response = client.post(reverse("unyank", args=["foo-bar", "1.0.0"]))
    assert response.status_code == 302
    assert yanked_in_index(index) == [False, False]


@pytest.mark.django_db
def test_yank_without_htmx_takes_the_reason_from_the_form(client, release):
    client.force_login(release.project.owner)
    client.post(reverse("yank", args=["foo-bar", "1.0.0"]), {"reason": " old "})
    assert Release.objects.get(pk=release.pk).yanked_reason == "old"


@pytest.mark.django_db
def test_yank_of_a_draft_on_the_website(client, release):
    owner = release.project.owner
    create_draft(owner, "foo-bar", "2.0.0")
    client.force_login(owner)

    response = client.post(reverse("yank", args=["foo-bar", "2.0.0"]))

    assert response.status_code == 400
    assert "only final versions" in response.text


@pytest.mark.django_db
def test_yank_command(index, release, capsys):
    call_command("yank", "Foo_Bar", "1.0.0", "--reason", "broken")
    assert yanked_in_index(index) == [True, False]
    assert Release.objects.get(pk=release.pk).yanked_reason == "broken"

    call_command("yank", "foo-bar", "1.0.0", "--undo")
    assert yanked_in_index(index) == [False, False]
    assert "Un-yanked foo-bar 1.0.0" in capsys.readouterr().out

    with pytest.raises(CommandError, match="no final version 3.0.0 of foo-bar"):
        call_command("yank", "foo-bar", "3.0.0")


@pytest.mark.django_db
def test_admin_unyank_action(admin_client, index, release):
    release.yank_git_entry()

    admin_client.post(
        reverse("admin:packages_release_changelist"),
        {"action": "unyank_release_in_git_index", "_selected_action": [release.pk]},
    )

    assert not Release.objects.get(pk=release.pk).yanked
    assert yanked_in_index(index) == [False, False]
