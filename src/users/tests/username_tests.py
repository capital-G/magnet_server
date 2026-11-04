"""Usernames are the public names: chosen at signup, unique ignoring case."""

import pytest
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains

from packages.tests.factories import AssetFactory, ReleaseFactory
from users.tests.factories import UserFactory
from users.validators import validate_username


@pytest.mark.parametrize(
    "username", ["ab", "dennis", "Dennis_S", "ddw-common", "4tune", "a" * 39]
)
def test_valid_usernames(username):
    validate_username(username)


@pytest.mark.parametrize(
    "username",
    ["a", "a" * 40, "dennis.s", "dennis@example", "-dennis", "_dennis", "dénnis", ""],
)
def test_invalid_usernames(username):
    with pytest.raises(ValidationError) as error:
        validate_username(username)
    assert error.value.code == "invalid"


@pytest.mark.parametrize("username", ["admin", "Admin", "deleted", "Magnet", "me"])
def test_reserved_usernames(username):
    with pytest.raises(ValidationError) as error:
        validate_username(username)
    assert error.value.code == "reserved"


@pytest.mark.django_db
def test_factory_usernames_are_valid():
    for user in UserFactory.create_batch(3):
        validate_username(user.username)


def signup(client, username, email="new@example.com"):
    return client.post(
        reverse("account_signup"),
        {
            "email": email,
            "username": username,
            "password1": "a long Passw0rd!",
            "password2": "a long Passw0rd!",
        },
    )


@pytest.mark.django_db
def test_signup_with_a_username(client):
    page = client.get(reverse("account_signup"))
    assertContains(page, 'name="username"')
    assertContains(page, "Your public name on magnet")

    response = signup(client, "Dennis_S")

    assert response.status_code == 302
    user = User.objects.get(email="new@example.com")
    assert user.username == "Dennis_S"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "username, error",
    [
        ("dennis", "already exists"),
        ("DENNIS", "already exists"),
        ("admin", "is reserved"),
        ("dennis.s", "letters, digits"),
        ("", "This field is required"),
    ],
)
def test_signup_refuses_usernames(client, username, error):
    UserFactory.create(username="dennis")

    response = signup(client, username)

    assert response.status_code == 200
    assertContains(response, error)
    assert not User.objects.filter(email="new@example.com").exists()


@pytest.mark.django_db
def test_usernames_are_unique_ignoring_case_in_the_database():
    UserFactory.create(username="dennis")
    with pytest.raises(IntegrityError), transaction.atomic():
        UserFactory.create(username="Dennis")


@pytest.mark.django_db
def test_admin_forms_check_the_username():
    """Django's `UserCreationForm` (the admin's) runs the field's validators."""
    form = UserCreationForm({"username": "dennis.s", "password1": "", "password2": ""})
    assert not form.is_valid()
    assert "letters, digits" in str(form.errors["username"])


@pytest.mark.django_db
def test_pages_show_the_username_not_the_email(client):
    release = ReleaseFactory.create(
        project__name="alpha",
        project__owner__username="owner1",
        project__owner__email="owner@example.com",
        owner__email="publisher@example.com",
        final=True,
    )
    asset = AssetFactory.create(release=release)

    project_page = client.get(reverse("project-detail", args=["alpha"]))
    asset_page = client.get(reverse("asset-detail", args=[asset.uuid]))

    assertContains(project_page, "owner1")
    assertContains(asset_page, release.owner.username)
    for page in [project_page, asset_page]:
        assertNotContains(page, "@example.com")
