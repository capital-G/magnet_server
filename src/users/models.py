import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserProfile(models.Model):
    """Stores additional information about a user
    by extending the existing Django User model through
    a one-to-one relationship."""

    uuid = models.UUIDField(
        editable=False,
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
    )

    created = models.DateTimeField(
        auto_now_add=True,
        help_text=_("The date and time the project was first submitted"),
    )

    last_update = models.DateTimeField(
        blank=False,
        auto_now=True,
        help_text=_("The date and time the project was last updated"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_profile",
        verbose_name=_("Internal account"),
        help_text=_("Reference to internal Django-Account"),
    )

    profile = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Profile"),
        help_text=_("Profile visible to other users. Supports markdown formatting."),
    )

    website_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("URL to personal website"),
    )

    def __str__(self):
        return str(self.user)


class CiToken(models.Model):
    """The name of an access token created on the website for scripts and CI
    (`MAGNET_TOKEN`). The token itself is django-oauth-toolkit's `AccessToken`, so
    the API authenticates it like a `magnet login` token; tokens without this row
    (`magnet login`, `create_token`) aren't listed on the tokens page."""

    access_token = models.OneToOneField(
        "oauth2_provider.AccessToken",
        on_delete=models.CASCADE,
        related_name="ci_token",
    )

    name = models.CharField(max_length=100, verbose_name=_("Name"))

    # a name, not a foreign key: the token may publish the first version, and
    # deleting the package mustn't turn it into a token for all packages
    package = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "The normalised name of the only package the token may publish; "
            "empty for all packages of the user"
        ),
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.name
