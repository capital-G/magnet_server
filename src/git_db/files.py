import json
import logging
from pathlib import Path

from git_db.models import GitArtifactModel, GitReleaseModel

logger = logging.getLogger(__name__)


class IndexWriteError(Exception):
    """A change which would break the rules of the index, so nothing got written."""


class DuplicateIndexEntry(IndexWriteError):
    pass


class IndexEntryMissing(IndexWriteError):
    pass


class KindMismatch(IndexWriteError):
    pass


class ProjectIndexFile:
    """The package file of a project: one line per release, in publish order,
    at `<first char of the normalized name>/<normalized name>.jsonl`,
    e.g. `d/ddw-common.jsonl`.

    Lines are never removed. They are immutable except for
    - artifacts, which can be appended
    - yanked, which flips with a yank and its undo
    """

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def for_name(cls, index_root: Path, normalized_name: str) -> "ProjectIndexFile":
        return cls(index_root / normalized_name[0] / f"{normalized_name}.jsonl")

    @property
    def releases(self) -> list[GitReleaseModel]:
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [GitReleaseModel(**json.loads(line)) for line in f if line.strip()]

    def write(self, releases: list[GitReleaseModel]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(f"{release.to_json()}\n" for release in releases))

    def add_release(self, release: GitReleaseModel) -> None:
        """Appends a release. Its version must be new and its kind the one of the
        earlier releases, since the kind is fixed by the first release (D2.3)."""
        releases = self.releases
        for existing in releases:
            if existing.version == release.version:
                raise DuplicateIndexEntry(
                    f"{release.name} {release.version} is already in the index"
                )
            if existing.kind != release.kind:
                raise KindMismatch(
                    f"{release.name} is a {existing.kind}, can't add a {release.kind}"
                )
        if not releases:
            logger.info(f"Create new package file {self.path.name}")
        self.write([*releases, release])

    def add_artifact(self, version: str, artifact: GitArtifactModel) -> None:
        releases = self.releases
        release = self._find(releases, version)
        for existing in release.artifacts:
            if (existing.abi, existing.platform) == (artifact.abi, artifact.platform):
                raise DuplicateIndexEntry(
                    f"{release.name} {version} already has an artifact "
                    f"for {artifact.abi}/{artifact.platform}"
                )
        release.artifacts.append(artifact)
        self.write(releases)

    def yank_release(self, version: str) -> None:
        self._set_yanked(version, True)

    def unyank_release(self, version: str) -> None:
        self._set_yanked(version, False)

    def _set_yanked(self, version: str, yanked: bool) -> None:
        releases = self.releases
        self._find(releases, version).yanked = yanked
        self.write(releases)

    def _find(self, releases: list[GitReleaseModel], version: str) -> GitReleaseModel:
        for release in releases:
            if release.version == version:
                return release
        raise IndexEntryMissing(f"{self.path.stem} {version} is not in the index")
