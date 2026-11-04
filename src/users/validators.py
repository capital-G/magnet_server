import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,38}$")

USERNAME_HELP = _(
    "Your public name on magnet, shown on your packages and your profile. "
    "2 to 39 characters: letters, digits, '-' and '_'."
)

# paths below the site root and names that would mislead; compared lowercase
RESERVED_USERNAMES = frozenset(
    {
        "about",
        "account",
        "accounts",
        "admin",
        "administrator",
        "anonymous",
        "api",
        # the sentinel user of deleted accounts, see `packages.models`
        "deleted",
        "dl",
        "help",
        "index",
        "login",
        "logout",
        "magnet",
        "me",
        "moderator",
        "new",
        "null",
        "package",
        "packages",
        "project",
        "projects",
        "register",
        "root",
        "search",
        "security",
        "settings",
        "signup",
        "staff",
        "static",
        "supercollider",
        "support",
        "system",
        "team",
        "token",
        "tokens",
        "user",
        "users",
    }
)


def validate_username(value: str) -> None:
    """The format of a username and the reserved names. Uniqueness (ignoring case)
    is allauth's signup check and the index of migration `0005`."""
    if not USERNAME_RE.match(value):
        raise ValidationError(
            _(
                "Use 2 to 39 characters: letters, digits, '-' and '_', "
                "starting with a letter or digit."
            ),
            code="invalid",
        )
    if value.lower() in RESERVED_USERNAMES:
        raise ValidationError(
            _("The username %(username)s is reserved."),
            code="reserved",
            params={"username": value},
        )
