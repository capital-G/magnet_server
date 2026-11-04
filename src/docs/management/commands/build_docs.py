from django.conf import settings
from django.core.management import BaseCommand, CommandError

from docs.tasks import rebuild
from packages.models import Project
from packages.validators import normalize_name


class Command(BaseCommand):
    help = (
        "Queues builds of the docs of the current version of the given packages or "
        "of all (e.g. after bumping the builder image)."
    )

    def add_arguments(self, parser):
        parser.add_argument("names", nargs="*", help="packages, in any spelling")
        parser.add_argument("--all", action="store_true", help="all packages")

    def handle(self, *args, **options):
        if not settings.MAGNET_DOCS_BUILDS:
            raise CommandError("Docs builds are off, see MAGNET_DOCS_BUILDS")
        if bool(options["names"]) == options["all"]:
            raise CommandError("Name packages or pass --all")
        projects = Project.objects.public()
        if options["names"]:
            names = {normalize_name(name) for name in options["names"]}
            projects = projects.filter(normalized_name__in=names)
            if unknown := names - set(
                projects.values_list("normalized_name", flat=True)
            ):
                raise CommandError(f"Unknown packages: {', '.join(sorted(unknown))}")
        queued = 0
        for project in projects.order_by("normalized_name"):
            if rebuild(project) is not None:
                queued += 1
        self.stdout.write(self.style.SUCCESS(f"Queued the docs of {queued} packages"))
