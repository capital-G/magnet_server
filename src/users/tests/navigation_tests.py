"""The user menu in the navbar and "My packages" (`/users/packages`)."""

import pytest
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains

from packages.tests.factories import ProjectMemberFactory, ReleaseFactory
from users.tests.factories import UserFactory


def menu_links(user):
    return [
        reverse("my-packages"),
        reverse("profile", args=[user.username]),
        reverse("tokens"),
        reverse("account_email"),
        reverse("account_logout"),
    ]


@pytest.mark.django_db
def test_anonymous_users_get_sign_in_and_register(client):
    page = client.get(reverse("home"))

    assertContains(page, reverse("account_login"))
    assertContains(page, reverse("account_signup"))
    assertContains(page, 'class="navbar-burger"')
    assertNotContains(page, reverse("my-packages"))
    assertNotContains(page, "navbar-dropdown")


@pytest.mark.django_db
def test_user_menu(client):
    user = UserFactory.create(username="olivia")
    client.force_login(user)

    page = client.get(reverse("home"))

    assertContains(page, "navbar-dropdown")
    assertContains(page, "olivia")
    for url in menu_links(user):
        assertContains(page, f'href="{url}"')
    assertNotContains(page, reverse("admin:index"))
    assertNotContains(page, reverse("account_signup"))


@pytest.mark.django_db
def test_staff_get_the_admin_in_the_menu(admin_client):
    assertContains(admin_client.get(reverse("home")), reverse("admin:index"))


@pytest.mark.django_db
def test_my_packages_needs_login(client):
    response = client.get(reverse("my-packages"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


@pytest.mark.django_db
def test_my_packages(client):
    user = UserFactory.create()
    # owned, with a yanked final version and two drafts
    ReleaseFactory.create(
        project__name="alpha", project__owner=user, version="1.0.0", final=True
    )
    alpha = user.project_memberships.get().project
    ReleaseFactory.create(project=alpha, version="1.1.0", final=True, yanked=True)
    ReleaseFactory.create(project=alpha, version="2.0.0")
    ReleaseFactory.create(project=alpha, version="2.1.0")
    # maintained, only a draft
    beta = ReleaseFactory.create(project__name="beta").project
    ProjectMemberFactory.create(project=beta, user=user)
    # someone else's
    ReleaseFactory.create(project__name="gamma", final=True)
    client.force_login(user)

    page = client.get(reverse("my-packages"))

    rows = page.content.decode().split("<tr>")[2:]
    assert ["alpha" in rows[0], "beta" in rows[1]] == [True, True]
    assert len(rows) == 2
    assert "Owner" in rows[0] and "1.1.0" in rows[0] and "yanked" in rows[0]
    assert "2 drafts" in rows[0]
    assert "Maintainer" in rows[1] and "none yet" in rows[1] and "1 draft" in rows[1]
    for slug in ["alpha", "beta"]:
        assertContains(page, reverse("team", args=[slug]))
    assertNotContains(page, "gamma")


@pytest.mark.django_db
def test_my_packages_empty(client):
    client.force_login(UserFactory.create())
    assertContains(client.get(reverse("my-packages")), "magnet publish")
