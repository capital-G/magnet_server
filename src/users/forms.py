from allauth.account.forms import SignupForm as AllauthSignupForm

from users.validators import USERNAME_HELP


class SignupForm(AllauthSignupForm):
    """allauth's signup with the rules of the username as help text."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_HELP
