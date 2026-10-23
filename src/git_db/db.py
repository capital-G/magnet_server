import fcntl
import logging
import os
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from git_db.files import IndexWriteError, ProjectIndexFile
from git_db.models import GitArtifactModel, GitIndexConfigModel, GitReleaseModel
from packages.models import Asset, Release

logger = logging.getLogger(__name__)

BRANCH = "main"
CONFIG_FILE = "config.json"
README_FILE = "README.md"
README = """# magnet index

The package index of [magnet](https://github.com/supercollider/magnet), the SuperCollider package manager.
It is written by the magnet server only, don't push to it.

- `config.json`: index format and the download URL template of the artifacts
- `<first char>/<normalized package name>.jsonl`: one line per version of a package
"""

# Git runs without the global and system config of the host, so e.g. `commit.gpgsign`
# or `core.autocrlf` can't break or alter the index, and with a fixed identity, so it
# works in containers and on CI.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "magnet-server",
    "GIT_AUTHOR_EMAIL": "magnet-server@localhost",
    "GIT_COMMITTER_NAME": "magnet-server",
    "GIT_COMMITTER_EMAIL": "magnet-server@localhost",
}


class GitCommandError(Exception):
    pass


class IndexNotInitialized(IndexWriteError):
    pass


class NoArtifacts(IndexWriteError):
    pass


class GitDatabase:
    """The package index is a bare git repository, which clients fetch to resolve
    dependencies. The server is its only writer (D1.5), and everything in it can be
    regenerated from the database.

    Each change is made in a temporary clone, committed and pushed back, while holding
    an exclusive lock, so concurrent writers (e.g. gunicorn workers) are serialized.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self.lock_path = repo_path.with_name(f"{repo_path.name}.lock")

    def _run(self, *args: str, cwd: Path | None = None) -> str:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=cwd if cwd else self.repo_path,
                stderr=subprocess.STDOUT,
                env={**os.environ, **GIT_ENV},
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                f"command '{e.cmd}' return with error (code {e.returncode}): {e.output}"
            ) from e

    @contextmanager
    def _lock(self) -> Generator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    @contextmanager
    def transaction(self, message: str) -> Generator[Path]:
        """Yields a checkout of the index; everything changed in it gets
        committed with `message` and pushed, unless an exception is raised."""
        if not self.repo_path.exists():
            raise IndexNotInitialized(
                f"No index at {self.repo_path}, run `manage.py init_index` first"
            )
        with self._lock(), tempfile.TemporaryDirectory() as temp_dir:
            checkout = Path(temp_dir)
            self._run("clone", "--quiet", str(self.repo_path), str(checkout))
            # an empty repository has no branch yet
            self._run("checkout", "--quiet", "-B", BRANCH, cwd=checkout)

            yield checkout

            self._run("add", "--all", cwd=checkout)
            if not self._run("status", "--porcelain", cwd=checkout).strip():
                logger.info(f"Index unchanged: {message}")
                return
            self._run("commit", "--quiet", "-m", message, cwd=checkout)
            # fast-forward only, which always holds as we hold the lock
            self._run(
                "push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=checkout
            )
            logger.info(f"Index updated: {message}")

    def init_index(self) -> None:
        """Creates the bare repository if missing and writes `config.json` (and a
        README if there is none). Idempotent: commits only if something changed,
        e.g. the download URL."""
        if not self.repo_path.exists():
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)
            self._run(
                "init",
                "--quiet",
                "--bare",
                f"--initial-branch={BRANCH}",
                str(self.repo_path),
                cwd=self.repo_path.parent,
            )
            logger.info(f"Created index repository {self.repo_path}")

        with self.transaction("update index config") as checkout:
            config = GitIndexConfigModel.from_settings()
            (checkout / CONFIG_FILE).write_text(f"{config.to_json()}\n")
            readme = checkout / README_FILE
            if not readme.exists():
                readme.write_text(README)

    def add_release(self, line: GitReleaseModel) -> None:
        with self.transaction(f"publish {line.name} {line.version}") as checkout:
            ProjectIndexFile.for_name(checkout, line.name).add_release(line)

    def add_artifact(self, name: str, version: str, artifact: GitArtifactModel) -> None:
        message = f"add artifact {artifact.abi}-{artifact.platform} to {name} {version}"
        with self.transaction(message) as checkout:
            ProjectIndexFile.for_name(checkout, name).add_artifact(version, artifact)

    def yank(self, name: str, version: str) -> None:
        with self.transaction(f"yank {name} {version}") as checkout:
            ProjectIndexFile.for_name(checkout, name).yank_release(version)

    def commit_release(self, release: Release) -> None:
        """Writes the release with its artifacts into the index, which publishes it.
        Sets `published_at` once that succeeded."""
        line = GitReleaseModel.from_release(release)
        if not line.artifacts:
            raise NoArtifacts(
                f"{line.name} {line.version} has no artifacts, upload them first"
            )
        self.add_release(line)
        release.published_at = timezone.now()
        release.save(update_fields=["published_at"])

    def commit_asset(self, asset: Asset) -> None:
        """Appends an artifact to an already published release, e.g. a lepton
        binary for another platform."""
        self.add_artifact(
            name=asset.release.project.normalized_name,
            version=asset.release.version,
            artifact=GitArtifactModel.from_asset(asset),
        )

    def yank_release(self, release: Release) -> None:
        self.yank(name=release.project.normalized_name, version=release.version)


def get_git_database() -> GitDatabase:
    return GitDatabase(Path(settings.GIT_INDEX_REPO_PATH))
