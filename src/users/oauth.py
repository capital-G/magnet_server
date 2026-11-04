import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from oauth2_provider.models import AccessToken, Application

from users.models import CiToken

# `OAUTH_APP_PUBLIC_KEY` of the magnet CLI (`../magnet_cli/src/http_client/auth.rs`).
# A data migration creates the application (`users/migrations/0002_…`).
CLI_CLIENT_ID = "8bMJG5cTIk5gdINwQWbYHb0NAflBSr6dw57mANDn"
CLI_APPLICATION_NAME = "magnet CLI"
# Where `magnet login` receives the code: a listener of the CLI on a random port. The
# port is left out, since django-oauth-toolkit allows any port for loopback IPs
# (RFC 8252 7.3). Added by the data migration `users/0004_…`, the same on every server.
CLI_LOOPBACK_REDIRECT_URI = "http://127.0.0.1/callback"

PUBLISH_SCOPE = "publish"


def get_cli_application() -> Application:
    return Application.objects.get(client_id=CLI_CLIENT_ID)


def create_access_token(user: User, days: int, scope: str) -> AccessToken:
    """A long-lived access token without refresh token for scripts and CI
    (`MAGNET_TOKEN`), bound to the CLI's application, so the API authenticates it
    like a `magnet login` token."""
    return AccessToken.objects.create(
        user=user,
        application=get_cli_application(),
        token=secrets.token_urlsafe(32),
        expires=timezone.now() + timedelta(days=days),
        scope=scope,
    )


@transaction.atomic
def create_ci_token(user: User, name: str, days: int, package: str = "") -> AccessToken:
    """A named token with the `publish` scope, created on the website, for all
    packages of `user` or only `package` (a normalised name)."""
    token = create_access_token(user, days, PUBLISH_SCOPE)
    CiToken.objects.create(access_token=token, name=name, package=package)
    return token
