"""The classes, class extensions and methods of a version, read from the `.sc` files
of its quark artifact (the files sclang compiles; leptons only ship binaries)."""

import logging

from django.db import transaction
from django.tasks import task
from django.utils import timezone

from packages.archive import read_files
from packages.models import (
    Asset,
    ClassDefinition,
    ClassFileError,
    MethodDefinition,
    Release,
)
from packages.sclang import compiled_folder, parse

logger = logging.getLogger(__name__)


def extract_classes(release: Release) -> None:
    """Replaces the classes of `release` with those of its quark artifact. All
    platform and IDE folders count (`compiled_folder`), each class remembers its
    platform folder."""
    definitions: list[tuple[ClassDefinition, list[MethodDefinition]]] = []
    errors: list[ClassFileError] = []
    quark = release.assets.filter(kind=Asset.Kind.quark).first()
    if quark is not None:
        platforms: dict[str, str] = {}

        def wanted(path: str) -> bool:
            folder = compiled_folder(path, platform=None, ide=None)
            if folder is not None:
                platforms[path] = folder
            return folder is not None

        for path, data in read_files(quark.file, wanted):
            # sclang reads bytes; anything that isn't ASCII only matters in strings
            parsed = parse(data.decode("utf-8", errors="replace"))
            if parsed.error:
                errors.append(
                    ClassFileError(release=release, path=path, message=parsed.error)
                )
            for c in parsed.classes:
                definition = ClassDefinition(
                    release=release,
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
    """`extract_classes` as a task (`settings.TASKS`): inline today, later on a
    worker. The release may be gone by then (a deleted draft can't be final, but a
    hard-deleted version can)."""
    release = Release.objects.filter(pk=release_id).first()
    if release is not None:
        extract_classes(release)


def enqueue_extract_classes(release: Release) -> None:
    """Extracts the classes of `release` once the current transaction commits."""
    transaction.on_commit(lambda: extract_classes_task.enqueue(str(release.pk)))
