"""CI tokens on the website (`users/tokens`)."""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken
from pytest_django.asserts import assertContains, assertNotContains

from users.models import CiToken
from users.oauth import create_access_token, create_ci_token, get_cli_application
from users.tests.factories import UserFactory

VERSIONS = "/api/v1/packages/{name}/versions/"


def create(client, name="CI", days=90, htmx=True):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(reverse("tokens"), {"name": name, "days": days}, headers=headers)


def shown_token(response) -> str:
    match = re.search(rb"<pre[^>]*>([^<]+)</pre>", response.content)
    assert match
    return match.group(1).decode()


def draft(client, token: str):
    return client.post(
        VERSIONS.format(name="foo"),
        {"version": "1.0.0"},
        content_type="application/json",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.django_db
def test_tokens_page_needs_login(client):
    response = client.get(reverse("tokens"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


@pytest.mark.django_db
def test_create_token(client):
    user = UserFactory.create()
    client.force_login(user)

    response = create(client, name="GitHub Actions", days=30)

    assert response.status_code == 200
    assertNotContains(response, 'class="navbar')  # only the partial
    token = AccessToken.objects.get(token=shown_token(response))
    assert token.user == user
    assert token.application == get_cli_application()
    assert token.scope == "publish"
    assert not hasattr(token, "refresh_token")
    assert token.ci_token.name == "GitHub Actions"
    assert abs(token.expires - (timezone.now() + timedelta(days=30))) < timedelta(
        minutes=1
    )
    assertContains(response, "MAGNET_TOKEN")
    assertContains(response, "GitHub Actions")


@pytest.mark.django_db
def test_create_token_without_htmx(client):
    client.force_login(UserFactory.create())
    response = create(client, htmx=False)
    assertContains(response, 'class="navbar')
    assert AccessToken.objects.filter(token=shown_token(response)).exists()


@pytest.mark.django_db
def test_token_is_shown_only_once(client):
    client.force_login(UserFactory.create())
    token = shown_token(create(client))

    response = client.get(reverse("tokens"))

    assertContains(response, "CI")
    assertNotContains(response, token)
    assertNotContains(response, "<pre")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data", [{"name": "", "days": 90}, {"name": "CI", "days": 7}, {"name": "x" * 101}]
)
def test_create_token_refusals(client, data):
    client.force_login(UserFactory.create())
    response = client.post(reverse("tokens"), data, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assertContains(response, "is-danger")
    assert not AccessToken.objects.exists()


@pytest.mark.django_db
def test_created_token_publishes(client):
    client.force_login(UserFactory.create())
    token = shown_token(create(client))
    client.logout()

    response = draft(client, token)

    assert response.status_code == 201
    assert response.json()["state"] == "draft"


@pytest.mark.django_db
def test_revoked_token_is_refused(client):
    user = UserFactory.create()
    client.force_login(user)
    token = shown_token(create(client))
    ci_token = CiToken.objects.get()

    response = client.post(
        reverse("revoke-token", args=[ci_token.pk]), headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assertNotContains(response, "<td>CI</td>")
    assert not CiToken.objects.exists()
    assert not AccessToken.objects.exists()
    client.logout()
    response = draft(client, token)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.django_db
def test_expired_token_is_refused(client):
    user = UserFactory.create()
    token = create_ci_token(user, "CI", days=1)
    token.expires = timezone.now() - timedelta(seconds=1)
    token.save()

    response = draft(client, token.token)
    assert response.status_code == 401
    assert "detail" in response.json()

    client.force_login(user)
    assertContains(client.get(reverse("tokens")), "expired")


@pytest.mark.django_db
def test_users_only_see_and_revoke_their_own_tokens(client):
    other = create_ci_token(UserFactory.create(), "Other's CI", days=90)
    user = UserFactory.create()
    create_ci_token(user, "My CI", days=90)
    # tokens of `magnet login`/`create_token` aren't listed
    create_access_token(user, days=1, scope="read publish")
    client.force_login(user)

    response = client.get(reverse("tokens"))
    assertContains(response, "My CI")
    assertNotContains(response, "Other&#x27;s CI")
    assert response.content.count(b"Revoke</button>") == 1

    response = client.post(reverse("revoke-token", args=[other.ci_token.pk]))
    assert response.status_code == 404
    assert AccessToken.objects.filter(pk=other.pk).exists()


@pytest.mark.django_db
def test_revoke_needs_post(client):
    user = UserFactory.create()
    token = create_ci_token(user, "CI", days=90)
    client.force_login(user)
    response = client.get(reverse("revoke-token", args=[token.ci_token.pk]))
    assert response.status_code == 405
