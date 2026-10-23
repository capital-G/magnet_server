from django.core.management import BaseCommand

from git_db.db import get_git_database


class Command(BaseCommand):
    help = (
        "Regenerates all package files of the index from the published releases "
        "in the database, in one commit. Commits nothing if the index is up to date."
    )

    def handle(self, *args, **options):
        get_git_database().rebuild()
        self.stdout.write(self.style.SUCCESS("Index rebuilt"))
