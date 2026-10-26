"""Inspection of an uploaded artifact (`.tar.gz`) without extracting it, see DESIGN.md
"Checks of the server". The checks mirror the CLI, which packages the artifacts
(`../magnet_cli/src/packaging/collect.rs`) and extracts them on install
(`compression.rs: extract`), so the server only accepts what every CLI can install."""

import gzip
import json
import tarfile
import tomllib
import zlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.utils.translation import gettext_lazy as _
from jsonschema.exceptions import best_match

from packages.globs import GlobError, GlobSet

MANIFEST_FILE = "magnet.toml"
# packaged if the manifest names no readme and the project has one
DEFAULT_README = "README.md"
# never packaged if at the project root
NEVER_PACKAGED = {"dist", "magnet_packages", "sclang_conf.yaml"}
# leptons and gluons have a source artifact (`source`/`any`), quarks don't
SOURCE_ABI = "source"
# where the binaries of `platform-paths` end up within an artifact
PLUGINS_DIR = "plugins"

DIGITS = "0123456789"

SCHEMA_PATH = Path(__file__).parent / "schemes" / "magnet.v1.schema.json"

# at most this many refused files are listed in an error
MAX_LISTED_FILES = 5


@dataclass
class ArchiveEntries:
    # normalised paths (`/`-separated, without `.` components) in archive order
    files: list[str]
    # the raw `magnet.toml` at the root, if any
    manifest: bytes | None


@dataclass
class InspectedArtifact:
    # `magnet.toml`, valid according to the manifest schema
    manifest: dict[str, Any]
    readme: str | None
    files: list[str]


class _LimitedGzipFile(gzip.GzipFile):
    """Counts the decompressed bytes and stops above `limit`, against gzip bombs."""

    def __init__(self, fileobj: File, limit: int):
        super().__init__(fileobj=fileobj, mode="rb")
        self.limit = limit
        self.unpacked_size = 0

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self.unpacked_size += len(data)
        if self.unpacked_size > self.limit:
            raise ValidationError(
                _("The archive unpacks to more than %(limit)s bytes."),
                params={"limit": self.limit},
            )
        return data


def _normalised_path(name: str) -> str:
    """The path of an archive entry, which must stay within the package and be
    possible on every platform (like the CLI's `is_relative_path`/`check_portable`)."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(
            _("The archive contains %(path)s, which points outside of the package."),
            params={"path": name},
        )
    for component in path.parts:
        if component.endswith((".", " ")):
            raise ValidationError(
                _(
                    "The archive contains %(path)s: %(name)s ends with a dot or a space, "
                    "which is not possible on Windows."
                ),
                params={"path": name, "name": component},
            )
        stem = component.split(".")[0].lower()
        numbered = len(stem) == 4 and stem[:3] in ("com", "lpt") and stem[3] in DIGITS
        if stem in ("con", "prn", "aux", "nul") or numbered:
            raise ValidationError(
                _(
                    "The archive contains %(path)s: %(name)s is a reserved name on Windows."
                ),
                params={"path": name, "name": component},
            )
    return "/".join(path.parts)


def _read_text_file(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    limit = settings.MAGNET_ARCHIVE_MAX_TEXT_FILE_SIZE
    if member.size > limit:
        raise ValidationError(
            _("%(path)s in the archive is larger than %(limit)s bytes."),
            params={"path": member.name, "limit": limit},
        )
    f = archive.extractfile(member)
    assert f is not None  # a file member
    return f.read()


def read_archive(file: File) -> ArchiveEntries:
    """Checks that `file` is a gzip'd tar which the CLI can extract as is: within the
    limits (`MAGNET_ARCHIVE_MAX_*`), only files and directories, with relative,
    portable paths that are unique even when ignoring the case. Reads `magnet.toml`."""
    files: list[str] = []
    folded_files: set[str] = set()
    manifest = None
    entries = 0
    file.open("rb")
    try:
        stream = _LimitedGzipFile(file, settings.MAGNET_ARCHIVE_MAX_UNPACKED_SIZE)
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                entries += 1
                if entries > settings.MAGNET_ARCHIVE_MAX_ENTRIES:
                    raise ValidationError(
                        _("The archive has more than %(limit)s entries."),
                        params={"limit": settings.MAGNET_ARCHIVE_MAX_ENTRIES},
                    )
                path = _normalised_path(member.name)
                if not (member.isfile() or member.isdir()):
                    raise ValidationError(
                        _(
                            "The archive contains %(path)s, which is neither a file nor a directory."
                        ),
                        params={"path": member.name},
                    )
                if not member.isfile():
                    continue
                if not path:
                    raise ValidationError(
                        _("The archive contains a file without a name.")
                    )
                # `a/b` and `A/b` would end up in the same file on macOS and Windows
                if path.lower() in folded_files:
                    raise ValidationError(
                        _(
                            "The archive contains %(path)s more than once (ignoring the case)."
                        ),
                        params={"path": member.name},
                    )
                folded_files.add(path.lower())
                files.append(path)
                if path == MANIFEST_FILE:
                    manifest = _read_text_file(archive, member)
        # Reading tar entries stops at the end-of-archive marker, so read the rest,
        # which also checks the gzip checksum and length (and the unpacked size).
        while stream.read(1 << 16):
            pass
    except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
        raise ValidationError(_("The file is not a valid .tar.gz archive.")) from e
    finally:
        file.seek(0)
    if not entries:
        raise ValidationError(_("The archive is empty."))
    return ArchiveEntries(files=files, manifest=manifest)


def _read_file(file: File, path: str) -> bytes:
    """A file of an archive which already passed `read_archive`."""
    try:
        with tarfile.open(fileobj=file, mode="r|gz") as archive:
            for member in archive:
                if member.isfile() and _normalised_path(member.name) == path:
                    return _read_text_file(archive, member)
    finally:
        file.seek(0)
    raise ValueError(f"{path} is not in the archive")


@cache
def manifest_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def parse_manifest(data: bytes) -> dict[str, Any]:
    try:
        manifest = tomllib.loads(data.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ValidationError(
            _("%(file)s is not valid TOML: %(error)s"),
            params={"file": MANIFEST_FILE, "error": e},
        ) from e
    validator = jsonschema.Draft7Validator(manifest_schema())
    if error := best_match(validator.iter_errors(manifest)):
        location = ".".join(str(p) for p in error.absolute_path) or "top level"
        raise ValidationError(
            _("%(file)s is invalid at %(location)s: %(error)s"),
            params={
                "file": MANIFEST_FILE,
                "location": location,
                "error": error.message,
            },
        )
    return manifest


def readme_path(manifest: dict[str, Any]) -> str:
    readme = manifest.get("metadata", {}).get("readme")
    if readme is None:
        return DEFAULT_README
    path = PurePosixPath(readme)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(
            _("The readme %(path)s points outside of the package."),
            params={"path": readme},
        )
    return "/".join(path.parts)


def refused_files(
    files: list[str], manifest: dict[str, Any], abi: str, platform: str
) -> list[str]:
    """The files of an artifact which `magnet package` wouldn't have selected
    (`collect.rs: collect_files`), each with the reason. Every file must be
    `magnet.toml`, the readme, or selected by the globs of `[artifact]`:
    `source-paths` for the source artifact (`source`/`any`), else `global-paths`
    or, for leptons and gluons, a binary in `plugins/`, whose platform needs an entry
    in `platform-paths` (the binaries themselves aren't matched against its globs)."""
    kind = manifest["package"]["kind"]
    artifact = manifest.get("artifact", {})
    is_source = (abi, platform) == (SOURCE_ABI, "any")
    binary_platforms = artifact.get("platform-paths", {})
    if kind == "quark":
        if is_source:
            raise ValidationError(
                _(
                    "A quark has no source artifact, since its artifact already is the source."
                )
            )
        if binary_platforms:
            raise ValidationError(
                _("A quark can not have `platform-paths`, since it has no binaries.")
            )
    has_binaries = kind != "quark" and not is_source
    if has_binaries and platform not in binary_platforms:
        raise ValidationError(
            _(
                "There are no `platform-paths` for %(platform)s in [artifact], "
                "which select the binaries of the package."
            ),
            params={"platform": platform},
        )
    globs_name = "source-paths" if is_source else "global-paths"
    try:
        selected = GlobSet(artifact.get(globs_name, []))
        exclude = GlobSet(artifact.get("exclude", []))
    except GlobError as e:
        raise ValidationError(str(e)) from e
    always_packaged = {MANIFEST_FILE, readme_path(manifest)}

    refused = []
    for path in files:
        if path in always_packaged:
            continue
        parts = path.split("/")
        if any(part.startswith(".") for part in parts):
            refused.append(f"{path} (hidden files are never packaged)")
        elif parts[0] in NEVER_PACKAGED:
            refused.append(f"{path} ({parts[0]} is never packaged)")
        elif has_binaries and len(parts) == 2 and parts[0] == PLUGINS_DIR:
            continue
        elif exclude.matches(path):
            refused.append(f"{path} (matched by exclude)")
        elif not selected.matches(path):
            refused.append(f"{path} (not selected by {globs_name})")
    return refused


def inspect_artifact(file: File, abi: str, platform: str) -> InspectedArtifact:
    """All checks of an uploaded artifact (`read_archive`, the manifest and the file
    selection), raising a `ValidationError` whose message the API returns."""
    entries = read_archive(file)
    if entries.manifest is None:
        raise ValidationError(
            _("The archive has no %(file)s at its root."),
            params={"file": MANIFEST_FILE},
        )
    manifest = parse_manifest(entries.manifest)
    if overrides := manifest.get("overrides"):
        # like the CLI's `check_publishable`: others couldn't reproduce them
        raise ValidationError(
            _("%(file)s can't be published with [overrides]: %(names)s"),
            params={"file": MANIFEST_FILE, "names": ", ".join(sorted(overrides))},
        )

    if refused := refused_files(entries.files, manifest, abi, platform):
        listed = ", ".join(refused[:MAX_LISTED_FILES])
        if len(refused) > MAX_LISTED_FILES:
            listed += f" and {len(refused) - MAX_LISTED_FILES} more"
        raise ValidationError(
            _(
                "The archive contains files which the manifest doesn't select: %(files)s"
            ),
            params={"files": listed},
        )

    readme = None
    path = readme_path(manifest)
    if path in entries.files:
        readme = _read_file(file, path).decode(errors="replace")
    elif "readme" in manifest.get("metadata", {}):
        raise ValidationError(
            _("The readme %(path)s of [metadata] is missing in the archive."),
            params={"path": path},
        )
    return InspectedArtifact(manifest=manifest, readme=readme, files=entries.files)
