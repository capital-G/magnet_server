"""The published docs (build step 5, docs_plan.md): `MAGNET_ARTIFACTS_ROOT/docs/<name>`
is a symlink to the folder of the build, `docs/.builds/<name>-<build id>`. Publishing
replaces the symlink atomically, so readers see either the old or the new docs, and
deletes the old folder afterwards."""

import os
import shutil
from pathlib import Path

from django.conf import settings

BUILDS = ".builds"


def docs_root() -> Path:
    return settings.MAGNET_ARTIFACTS_ROOT / "docs"


def build_dir(name: str, build_id: int) -> Path:
    """Where a build writes its docs before they get published."""
    return docs_root() / BUILDS / f"{name}-{build_id}"


def published_dir(name: str) -> Path | None:
    """The folder of the published docs of a package, if any."""
    link = docs_root() / name
    return link.resolve() if link.is_symlink() else None


def publish(name: str, folder: Path) -> None:
    """Makes `folder` (a `build_dir`) the docs of the package `name`."""
    root = docs_root()
    old = published_dir(name)
    temporary = root / f".{name}.{folder.name}.link"
    temporary.unlink(missing_ok=True)
    # relative, so it also resolves where the folder is mounted elsewhere (nginx)
    os.symlink(folder.relative_to(root), temporary)
    os.replace(temporary, root / name)
    if old is not None and old != folder.resolve():
        shutil.rmtree(old, ignore_errors=True)


def unpublish(name: str) -> None:
    """Removes the docs of the package `name`."""
    old = published_dir(name)
    (docs_root() / name).unlink(missing_ok=True)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
