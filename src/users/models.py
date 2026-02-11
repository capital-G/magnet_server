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
