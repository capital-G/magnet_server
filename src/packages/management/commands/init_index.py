from django.conf import settings
from django.core.management import BaseCommand

from git_db.db import get_git_database


class Command(BaseCommand):
    help = (
        "Creates the git index (a bare repository at GIT_INDEX_REPO_PATH) if missing "
        "and writes its config.json. Safe to run repeatedly, e.g. on every start."
    )

    def handle(self, *args, **options):
        get_git_database().init_index()
        self.stdout.write(
            self.style.SUCCESS(f"Index ready at {settings.GIT_INDEX_REPO_PATH}")
        )
