import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from packages.models import ANY, Asset, Dependency, Project
from packages.tests.factories import ProjectFactory, ReleaseFactory
from packages.validators import (
    normalize_name,
    validate_artifact_tag,
    validate_name,
    validate_version,
    validate_version_req,
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
    colliding = ProjectFactory.build(name="DDW_Common", owner=existing.owner)

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


@pytest.mark.django_db
def test_quark_asset_must_be_any_any():
    release = ReleaseFactory(project__kind=Project.Kind.quark)

    Asset(release=release, abi=ANY, platform=ANY).clean()
    with pytest.raises(ValidationError):
        Asset(release=release, abi="api3", platform="linux_x86_64").clean()


@pytest.mark.django_db
def test_lepton_asset_may_target_a_platform():
    release = ReleaseFactory(project__kind=Project.Kind.lepton)
    Asset(release=release, abi="api3", platform="linux_x86_64").clean()


@pytest.mark.django_db
def test_asset_identifier():
    release = ReleaseFactory(project__name="DDW_Common", version="0.1.0")
    asset = Asset(release=release, abi=ANY, platform=ANY)
    assert asset.identifier == "ddw-common-0.1.0-quark-any-any"


@pytest.mark.django_db
def test_dependency_on_itself_is_rejected():
    release = ReleaseFactory()
    with pytest.raises(ValidationError):
        Dependency(release=release, dependency=release.project, constraint="*").clean()

    Dependency(release=release, dependency=ProjectFactory(), constraint="*").clean()
