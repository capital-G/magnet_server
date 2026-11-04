"""Post-processing of rendered docs (build step 4, docs_plan.md)."""

import shutil
from pathlib import Path
from typing import Any


def postprocess(rendered: Path, target: Path, docmap: dict[str, Any]) -> None:
    """Writes the published docs into `target` (which doesn't exist yet) from the
    output of render.scd in `rendered`. For now a copy as is; S46 rewrites the links
    by owner, shares the static files and adds the top bar."""
    shutil.copytree(rendered, target)
