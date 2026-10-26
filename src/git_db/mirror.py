"""The static HTTP mirror of the index (`config.json`'s `http`), for clients
without git, e.g. the browser (DESIGN.md "magnet-index"). nginx serves it from
`MAGNET_ARTIFACTS_ROOT/index/`; git stays the source of truth."""

import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

MIRROR_DIR = "index"
# the files of the index a client reads; e.g. the README isn't mirrored
MIRRORED_GLOBS = ("config.json", "*/*.jsonl")
# like the files Django's storage writes; readable for nginx
FILE_MODE = 0o644


def mirror_root() -> Path:
    return Path(settings.MAGNET_ARTIFACTS_ROOT) / MIRROR_DIR


def mirror_url() -> str:
    return f"{settings.MAGNET_DOWNLOAD_BASE_URL.rstrip('/')}/{MIRROR_DIR}/"


def mirrored_files(root: Path) -> set[Path]:
    return {
        path.relative_to(root)
        for pattern in MIRRORED_GLOBS
        for path in root.glob(pattern)
        if path.is_file()
    }


def export_index(checkout: Path, target: Path) -> None:
    """Makes `target` equal to the index files of `checkout`. Each changed file is
    replaced atomically, so a reader gets the old or the new file, never a part,
    and unchanged files keep their mtime (and nginx's `ETag`). Files that vanished
    from the index are removed. Always compares everything, so an export that was
    interrupted gets repaired by the next one."""
    source_files = mirrored_files(checkout)
    for relative in sorted(source_files):
        content = (checkout / relative).read_bytes()
        path = target / relative
        if path.is_file() and path.read_bytes() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temp_file:
            temp_file.write(content)
        os.chmod(temp_file.name, FILE_MODE)
        os.replace(temp_file.name, path)

    for relative in mirrored_files(target) - source_files:
        (target / relative).unlink()
        if relative.parent != Path(".") and not any(
            (target / relative.parent).iterdir()
        ):
            (target / relative.parent).rmdir()
