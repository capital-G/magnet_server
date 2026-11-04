from allauth.account.forms import SignupForm as AllauthSignupForm
from django import forms
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

from users.models import UserProfile
from users.validators import USERNAME_HELP


class SignupForm(AllauthSignupForm):
    """allauth's signup with the rules of the username as help text."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_HELP


class ProfileForm(forms.ModelForm):
    """The public part of a profile, edited by its user on `/users/settings`."""

    profile = forms.CharField(
        required=False,
        max_length=5000,
        label=_("About you"),
        help_text=_("Markdown, shown on your profile."),
        widget=forms.Textarea(attrs={"rows": 8}),
    )
    # only web links, no `ftp:` (the default of `URLField`)
    website_url = forms.URLField(
        required=False,
        label=_("Website"),
        validators=[URLValidator(schemes=["http", "https"])],
    )

    class Meta:
        model = UserProfile
        fields = ["profile", "website_url"]
