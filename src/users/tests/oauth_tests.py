import base64
import hashlib
import importlib
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from django.apps import apps
from django.core.management import CommandError, call_command
from oauth2_provider.models import AccessToken, RefreshToken

from users.oauth import CLI_CLIENT_ID, get_cli_application
from users.tests.factories import UserFactory

REDIRECT_URI = "http://localhost:8000/users/display-token"

migration = importlib.import_module("users.migrations.0002_cli_oauth_application")


@pytest.mark.django_db
def test_migration_creates_the_cli_application():
    application = get_cli_application()

    assert application.client_type == "public"
    assert application.authorization_grant_type == "authorization-code"
    assert application.redirect_uris.split() == [
        REDIRECT_URI,
        "http://127.0.0.1:8000/users/display-token",
    ]
    assert application.skip_authorization


@pytest.mark.django_db
def test_migration_updates_an_existing_application(settings):
    # e.g. one made by hand in the admin
    application = get_cli_application()
    application.client_type = "confidential"
    application.redirect_uris = "http://example.com/"
    application.save()
    settings.MAGNET_OAUTH_REDIRECT_URIS = [
        "https://magnet.example.org/users/display-token"
    ]

    migration.create_cli_application(apps, None)

    application = get_cli_application()
    assert application.client_type == "public"
    assert application.redirect_uris == "https://magnet.example.org/users/display-token"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.mark.django_db
def test_magnet_login_and_logout(client):
    """The requests of `magnet login`/`logout` (`../magnet_cli/src/http_client/auth.rs`)."""
    client.force_login(UserFactory.create())
    verifier, challenge = pkce()

    # no consent page: straight to the page that shows the code
    response = client.get(
        "/o/authorize/",
        {
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "client_id": CLI_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert response.status_code == 302
    location = urlparse(response.url)
    assert location._replace(query="").geturl() == REDIRECT_URI
    code = parse_qs(location.query)["code"][0]

    client.logout()  # the CLI has no session
    response = client.post(
        "/o/token/",
        {
            "client_id": CLI_CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    assert response.status_code == 200
    token = response.json()
    assert token["scope"] == "read publish"

    response = client.post(
        "/o/token/",
        {
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": CLI_CLIENT_ID,
        },
    )
    assert response.status_code == 200
    token = response.json()

    # logout revokes the refresh token with only the client id
    response = client.post(
        "/o/revoke_token/",
        {
            "token": token["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": CLI_CLIENT_ID,
        },
    )
    assert response.status_code == 200
    assert RefreshToken.objects.get(token=token["refresh_token"]).revoked
    assert not AccessToken.objects.filter(token=token["access_token"]).exists()


@pytest.mark.django_db
def test_create_token(capsys):
    user = UserFactory.create(email="Ada@example.org")

    call_command("create_token", "--user", "ada@example.org", "--days", "30")

    printed = capsys.readouterr().out.strip()
    token = AccessToken.objects.get(token=printed)
    assert token.user == user
    assert token.application == get_cli_application()
    assert token.scope == "read publish"
    assert not token.is_expired()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "args, message",
    [
        (["--user", "nobody@example.org"], "no single user"),
        (["--user", "ada@example.org", "--scope", "read admin"], "Unknown scopes"),
        (["--user", "ada@example.org", "--days", "0"], "at least 1"),
    ],
)
def test_create_token_refuses(args, message):
    UserFactory.create(email="ada@example.org")

    with pytest.raises(CommandError, match=message):
        call_command("create_token", *args)
