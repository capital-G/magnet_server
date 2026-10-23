from pydantic import BaseModel, Field

from packages.models import Asset, Dependency, Release

"""
Although we are using DRF it doesn't seem useful when dealing with data which is not
necessarily linked to an object in the database (see `read_only` fields).
Therefore it seems better to handle JSON verification logic via pydantic.
"""


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


class GitDependencyModel(BaseModel):
    """Serializer for a field of the :class:`ReleaseGitEntrySerializer`."""

    name: str
    constraint: str

    @classmethod
    def from_dependency(cls, dependency: Dependency) -> "GitDependencyModel":
        return cls(
            name=dependency.dependency.slug,
            constraint=dependency.constraint,
        )


class GitReleaseModel(BaseModel):
    """
    This serializer is not attached to a model and is used
    to enter a new release into the distributed GIT repository.
    It is not stored in the database but
    is instead used to provide serialization for Python and Rust
    through the OpenAPI exchange.

    It also validates the git entry upon release.

    Pay attention when updating this model as this can change not just future
    but also existing entries since some updates (e.g. yanked) require a rewrite
    of the entry.
    """

    name: str
    version: str
    yanked: bool = Field(default=False)

    dependencies: list[GitDependencyModel] = Field(default_factory=list)
    assets: list[GitArtifactModel] = Field(default_factory=list)

    @classmethod
    def from_release(cls, release: Release) -> "GitReleaseModel":
        return cls(
            name=release.project.name,
            version=release.version,
            yanked=release.yanked,
            dependencies=[
                GitDependencyModel.from_dependency(d)
                for d in Dependency.objects.filter(release=release)
            ],
            assets=[GitArtifactModel.from_asset(a) for a in release.assets.all()],
        )

    def __eq__(self, other) -> bool:
        return self.name == other.name and self.version == other.version
