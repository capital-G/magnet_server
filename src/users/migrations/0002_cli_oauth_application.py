from django.conf import settings
from django.db import migrations

# copied from `users.oauth`, as migrations shouldn't import app code that may change
CLI_CLIENT_ID = "8bMJG5cTIk5gdINwQWbYHb0NAflBSr6dw57mANDn"


def create_cli_application(apps, schema_editor):
    """The public OAuth application `magnet login` uses (authorization code + PKCE).
    Updates an existing one, e.g. made by hand in the admin. The redirect URIs come
    from `MAGNET_OAUTH_REDIRECT_URIS` at migration time; later changes of the
    setting need an edit in the admin."""
    Application = apps.get_model("oauth2_provider", "Application")
    Application.objects.update_or_create(
        client_id=CLI_CLIENT_ID,
        defaults={
            "name": "magnet CLI",
            "client_type": "public",
            "authorization_grant_type": "authorization-code",
            "client_secret": "",
            "hash_client_secret": False,
            "redirect_uris": " ".join(settings.MAGNET_OAUTH_REDIRECT_URIS),
            # first-party app, PKCE protects the code and the redirect is our own page
            "skip_authorization": True,
            "algorithm": "",
            "user": None,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
        ("oauth2_provider", "0013_alter_application_authorization_grant_type_device"),
    ]

    operations = [
        migrations.RunPython(create_cli_application, migrations.RunPython.noop),
    ]
