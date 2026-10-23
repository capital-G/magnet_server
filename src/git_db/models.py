import json

from django.conf import settings
from pydantic import BaseModel

from packages.models import Asset, Release, artifact_path

"""
Models of the index files the magnet CLI reads (`../magnet_cli/src/git_client/models.rs`).
See "The contract" in `implementation_plan.md`: the CLI code wins on any difference.

Although we are using DRF it doesn't seem useful when dealing with data which is not
necessarily linked to an object in the database (see `read_only` fields).
Therefore it seems better to handle JSON verification logic via pydantic.
"""

INDEX_FORMAT = 1


def to_json(data: dict) -> str:
    # `json.dumps` default separators (`, ` and `: `) like the CLI fixtures,
    # so diffs of the index stay readable. Keys keep the order of the models.
    return json.dumps(data)


class GitIndexConfigModel(BaseModel):
    """`config.json` at the root of the index."""

    index_format: int = INDEX_FORMAT
    # URL template of an artifact with the placeholders
    # `{name}`, `{version}`, `{kind}`, `{abi}` and `{platform}`
    dl: str

    @classmethod
    def from_settings(cls) -> "GitIndexConfigModel":
        template = artifact_path(
            name="{name}",
            version="{version}",
            kind="{kind}",
            abi="{abi}",
            platform="{platform}",
        )
        base_url = settings.MAGNET_DOWNLOAD_BASE_URL.rstrip("/")
        return cls(dl=f"{base_url}/{template}")

    def to_json(self) -> str:
        return to_json({"index-format": self.index_format, "dl": self.dl})


class GitArtifactModel(BaseModel):
    """An artifact of a release, identified by its (abi, platform)."""

    abi: str
    platform: str
    sha256: str

    @classmethod
    def from_asset(cls, asset: Asset) -> "GitArtifactModel":
        return cls(
            abi=asset.abi,
            platform=asset.platform,
            sha256=asset.sha256_checksum,
        )


class GitReleaseModel(BaseModel):
    """A line of a package file, i.e. one version of a package.

    Pay attention when updating this model as this can change not just future
    but also existing entries since some updates (e.g. yanked) require a rewrite
    of the entry. The field order is the key order of the line.
    """

    # the normalized name
    name: str
    version: str
    kind: str
    # normalized name of the dependency -> version requirement
    dependencies: dict[str, str]
    yanked: bool = False
    artifacts: list[GitArtifactModel]

    @classmethod
    def from_release(cls, release: Release) -> "GitReleaseModel":
        project = release.project
        dependencies = {
            d.dependency.normalized_name: d.constraint
            for d in release.dependencies_from.select_related("dependency")
        }
        return cls(
            name=project.normalized_name,
            version=release.version,
            kind=project.kind,
            # sorted, so the line doesn't depend on the order of the database rows
            dependencies=dict(sorted(dependencies.items())),
            yanked=release.yanked,
            artifacts=[GitArtifactModel.from_asset(a) for a in release.assets.all()],
        )

    def to_json(self) -> str:
        return to_json(self.model_dump())
