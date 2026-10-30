"""Which state the `../magnet_cli` checkout is in, for the tests with the real CLI.
During Milestone 3 the server leads and the CLI follows (`implementation_plan.md`,
"Coordination with the CLI"), so these tests skip while the two don't match."""

import json
from functools import cache
from pathlib import Path

import pytest

MAGNET_CLI = Path(__file__).resolve().parents[4] / "magnet_cli"
MAGNET = MAGNET_CLI / "target" / "debug" / "magnet"


@cache
def cli_has_artifact_kinds() -> bool:
    """Whether the CLI's manifest schema has the artifact kinds
    (`[artifact.quark]`, client step C8.1)."""
    path = MAGNET_CLI / "schemes" / "magnet.v1.schema.json"
    if not path.is_file():
        return False
    schema = json.loads(path.read_text())
    artifact = schema["properties"].get("artifact", {})
    return "quark" in artifact.get("properties", {})


def cli_publishes_artifact_kinds() -> bool:
    """Whether the debug build of the CLI uploads to the route with the artifact's
    kind (client step C8.6): its publish client has it, and the build is newer."""
    client = MAGNET_CLI / "src" / "http_client" / "publish.rs"
    if not (client.is_file() and MAGNET.is_file()):
        return False
    return (
        '"artifacts/{}/{}/{}/"' in client.read_text()
        and MAGNET.stat().st_mtime >= client.stat().st_mtime
    )


def fixtures_have_kinds(fixtures: Path) -> bool:
    """Whether the CLI's index fixtures are in the format with artifact kinds
    (`kinds` in the lines, client step C8.7)."""
    for path in (fixtures / "index").glob("*/*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip():
                return "kinds" in json.loads(line)
    return False


# for the tests which use the manifest with artifact kinds
needs_cli_with_artifact_kinds = pytest.mark.skipif(
    not cli_has_artifact_kinds(),
    reason="needs ../magnet_cli with artifact kinds (client step C8.1)",
)


# for the tests which run `magnet publish` against the server
needs_cli_publishing_artifact_kinds = pytest.mark.skipif(
    not cli_publishes_artifact_kinds(),
    reason="needs a debug build of ../magnet_cli which uploads artifacts with their "
    "kind (client step C8.6)",
)
