import hashlib
import json
from pathlib import Path
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.management import BaseCommand, CommandError

from git_db.db import GitCommandError
from git_db.files import IndexWriteError
from packages.management.utils import resolve_owner
from packages.models import ANY, Project, Release, artifact_filename
from packages.services import get_or_create_project, publish_release


class Command(BaseCommand):
    help = (
        "Publishes the fixture packages of the magnet CLI (`tests/fixtures` of "
        "magnet_cli: `index/*/*.jsonl` and their tarballs in `dl/`), so the real CLI "
        "can be tested against this server. Lines the server can't publish yet "
        "(non-quarks, legacy dependencies) and lines without a tarball are skipped. Already published releases "
        "are skipped too, so it's safe to run repeatedly."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "fixtures",
            type=Path,
            help="path to the CLI fixtures, e.g. ../../magnet_cli/tests/fixtures",
        )
        parser.add_argument(
            "--owner", help="username of the owner, defaults to the first superuser"
        )

    def handle(self, *args, **options):
        fixtures = options["fixtures"]
        index_dir, dl_dir = fixtures / "index", fixtures / "dl"
        if not index_dir.is_dir() or not dl_dir.is_dir():
            raise CommandError(f"{fixtures} has no index/ and dl/ directories")
        owner = resolve_owner(options["owner"])

        lines = [
            line
            for line in self.read_index(index_dir)
            if self.is_publishable(line, dl_dir)
        ]

        # dependencies must refer to existing projects, so create all of them
        # first and publish in file order afterwards
        for line in lines:
            get_or_create_project(name=line["name"], kind=line["kind"], owner=owner)

        for line in lines:
            self.publish(line, dl_dir, owner)

        for line in lines:
            if line["yanked"]:
                release = Release.objects.get(
                    project__normalized_name=line["name"], version=line["version"]
                )
                if not release.yanked:
                    release.yank_git_entry()
                    self.stdout.write(f"Yanked {line['name']} {line['version']}")

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(lines)} releases"))

    def read_index(self, index_dir: Path) -> list[dict[str, Any]]:
        lines = []
        for path in sorted(index_dir.glob("*/*.jsonl")):
            lines += [
                json.loads(line) for line in path.read_text().splitlines() if line
            ]
        return lines

    def is_publishable(self, line: dict[str, Any], dl_dir: Path) -> bool:
        release = f"{line['name']} {line['version']}"
        if line["kind"] != Project.Kind.quark:
            self.stderr.write(self.style.WARNING(f"Skip {release}: not a quark"))
            return False
        if any(not isinstance(r, str) for r in line["dependencies"].values()):
            self.stderr.write(self.style.WARNING(f"Skip {release}: legacy dependency"))
            return False
        if [(a["abi"], a["platform"]) for a in line["artifacts"]] != [(ANY, ANY)]:
            self.stderr.write(
                self.style.WARNING(f"Skip {release}: not a single any/any artifact")
            )
            return False
        # some fixtures only exist for the resolver tests and have fake checksums
        path = self.tarball(line, dl_dir)
        if not path.is_file():
            self.stderr.write(self.style.WARNING(f"Skip {release}: no {path.name}"))
            return False
        return True

    def tarball(self, line: dict[str, Any], dl_dir: Path) -> Path:
        return dl_dir / artifact_filename(
            line["name"], line["version"], line["kind"], ANY, ANY
        )

    def publish(self, line: dict[str, Any], dl_dir: Path, owner: User) -> None:
        name, version, kind = line["name"], line["version"], line["kind"]
        release = f"{name} {version}"
        if Release.objects.filter(
            project__normalized_name=name, version=version, published_at__isnull=False
        ).exists():
            self.stdout.write(f"Skip {release}: already published")
            return

        path = self.tarball(line, dl_dir)
        expected = line["artifacts"][0]["sha256"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise CommandError(f"{path} doesn't match the sha256 of {release}")

        try:
            with path.open("rb") as f:
                publish_release(
                    name=name,
                    version=version,
                    kind=kind,
                    file=File(f, name=path.name),
                    owner=owner,
                    dependencies=line["dependencies"],
                    uploaded_via="manage.py seed_cli_fixtures",
                )
        except ValidationError as e:
            raise CommandError(f"{release}: {'; '.join(e.messages)}") from e
        except (IndexWriteError, GitCommandError) as e:
            raise CommandError(f"{release}: {e}") from e
        self.stdout.write(f"Published {release}")
