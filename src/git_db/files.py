import json
import logging
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from git_db.models import GitArtifactModel, GitReleaseModel
from packages.models import Asset, Project, Release

logger = logging.getLogger(__name__)


class DuplicateIndexEntry(Exception):
    pass


class IndexEntryMissing(Exception):
    pass


class ProjectIndexFile:
    """A project index file stores each release of a project in a line.
    In order to reduce file load on the index the files are arranged for e.g.
    MathLib (which has `mathlib` as slug): `m/mathlib` without file extension.

    Each line addresses a specific release and is immutable except for
    - assets (so it becomes possible to address)
    - yanked (can change to true)

    """

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def for_project(cls, base_path: Path, project: Project):
        """In order to avoid race conditions, please fork the git repository
        locally via worktree and use this as base path."""
        path = base_path / project.slug[0].lower() / f"{project.slug.lower()}"

        # make sure that the single path file name exists
        path.parent.mkdir(parents=True, exist_ok=True)

        if not path.exists():
            logger.info(f"Create new project index file for {project}")
            path.touch()

        return cls(path)

    def add_release(self, release: Release):
        """Adds a release to the index file.
        If the semantic version already exists, an exception will be raised."""
        serialized = GitReleaseModel.from_release(release)

        if self._version_exists(serialized):
            raise DuplicateIndexEntry("Version already present in index file")

        with self.path.open(mode="a") as f:
            print(serialized.model_dump_json(), file=f)

    def add_asset(self, asset: Asset):
        serialized = GitArtifactModel.from_asset(asset)

        # maybe check if already yanked?
        if not self._version_exists(GitReleaseModel.from_release(asset.release)):
            raise IndexEntryMissing("Can not add asset to non-existing release")

        with tempfile.NamedTemporaryFile(mode="w") as new_file:
            for existing_release in self.releases:
                if existing_release.version == asset.release.version:
                    # @todo check that this asset does not exist yet
                    existing_release.assets.append(serialized)
                print(existing_release.model_dump_json(), file=new_file)
            # don't forget to flush the file so the content has indeed been written
            new_file.flush()
            shutil.copy(new_file.name, self.path)

    def yank_release(self, release: Release):
        # maybe check if already yanked?
        if not self._version_exists(GitReleaseModel.from_release(release)):
            raise IndexEntryMissing("Can not yank non-existing release")

        with tempfile.NamedTemporaryFile(mode="w") as new_file:
            for existing_release in self.releases:
                if existing_release == release:
                    existing_release.yanked = True
                print(existing_release.model_dump_json(), file=new_file)
            # don't forget to flush the file so the content has indeed been written
            new_file.flush()
            shutil.copy(new_file.name, self.path)

    def _version_exists(self, release: GitReleaseModel):
        # maybe add additional kwargs to check the version against
        return release in list(self.releases)

    def verify_integrity(self) -> None:
        for _release in self.releases:
            pass

    @property
    def releases(self) -> Iterator[GitReleaseModel]:
        # maybe this should be cached upon init and not cached upon request? :?
        with self.path.open(mode="r") as f:
            for line in f.readlines():
                yield GitReleaseModel(**json.loads(line))
