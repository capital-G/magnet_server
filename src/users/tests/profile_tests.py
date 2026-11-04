"""Profiles (`/users/<username>`) and the links to them."""

import pytest
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains

from packages.models import ProjectMember
from packages.tests.factories import (
    AssetFactory,
    ProjectFactory,
    ProjectMemberFactory,
    ReleaseFactory,
)
from users.tests.factories import UserFactory
from users.urls import urlpatterns
from users.validators import RESERVED_USERNAMES


@pytest.fixture
def olivia():
    """Owns the public package `alpha` and the draft-only `beta`, maintains `gamma`."""
    user = UserFactory.create(username="olivia", email="olivia@example.com")
    ReleaseFactory.create(
        project__name="alpha", project__owner=user, summary="Alpha!", final=True
    )
    ReleaseFactory.create(project__name="beta", project__owner=user)
    gamma = ReleaseFactory.create(project__name="gamma", final=True).project
    ProjectMemberFactory.create(project=gamma, user=user)
    return user


def profile_url(username):
    return reverse("profile", args=[username])


def link(username):
    return f'<a href="{profile_url(username)}">{username}</a>'


@pytest.mark.django_db
def test_profile_lists_public_packages_with_roles(client, olivia):
    page = client.get(profile_url("olivia"))

    assertContains(page, "olivia")
    assertContains(page, "Member since")
    assertContains(page, "Alpha!")
    assertContains(page, '<span class="tag is-light ml-2">Owner</span>', html=True)
    assertContains(page, '<span class="tag is-light ml-2">Maintainer</span>', html=True)
    assertContains(page, reverse("project-detail", args=["gamma"]))
    # draft-only packages are only visible for their team
    assertNotContains(page, reverse("project-detail", args=["beta"]))
    assertNotContains(page, "@example.com")


@pytest.mark.django_db
def test_team_members_see_draft_only_packages_on_profiles(client, olivia):
    beta = olivia.project_memberships.get(project__name="beta").project
    colleague = ProjectMemberFactory.create(project=beta).user

    for user in [olivia, colleague]:
        client.force_login(user)
        page = client.get(profile_url("olivia"))
        assertContains(page, reverse("project-detail", args=["beta"]))
        assertContains(page, "drafts only")

    client.force_login(UserFactory.create())
    assertNotContains(client.get(profile_url("olivia")), "drafts only")


@pytest.mark.django_db
def test_profile_lookup(client, olivia):
    assert client.get(profile_url("OLIVIA")).status_code == 200
    assert client.get(profile_url("nobody")).status_code == 404
    olivia.is_active = False
    olivia.save()
    assert client.get(profile_url("olivia")).status_code == 404


@pytest.mark.django_db
def test_profile_without_packages(client):
    UserFactory.create(username="newbie")
    assertContains(client.get(profile_url("newbie")), "No packages yet.")


@pytest.mark.django_db
def test_project_page_links_team_and_publishers(client, olivia):
    alpha = olivia.project_memberships.get(project__name="alpha").project
    ProjectMemberFactory.create(project=alpha, user__username="max")
    ProjectMemberFactory.create(
        project=alpha, user__username="otto", role=ProjectMember.Role.owner
    )
    publisher = alpha.releases.get().owner

    page = client.get(reverse("project-detail", args=["alpha"]))

    assertContains(page, "Owners")
    assertContains(page, "Maintainer<")
    for username in ["olivia", "otto", "max"]:
        assertContains(page, link(username), html=True)
    # the versions table
    assertContains(page, "Published by")
    assertContains(page, link(publisher.username), html=True)


@pytest.mark.django_db
def test_asset_and_team_pages_link_profiles(client):
    project = ProjectFactory.create(owner__username="olivia")
    asset = AssetFactory.create(release__project=project, release__final=True)

    page = client.get(reverse("asset-detail", args=[asset.uuid]))
    assertContains(page, link(asset.uploader.username), html=True)

    client.force_login(project.owners().get())
    page = client.get(reverse("team", args=[project.slug]))
    assertContains(page, link("olivia"), html=True)


def test_reserved_usernames_cover_the_routes_next_to_profiles():
    """A user with such a name would have an unreachable profile."""
    fixed = {str(p.pattern).split("/")[0] for p in urlpatterns} - {"<str:username>"}
    assert fixed <= RESERVED_USERNAMES
