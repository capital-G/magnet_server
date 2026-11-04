"""What the website shows about the docs of a package."""

from dataclasses import dataclass

from django.conf import settings

from docs import publish
from docs.models import DocBuild
from packages.models import Project


def latest_build(project: Project, *statuses: str) -> DocBuild | None:
    """The newest build of the package (with one of `statuses`), skipped ones aside."""
    builds = DocBuild.objects.filter(release__project=project).exclude(
        status=DocBuild.Status.skipped
    )
    if statuses:
        builds = builds.filter(status__in=statuses)
    return builds.select_related("release").order_by("-created_at", "-pk").first()


def docs_url(project: Project) -> str:
    return f"{settings.MAGNET_DOCS_BASE_URL}{project.normalized_name}/"


@dataclass(frozen=True)
class DocsStatus:
    # the published docs, None if there are none
    url: str | None
    built: DocBuild | None
    # the newest build, for owners and maintainers
    latest: DocBuild | None


def docs_status(project: Project) -> DocsStatus:
    published = publish.published_dir(project.normalized_name) is not None
    return DocsStatus(
        url=docs_url(project) if published else None,
        built=latest_build(project, DocBuild.Status.built) if published else None,
        latest=latest_build(project),
    )
