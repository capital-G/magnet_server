"""Rules shared with the magnet CLI (`../magnet_cli`), which parses everything the
server writes into the index. Keep them in sync with the CLI, not the other way round."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# same as `MagnetManifestV1::is_valid_name` of the CLI
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

_NUM = r"(?:0|[1-9][0-9]*)"

# Only `MAJOR.MINOR.PATCH`, as pre-releases are not supported yet (D1.2).
# Pre-releases or build metadata would make the CLI skip the index line.
VERSION_RE = re.compile(rf"^({_NUM})\.({_NUM})\.({_NUM})$")

# Requirement syntax of the Rust `semver` crate (`VersionReq`), which the CLI uses:
# comma separated comparators with an optional operator and a partial version
# with wildcards (`1`, `1.2`, `1.*`, `1.2.x`), or a single `*`.
# Checked against `semver` 1.0.27; unlike the crate, pre-release and build
# metadata are rejected, since no version could match them.
_WILD = r"[*xX]"
_PARTIAL = rf"{_NUM}(?:\.{_WILD}(?:\.{_WILD})?|\.{_NUM}(?:\.(?:{_NUM}|{_WILD}))?)?"
_COMPARATOR = rf"\s*(?:=|>=?|<=?|~|\^)?\s*{_PARTIAL}\s*"
VERSION_REQ_RE = re.compile(rf"^\s*(?:{_WILD}|{_COMPARATOR}(?:,{_COMPARATOR})*)\s*$")

# abi and platform of an artifact, e.g. `any`, `api3`, `linux_x86_64`
ARTIFACT_TAG_RE = re.compile(r"^[a-z0-9_]+$")


def normalize_name(name: str) -> str:
    """`DDW_Common` and `ddw-common` refer to the same package.
    Same as `MagnetManifestV1::normalize_name` of the CLI. Not `slugify`, which keeps `_`."""
    return name.lower().replace("_", "-")


def validate_name(value: str) -> None:
    if not NAME_RE.match(value):
        raise ValidationError(
            _("Must start with a letter and only contain letters, digits, '_' and '-'.")
        )


def parse_version(value: str) -> tuple[int, int, int]:
    if not (match := VERSION_RE.match(value)):
        raise ValidationError(
            _("Must be a version of the form MAJOR.MINOR.PATCH, e.g. 1.2.3.")
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def validate_version(value: str) -> None:
    parse_version(value)


def validate_version_req(value: str) -> None:
    if not VERSION_REQ_RE.match(value):
        raise ValidationError(
            _("Must be a version requirement like ^1.2.3, ~1.2, >=1.0, <2 or *.")
        )


def validate_artifact_tag(value: str) -> None:
    if not ARTIFACT_TAG_RE.match(value):
        raise ValidationError(_("Must only contain lowercase letters, digits and '_'."))
