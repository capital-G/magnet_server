from django.core.management import BaseCommand

from packages.classes import extract_classes
from packages.models import Release
from packages.validators import normalize_name


class Command(BaseCommand):
    help = (
        "Reads the classes and methods of the final versions from their quark "
        "artifacts again, of all packages or the given ones (e.g. after a change of "
        "the parser)."
    )

    def add_arguments(self, parser):
        parser.add_argument("names", nargs="*", help="packages, in any spelling")

    def handle(self, *args, **options):
        releases = Release.objects.final().select_related("project")
        if options["names"]:
            releases = releases.filter(
                project__normalized_name__in=[
                    normalize_name(name) for name in options["names"]
                ]
            )
        count = 0
        for release in releases.iterator():
            extract_classes(release)
            count += 1
        self.stdout.write(
            self.style.SUCCESS(f"Extracted the classes of {count} versions")
        )
