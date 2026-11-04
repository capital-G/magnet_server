from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):
    """Usernames are public names, so `Foo` and `foo` can't both exist. allauth
    checks it at signup; this index also covers the admin and the commands.
    Django's `User` can't get a constraint, so it's an index in SQL (sqlite and
    postgres both have `lower()`)."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0004_cli_loopback_redirect"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE UNIQUE INDEX users_username_lower_uniq ON auth_user (lower(username));",
            "DROP INDEX users_username_lower_uniq;",
        ),
    ]
