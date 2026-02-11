import logging
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from django.conf import settings

from git_db.files import ProjectIndexFile
from packages.models import Asset, Release

logger = logging.getLogger(__name__)


class GitCommandError(Exception):
    pass


class GitDatabase:
    """Although we verify the integrity of the package on the
    magnet server, the actual package index is stored in a git repository
    which acts as a distributed database for the most common entries of a package.

    This allows to distribute the content of the package index for a local cache via git.
    """

    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    def _run(self, *args: str, cwd: Optional[Path] = None) -> bytes:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=cwd if cwd else self.repo_path,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            raise GitCommandError(
                f"command '{e.cmd}' return with error (code {e.returncode}): {e.output.decode()}"
            )

    @contextmanager
    def temporary_checkout(self):
        """In order to avoid race conditions in a multithreaded server environment,
        each modification of the git repo needs to be done in a temporary checkout which
        then gets merged to the main repo.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_repo_path = Path(temp_dir)
            # clone bare repo locally
            self._run(
                "clone",
                "--branch",
                "main",
                "--single-branch",
                str(self.repo_path),
                str(temp_repo_path),
            )
            yield temp_repo_path

            # push changes back to the main repo
            # fast-forward only to avoid conflicts
            self._run(
                "push",
                str(self.repo_path),
                "HEAD:refs/heads/main",
                cwd=temp_repo_path,
            )

    def commit_release(self, release: Release):
        """
        Serializes a given package to a JSON structure and adds it to the git repository
        which acts as shared truth for the package index.
        Only when this action succeeds a package is considered released.

        :param release: Package to enter into the database
        :return: None. Raises exception if failed.
        """
        with self.temporary_checkout() as temp_repo_path:
            project_file = ProjectIndexFile.for_project(temp_repo_path, release.project)
            project_file.add_release(release)
            self._run(
                "add",
                str(project_file.path.relative_to(temp_repo_path)),
                cwd=temp_repo_path,
            )
            self._run(
                "commit",
                "-m",
                f"publish {release.project.name}@{release.version}",
                cwd=temp_repo_path,
            )

    def yank_release(self, release: Release):
        with self.temporary_checkout() as temp_repo_path:
            project_file = ProjectIndexFile.for_project(temp_repo_path, release.project)
            project_file.yank_release(release)
            self._run(
                "add",
                str(project_file.path.relative_to(temp_repo_path)),
                cwd=temp_repo_path,
            )
            self._run(
                "commit",
                "-m",
                f"yank {release.project.name}@{release.version}",
                cwd=temp_repo_path,
            )

    def commit_asset(self, asset: Asset):
        with self.temporary_checkout() as temp_repo_path:
            project_file = ProjectIndexFile.for_project(
                temp_repo_path, asset.release.project
            )
            project_file.add_asset(asset)
            self._run(
                "add",
                str(project_file.path.relative_to(temp_repo_path)),
                cwd=temp_repo_path,
            )
            self._run(
                "commit",
                "-m",
                f"publish {asset.release.project.name}@{asset.release.version} asset {asset}",
                cwd=temp_repo_path,
            )


# acts as a singleton (this isn't thread-safe?)
git_database = GitDatabase(settings.GIT_INDEX_REPO_PATH)
