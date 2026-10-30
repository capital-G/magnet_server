"""Publishing (DESIGN.md "Publishing a package"): a version starts as a draft, gets
its artifacts and is finalised, which writes it into the index. Only final versions
are in the index, and every step can be repeated (e.g. by each job of a CI matrix).
Used by the publish API and, via `publish_release()`, the management commands."""

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from git_db.db import get_git_database
from packages.archive import InspectedArtifact, declared_kinds, inspect_artifact
from packages.models import (
    ANY,
    SOURCE_KIND,
    Asset,
    DeletedRelease,
    Dependency,
    GitDependency,
    Project,
    Release,
    ReservedName,
    git_index_entry,
)
from packages.validators import (
    normalize_name,
    validate_artifact_kind_tags,
    validate_artifact_tag,
    validate_git_url,
    validate_name,
    validate_version,
    validate_version_req,
)

logger = logging.getLogger(__name__)


class Conflict(Exception):
    """A request which contradicts what is stored, e.g. another artifact for the
    same abi and platform (the API answers `409`)."""


@dataclass
class IndexFields:
    """The fields of a manifest which reach the index. All artifacts of a version
    must agree on them (name and version are checked against the release)."""

    kinds: list[str]
    sclang: str | None
    # normalized name -> version requirement or git dependency, as in the index
    # line (`Release.index_dependencies`), sorted
    dependencies: dict[str, Any]


def join_and(items: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`"""
    if len(items) < 2:
        return "".join(items)
    return _("%(items)s and %(last)s") % {
        "items": ", ".join(items[:-1]),
        "last": items[-1],
    }


def label(release: Release) -> str:
    """A release in messages for the CLI, which knows packages by their normalized
    name (`str(release)` has the display name)."""
    return f"{release.project.normalized_name} {release.version}"


def ensure_name_available(name: str, user: User) -> None:
    """Refuses to register a new package under a reserved name, unless the
    reservation was claimed by `user`. Only for new packages: existing projects
    with a reserved name keep working for their owners and maintainers."""
    reservation = ReservedName.objects.filter(
        normalized_name=normalize_name(name)
    ).first()
    if reservation is not None and reservation.claimed_by_id != user.pk:
        raise ValidationError(
            _("The name %(name)s is reserved (%(reason)s).")
            % {"name": name, "reason": reservation.get_reason_display()}
        )


def ensure_can_publish(user: User, project: Project) -> None:
    if not project.can_publish(user):
        raise PermissionDenied(
            _("You are not an owner or maintainer of %(project)s.")
            % {"project": project.normalized_name}
        )


def get_or_create_project(*, name: str, owner: User) -> Project:
    """The project with the normalized `name`, created if missing (for the
    management commands). A new project's name must not be reserved for someone
    else."""
    project = Project.objects.filter(normalized_name=normalize_name(name)).first()
    if project is None:
        ensure_name_available(name, owner)
        project = Project(name=name, owner=owner)
        project.full_clean()
        project.save()
        logger.info(f"Created project {project}")
    return project


def _project_for_draft(user: User, name: str) -> Project:
    """The existing project, which `user` must be allowed to publish to, or a new
    one owned by `user`."""
    normalized_name = normalize_name(name)
    project = Project.objects.filter(normalized_name=normalized_name).first()
    if project is None:
        ensure_name_available(name, user)
        project = Project(name=name, owner=user)
        try:
            with transaction.atomic():
                project.save()
        except IntegrityError:
            # created by a concurrent request
            project = Project.objects.filter(normalized_name=normalized_name).first()
            if project is None:
                raise
        else:
            logger.info(f"Created project {project}")
            return project
    ensure_can_publish(user, project)
    return project


def create_draft(user: User, name: str, version: str) -> tuple[Release, bool]:
    """The draft (or final version) `version` of the package `name` (any spelling),
    and whether it was created. A new package is owned by `user`."""
    validate_name(name)
    validate_version(version)
    project = _project_for_draft(user, name)

    if release := project.releases.filter(version=version).first():
        return release, False
    if DeletedRelease.objects.filter(
        normalized_name=project.normalized_name, version=version
    ).exists():
        raise ValidationError(
            _("%(name)s %(version)s was deleted, its version can't be used again.")
            % {"name": project.normalized_name, "version": version}
        )

    release = Release(project=project, version=version, owner=user)
    try:
        with transaction.atomic():
            release.save()
    except IntegrityError:
        # created by a concurrent request, e.g. another job of a CI matrix
        return project.releases.get(version=version), False
    logger.info(f"Created draft {release}")
    return release, True


def file_sha256(file: File) -> str:
    hasher = hashlib.sha256()
    for chunk in file.chunks():
        hasher.update(chunk)
    file.seek(0)
    return hasher.hexdigest()


def _existing_asset(
    release: Release, kind: str, abi: str, platform: str, sha256: str
) -> Asset | None:
    """The identical artifact if it was uploaded already. Artifacts are never
    replaced, so a different one is a conflict."""
    asset = release.assets.filter(kind=kind, abi=abi, platform=platform).first()
    if asset is None:
        return None
    if asset.sha256_checksum != sha256:
        raise Conflict(
            _(
                "%(release)s already has an artifact %(kind)s-%(abi)s-%(platform)s "
                "with another sha256 (%(sha256)s), artifacts can't be replaced."
            )
            % {
                "release": label(release),
                "kind": kind,
                "abi": abi,
                "platform": platform,
                "sha256": asset.sha256_checksum,
            }
        )
    return asset


def git_dependency(name: str, value: dict[str, Any]) -> dict[str, Any]:
    """The index entry of a git or legacy quark dependency of a manifest, which
    must be pinned by a full commit like the CLI's `check_publishable` demands
    (a branch or tag could move). The server never runs git for it."""
    hint = _('pin it via `ref = "<commit SHA>"`')
    git = value.get("git")
    if git is None:
        raise ValidationError(
            _(
                "%(name)s is a legacy quark without a git URL and ref, add "
                '`git = { url = "…", ref = "<commit SHA>" }`'
            )
            % {"name": name}
        )
    if "url" not in git:
        # only a legacy quark can leave it out, the CLI looks it up in the quarks
        # directory then; the index needs a fixed URL
        raise ValidationError(
            _(
                '%(name)s is a legacy quark pinned by name, add `url = "…"` and '
                '`ref = "<commit SHA>"` to its git table'
            )
            % {"name": name}
        )
    if len([key for key in ("branch", "tag", "ref") if key in git]) > 1:
        raise ValidationError(
            _("%(name)s can only have one of branch, tag or ref") % {"name": name}
        )
    ref = git.get("ref")
    if ref is None:
        if "tag" in git:
            problem = _("%(name)s is pinned to a git tag, which can move, %(hint)s")
        elif "branch" in git:
            problem = _("%(name)s is pinned to a git branch, %(hint)s")
        else:
            problem = _("%(name)s is a git dependency without a ref, %(hint)s")
        raise ValidationError(problem % {"name": name, "hint": hint})
    if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        raise ValidationError(
            _("%(name)s is pinned to an abbreviated commit, %(hint)s")
            % {"name": name, "hint": hint}
        )
    try:
        validate_git_url(git["url"])
    except ValidationError as e:
        raise ValidationError([f"{name}: {message}" for message in e.messages]) from e
    return git_index_entry(
        url=git["url"],
        ref=ref.lower(),
        legacy_quark=value.get("legacy-quark", False),
        ignore_quark_deps=value.get("ignore-quark-deps", False),
    )


def manifest_dependencies(manifest: dict[str, Any], own_name: str) -> dict[str, Any]:
    """The publishable dependencies of a manifest as they go into the index line:
    normalized name -> version requirement or git dependency (`git_dependency`),
    sorted. Refuses everything others couldn't reproduce (like the CLI's
    `check_publishable`) and magnet packages without a final version."""
    problems = []
    dependencies: dict[str, Any] = {}
    for name, value in manifest.get("dependencies", {}).items():
        entry: Any
        if isinstance(value, str):
            entry = value
        elif "path" in value:
            problems.append(_("%(name)s is a local path dependency") % {"name": name})
            continue
        elif "index" in value:
            problems.append(_("%(name)s comes from another index") % {"name": name})
            continue
        elif "git" in value or value.get("legacy-quark"):
            try:
                entry = git_dependency(name, value)
            except ValidationError as e:
                problems += e.messages
                continue
        else:
            # like the CLI, a table without a version accepts any version
            entry = value.get("version", "*")

        normalized_name = normalize_name(name)
        try:
            validate_name(name)
            if isinstance(entry, str):
                validate_version_req(entry)
        except ValidationError as e:
            problems += [f"{name}: {message}" for message in e.messages]
            continue
        if normalized_name == own_name:
            problems.append(_("%(name)s can't depend on itself") % {"name": name})
        elif normalized_name in dependencies:
            problems.append(_("%(name)s is listed twice") % {"name": name})
        elif (
            isinstance(entry, str)
            and not Project.objects.public()
            .filter(normalized_name=normalized_name)
            .exists()
        ):
            problems.append(
                _("%(name)s is not a package with a published version") % {"name": name}
            )
        else:
            dependencies[normalized_name] = entry
    if problems:
        raise ValidationError(problems)
    return dict(sorted(dependencies.items()))


def index_fields(manifest: dict[str, Any], release: Release) -> IndexFields:
    """Checks the fields of a (schema-valid) manifest which reach the index."""
    package = manifest["package"]
    validate_name(package["name"])
    if normalize_name(package["name"]) != release.project.normalized_name:
        raise ValidationError(
            _("The manifest is for %(name)s, not %(project)s.")
            % {"name": package["name"], "project": release.project.normalized_name}
        )
    if package["version"] != release.version:
        raise ValidationError(
            _("The manifest is for version %(version)s, not %(release)s.")
            % {"version": package["version"], "release": release.version}
        )
    sclang = package.get("sclang")
    if sclang is not None:
        validate_version_req(sclang)
    return IndexFields(
        kinds=declared_kinds(manifest),
        sclang=sclang,
        dependencies=manifest_dependencies(manifest, release.project.normalized_name),
    )


def stored_index_fields(release: Release) -> IndexFields:
    return IndexFields(
        kinds=release.kinds,
        sclang=release.sclang or None,
        dependencies=release.index_dependencies(),
    )


def _store_dependencies(release: Release, dependencies: dict[str, Any]) -> None:
    """Stores dependencies in the form of `manifest_dependencies`."""
    for name, entry in dependencies.items():
        dependency: Dependency | GitDependency
        if isinstance(entry, str):
            dependency = Dependency(
                release=release,
                dependency=Project.objects.get(normalized_name=name),
                constraint=entry,
            )
        else:
            dependency = GitDependency(
                release=release,
                name=name,
                url=entry["git"]["url"],
                ref=entry["git"]["ref"],
                legacy_quark=entry.get("legacy-quark", False),
                ignore_quark_deps=entry.get("ignore-quark-deps", False),
            )
        dependency.full_clean()
        dependency.save()


def _store_manifest(
    release: Release, inspected: InspectedArtifact, fields: IndexFields
) -> None:
    """Stores the manifest of a version's first artifact."""
    metadata = inspected.manifest.get("metadata", {})
    release.kinds = fields.kinds
    release.sclang = fields.sclang or ""
    release.summary = metadata.get("summary", "")
    release.description = metadata.get("description", "")
    release.license = metadata.get("license", "")
    release.repository_url = metadata.get("repository", "")
    release.readme = inspected.readme or ""
    release.full_clean()
    release.save()
    _store_dependencies(release, fields.dependencies)

    # the CLI sends the normalized name, the manifest has the spelling
    project = release.project
    name = inspected.manifest["package"]["name"]
    if project.name != name and not project.releases.final().exists():
        project.name = name
        project.save(update_fields=["name"])


def _store_asset(
    release: Release,
    uploader: User,
    *,
    kind: str,
    abi: str,
    platform: str,
    file: File,
    sha256: str = "",
    uploaded_via: str = "",
    validate_file: bool = True,
) -> Asset:
    asset = Asset(
        release=release,
        uploader=uploader,
        file=file,
        kind=kind,
        abi=abi,
        platform=platform,
        sha256_checksum=sha256,
        uploaded_via=uploaded_via,
    )
    asset.full_clean(exclude=None if validate_file else ["file"])
    asset.save()
    return asset


def add_artifact(
    user: User,
    release: Release,
    *,
    kind: str,
    abi: str,
    platform: str,
    file: File,
    sha256: str,
    uploaded_via: str = "",
) -> tuple[Asset, bool]:
    """Adds an artifact of `kind` to a draft or a final version (an append, which
    reaches the index right away), and whether it was added: an identical artifact
    is returned as is. `sha256` is what the client sent, the server computes its own.

    The archive is inspected (`inspect_artifact`), so its manifest must declare
    `kind` (every version has a `source`) and match the release. The first artifact
    of a version stores the manifest's kinds, `sclang`, dependencies and metadata,
    every later one must agree on the index fields."""
    ensure_can_publish(user, release.project)
    validate_artifact_tag(abi)
    validate_artifact_tag(platform)
    validate_artifact_kind_tags(kind, abi, platform)
    actual = file_sha256(file)
    if actual != sha256:
        raise ValidationError(
            _("The sha256 of the upload is %(actual)s, not %(sent)s.")
            % {"actual": actual, "sent": sha256}
        )
    if existing := _existing_asset(release, kind, abi, platform, actual):
        return existing, False

    inspected = inspect_artifact(file, kind, abi, platform)
    fields = index_fields(inspected.manifest, release)

    with transaction.atomic():
        # serializes the uploads of a version, e.g. of a CI matrix
        release = (
            Release.objects.select_for_update()
            .select_related("project")
            .get(pk=release.pk)
        )
        if existing := _existing_asset(release, kind, abi, platform, actual):
            return existing, False
        if release.assets.exists():
            stored = stored_index_fields(release)
            if differences := [
                field
                for field in ("kinds", "sclang", "dependencies")
                if getattr(stored, field) != getattr(fields, field)
            ]:
                raise ValidationError(
                    _(
                        "The manifest differs from the one of the version's "
                        "earlier artifacts in: %(fields)s"
                    )
                    % {"fields": ", ".join(differences)}
                )
        else:
            _store_manifest(release, inspected, fields)

        asset = _store_asset(
            release,
            user,
            kind=kind,
            abi=abi,
            platform=platform,
            file=file,
            sha256=actual,
            uploaded_via=uploaded_via,
            # inspected already
            validate_file=False,
        )
        if release.is_final:
            try:
                get_git_database().commit_asset(asset)
            except BaseException:
                # it was never published, and a retry would hit "artifact exists"
                asset.file.delete(save=False)
                raise
    logger.info(f"Added {asset} to {release}")
    return asset, True


def missing_artifacts(release: Release) -> list[str]:
    """What a version still needs before it can be finalised: its source artifact
    (always), the quark artifact if it declares `quark`, and at least one lepton
    artifact if it declares `lepton`. More lepton artifacts can be appended to the
    final version. "At least one binary" is a policy, which gets relaxed once the
    client can build leptons from their source."""
    uploaded = set(release.assets.values_list("kind", flat=True))
    missing = []
    if SOURCE_KIND not in uploaded:
        missing.append(_("its source artifact"))
    if "quark" in release.kinds and "quark" not in uploaded:
        missing.append(_("its quark artifact"))
    if "lepton" in release.kinds and "lepton" not in uploaded:
        missing.append(_("a lepton artifact"))
    return missing


def finalise(user: User, release: Release) -> Release:
    """`finalise_release` for an owner or maintainer of the package."""
    ensure_can_publish(user, release.project)
    return finalise_release(release)


def finalise_release(release: Release, *, complete: bool = True) -> Release:
    """Writes the draft into the index, which publishes it, once it has the artifacts
    of `missing_artifacts`. The management commands publish without that check
    (`complete=False`), e.g. the CLI's fixtures, whose quark versions have no source.
    A final version is returned as is."""
    with transaction.atomic():
        release = (
            Release.objects.select_for_update()
            .select_related("project")
            .get(pk=release.pk)
        )
        if release.is_final:
            return release
        if not release.kinds:
            raise ValidationError(
                _("%(release)s has no artifacts yet.") % {"release": label(release)}
            )
        if complete and (missing := missing_artifacts(release)):
            raise ValidationError(
                _("%(release)s needs %(missing)s before it can be finalised.")
                % {"release": label(release), "missing": join_and(missing)}
            )
        get_git_database().commit_release(release)
    logger.info(f"Finalised {release}")
    return release


def delete_draft(user: User, release: Release) -> None:
    """Deletes a draft with its artifacts, which frees its version."""
    ensure_can_publish(user, release.project)
    with transaction.atomic():
        release = Release.objects.select_for_update().get(pk=release.pk)
        if release.is_final:
            raise Conflict(
                _("%(release)s is final and can't be deleted, yank it instead.")
                % {"release": label(release)}
            )
        files = [asset.file for asset in release.assets.all()]
        release.delete()

        def delete_files():
            for file in files:
                file.storage.delete(file.name)

        transaction.on_commit(delete_files)
    logger.info(f"Deleted draft {release}")


def _ensure_final(release: Release) -> None:
    if not release.is_final:
        raise ValidationError(
            _("%(release)s is a draft, only final versions can be yanked.")
            % {"release": label(release)}
        )


def yank(user: User, release: Release, reason: str = "") -> None:
    """Yanks a final version: the resolver skips it for new lock files, existing
    ones keep it (and `magnet install` warns). Its artifacts stay downloadable."""
    ensure_can_publish(user, release.project)
    _ensure_final(release)
    if not release.yanked:
        release.yank_git_entry(reason)
        logger.info(f"Yanked {release}")


def unyank(user: User, release: Release) -> None:
    """Undoes a yank."""
    ensure_can_publish(user, release.project)
    _ensure_final(release)
    if release.yanked:
        release.unyank_git_entry()
        logger.info(f"Unyanked {release}")


@dataclass
class ArtifactFile:
    """An artifact for `publish_release`."""

    kind: str
    file: File
    abi: str = ANY
    platform: str = ANY


@transaction.atomic
def publish_release(
    *,
    name: str,
    version: str,
    kinds: list[str],
    artifacts: list[ArtifactFile],
    owner: User,
    dependencies: dict[str, Any] | None = None,
    uploaded_via: str = "",
) -> Release:
    """Publishes a version with its artifacts in one go (draft, artifacts,
    finalise), for the management commands. Unlike `add_artifact`, the kinds and
    dependencies are passed in and the artifacts only get the M1 checks, since the
    M1 fixture tarballs have no `magnet.toml`, and the version needs no particular
    artifacts (`missing_artifacts`), only at least one.

    Dependencies map a package name (any spelling) to a version requirement, which
    must refer to an existing project, or to a git dependency in the form of the
    index line (also the one of the manifest). If a step fails, the database
    changes are rolled back and the stored artifacts are removed again."""
    project = get_or_create_project(name=name, owner=owner)
    release, created = create_draft(owner, name, version)
    if not created:
        raise ValidationError(
            _("%(release)s exists already.") % {"release": label(release)}
        )
    release.kinds = kinds
    release.full_clean()
    release.save(update_fields=["kinds"])

    for dependency_name, requirement in (dependencies or {}).items():
        if not isinstance(requirement, str):
            _store_dependencies(
                release,
                {
                    normalize_name(dependency_name): git_dependency(
                        dependency_name, requirement
                    )
                },
            )
            continue
        dependency_project = Project.objects.filter(
            normalized_name=normalize_name(dependency_name)
        ).first()
        if dependency_project is None:
            raise ValidationError(
                _("The dependency %(name)s is not a known package.")
                % {"name": dependency_name}
            )
        dependency = Dependency(
            release=release, dependency=dependency_project, constraint=requirement
        )
        dependency.full_clean()
        dependency.save()

    assets: list[Asset] = []
    try:
        for artifact in artifacts:
            assets.append(
                _store_asset(
                    release,
                    owner,
                    kind=artifact.kind,
                    abi=artifact.abi,
                    platform=artifact.platform,
                    file=artifact.file,
                    uploaded_via=uploaded_via,
                )
            )
        # the owner may publish (`create_draft`); without the finalise rules
        finalise_release(release, complete=False)
    except BaseException:
        for asset in assets:
            asset.file.delete(save=False)
        raise

    logger.info(f"Published {project.normalized_name} {version}")
    return release
