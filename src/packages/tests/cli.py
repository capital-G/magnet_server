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


def fixtures_have_kinds(fixtures: Path) -> bool:
    """Whether the CLI's index fixtures are in the format with artifact kinds
    (`kinds` in the lines, client step C8.7)."""
    for path in (fixtures / "index").glob("*/*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip():
                return "kinds" in json.loads(line)
    return False


# for the tests which still use the manifest with one kind per package
needs_cli_without_artifact_kinds = pytest.mark.skipif(
    cli_has_artifact_kinds(),
    reason="written for the CLI before artifact kinds, which ../magnet_cli has "
    "already; adapted in S25 (publish)",
)


# for the tests which use the manifest with artifact kinds
needs_cli_with_artifact_kinds = pytest.mark.skipif(
    not cli_has_artifact_kinds(),
    reason="needs ../magnet_cli with artifact kinds (client step C8.1)",
)
