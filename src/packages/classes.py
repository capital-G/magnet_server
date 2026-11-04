"""The classes, class extensions and methods of a version, read from the `.sc` files
of its quark artifact (the files sclang compiles; leptons only ship binaries)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from django.core.files import File
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.tasks import task
from django.utils import timezone

from packages.archive import read_files
from packages.models import (
    Asset,
    ClassDefinition,
    ClassFileError,
    MethodDefinition,
    Project,
    Release,
)
from packages.sclang import compiled_folder, parse

logger = logging.getLogger(__name__)


# a class or extension with its methods, saved or not
Definition = tuple[ClassDefinition, list[MethodDefinition]]


def read_class_files(file: File) -> tuple[list[Definition], list[ClassFileError]]:
    """The classes (unsaved, without release) of the `.sc` files in an archive which
    sclang compiles on any platform or IDE (`compiled_folder`); each class remembers
    its platform folder. Also the files that don't parse."""
    definitions: list[Definition] = []
    errors: list[ClassFileError] = []
    platforms: dict[str, str] = {}

    def wanted(path: str) -> bool:
        folder = compiled_folder(path, platform=None, ide=None)
        if folder is not None:
            platforms[path] = folder
        return folder is not None

    for path, data in read_files(file, wanted):
        # sclang reads bytes; anything that isn't ASCII only matters in strings
        parsed = parse(data.decode("utf-8", errors="replace"))
        if parsed.error:
            errors.append(ClassFileError(path=path, message=parsed.error))
        for c in parsed.classes:
            definition = ClassDefinition(
                name=c.name,
                superclass=c.superclass,
                is_extension=c.is_extension,
                path=path,
                line=c.line,
                platform=platforms[path],
            )
            methods = [
                MethodDefinition(
                    class_definition=definition,
                    name=m.name,
                    is_class_method=m.is_class_method,
                )
                for m in c.methods
            ]
            definitions.append((definition, methods))
    return definitions, errors


def extract_classes(release: Release) -> None:
    """Replaces the classes of `release` with those of its quark artifact."""
    definitions: list[Definition] = []
    errors: list[ClassFileError] = []
    quark = release.assets.filter(kind=Asset.Kind.quark).first()
    if quark is not None:
        definitions, errors = read_class_files(quark.file)
    for definition, _ in definitions:
        definition.release = release
    for error in errors:
        error.release = release
    with transaction.atomic():
        release.class_definitions.all().delete()
        release.class_file_errors.all().delete()
        ClassDefinition.objects.bulk_create([d for d, _ in definitions])
        MethodDefinition.objects.bulk_create(
            [m for _, methods in definitions for m in methods]
        )
        ClassFileError.objects.bulk_create(errors)
        release.classes_extracted_at = timezone.now()
        release.save(update_fields=["classes_extracted_at"])
    logger.info(
        f"Extracted {len(definitions)} classes of {release} ({len(errors)} errors)"
    )


@task
def extract_classes_task(release_id: str) -> None:
    """`extract_classes` as a task (`settings.TASKS`): inline in tests and
    runserver, on the `worker` service in docker/prod. The release may be gone by then (a deleted draft can't be final, but a
    hard-deleted version can)."""
    release = Release.objects.filter(pk=release_id).first()
    if release is not None:
        extract_classes(release)


def enqueue_extract_classes(release: Release) -> None:
    """Extracts the classes of `release` once the current transaction commits."""
    transaction.on_commit(lambda: extract_classes_task.enqueue(str(release.pk)))


def current_release(project: Project) -> Release | None:
    """The newest final, non-yanked version, the one search and conflicts use
    (like `ClassDefinition.objects.current()`)."""
    return (
        Release.objects.final()
        .filter(project=project, yanked=False)
        .order_by("-version_major", "-version_minor", "-version_patch")
        .first()
    )


@dataclass(frozen=True)
class NameConflict:
    """A clash in sclang's flat namespace with the current version of another
    package: the same class defined twice (sclang refuses to compile), or the same
    method added by two extensions (one silently replaces the other)."""

    kind: str  # "class" or "method"
    class_name: str
    # "" for a class conflict
    method: str
    is_class_method: bool
    other: Release

    @property
    def label(self) -> str:
        """`Foo`, or `Foo:-bar` / `Foo:*bar` like in SCDoc."""
        if self.kind == "class":
            return self.class_name
        return f"{self.class_name}:{'*' if self.is_class_method else '-'}{self.method}"


def _same_platform(a: ClassDefinition, b: ClassDefinition) -> bool:
    """Files in different platform folders are never compiled together."""
    return not (a.platform and b.platform and a.platform != b.platform)


def find_conflicts(
    own: list[Definition], exclude_project_id: object = None
) -> list[NameConflict]:
    """The conflicts of the classes `own` with the current versions of the other
    packages (all but `exclude_project_id`), sorted by kind, name and package."""
    if not own:
        return []
    others = (
        ClassDefinition.objects.current()
        .exclude(release__project_id=exclude_project_id)
        .filter(name__in={definition.name for definition, _ in own})
        .select_related("release__project")
        .prefetch_related("methods")
    )
    found: set[NameConflict] = set()
    for other in others:
        for mine, methods in own:
            if mine.name != other.name or not _same_platform(mine, other):
                continue
            if not mine.is_extension and not other.is_extension:
                found.add(NameConflict("class", mine.name, "", False, other.release))
            elif mine.is_extension and other.is_extension:
                theirs = {(m.name, m.is_class_method) for m in other.methods.all()}
                for method in methods:
                    if (method.name, method.is_class_method) in theirs:
                        found.add(
                            NameConflict(
                                "method",
                                mine.name,
                                method.name,
                                method.is_class_method,
                                other.release,
                            )
                        )
    return sorted(
        found,
        key=lambda c: (
            c.kind,
            c.class_name,
            c.method,
            c.other.project.normalized_name,
        ),
    )


def conflicts(release: Release) -> list[NameConflict]:
    """The conflicts of `release` (a version of a package) with the current
    versions of the other packages."""
    own = [
        (definition, list(definition.methods.all()))
        for definition in release.class_definitions.prefetch_related("methods")
    ]
    return find_conflicts(own, release.project_id)


@dataclass
class PackageConflicts:
    """The conflicts with one other package, for a short summary per package (a
    fork of a big quark would otherwise list hundreds of names)."""

    other: Release
    classes: list[NameConflict]
    methods: list[NameConflict]


def group_by_package(found: list[NameConflict]) -> list[PackageConflicts]:
    """`found` grouped by the other package, by its name."""
    # one current version per package
    groups: dict[str, PackageConflicts] = {}
    for conflict in found:
        group = groups.setdefault(
            conflict.other.project.normalized_name,
            PackageConflicts(conflict.other, [], []),
        )
        (group.classes if conflict.kind == "class" else group.methods).append(conflict)
    return [groups[name] for name in sorted(groups)]


def conflicts_by_package(release: Release) -> list[PackageConflicts]:
    """`conflicts(release)` grouped by the other package."""
    return group_by_package(conflicts(release))


@dataclass
class ClassFile:
    """A `.sc` file of a version with its classes and extensions, for the tree of
    the "Classes" tab."""

    path: str
    platform: str
    definitions: list[ClassDefinition]


def class_files(release: Release) -> list[ClassFile]:
    """The classes of `release` grouped by file, files by path, classes in file
    order; each definition's methods are prefetched."""
    files: dict[str, ClassFile] = {}
    for definition in release.class_definitions.order_by(
        "path", "line"
    ).prefetch_related("methods"):
        files.setdefault(
            definition.path, ClassFile(definition.path, definition.platform, [])
        ).definitions.append(definition)
    return list(files.values())


# results per kind (classes, methods) of `search`
SEARCH_LIMIT = 50


@dataclass
class SearchResults:
    classes: Sequence[ClassDefinition]
    methods: Sequence[MethodDefinition]
    # whether there were more than `SEARCH_LIMIT` of a kind
    truncated: bool


def search(query: str) -> SearchResults:
    """Classes, extensions and methods of the packages' current versions whose name
    starts with `query` (ignoring case), exact matches first; e.g. to find the
    package that provides a missing class or method."""
    query = query.strip()
    if not query:
        return SearchResults([], [], False)

    def exact_first(field: str) -> Case:
        return Case(
            When(**{f"{field}__iexact": query}, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )

    current = ClassDefinition.objects.current()
    classes = list(
        current.filter(name__istartswith=query)
        .alias(exact=exact_first("name"))
        .select_related("release__project")
        .order_by("exact", "name", "is_extension", "release__project__normalized_name")[
            : SEARCH_LIMIT + 1
        ]
    )
    methods = list(
        MethodDefinition.objects.filter(
            class_definition__in=current, name__istartswith=query
        )
        .alias(exact=exact_first("name"))
        .select_related("class_definition__release__project")
        .order_by(
            "exact",
            "name",
            "class_definition__name",
            "class_definition__release__project__normalized_name",
        )[: SEARCH_LIMIT + 1]
    )
    return SearchResults(
        classes[:SEARCH_LIMIT],
        methods[:SEARCH_LIMIT],
        len(classes) > SEARCH_LIMIT or len(methods) > SEARCH_LIMIT,
    )
