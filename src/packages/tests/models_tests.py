import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from packages.models import ANY, Asset, Dependency, Release
from packages.tests.factories import AssetFactory, ProjectFactory, ReleaseFactory
from packages.validators import (
    normalize_name,
    validate_artifact_tag,
    validate_name,
    validate_release_kinds,
    validate_version,
    validate_version_req,
    version_req_matches,
)


@pytest.mark.parametrize(
    "name, normalized",
    [
        ("ddw-common", "ddw-common"),
        ("DDW_Common", "ddw-common"),
        ("ddw_common", "ddw-common"),
        # no camelCase splitting, same as the CLI
        ("ddwCommon", "ddwcommon"),
        ("MathLib", "mathlib"),
    ],
)
def test_normalize_name(name, normalized):
    assert normalize_name(name) == normalized


@pytest.mark.parametrize(
    "name",
    ["a", "alpha", "DDW_Common", "ddw-common", "a1_-", "3dj", "3Dj", "42hello", "1"],
)
def test_valid_name(name):
    validate_name(name)


@pytest.mark.parametrize(
    "name", ["", "-alpha", "_alpha", "_x", "-x", "x.y", "al pha", "alpha.beta", "älpha"]
)
def test_invalid_name(name):
    with pytest.raises(ValidationError):
        validate_name(name)


@pytest.mark.django_db
def test_project_normalized_name_and_slug():
    project = ProjectFactory(name="DDW_Common")
    assert project.normalized_name == "ddw-common"
    assert project.slug == "ddw-common"


@pytest.mark.django_db
def test_project_name_collision():
    existing = ProjectFactory(name="ddw-common")
    colliding = ProjectFactory.build(name="DDW_Common", owner=existing.owners().get())

    with pytest.raises(ValidationError) as e:
        colliding.full_clean()
    assert "ddw-common" in e.value.message_dict["name"][0]

    with pytest.raises(IntegrityError):
        colliding.save()


@pytest.mark.django_db
def test_project_rename_keeps_own_name():
    project = ProjectFactory(name="ddw-common")
    project.name = "DDW_Common"
    project.full_clean()


@pytest.mark.parametrize("version", ["0.0.0", "1.2.3", "10.20.30", "1.10.0"])
def test_valid_version(version):
    validate_version(version)


@pytest.mark.parametrize(
    "version",
    ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "v1.2.3", "1.2.3-beta", "1.2.3+b"],
)
def test_invalid_version(version):
    with pytest.raises(ValidationError):
        validate_version(version)


# checked against the Rust `semver` crate 1.0.27, which the CLI uses
@pytest.mark.parametrize(
    "constraint",
    [
        "1.2.3",
        "^1.2.3",
        "~1.2.3",
        "=1.2.3",
        ">1.2.3",
        ">=1.2.3",
        "<1.2.3",
        "<=1.2.3",
        "1.2",
        "1",
        "^0.1",
        "*",
        "x",
        "1.*",
        "1.2.x",
        "^1.*",
        ">=1.0.0, <2.0.0",
        ">=1.0.0,<2.0.0",
        " >= 1.0.0 , < 2 ",
    ],
)
def test_valid_version_req(constraint):
    validate_version_req(constraint)


@pytest.mark.parametrize(
    "constraint",
    [
        "",
        " ",
        "01.2.3",
        "1.02",
        "v1.2.3",
        "=>1.2.3",
        "~>1.2",
        "1.2.3.4",
        "1.*.3",
        "*.1",
        ">=*",
        "1 2",
        "1.2.3,",
        "*, 1.2",
        # valid in Rust, but pre-releases aren't supported (D1.2)
        "1.2.3-beta",
        "1.2.3+build",
    ],
)
def test_invalid_version_req(constraint):
    with pytest.raises(ValidationError):
        validate_version_req(constraint)


@pytest.mark.parametrize(
    "requirement, matching, not_matching",
    [
        ("*", ["0.0.1", "3.14.1"], []),
        (">=3.13", ["3.13.0", "3.14.1", "4.0.0"], ["3.12.9"]),
        (">3.13", ["3.14.0"], ["3.13.9"]),
        (">3.13.1", ["3.13.2"], ["3.13.1"]),
        ("<3.14", ["3.13.9"], ["3.14.0"]),
        ("<=3.14", ["3.14.9"], ["3.15.0"]),
        ("=3.14", ["3.14.0", "3.14.9"], ["3.15.0", "3.13.9"]),
        ("=3.14.1", ["3.14.1"], ["3.14.2"]),
        # no operator: caret
        ("3.13", ["3.13.0", "3.99.0"], ["4.0.0", "3.12.0"]),
        ("^0.2.3", ["0.2.9"], ["0.3.0", "0.2.2"]),
        ("^0.0.3", ["0.0.3"], ["0.0.4"]),
        ("^0.0", ["0.0.9"], ["0.1.0"]),
        ("^0", ["0.9.0"], ["1.0.0"]),
        ("~3.13.1", ["3.13.5"], ["3.14.0", "3.13.0"]),
        ("~3", ["3.99.0"], ["4.0.0"]),
        ("3.*", ["3.0.0", "3.14.1"], ["4.0.0"]),
        ("3.14.x", ["3.14.1"], ["3.15.0"]),
        (" >= 3.13 , < 3.15 ", ["3.14.1"], ["3.15.0", "3.12.0"]),
    ],
)
def test_version_req_matches(requirement, matching, not_matching):
    assert all(version_req_matches(requirement, v) for v in matching)
    assert not any(version_req_matches(requirement, v) for v in not_matching)


@pytest.mark.parametrize("tag", ["any", "source", "api3", "linux_x86_64"])
def test_valid_artifact_tag(tag):
    validate_artifact_tag(tag)


@pytest.mark.parametrize("tag", ["", "Linux", "linux-x86_64", "macos arm64"])
def test_invalid_artifact_tag(tag):
    with pytest.raises(ValidationError):
        validate_artifact_tag(tag)


@pytest.mark.django_db
def test_releases_ordered_by_version():
    project = ProjectFactory()
    for version in ["1.9.0", "1.10.0", "0.20.0", "1.2.0", "1.10.1"]:
        ReleaseFactory(project=project, version=version)

    assert [r.version for r in project.releases.all()] == [
        "1.10.1",
        "1.10.0",
        "1.9.0",
        "1.2.0",
        "0.20.0",
    ]


@pytest.mark.django_db
def test_release_with_invalid_version_is_not_saved():
    with pytest.raises(ValidationError):
        ReleaseFactory(version="1.2.3-beta")


@pytest.mark.parametrize("kinds", [[], ["quark"], ["lepton"], ["quark", "lepton"]])
def test_valid_release_kinds(kinds):
    validate_release_kinds(kinds)


@pytest.mark.parametrize(
    "kinds",
    [
        ["lepton", "quark"],
        ["quark", "quark"],
        ["source"],
        ["gluon"],
        ["Quark"],
        "quark",
        {"quark": True},
    ],
)
def test_invalid_release_kinds(kinds):
    with pytest.raises(ValidationError):
        validate_release_kinds(kinds)


@pytest.mark.django_db
def test_release_kinds_are_validated():
    with pytest.raises(ValidationError) as e:
        ReleaseFactory.build(kinds=["lepton", "quark"]).full_clean(
            exclude=["project", "owner"]
        )
    assert "kinds" in e.value.message_dict


@pytest.mark.parametrize(
    "kind, abi, platform",
    [
        ("source", ANY, ANY),
        ("quark", ANY, ANY),
        ("lepton", "api3_0", "linux_x86_64"),
    ],
)
@pytest.mark.django_db
def test_valid_tags_per_kind(kind, abi, platform):
    release = ReleaseFactory(kinds=["quark", "lepton"])
    Asset(release=release, kind=kind, abi=abi, platform=platform).clean()


@pytest.mark.parametrize(
    "kind, abi, platform",
    [
        ("source", "api3_0", ANY),
        ("source", ANY, "linux_x86_64"),
        ("quark", "api3_0", "linux_x86_64"),
        ("quark", ANY, "linux_x86_64"),
        ("lepton", ANY, ANY),
        ("lepton", ANY, "linux_x86_64"),
        ("lepton", "api3_0", ANY),
    ],
)
@pytest.mark.django_db
def test_invalid_tags_per_kind(kind, abi, platform):
    release = ReleaseFactory(kinds=["quark", "lepton"])
    with pytest.raises(ValidationError):
        Asset(release=release, kind=kind, abi=abi, platform=platform).clean()


@pytest.mark.django_db
def test_asset_kind_must_be_declared_by_its_release():
    release = ReleaseFactory(kinds=["quark"])

    Asset(release=release, kind="source", abi=ANY, platform=ANY).clean()
    with pytest.raises(ValidationError) as e:
        Asset(
            release=release, kind="lepton", abi="api3_0", platform="linux_x86_64"
        ).clean()
    assert "doesn't declare the kind lepton" in e.value.message_dict["kind"][0]


@pytest.mark.django_db
def test_asset_kind_is_validated():
    asset = AssetFactory.build(release=ReleaseFactory(), kind="gluon")
    with pytest.raises(ValidationError) as e:
        asset.full_clean()
    assert "kind" in e.value.message_dict


@pytest.mark.django_db
def test_assets_are_unique_per_kind_abi_and_platform():
    release = ReleaseFactory(kinds=["quark", "lepton"])
    AssetFactory(release=release, kind="source")
    # the same abi and platform for another kind
    AssetFactory(release=release, kind="quark")
    AssetFactory(release=release, kind="lepton", abi="api3_0", platform="macos_arm64")
    linux = AssetFactory(
        release=release, kind="lepton", abi="api3_0", platform="linux_x86_64"
    )

    # (the storage refuses a second file at the same path before the database does)
    with pytest.raises(IntegrityError):
        Asset.objects.filter(pk=linux.pk).update(platform="macos_arm64")


@pytest.mark.django_db
def test_asset_identifier_and_path_have_the_assets_kind():
    release = ReleaseFactory(
        project__name="DDW_Common", version="0.1.0", kinds=["lepton"]
    )
    source = AssetFactory(release=release, kind="source")
    binary = AssetFactory(
        release=release, kind="lepton", abi="api3_0", platform="macos_arm64"
    )

    assert source.identifier == "ddw-common-0.1.0-source-any-any"
    assert (
        source.file.name == "dl/ddw-common/0.1.0/ddw-common-0.1.0-source-any-any.tar.gz"
    )
    assert binary.identifier == "ddw-common-0.1.0-lepton-api3_0-macos_arm64"


@pytest.mark.django_db
def test_versions_of_a_package_may_have_different_kinds():
    project = ProjectFactory()
    ReleaseFactory(project=project, version="1.0.0", kinds=["quark"])
    ReleaseFactory(project=project, version="2.0.0", kinds=["quark", "lepton"])

    assert [(r.version, r.kinds) for r in Release.objects.filter(project=project)] == [
        ("2.0.0", ["quark", "lepton"]),
        ("1.0.0", ["quark"]),
    ]


@pytest.mark.django_db
def test_dependency_on_itself_is_rejected():
    release = ReleaseFactory()
    with pytest.raises(ValidationError):
        Dependency(release=release, dependency=release.project, constraint="*").clean()

    Dependency(release=release, dependency=ProjectFactory(), constraint="*").clean()
