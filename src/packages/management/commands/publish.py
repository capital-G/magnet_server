from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.management import BaseCommand, CommandError

from git_db.db import GitCommandError
from git_db.files import IndexWriteError
from packages.management.utils import resolve_owner
from packages.models import ANY, Project
from packages.services import publish_release


def dependency(value: str) -> tuple[str, str]:
    name, separator, requirement = value.partition("=")
    if not separator or not name or not requirement:
        raise ValueError(value)
    return name, requirement


class Command(BaseCommand):
    help = (
        "Publishes a release with its artifact: creates the project if missing, "
        "stores the artifact and writes the release into the index."
    )

    def create_parser(self, prog_name, subcommand, **kwargs):
        # replace Django's own --version (which prints the Django version)
        return super().create_parser(
            prog_name, subcommand, conflict_handler="resolve", **kwargs
        )

    def add_arguments(self, parser):
        parser.add_argument(
            "--name", required=True, help="package name, e.g. DDW_Common"
        )
        parser.add_argument("--version", required=True, help="MAJOR.MINOR.PATCH")
        parser.add_argument("--kind", required=True, choices=Project.Kind.values)
        parser.add_argument(
            "--file", required=True, type=Path, help="the artifact as .tar.gz"
        )
        parser.add_argument(
            "--dependency",
            action="append",
            default=[],
            type=dependency,
            metavar="NAME=REQUIREMENT",
            help="e.g. beta=^1.0.0, can be repeated",
        )
        parser.add_argument("--abi", default=ANY)
        parser.add_argument("--platform", default=ANY)
        parser.add_argument(
            "--owner", help="username of the owner, defaults to the first superuser"
        )

    def handle(self, *args, **options):
        owner = resolve_owner(options["owner"])
        path = options["file"]
        if not path.is_file():
            raise CommandError(f"{path} is not a file")

        try:
            with path.open("rb") as f:
                release = publish_release(
                    name=options["name"],
                    version=options["version"],
                    kind=options["kind"],
                    file=File(f, name=path.name),
                    owner=owner,
                    dependencies=dict(options["dependency"]),
                    abi=options["abi"],
                    platform=options["platform"],
                    uploaded_via="manage.py publish",
                )
        except ValidationError as e:
            raise CommandError("; ".join(e.messages)) from e
        except (IndexWriteError, GitCommandError) as e:
            raise CommandError(str(e)) from e

        asset = release.assets.get()
        self.stdout.write(
            self.style.SUCCESS(
                f"Published {release.project.normalized_name} {release.version} "
                f"({asset.sha256_checksum}) at {asset.file.url}"
            )
        )
