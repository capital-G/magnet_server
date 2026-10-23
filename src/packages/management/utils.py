from django.contrib.auth.models import User
from django.core.management import CommandError


def resolve_owner(username: str | None) -> User:
    """The user with `username`, by default the first superuser."""
    users = User.objects
    if username:
        if user := users.filter(username=username).first():
            return user
        raise CommandError(f"There is no user {username}")
    if user := users.filter(is_superuser=True).order_by("pk").first():
        return user
    raise CommandError(
        "There is no superuser to own the packages, "
        "create one with `manage.py createsuperuser` or pass --owner"
    )
