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
def create_ci_token(user: User, name: str, days: int) -> AccessToken:
    """A named token with the `publish` scope, created on the website."""
    token = create_access_token(user, days, PUBLISH_SCOPE)
    CiToken.objects.create(access_token=token, name=name)
    return token
