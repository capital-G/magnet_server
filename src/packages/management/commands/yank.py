from django.core.management import BaseCommand, CommandError

from git_db.db import GitCommandError
from git_db.files import IndexWriteError
from packages.models import Release
from packages.validators import normalize_name


class Command(BaseCommand):
    help = (
        "Yanks a final version (or undoes it with --undo), like its owners can on "
        "the website. New lock files skip a yanked version, existing ones keep it."
    )

    def add_arguments(self, parser):
        parser.add_argument("name", help="package name in any spelling")
        parser.add_argument("version", help="MAJOR.MINOR.PATCH")
        parser.add_argument("--undo", action="store_true", help="un-yank")
        parser.add_argument("--reason", default="", help="shown on the website")

    def handle(self, *args, **options):
        name, version = normalize_name(options["name"]), options["version"]
        release = (
            Release.objects.final()
            .filter(project__normalized_name=name, version=version)
            .first()
        )
        if release is None:
            raise CommandError(f"There is no final version {version} of {name}")

        try:
            if options["undo"]:
                release.unyank_git_entry()
            else:
                release.yank_git_entry(options["reason"])
        except (IndexWriteError, GitCommandError) as e:
            raise CommandError(str(e)) from e
        done = "Un-yanked" if options["undo"] else "Yanked"
        self.stdout.write(self.style.SUCCESS(f"{done} {name} {version}"))
