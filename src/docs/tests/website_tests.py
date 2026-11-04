"""The docs on the website: the sidebar of the project page, the builds page and the
owners' rebuild button."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains, assertNotContains

from docs import publish
from docs.models import DocBuild
from packages.tests.factories import (
    ProjectFactory,
    ProjectMemberFactory,
    ReleaseFactory,
)
from users.tests.factories import UserFactory

DOCS_URL = "http://docs.test/foo/"


@pytest.fixture
def owner():
    return UserFactory.create()


@pytest.fixture
def maintainer(project):
    return ProjectMemberFactory.create(project=project).user


@pytest.fixture
def project(owner, settings):
    settings.MAGNET_DOCS_BASE_URL = "http://docs.test/"
    return ProjectFactory.create(name="Foo", owner=owner)


@pytest.fixture
def release(project):
    return ReleaseFactory.create(
        project=project, version="1.0.0", final=True, sclang=">=3.15"
    )


def build(release, status: str, **kwargs) -> DocBuild:
    return DocBuild.objects.create(release=release, status=status, **kwargs)


def published() -> None:
    folder = publish.build_dir("foo", 1)
    folder.mkdir(parents=True)
    publish.publish("foo", folder)


def page(client):
    return client.get(reverse("project-detail", args=["foo"]))


def builds_page(client, **query):
    return client.get(reverse("docs-builds", args=["foo"]), query)


@pytest.mark.django_db
def test_everyone_sees_published_docs(client, release):
    build(release, DocBuild.Status.built, log="secret log")
    published()

    response = page(client)

    assertContains(response, f'href="{DOCS_URL}"')
    assertContains(response, "Docs of 1.0.0")
    # the status is for the team
    assertNotContains(response, reverse("docs-builds", args=["foo"]))


@pytest.mark.django_db
def test_without_docs_the_box_says_so(client, release):
    build(release, DocBuild.Status.failed, error="render failed")

    response = page(client)

    assertContains(response, "No docs published yet.")
    assertNotContains(response, DOCS_URL)
    assertNotContains(response, "render failed")
    assertNotContains(response, reverse("docs-builds", args=["foo"]))


@pytest.mark.django_db
def test_the_team_finds_the_builds_page_before_any_build(client, maintainer, release):
    client.force_login(maintainer)

    response = page(client)

    assertContains(response, "No docs published yet.")
    assertContains(response, reverse("docs-builds", args=["foo"]))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status, shown",
    [
        (DocBuild.Status.queued, "queued"),
        (DocBuild.Status.building, "building"),
        (DocBuild.Status.built, "built"),
        (DocBuild.Status.failed, "failed"),
    ],
)
def test_the_team_sees_the_latest_build(client, maintainer, release, status, shown):
    build(release, status, error="install failed (exit code 1)")
    client.force_login(maintainer)

    response = page(client)

    assertContains(response, f">{shown}</span>")
    assertContains(response, "install failed (exit code 1)")
    assertContains(response, reverse("docs-builds", args=["foo"]))


@pytest.mark.django_db
def test_the_team_sees_an_sclang_mismatch(client, maintainer, release):
    build(release, DocBuild.Status.built, sclang_version="3.14.1", sclang_mismatch=True)
    published()
    client.force_login(maintainer)

    response = page(client)

    assertContains(response, "Docs of 1.0.0")
    assertContains(
        response, "SuperCollider 3.14.1 isn't in its sclang range (&gt;=3.15)"
    )


@pytest.mark.django_db
def test_builds_page_is_for_the_team(client, release):
    assert builds_page(client).status_code == 302
    client.force_login(UserFactory.create())
    assert builds_page(client).status_code == 403


@pytest.mark.django_db
def test_builds_page_shows_the_builds_and_a_log(client, maintainer, release):
    old = build(release, DocBuild.Status.built, log="old log")
    build(release, DocBuild.Status.failed, error="render failed", log="new log")
    client.force_login(maintainer)

    response = builds_page(client)

    assertContains(response, "render failed")
    assertContains(response, "new log")
    assertNotContains(response, "old log")
    assertContains(response, f"?build={old.pk}")
    # maintainers don't rebuild
    assertNotContains(response, reverse("docs-rebuild", args=["foo"]))

    assertContains(builds_page(client, build=old.pk), "old log")
    other = build(ReleaseFactory.create(final=True), DocBuild.Status.built)
    assert builds_page(client, build=other.pk).status_code == 404


@pytest.mark.django_db
def test_owners_see_the_rebuild_button(client, owner, release, settings):
    client.force_login(owner)
    assertNotContains(builds_page(client), reverse("docs-rebuild", args=["foo"]))

    settings.MAGNET_DOCS_BUILDS = True
    assertContains(builds_page(client), reverse("docs-rebuild", args=["foo"]))


def rebuild(client):
    return client.post(reverse("docs-rebuild", args=["foo"]), follow=True)


@pytest.mark.django_db
def test_owners_rebuild_the_docs(client, owner, release, settings):
    settings.MAGNET_DOCS_BUILDS = True
    old = build(release, DocBuild.Status.built)
    DocBuild.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(minutes=11)
    )
    client.force_login(owner)

    response = rebuild(client)

    assertContains(response, "Queued the docs of 1.0.0.")
    assert DocBuild.objects.filter(status=DocBuild.Status.queued).count() == 1
    # not while one is queued
    assertContains(rebuild(client), "A build is queued or running already.")
    assert DocBuild.objects.count() == 2


@pytest.mark.django_db
def test_rebuilds_are_rate_limited(client, owner, release, settings):
    settings.MAGNET_DOCS_BUILDS = True
    build(release, DocBuild.Status.failed)
    client.force_login(owner)

    response = rebuild(client)

    assertContains(response, "The last build is less than 10 minutes old")
    assert DocBuild.objects.count() == 1


@pytest.mark.django_db
def test_rebuild_without_docs_builds(client, owner, release):
    client.force_login(owner)

    assertContains(rebuild(client), "Docs builds are off.")


@pytest.mark.django_db
def test_maintainers_cant_rebuild(client, maintainer, release, settings):
    settings.MAGNET_DOCS_BUILDS = True
    client.force_login(maintainer)

    assert rebuild(client).status_code == 403
    assert not DocBuild.objects.exists()
