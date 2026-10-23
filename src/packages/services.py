"""Publishing, shared by the management commands and (in M2) the publish API."""

import logging

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.utils.translation import gettext as _

from git_db.db import get_git_database
from packages.models import ANY, Asset, Dependency, Project, Release
from packages.validators import normalize_name

logger = logging.getLogger(__name__)


def get_or_create_project(*, name: str, kind: str, owner: User) -> Project:
    """The project with the normalized `name`, created if missing.
    Its kind must match, since the kind of a package is fixed (D2.3)."""
    project = Project.objects.filter(normalized_name=normalize_name(name)).first()
    if project is None:
        project = Project(name=name, kind=kind, owner=owner)
        project.full_clean()
        project.save()
        logger.info(f"Created project {project}")
    elif project.kind != kind:
        raise ValidationError(
            _("%(project)s is a %(kind)s, not a %(requested)s.")
            % {"project": project, "kind": project.kind, "requested": kind}
        )
    return project


@transaction.atomic
def publish_release(
    *,
    name: str,
    version: str,
    kind: str,
    file: File,
    owner: User,
    dependencies: dict[str, str] | None = None,
    abi: str = ANY,
    platform: str = ANY,
    uploaded_via: str = "",
) -> Release:
    """Creates the release with its dependencies and artifact (and the project, if
    it's new) and writes it into the index, which publishes it.

    Dependencies map a package name (any spelling) to a version requirement and
    must refer to existing projects. Everything is validated before anything is
    stored; if a later step fails, the database changes are rolled back and the
    stored artifact is removed again, since it was never published.

    @todo M2: check that `owner` may publish to an existing project (maintainers).
    """
    project = get_or_create_project(name=name, kind=kind, owner=owner)

    release = Release(project=project, version=version, owner=owner)
    release.full_clean()
    release.save()

    for dependency_name, requirement in (dependencies or {}).items():
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

    asset = Asset(
        release=release,
        uploader=owner,
        file=file,
        abi=abi,
        platform=platform,
        uploaded_via=uploaded_via,
    )
    asset.full_clean()
    asset.save()

    try:
        get_git_database().commit_release(release)
    except BaseException:
        asset.file.delete(save=False)
        raise

    logger.info(f"Published {project.normalized_name} {version}")
    return release
