from pydantic import BaseModel, Field

from packages.models import Asset, Dependency, Release

"""
Although we are using DRF it doesn't seem useful when dealing with data which is not
necessarily linked to an object in the database (see `read_only` fields).
Therefore it seems better to handle JSON verification logic via pydantic.
"""


class GitServerExtensionBinaryAssetModel(BaseModel):
    sha_256: str
    asset_format: Asset.AssetFormat
    architecture: Asset.Architecture
    os: Asset.OperatingSystem
    abi: Asset.ServerAbiVersion
    yanked: bool = Field(default=False)

    @classmethod
    def from_asset(cls, asset: Asset) -> "GitServerExtensionBinaryAssetModel":
        assert asset.asset_format == Asset.AssetFormat.server_extension_binary
        return cls(
            sha_256=asset.sha256_checksum,
            asset_format=asset.asset_format,  # type: ignore
            architecture=asset.architecture,  # type: ignore
            os=asset.operating_system,  # type: ignore
            abi=asset.server_abi_version,  # type: ignore
        )

    def __eq__(self, other):
        return self.sha_256 == other.sha_256


class GitQuarkSourceAssetModel(BaseModel):
    """Serializer for git index regarding available implementations of a given version
    which can be either source code or compiled binaries."""

    sha_256: str
    asset_format: Asset.AssetFormat
    yanked: bool = Field(default=False)

    @classmethod
    def from_asset(cls, asset: Asset) -> "GitQuarkSourceAssetModel":
        assert asset.asset_format == Asset.AssetFormat.quark_source
        return cls(
            asset_format=asset.asset_format,  # type: ignore
            sha_256=asset.sha256_checksum,
        )

    def __eq__(self, other):
        return self.sha_256 == other.sha_256


def to_git_asset(
    asset: Asset,
) -> GitServerExtensionBinaryAssetModel | GitQuarkSourceAssetModel:
    """Helper method which derives to designated JSON representation"""
    if asset.asset_format == Asset.AssetFormat.quark_source:
        return GitQuarkSourceAssetModel.from_asset(asset)
    elif asset.asset_format == Asset.AssetFormat.server_extension_binary:
        return GitServerExtensionBinaryAssetModel.from_asset(asset)
    raise NotImplementedError


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
    assets: list[GitServerExtensionBinaryAssetModel | GitQuarkSourceAssetModel] = Field(
        default_factory=list
    )

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
            assets=[to_git_asset(a) for a in release.assets.all()],
        )

    def __eq__(self, other) -> bool:
        return self.name == other.name and self.version == other.version
