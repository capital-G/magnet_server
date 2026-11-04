"""Runs the containers of a docs build with the docker or podman CLI
(`settings.MAGNET_DOCS_RUNNER`): the same flags work for both engines, also against a
remote socket (`DOCKER_HOST`, `CONTAINER_HOST`). Every container is read-only, without
capabilities and limited in memory, processes and time (docs_plan.md "Deployment
decisions")."""

import logging
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool = False


@dataclass(frozen=True)
class Container:
    """A container to run: `command` in the builder image."""

    name: str
    command: list[str]
    mounts: list[Mount] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # the engine's default network if None
    network: str | None = None
    workdir: str | None = None


@dataclass(frozen=True)
class Result:
    # None if the container was killed after the timeout
    returncode: int | None
    output: str

    @property
    def timed_out(self) -> bool:
        return self.returncode is None


class Runner:
    """Starts containers via the CLI, waits at most `timeout` seconds for each."""

    def __init__(self, cli: list[str] | None = None) -> None:
        self.cli = cli if cli is not None else shlex.split(settings.MAGNET_DOCS_RUNNER)

    def run_command(self, container: Container) -> list[str]:
        command = [
            *self.cli,
            "run",
            "--rm",
            "--name",
            container.name,
            "--read-only",
            # HOME for sclang's user app support folder and the CLI's caches
            "--tmpfs",
            "/tmp",
            "--env",
            "HOME=/tmp/home",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            settings.MAGNET_DOCS_MEMORY,
            "--pids-limit",
            str(settings.MAGNET_DOCS_PIDS_LIMIT),
        ]
        if settings.MAGNET_DOCS_CPUS:
            command += ["--cpus", settings.MAGNET_DOCS_CPUS]
        if container.network:
            command += ["--network", container.network]
        for mount in container.mounts:
            # z: relabels the folder for containers where SELinux enforces (e.g.
            # podman machine), a no-op elsewhere
            options = "ro,z" if mount.read_only else "z"
            command += ["--volume", f"{mount.source}:{mount.target}:{options}"]
        for name, value in container.env.items():
            command += ["--env", f"{name}={value}"]
        if container.workdir:
            command += ["--workdir", container.workdir]
        return [*command, settings.MAGNET_DOCS_IMAGE, *container.command]

    def run(self, container: Container, timeout: float) -> Result:
        command = self.run_command(container)
        logger.info("Running %s", shlex.join(command))
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as expired:
            # only the CLI got killed, the container keeps running until it's killed
            # (`--rm` removes it then)
            self.kill(container.name)
            return Result(None, _decode(expired.output))
        return Result(completed.returncode, _decode(completed.stdout))

    def kill(self, name: str) -> None:
        subprocess.run(
            [*self.cli, "kill", name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )


def _decode(output: bytes | None) -> str:
    return (output or b"").decode("utf-8", errors="replace")


def get_runner() -> Runner:
    """The runner of the builds; tests replace it."""
    return Runner()
