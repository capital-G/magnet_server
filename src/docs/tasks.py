"""Builds the docs of a version (docs_plan.md "Build flow"), as a task of the queue
`docs`, run by its own worker one at a time:

1. skip the version if it isn't the package's current one anymore
2. install: `magnet install --no-binaries` of the version in a temporary project, with
   network but without sclang, so a `sclang` requirement the builder doesn't meet
   doesn't stop it (the build gets `sclang_mismatch` instead), and without the binaries
   of leptons, which docs don't need
3. render: docs_builder/render.scd, without network, the packages read-only
4. post-process the output (`docs.rewrite`): links by owner, the magnet bar
5. publish it (`docs.publish`); a failed build keeps the previous docs
"""

import json
import logging
import re
import shutil
import traceback
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.tasks import task
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from docs import publish, rewrite
from docs.models import DocBuild
from docs.runner import Container, Mount, Runner, get_runner
from docs.status import latest_build
from packages.classes import current_release
from packages.models import Project, Release
from packages.validators import version_req_matches

logger = logging.getLogger(__name__)

# characters of a build's log, which loses its middle beyond that
LOG_LIMIT = 200_000
# paths in the builder image (docs_builder/Dockerfile)
MAGNET = "/usr/local/bin/magnet"
CLASS_LIBRARY = "/usr/local/share/SuperCollider/SCClassLibrary"
RENDER_SCRIPT = "/opt/magnet/render.scd"
# a PATH without sclang, so `magnet install` resolves without a target sclang
INSTALL_PATH = "/usr/bin:/bin"
DOCMAP = "docmap.json"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# how often owners may rebuild the docs of their package by hand
REBUILD_INTERVAL = timedelta(minutes=10)


def request_build(release: Release) -> DocBuild | None:
    """Queues a build of the docs of `release` (enqueued once the transaction
    commits), unless builds are off (`MAGNET_DOCS_BUILDS`). A package has at most one
    queued build: if there is one, it builds `release` instead."""
    if not settings.MAGNET_DOCS_BUILDS:
        return None
    with transaction.atomic():
        queued = (
            DocBuild.objects.select_for_update()
            .filter(release__project=release.project_id, status=DocBuild.Status.queued)
            .first()
        )
        if queued is not None:
            if queued.release_id != release.pk:
                queued.release = release
                queued.save(update_fields=["release"])
            return queued
        build = DocBuild.objects.create(release=release)
        transaction.on_commit(lambda: build_docs_task.enqueue(build.pk))
    return build


def update_docs(project: Project) -> None:
    """After the versions of `project` changed (finalised, yanked, un-yanked, deleted):
    builds the docs of the current version unless its latest build is for it already
    (also a failed one, rebuilding is up to `rebuild`; a built one only while its docs
    are published), removes them if no version is left. Nothing without
    `MAGNET_DOCS_BUILDS`."""
    if not settings.MAGNET_DOCS_BUILDS:
        return
    current = current_release(project)
    if current is None:
        name = project.normalized_name
        transaction.on_commit(lambda: publish.unpublish(name))
        return
    latest = latest_build(project)
    if latest is not None and latest.release_id == current.pk:
        # built docs are gone if no version was left meanwhile (yank, un-yank)
        built = latest.status == DocBuild.Status.built
        if not built or publish.published_dir(project.normalized_name) is not None:
            return
    request_build(current)


def rebuild(project: Project) -> DocBuild | None:
    """Builds the docs of the current version again (admin, `build_docs`); None if
    the package has none or builds are off."""
    current = current_release(project)
    return request_build(current) if current is not None else None


def owner_rebuild(user: User | AnonymousUser, project: Project) -> DocBuild:
    """`rebuild` for an owner of the package (the docs page): not while a build is
    queued or running, and at most once per `REBUILD_INTERVAL`."""
    if not project.can_manage(user):
        raise PermissionDenied
    if not settings.MAGNET_DOCS_BUILDS:
        raise ValidationError(_("Docs builds are off."))
    latest = latest_build(project)
    if latest is not None:
        if latest.status in (DocBuild.Status.queued, DocBuild.Status.building):
            raise ValidationError(_("A build is queued or running already."))
        if latest.created_at > timezone.now() - REBUILD_INTERVAL:
            raise ValidationError(
                _(
                    "The last build is less than %(minutes)d minutes old, try again later."
                )
                % {"minutes": int(REBUILD_INTERVAL.total_seconds() // 60)}
            )
    build = rebuild(project)
    if build is None:
        raise ValidationError(_("The package has no current version."))
    return build


@task(queue_name="docs")
def build_docs_task(build_id: int) -> None:
    build = (
        DocBuild.objects.select_related("release__project").filter(pk=build_id).first()
    )
    # gone with its version, or built by hand already
    if build is not None and build.status == DocBuild.Status.queued:
        build_docs(build)


def build_docs(build: DocBuild) -> None:
    release = build.release
    build.started_at = timezone.now()
    if current_release(release.project) != release:
        _finish(
            build,
            DocBuild.Status.skipped,
            log=[f"{release} isn't the current version of the package anymore\n"],
        )
        return

    build.status = DocBuild.Status.building
    build.sclang_version = settings.MAGNET_DOCS_SCLANG_VERSION
    build.sclang_mismatch = bool(release.sclang) and not version_req_matches(
        release.sclang, build.sclang_version
    )
    build.save()

    log: list[str] = []
    work = settings.MAGNET_DOCS_WORK_DIR / str(build.pk)
    try:
        error = _build(build, work, log)
    except Exception as exception:
        logger.exception("Building %s failed", build)
        log.append(traceback.format_exc())
        error = f"unexpected error: {exception}"
    finally:
        # files of the containers' user may stay, the next build of this id is far off
        shutil.rmtree(work, ignore_errors=True)
    _finish(
        build,
        DocBuild.Status.failed if error else DocBuild.Status.built,
        error=error or "",
        log=log,
    )


def _build(build: DocBuild, work: Path, log: list[str]) -> str | None:
    """Steps 2-5, returns why the build failed (None if it didn't)."""
    release = build.release
    name = release.project.normalized_name
    shutil.rmtree(work, ignore_errors=True)
    out = work / "out"
    out.mkdir(parents=True)
    manifest = work / "magnet.toml"
    manifest.write_text(
        "magnet-version = 1\n\n"
        '[package]\nname = "magnet-docs-build"\nversion = "0.0.0"\n\n'
        f"[dependencies]\n{json.dumps(name)} = {json.dumps('=' + release.version)}\n"
    )
    # the containers run as the image's user, which isn't the worker's; the CLI
    # opens the manifest for writing
    for path in (work, out, manifest):
        path.chmod(0o777 if path.is_dir() else 0o666)

    runner = get_runner()
    env = {"PATH": INSTALL_PATH}
    if settings.MAGNET_DOCS_GIT_INDEX_URL:
        env["MAGNET_GIT_INDEX_URL"] = settings.MAGNET_DOCS_GIT_INDEX_URL
    install = Container(
        name=f"magnet-docs-{build.pk}-install",
        # docs need no binaries, and without sclang a lepton's would need a target
        command=[MAGNET, "install", "--no-binaries"],
        mounts=[Mount(work, "/work")],
        env=env,
        network=settings.MAGNET_DOCS_INSTALL_NETWORK or None,
        workdir="/work",
    )
    if error := _run(runner, install, "install", log):
        return error

    render = Container(
        name=f"magnet-docs-{build.pk}-render",
        command=[
            "sclang",
            "-a",
            "--include-path",
            CLASS_LIBRARY,
            "--include-path",
            "/packages",
            RENDER_SCRIPT,
            "/packages",
            name,
            "/out",
        ],
        mounts=[
            Mount(work / "magnet_packages", "/packages", read_only=True),
            Mount(out, "/out"),
        ],
        network="none",
    )
    if error := _run(runner, render, "render", log):
        return error

    try:
        docmap = json.loads((out / DOCMAP).read_text())
    except (OSError, ValueError) as exception:
        log.append(f"{DOCMAP}: {exception}\n")
        return f"the render step wrote no {DOCMAP}"
    build.sclang_version = docmap.get("sclang_version") or build.sclang_version
    if failed := [e["document"] for e in docmap.get("errors", [])]:
        log.append(f"documents which failed to render: {', '.join(failed)}\n")

    target = publish.build_dir(name, build.pk)
    shutil.rmtree(target, ignore_errors=True)
    try:
        build.documents = rewrite.postprocess(
            out, target, docmap, context(build, docmap)
        )
        publish.publish(name, target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return None


def context(build: DocBuild, docmap: dict[str, Any]) -> rewrite.Context:
    release = build.release
    project = release.project
    dependencies = {
        document["dependency"]
        for document in docmap["documents"].values()
        if document["owner"] == "dependency"
    }
    return rewrite.Context(
        name=project.normalized_name,
        version=release.version,
        summary=release.summary,
        sclang=release.sclang,
        sclang_version=build.sclang_version,
        sclang_mismatch=build.sclang_mismatch,
        project_url=urljoin(
            settings.MAGNET_API_BASE_URL, reverse("project-detail", args=[project.slug])
        ),
        repository_url=release.repository_url,
        docs_base_url=settings.MAGNET_DOCS_BASE_URL,
        hosted_dependencies=frozenset(
            Project.objects.public()
            .filter(normalized_name__in=dependencies)
            .values_list("normalized_name", flat=True)
        ),
    )


def _run(runner: Runner, container: Container, step: str, log: list[str]) -> str | None:
    log.append(f"$ {step}\n")
    try:
        result = runner.run(container, timeout=settings.MAGNET_DOCS_TIMEOUT)
    except OSError as exception:
        log.append(f"{exception}\n")
        return f"the {step} container could not be started: {exception}"
    # the CLI colours its log
    log.append(ANSI_RE.sub("", result.output))
    if result.timed_out:
        return f"{step} timed out after {settings.MAGNET_DOCS_TIMEOUT} seconds"
    if result.returncode != 0:
        return f"{step} failed (exit code {result.returncode})"
    return None


def shorten_log(log: str, limit: int = LOG_LIMIT) -> str:
    """Keeps the beginning (e.g. compile errors) and the end of a long log."""
    if len(log) <= limit:
        return log
    half = limit // 2
    return f"{log[:half]}\n[… {len(log) - 2 * half} characters …]\n{log[-half:]}"


def _finish(
    build: DocBuild, status: str, error: str = "", log: Iterable[str] = ()
) -> None:
    build.status = status
    build.error = error[:255]
    build.log = shorten_log("".join(log))
    build.finished_at = timezone.now()
    build.save()
    logger.info("%s: %s %s", build, status, error)
