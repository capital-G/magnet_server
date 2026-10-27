from django.db import migrations

# copied from `users.oauth`, as migrations shouldn't import app code that may change
CLI_CLIENT_ID = "8bMJG5cTIk5gdINwQWbYHb0NAflBSr6dw57mANDn"
CLI_LOOPBACK_REDIRECT_URI = "http://127.0.0.1/callback"


def add_loopback_redirect(apps, schema_editor):
    """`magnet login` receives the code on `http://127.0.0.1:<random port>/callback`
    (RFC 8252), so the CLI's application gets that redirect URI next to the
    configured ones, which stay as they are (e.g. edited in the admin)."""
    Application = apps.get_model("oauth2_provider", "Application")
    application = Application.objects.filter(client_id=CLI_CLIENT_ID).first()
    if application is None:
        return
    uris = application.redirect_uris.split()
    if CLI_LOOPBACK_REDIRECT_URI not in uris:
        application.redirect_uris = " ".join([*uris, CLI_LOOPBACK_REDIRECT_URI])
        application.save(update_fields=["redirect_uris"])


def remove_loopback_redirect(apps, schema_editor):
    Application = apps.get_model("oauth2_provider", "Application")
    application = Application.objects.filter(client_id=CLI_CLIENT_ID).first()
    if application is None:
        return
    uris = application.redirect_uris.split()
    application.redirect_uris = " ".join(
        uri for uri in uris if uri != CLI_LOOPBACK_REDIRECT_URI
    )
    application.save(update_fields=["redirect_uris"])


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_ci_token"),
    ]

    operations = [
        migrations.RunPython(add_loopback_redirect, remove_loopback_redirect),
    ]
