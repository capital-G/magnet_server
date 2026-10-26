from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import BaseCommand, CommandError

from users.oauth import create_access_token


class Command(BaseCommand):
    help = (
        "Creates an access token for scripts and CI (`MAGNET_TOKEN`) and prints it "
        "to stdout. It's shown only once."
    )

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="email of the user")
        parser.add_argument(
            "--days", type=int, default=90, help="days until it expires (default 90)"
        )
        parser.add_argument(
            "--scope",
            default=" ".join(settings.OAUTH2_PROVIDER["DEFAULT_SCOPES"]),
            help="space-separated scopes (default: %(default)s)",
        )

    def handle(self, *args, **options):
        users = User.objects.filter(email__iexact=options["user"])
        if users.count() != 1:
            raise CommandError(f"There is no single user with email {options['user']}")
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        scopes = options["scope"].split()
        if unknown := set(scopes) - set(settings.OAUTH2_PROVIDER["SCOPES"]):
            raise CommandError(f"Unknown scopes: {' '.join(sorted(unknown))}")

        token = create_access_token(users.get(), options["days"], " ".join(scopes))
        self.stderr.write(
            f"Token for {token.user} with scope '{token.scope}', expires {token.expires}"
        )
        # only the token on stdout, e.g. for `MAGNET_TOKEN=$(manage.py create_token …)`
        self.stdout.write(token.token)
