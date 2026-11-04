"""CI tokens on the website (`users/tokens`)."""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from oauth2_provider.models import AccessToken
from pytest_django.asserts import assertContains, assertNotContains

from packages.tests.factories import ReleaseFactory
from users.models import CiToken
from users.oauth import create_access_token, create_ci_token, get_cli_application
from users.tests.factories import UserFactory

VERSIONS = "/api/v1/packages/{name}/versions/"


def create(client, name="CI", days=90, htmx=True):
    """A token for all packages."""
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        reverse("tokens"),
        {"name": name, "days": days, "scope": "all"},
        headers=headers,
    )


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
def test_tokens_expire_after_a_year_by_default_and_up_to_two(client):
    client.force_login(UserFactory.create())

    page = client.get(reverse("tokens"))
    assertContains(page, '<option value="365" selected>1 year</option>', html=True)
    assertContains(page, '<option value="730">2 years</option>', html=True)

    token = AccessToken.objects.get(token=shown_token(create(client, days=730)))
    assert abs(token.expires - (timezone.now() + timedelta(days=730))) < timedelta(
        minutes=1
    )


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
    "data",
    [
        {"name": "", "days": 90},
        {"name": "CI", "days": 7},
        {"name": "CI", "days": 731},
        {"name": "x" * 101},
    ],
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


@pytest.mark.django_db
def test_tokens_page_warns_about_tokens_for_all_packages(client):
    user = UserFactory.create()
    ReleaseFactory.create(project__name="My_Quark", project__owner=user)
    client.force_login(user)

    page = client.get(reverse("tokens"))

    # one package is the default
    assertContains(
        page,
        '<input type="radio" name="scope" value="package" x-model="scope" checked>',
        html=True,
    )
    assertContains(page, "scope: 'package'")
    assertContains(page, "publish new versions of <strong>every</strong> package")
    # the user's packages to choose from, or a new one
    assertContains(page, '<option value="my-quark">my-quark</option>', html=True)
    assertContains(page, '<option value="">New package&hellip;</option>', html=True)
    assertContains(page, "package: 'my-quark'")
    assertContains(page, "the token can publish its first version")


@pytest.mark.django_db
def test_without_packages_only_a_new_package_can_be_entered(client):
    client.force_login(UserFactory.create())

    page = client.get(reverse("tokens"))

    assertNotContains(page, '<select name="package"')
    assertContains(page, 'name="new_package"')


@pytest.mark.django_db
def test_create_a_token_for_one_package(client):
    client.force_login(UserFactory.create())

    response = client.post(
        reverse("tokens"),
        {"name": "CI", "days": 365, "scope": "package", "package": " My_Quark "},
        headers={"HX-Request": "true"},
    )

    token = AccessToken.objects.get(token=shown_token(response))
    assert token.ci_token.package == "my-quark"
    assertContains(response, "my-quark\n")
    assertContains(response, "not published yet")

    response = client.post(
        reverse("tokens"),
        {"name": "New", "days": 365, "scope": "package", "new_package": "Fresh_Quark"},
        headers={"HX-Request": "true"},
    )
    token = AccessToken.objects.get(token=shown_token(response))
    assert token.ci_token.package == "fresh-quark"

    response = create(client, name="All")
    token = AccessToken.objects.get(token=shown_token(response))
    assert token.ci_token.package == ""
    assertContains(response, "all packages")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data, error",
    [
        ({"package": "", "new_package": ""}, "Enter the name of the package."),
        ({"package": "", "new_package": "my quark"}, "only contain letters, digits"),
        # a tampered select
        ({"package": "my quark"}, "only contain letters, digits"),
        # all packages only when chosen
        ({"scope": ""}, "This field is required."),
    ],
)
def test_token_package_refusals(client, data, error):
    client.force_login(UserFactory.create())

    response = client.post(
        reverse("tokens"),
        {"name": "CI", "days": 365, "scope": "package", **data},
        headers={"HX-Request": "true"},
    )

    assertContains(response, error)
    assert not AccessToken.objects.exists()


def api(client, token, method, url, **kwargs):
    return getattr(client, method)(
        url, headers={"Authorization": f"Bearer {token.token}"}, **kwargs
    )


@pytest.mark.django_db
def test_a_token_for_one_package_only_publishes_it(client):
    user = UserFactory.create()
    for name in ["alpha", "beta"]:
        ReleaseFactory.create(project__name=name, project__owner=user, version="1.0.0")
    token = create_ci_token(user, "CI", days=1, package="alpha")
    version = "/api/v1/packages/{}/versions/1.0.0/"
    refusal = "The token 'CI' may only publish alpha."

    # the others: get, delete, upload, finalise, a new draft
    for method, url in [
        ("get", version.format("beta")),
        ("delete", version.format("beta")),
        ("put", version.format("beta") + "artifacts/quark/any/any/"),
        ("post", version.format("beta") + "finalise/"),
        ("post", VERSIONS.format(name="gamma")),
    ]:
        response = api(client, token, method, url)
        assert (response.status_code, response.json()) == (403, {"detail": refusal})

    # its own package in any spelling
    assert api(client, token, "get", version.format("Alpha")).status_code == 200
    response = api(
        client,
        token,
        "post",
        VERSIONS.format(name="alpha"),
        data={"version": "2.0.0"},
        content_type="application/json",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_a_token_for_a_new_package_publishes_its_first_version(client):
    token = create_ci_token(UserFactory.create(), "CI", days=1, package="fresh")

    assert draft(client, token.token).status_code == 403  # `foo`
    response = api(
        client,
        token,
        "post",
        VERSIONS.format(name="fresh"),
        data={"version": "1.0.0"},
        content_type="application/json",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_restricted_tokens_still_need_the_team(client):
    ReleaseFactory.create(project__name="alpha", version="1.0.0", final=True)
    token = create_ci_token(UserFactory.create(), "CI", days=1, package="alpha")

    response = api(
        client,
        token,
        "post",
        VERSIONS.format(name="alpha"),
        data={"version": "2.0.0"},
        content_type="application/json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_tokens_for_all_packages_publish_every_package_of_the_user(client):
    user = UserFactory.create()
    for name in ["alpha", "beta"]:
        ReleaseFactory.create(project__name=name, project__owner=user, version="1.0.0")
    token = create_ci_token(user, "CI", days=1)

    for name in ["alpha", "beta"]:
        url = f"/api/v1/packages/{name}/versions/1.0.0/"
        assert api(client, token, "get", url).status_code == 200


def create_for(client, package):
    return client.post(
        reverse("tokens"),
        {"name": "CI", "days": 365, "scope": "package", "new_package": package},
        headers={"HX-Request": "true"},
    )


@pytest.mark.django_db
def test_no_tokens_for_packages_of_others_or_reserved_names(client):
    ReleaseFactory.create(project__name="taken", final=True)
    client.force_login(UserFactory.create())

    assertContains(create_for(client, "Taken"), "taken is someone else&#x27;s package.")
    # a legacy quark name, reserved by the data migration
    assertContains(create_for(client, "wslib"), "The name wslib is reserved")
    assert not AccessToken.objects.exists()


@pytest.mark.django_db
def test_tokens_for_packages_published_by_someone_else_since(client):
    """Someone else publishing the name first doesn't make the token work for it,
    and the list marks it."""
    user = UserFactory.create()
    client.force_login(user)
    token = AccessToken.objects.get(token=shown_token(create_for(client, "fresh")))
    assertContains(client.get(reverse("tokens")), "not published yet")

    ReleaseFactory.create(project__name="fresh", version="1.0.0", final=True)

    assertContains(client.get(reverse("tokens")), ">not yours</span>")
    response = api(
        client,
        token,
        "post",
        VERSIONS.format(name="fresh"),
        data={"version": "2.0.0"},
        content_type="application/json",
    )
    assert response.status_code == 403
