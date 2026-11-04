"""What the website shows about the docs of a package."""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings

from docs import publish
from docs.models import DocBuild
from packages.models import ClassDefinition, Project


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


def add_docs_urls(definitions: Iterable[ClassDefinition]) -> None:
    """Sets `docs_url` on the class definitions (not extensions) whose version has
    published docs with a page of the class (the Classes tab, the class search)."""
    definitions = [d for d in definitions if not d.is_extension]
    releases = {d.release_id: d.release for d in definitions}
    if not releases:
        return
    # each package's docs are those of its newest built build, if published
    builds = (
        DocBuild.objects.filter(
            release__project__in={r.project_id for r in releases.values()},
            status=DocBuild.Status.built,
        )
        .select_related("release__project")
        .order_by("release__project", "-created_at", "-pk")
    )
    pages: dict[tuple[UUID, str], str] = {}
    seen: set[UUID] = set()
    for build in builds:
        project = build.release.project
        if project.pk in seen:
            continue
        seen.add(project.pk)
        if build.release_id not in releases:
            continue
        if publish.published_dir(project.normalized_name) is None:
            continue
        for path in build.documents:
            folder, _, name = path.partition("/")
            if folder == "Classes":
                pages[(build.release_id, name)] = f"{docs_url(project)}{path}.html"
    for definition in definitions:
        if url := pages.get((definition.release_id, definition.name)):
            definition.docs_url = url
