"""The container CLI calls of `docs.runner`."""

import sys
from pathlib import Path

from docs.runner import Container, Mount, Runner


def test_run_command(settings):
    settings.MAGNET_DOCS_IMAGE = "builder:test"
    settings.MAGNET_DOCS_MEMORY = "1g"
    settings.MAGNET_DOCS_PIDS_LIMIT = 100
    settings.MAGNET_DOCS_CPUS = ""
    container = Container(
        name="magnet-docs-1-render",
        command=["sclang", "render.scd"],
        mounts=[Mount(Path("/w/pkgs"), "/packages", read_only=True)],
        env={"A": "b"},
        network="none",
        workdir="/work",
    )

    command = Runner(["podman", "--remote"]).run_command(container)

    assert command == [
        "podman",
        "--remote",
        "run",
        "--rm",
        "--name",
        "magnet-docs-1-render",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "--env",
        "HOME=/tmp/home",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "1g",
        "--pids-limit",
        "100",
        "--network",
        "none",
        "--volume",
        "/w/pkgs:/packages:ro,z",
        "--env",
        "A=b",
        "--workdir",
        "/work",
        "builder:test",
        "sclang",
        "render.scd",
    ]


def test_cpus_and_the_runner_come_from_the_settings(settings):
    settings.MAGNET_DOCS_RUNNER = "docker --context x"
    settings.MAGNET_DOCS_CPUS = "1.5"

    command = Runner().run_command(Container(name="c", command=["true"]))

    assert command[:3] == ["docker", "--context", "x"]
    assert command[command.index("--cpus") + 1] == "1.5"
    assert "--network" not in command


def fake_cli(tmp_path: Path, script: str) -> list[str]:
    """A CLI that records its arguments and runs `script` for `run`."""
    path = tmp_path / "cli.py"
    path.write_text(
        "import sys, time\n"
        f"open({str(tmp_path / 'calls')!r}, 'a').write(' '.join(sys.argv[1:3]) + '\\n')\n"
        "if sys.argv[1] == 'run':\n"
        f"    {script}\n"
    )
    return [sys.executable, str(path)]


def test_run_returns_the_output_and_exit_code(tmp_path):
    runner = Runner(fake_cli(tmp_path, "print('out'); sys.exit(3)"))

    result = runner.run(Container(name="c", command=["x"]), timeout=10)

    assert (result.returncode, result.output, result.timed_out) == (3, "out\n", False)


def test_a_container_running_too_long_gets_killed(tmp_path):
    runner = Runner(fake_cli(tmp_path, "print('started', flush=True); time.sleep(30)"))

    result = runner.run(Container(name="slow", command=["x"]), timeout=1)

    assert result.timed_out
    assert result.output == "started\n"
    assert (tmp_path / "calls").read_text() == "run --rm\nkill slow\n"
