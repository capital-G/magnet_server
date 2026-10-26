"""Hard delete by an admin (DESIGN.md "Deletion"), e.g. for malware or a leaked
secret. Unlike a yank, it removes the version from the index, the database and the
storage. A tombstone keeps its version number from being used again, so a lock
file never refers to other contents, and downloads of its artifacts answer `410`
(a gone marker, which nginx checks), so the CLI can say what happened."""

import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction

from git_db.db import get_git_database
from packages.models import DeletedRelease, Dependency, Project, Release, ReservedName

logger = logging.getLogger(__name__)

# below MAGNET_ARTIFACTS_ROOT; `gone/<download path>` marks a deleted artifact
GONE_DIR = "gone"


def gone_marker(path: str) -> Path:
    """The marker of the artifact at the storage (= download) path `path`."""
    return Path(settings.MAGNET_ARTIFACTS_ROOT) / GONE_DIR / path


def _remove_artifacts(paths: list[str]) -> None:
    for path in paths:
        # the marker first, so the URL never answers 404 in between
        marker = gone_marker(path)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        default_storage.delete(path)
        logger.info(f"Deleted artifact {path}")


def dependents(project: Project) -> list[Release]:
    """Releases of other packages which depend on `project`."""
    return [
        dependency.release
        for dependency in Dependency.objects.filter(dependency=project)
        .exclude(release__project=project)
        .select_related("release__project")
    ]


def delete_release_permanently(release: Release, reason: str) -> None:
    """Deletes a version (final or draft) with its artifacts."""
    with transaction.atomic():
        release = (
            Release.objects.select_for_update()
            .select_related("project")
            .get(pk=release.pk)
        )
        name, version = release.project.normalized_name, release.version
        paths = [asset.file.name for asset in release.assets.all()]
        DeletedRelease.objects.create(
            normalized_name=name, version=version, reason=reason
        )
        was_final = release.is_final
        release.delete()
        # last, so a failed index write rolls the database back
        if was_final:
            get_git_database().remove_release(name, version)
        transaction.on_commit(lambda: _remove_artifacts(paths))
    logger.info(f"Deleted {name} {version} permanently: {reason}")


def delete_project_permanently(project: Project, reason: str) -> None:
    """Deletes a package with all its versions and reserves its name. Refused while
    other packages depend on it, since their index lines name it."""
    if releases := dependents(project):
        listed = ", ".join(f"{r.project.normalized_name} {r.version}" for r in releases)
        raise ValidationError(
            f"{project.normalized_name} can't be deleted, "
            f"other versions depend on it: {listed}"
        )
    with transaction.atomic():
        project = Project.objects.select_for_update().get(pk=project.pk)
        name = project.normalized_name
        releases = list(project.releases.all())
        paths = [
            asset.file.name for release in releases for asset in release.assets.all()
        ]
        DeletedRelease.objects.bulk_create(
            DeletedRelease(normalized_name=name, version=r.version, reason=reason)
            for r in releases
        )
        # also replaces a legacy quark reservation and its claim
        ReservedName.objects.update_or_create(
            normalized_name=name,
            defaults={
                "reason": ReservedName.Reason.deleted,
                "note": reason,
                "claimed_by": None,
            },
        )
        had_final = any(release.is_final for release in releases)
        project.delete()
        if had_final:
            get_git_database().remove_package(name)
        transaction.on_commit(lambda: _remove_artifacts(paths))
    logger.info(f"Deleted {name} permanently: {reason}")
