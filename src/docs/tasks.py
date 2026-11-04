"""Builds the docs of a version (docs_plan.md "Build flow"), as a task of the queue
`docs`, run by its own worker one at a time:

1. skip the version if it isn't the package's current one anymore
2. install: `magnet install` of the version in a temporary project, with network but
   without sclang, so a `sclang` requirement the builder doesn't meet doesn't stop it
   (the build gets `sclang_mismatch` instead)
3. render: docs_builder/render.scd, without network, the packages read-only
4. post-process the output (`docs.rewrite`)
5. publish it (`docs.publish`); a failed build keeps the previous docs
"""

import json
import logging
import shutil
import traceback
from collections.abc import Iterable
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.tasks import task
from django.utils import timezone

from docs import publish, rewrite
from docs.models import DocBuild
from docs.runner import Container, Mount, Runner, get_runner
from packages.classes import current_release
from packages.models import Release
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
        command=[MAGNET, "install"],
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
        rewrite.postprocess(out, target, docmap)
        publish.publish(name, target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return None


def _run(runner: Runner, container: Container, step: str, log: list[str]) -> str | None:
    log.append(f"$ {step}\n")
    try:
        result = runner.run(container, timeout=settings.MAGNET_DOCS_TIMEOUT)
    except OSError as exception:
        log.append(f"{exception}\n")
        return f"the {step} container could not be started: {exception}"
    log.append(result.output)
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
