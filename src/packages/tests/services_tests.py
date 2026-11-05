import hashlib
import json

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from pytest_django.asserts import assertContains

from git_db.db import GitDatabase, get_git_database
from git_db.tests.git_db_tests import show
from packages import services
from packages.models import Asset, DeletedRelease, Project, Release, ReservedName
from packages.services import (
    Conflict,
    add_artifact,
    create_draft,
    delete_draft,
    finalise,
)
from packages.tests.factories import ProjectMemberFactory, ReleaseFactory, tar_gz
from users.tests.factories import UserFactory

SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def manifest(
    *,
    name="Foo_Bar",
    version="1.0.0",
    kinds=("quark",),
    sclang: str | None = ">=3.13",
    dependencies="",
    metadata='summary = "A test package"\nlicense = "MIT"\n'
    'repository = "https://example.org/foo"\n',
) -> bytes:
    lines = [
        "magnet-version = 1",
        "[package]",
        f'name = "{name}"',
        f'version = "{version}"',
    ]
    if sclang:
        lines.append(f'sclang = "{sclang}"')
    lines += [
        "[metadata]",
        metadata,
        "[artifact.source]",
        'from = "paths"',
        'paths = ["Classes/**", "src/**"]',
    ]
    if "quark" in kinds:
        lines += ["[artifact.quark]", 'paths = ["Classes/**.sc"]']
    if "lepton" in kinds:
        lines += [
            "[artifact.lepton]",
            'platform-paths = { macos_arm64 = ["build/*.scx"] }',
        ]
    lines += ["[dependencies]", dependencies]
    return "\n".join(lines).encode()


def quark(content=b"Foo {}\n", **kwargs) -> bytes:
    return tar_gz(
        ("Classes/Foo.sc", content),
        ("README.md", b"# Foo\n"),
        ("magnet.toml", manifest(**kwargs)),
    )


def source(**kwargs) -> bytes:
    """The source artifact (`from = "paths"`) of `manifest(**kwargs)`."""
    return tar_gz(
        ("Classes/Foo.sc", b"Foo {}\n"),
        ("magnet.toml", manifest(**kwargs)),
        ("src/Foo.cpp", b"// Foo\n"),
    )


def lepton_source(**kwargs) -> bytes:
    return source(kinds=("lepton",), **kwargs)


def lepton_binary(content=b"binary", abi="api3_0", kinds=("lepton",), **kwargs):
    return tar_gz(
        ("magnet.toml", manifest(kinds=kinds, **kwargs)),
        (f"plugins/macos_arm64/{abi}/Foo.scx", content),
    )


def upload(
    user, release, data: bytes, kind="quark", abi="any", platform="any", sha256=None
):
    return add_artifact(
        user,
        release,
        kind=kind,
        abi=abi,
        platform=platform,
        file=ContentFile(data, name="upload.tar.gz"),
        sha256=sha256 or hashlib.sha256(data).hexdigest(),
    )


def upload_lepton(user, release, abi="api3_0", **kwargs):
    """A lepton artifact for `abi`/`macos_arm64`."""
    return upload(
        user,
        release,
        lepton_binary(abi=abi, **kwargs),
        kind="lepton",
        abi=abi,
        platform="macos_arm64",
    )


def upload_quark(user, release, **kwargs):
    """The source and the quark artifact of a quark version, which can be finalised
    then; returns the quark artifact."""
    upload(user, release, source(**kwargs), kind="source")
    return upload(user, release, quark(**kwargs))


@pytest.fixture
def index() -> GitDatabase:
    call_command("init_index")
    return get_git_database()


@pytest.fixture
def user():
    return UserFactory.create()


@pytest.fixture
def alpha() -> Project:
    """A package with a final version, which others can depend on."""
    return ReleaseFactory.create(project__name="alpha", final=True).project


def index_lines(index: GitDatabase, name: str) -> list[dict]:
    try:
        content = show(index.repo_path, f"{name[0]}/{name}.jsonl")
    except Exception:
        return []
    return [json.loads(line) for line in content.splitlines()]


def artifact_files() -> list[str]:
    root = settings.MAGNET_ARTIFACTS_ROOT
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.tar.gz"))


@pytest.mark.django_db
def test_quark_from_draft_to_final(index, user, alpha):
    data = quark(dependencies='Alpha = "^1"')

    release, created = create_draft(user, "foo-bar", "1.0.0")
    assert created and not release.is_final
    project = release.project
    assert (project.name, project.owners().get()) == ("foo-bar", user)

    asset, created = upload(user, release, data)
    assert created
    assert asset.file.name == "dl/foo-bar/1.0.0/foo-bar-1.0.0-quark-any-any.tar.gz"
    assert asset.sha256_checksum == hashlib.sha256(data).hexdigest()
    assert index_lines(index, "foo-bar") == []  # drafts never reach the index

    release.refresh_from_db()
    project.refresh_from_db()
    assert (release.kinds, release.sclang) == (["quark"], ">=3.13")
    assert (release.summary, release.license, release.repository_url) == (
        "A test package",
        "MIT",
        "https://example.org/foo",
    )
    assert release.readme == "# Foo\n"
    # the CLI sends the normalized name, the manifest has the spelling
    assert project.name == "Foo_Bar"

    with pytest.raises(
        ValidationError,
        match="foo-bar 1.0.0 needs its source artifact before it can be finalised",
    ):
        finalise(user, release)
    source_asset, _ = upload(
        user, release, source(dependencies='Alpha = "^1"'), kind="source"
    )
    release = finalise(user, release)

    assert release.is_final
    assert index_lines(index, "foo-bar") == [
        {
            "name": "foo-bar",
            "version": "1.0.0",
            "kinds": ["quark"],
            "sclang": ">=3.13",
            "dependencies": {"alpha": "^1"},
            "yanked": False,
            "artifacts": [
                {
                    "kind": "quark",
                    "abi": "any",
                    "platform": "any",
                    "sha256": asset.sha256_checksum,
                },
                {
                    "kind": "source",
                    "abi": "any",
                    "platform": "any",
                    "sha256": source_asset.sha256_checksum,
                },
            ],
        }
    ]


@pytest.mark.django_db
def test_quark_with_mapped_paths(index, user):
    # `{ from, as }` entries of `[artifact.quark] paths` (client step C6.16)
    toml = manifest().replace(
        b'paths = ["Classes/**.sc"]',
        b'paths = ["Classes/**.sc", { from = "external/faust", as = "faustlibs" }]',
    )
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(
        user,
        release,
        tar_gz(("Classes/Foo.sc", b"Foo {}\n"), ("magnet.toml", toml)),
        kind="source",
    )
    files = [("Classes/Foo.sc", b"Foo {}\n"), ("magnet.toml", toml)]

    with pytest.raises(ValidationError, match=r"faust/all.lib \(not selected"):
        upload(user, release, tar_gz(*files, ("faust/all.lib", b"")))
    upload(user, release, tar_gz(*files, ("faustlibs/all.lib", b"")))
    release = finalise(user, release)

    assert release.is_final
    assert [line["kinds"] for line in index_lines(index, "foo-bar")] == [["quark"]]


@pytest.mark.django_db
def test_every_step_can_be_repeated(index, user):
    data = quark()
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, source(), kind="source")
    asset, _ = upload(user, release, data)

    assert create_draft(user, "Foo_Bar", "1.0.0") == (release, False)
    assert upload(user, release, data) == (asset, False)
    finalise(user, release)
    finalise(user, release)
    assert create_draft(user, "foo-bar", "1.0.0") == (release, False)
    assert upload(user, release, data) == (asset, False)

    assert len(index_lines(index, "foo-bar")) == 1
    assert Asset.objects.count() == 2


@pytest.mark.django_db
def test_lepton_binary_before_its_source(index, user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(
        user,
        release,
        lepton_binary(),
        kind="lepton",
        abi="api3_0",
        platform="macos_arm64",
    )

    with pytest.raises(ValidationError, match="needs its source artifact"):
        finalise(user, release)
    assert not release.is_final

    source, _ = upload(user, release, lepton_source(), kind="source")
    finalise(user, release)

    [line] = index_lines(index, "foo-bar")
    # upload order
    assert [(a["kind"], a["abi"], a["platform"]) for a in line["artifacts"]] == [
        ("lepton", "api3_0", "macos_arm64"),
        ("source", "any", "any"),
    ]


@pytest.mark.django_db
def test_append_to_a_final_version(index, user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, lepton_source(), kind="source")
    upload_lepton(user, release)
    finalise(user, release)

    # e.g. a binary for a newer plugin API, built later
    binary, created = upload_lepton(user, release, abi="api4_0")

    assert created
    [line] = index_lines(index, "foo-bar")
    assert [(a["kind"], a["abi"]) for a in line["artifacts"]] == [
        ("source", "any"),
        ("lepton", "api3_0"),
        ("lepton", "api4_0"),
    ]
    assert line["artifacts"][-1] == {
        "kind": "lepton",
        "abi": "api4_0",
        "platform": "macos_arm64",
        "sha256": binary.sha256_checksum,
    }


@pytest.mark.django_db
def test_failed_append_removes_the_artifact(index, user, monkeypatch):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, lepton_source(), kind="source")
    upload_lepton(user, release)
    finalise(user, release)

    def fail(self, asset):
        raise RuntimeError("push failed")

    monkeypatch.setattr(GitDatabase, "commit_asset", fail)
    with pytest.raises(RuntimeError):
        upload_lepton(user, release, abi="api4_0")

    assert release.assets.count() == 2
    assert artifact_files() == [
        "dl/foo-bar/1.0.0/foo-bar-1.0.0-lepton-api3_0-macos_arm64.tar.gz",
        "dl/foo-bar/1.0.0/foo-bar-1.0.0-source-any-any.tar.gz",
    ]


@pytest.mark.django_db
def test_artifacts_are_never_replaced(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, quark())

    with pytest.raises(
        Conflict, match="already has an artifact quark-any-any with another sha256"
    ):
        upload(user, release, quark(content=b"Changed {}\n"))


@pytest.mark.django_db
def test_sha256_must_match_the_upload(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")

    with pytest.raises(ValidationError, match="The sha256 of the upload is"):
        upload(user, release, quark(), sha256="0" * 64)
    assert not Asset.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "binary, differences",
    [
        (lepton_binary(sclang=">=3.14"), "sclang"),
        (lepton_binary(dependencies='alpha = "^2"'), "dependencies"),
    ],
)
def test_artifacts_of_a_version_agree_on_the_index_fields(
    user, alpha, binary, differences
):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, lepton_source(dependencies='alpha = "^1"'), kind="source")

    with pytest.raises(ValidationError, match=f"earlier artifacts in: {differences}"):
        upload(
            user, release, binary, kind="lepton", abi="api3_0", platform="macos_arm64"
        )


@pytest.mark.django_db
def test_metadata_and_crlf_dont_need_to_agree(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, lepton_source(), kind="source")

    crlf = tar_gz(
        (
            "magnet.toml",
            manifest(kinds=("lepton",), metadata="").replace(b"\n", b"\r\n"),
        ),
        ("plugins/macos_arm64/api3_0/Foo.scx", b"binary"),
    )
    upload(user, release, crlf, kind="lepton", abi="api3_0", platform="macos_arm64")

    release.refresh_from_db()
    assert release.summary == "A test package"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"name": "other"}, "The manifest is for other, not foo-bar"),
        ({"version": "1.0.1"}, "for version 1.0.1, not 1.0.0"),
        ({"sclang": "~>3"}, "version requirement"),
        ({"metadata": 'repository = "not a url"'}, "repository_url"),
    ],
)
def test_manifest_must_match_the_release(user, kwargs, error):
    release, _ = create_draft(user, "foo-bar", "1.0.0")

    with pytest.raises(ValidationError, match=error):
        upload(user, release, quark(**kwargs))
    assert not Asset.objects.exists()
    assert artifact_files() == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind, abi, platform, error",
    [
        ("quark", "api3_0", "macos_arm64", "A quark artifact has the abi and platform"),
        ("source", "any", "macos_arm64", "A source artifact has the abi and platform"),
        ("lepton", "any", "any", "A lepton artifact needs an abi and a platform"),
        ("gluon", "any", "any", "gluon is not an artifact kind"),
    ],
)
def test_tags_per_kind(user, kind, abi, platform, error):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    with pytest.raises(ValidationError, match=error):
        upload(user, release, quark(), kind=kind, abi=abi, platform=platform)
    assert not Asset.objects.exists()


@pytest.mark.django_db
def test_undeclared_kind_is_refused(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    with pytest.raises(ValidationError, match=r"There is no \[artifact.lepton\]"):
        upload_lepton(user, release, kinds=("quark",))


@pytest.mark.django_db
def test_artifacts_of_a_version_agree_on_the_kinds(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, source(kinds=("quark", "lepton")), kind="source")

    with pytest.raises(ValidationError, match="earlier artifacts in: kinds"):
        upload(user, release, quark(kinds=("quark",)))
    upload(user, release, quark(kinds=("quark", "lepton")))


@pytest.mark.django_db
def test_quark_and_lepton_are_final_once_complete(index, user):
    kinds = ("quark", "lepton")
    release, _ = create_draft(user, "foo-bar", "1.0.0")

    with pytest.raises(ValidationError, match="foo-bar 1.0.0 has no artifacts yet"):
        finalise(user, release)
    upload_lepton(user, release, kinds=kinds)
    with pytest.raises(
        ValidationError,
        match="foo-bar 1.0.0 needs its source artifact and its quark artifact before",
    ):
        finalise(user, release)
    upload(user, release, quark(kinds=kinds))
    with pytest.raises(ValidationError, match="1.0.0 needs its source artifact before"):
        finalise(user, release)
    assert index_lines(index, "foo-bar") == []
    upload(user, release, source(kinds=kinds), kind="source")

    finalise(user, release)

    [line] = index_lines(index, "foo-bar")
    assert line["kinds"] == ["quark", "lepton"]
    assert [(a["kind"], a["abi"], a["platform"]) for a in line["artifacts"]] == [
        ("lepton", "api3_0", "macos_arm64"),
        ("quark", "any", "any"),
        ("source", "any", "any"),
    ]


@pytest.mark.django_db
def test_quark_and_lepton_need_a_lepton_artifact(user):
    kinds = ("quark", "lepton")
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, source(kinds=kinds), kind="source")

    with pytest.raises(
        ValidationError,
        match="needs its quark artifact and a lepton artifact before it can be",
    ):
        finalise(user, release)
    upload(user, release, quark(kinds=kinds))
    with pytest.raises(ValidationError, match="needs a lepton artifact before"):
        finalise(user, release)


@pytest.mark.django_db
def test_version_without_source_is_never_final(index, user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, quark())

    for _attempt in range(2):
        with pytest.raises(ValidationError, match="needs its source artifact before"):
            finalise(user, release)
    release.refresh_from_db()
    assert not release.is_final
    assert index_lines(index, "foo-bar") == []


@pytest.mark.django_db
def test_versions_of_a_package_with_different_kinds(index, user):
    first, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, first)
    finalise(user, first)

    second, _ = create_draft(user, "foo-bar", "2.0.0")
    kinds = ("quark", "lepton")
    upload(user, second, source(version="2.0.0", kinds=kinds), kind="source")
    upload(user, second, quark(version="2.0.0", kinds=kinds))
    upload_lepton(user, second, version="2.0.0", kinds=kinds)
    finalise(user, second)

    lines = index_lines(index, "foo-bar")
    assert [(line["version"], line["kinds"]) for line in lines] == [
        ("1.0.0", ["quark"]),
        ("2.0.0", ["quark", "lepton"]),
    ]
    assert [len(line["artifacts"]) for line in lines] == [2, 3]
    # and back to a quark only
    third, _ = create_draft(user, "foo-bar", "3.0.0")
    upload_quark(user, third, version="3.0.0")
    finalise(user, third)
    assert index_lines(index, "foo-bar")[-1]["kinds"] == ["quark"]


@pytest.mark.django_db
def test_source_artifact_has_platform_any(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    with pytest.raises(ValidationError, match="platform 'any'"):
        upload(user, release, source(), kind="source", platform="macos_arm64")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "dependencies, error",
    [
        ('bar = { path = "../bar" }', "bar is a local path dependency"),
        ('bar = { index = "https://other.org" }', "bar comes from another index"),
        (
            'bar = { git = { url = "https://x.org/bar.git", tag = "v1" } }',
            "bar is pinned to a git tag, which can move, pin it via `ref = ",
        ),
        (
            'bar = { git = { url = "https://x.org/bar.git", branch = "main" } }',
            "bar is pinned to a git branch, pin it via `ref = ",
        ),
        (
            'bar = { legacy-quark = true, git = { url = "https://x.org/bar.git", ref = "abc" } }',
            "bar is pinned to an abbreviated commit, pin it via `ref = ",
        ),
        (
            'bar = { git = { url = "https://x.org/bar.git" } }',
            "bar is a git dependency without a ref, pin it via `ref = ",
        ),
        (
            "bar = { legacy-quark = true }",
            "bar is a legacy quark without a git URL and ref, add `git = ",
        ),
        (
            f'bar = {{ legacy-quark = true, git = {{ ref = "{SHA}" }} }}',
            'bar is a legacy quark pinned by name, add `url = "…"` and `ref = ',
        ),
        (
            f'bar = {{ git = {{ url = "https://x.org/bar.git", ref = "{SHA}", tag = "v1" }} }}',
            "bar can only have one of branch, tag or ref",
        ),
        (
            f'bar = {{ git = {{ url = "git@github.com:x/bar.git", ref = "{SHA}" }} }}',
            "bar: Must be an absolute URL",
        ),
        (
            f'bar = {{ git = {{ url = "../bar", ref = "{SHA}" }} }}',
            "bar: Must be an absolute URL",
        ),
        (
            f'bar = {{ git = {{ url = "https:///bar.git", ref = "{SHA}" }} }}',
            "bar: Must be an absolute URL",
        ),
        (
            f'"x.y" = {{ git = {{ url = "https://x.org/x.y.git", ref = "{SHA}" }} }}',
            "x.y: Must start with a letter or digit",
        ),
        (
            f'Foo_Bar = {{ git = {{ url = "https://x.org/foo.git", ref = "{SHA}" }} }}',
            "Foo_Bar can't depend on itself",
        ),
        (
            f'alpha = "^1"\nALPHA = {{ git = {{ url = "https://x.org/a.git", ref = "{SHA}" }} }}',
            "ALPHA is listed twice",
        ),
        ('beta = "^1"', "beta is not a package with a published version"),
        ('alpha = "~>1"', "alpha: .*version requirement"),
        ('Foo-Bar = "*"', "Foo-Bar can't depend on itself"),
        ('alpha = "^1"\nALPHA = "^1"', "ALPHA is listed twice"),
        ('"x.y" = "*"', "x.y: Must start with a letter or digit"),
    ],
)
def test_refused_dependencies(user, alpha, dependencies, error):
    ReleaseFactory.create(project__name="beta")  # a draft only
    release, _ = create_draft(user, "foo-bar", "1.0.0")

    with pytest.raises(ValidationError, match=error):
        upload(user, release, quark(dependencies=dependencies))


@pytest.mark.django_db
def test_accepted_dependencies(index, user, alpha):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, release, dependencies='ALPHA = { version = ">=1, <2" }')
    finalise(user, release)

    [line] = index_lines(index, "foo-bar")
    assert line["dependencies"] == {"alpha": ">=1, <2"}


@pytest.mark.django_db
def test_accepted_git_and_legacy_quark_dependencies(index, user, alpha):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    dependencies = f"""
alpha = "^1"
ddwCommon = {{ legacy-quark = true, git = {{ url = "https://github.com/jamshark70/ddwCommon.git", ref = "{SHA.upper()}" }} }}
Some_Quark = {{ legacy-quark = true, ignore-quark-deps = true, git = {{ url = "https://x.org/some.git", ref = "{SHA}" }} }}
magnetProject = {{ git = {{ url = "ssh://git@x.org/magnet-project.git", ref = "{SHA}" }} }}
"""
    upload_quark(user, release, dependencies=dependencies)
    finalise(user, release)

    [line] = index_lines(index, "foo-bar")
    # the format of the CLI's fixture `d/delta.jsonl` (0.2.0), keys normalized, the
    # ref lowercase, `ignore-quark-deps` only when set
    assert line["dependencies"] == {
        "alpha": "^1",
        "ddwcommon": {
            "legacy-quark": True,
            "git": {
                "url": "https://github.com/jamshark70/ddwCommon.git",
                "ref": SHA,
            },
        },
        "magnetproject": {
            "git": {"url": "ssh://git@x.org/magnet-project.git", "ref": SHA}
        },
        "some-quark": {
            "legacy-quark": True,
            "ignore-quark-deps": True,
            "git": {"url": "https://x.org/some.git", "ref": SHA},
        },
    }
    # key order as the fixture
    raw = show(index.repo_path, "f/foo-bar.jsonl")
    assert '"ddwcommon": {"legacy-quark": true, "git": {"url": ' in raw
    assert release.git_dependencies.count() == 3

    # the line can be regenerated from the database
    index.rebuild()
    assert show(index.repo_path, "f/foo-bar.jsonl") == raw


@pytest.mark.django_db
def test_project_page_shows_git_dependencies(client, index, user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(
        user,
        release,
        dependencies=f'ddwCommon = {{ legacy-quark = true, git = {{ url = "https://x.org/ddw.git", ref = "{SHA}" }} }}',
    )
    finalise(user, release)

    response = client.get(reverse("project-detail", args=[release.project.slug]))
    assertContains(response, "ddwcommon")
    assertContains(response, "legacy quark")
    assertContains(response, f'git https://x.org/ddw.git @ <code title="{SHA}">')


@pytest.mark.django_db
def test_artifacts_of_a_version_agree_on_git_dependencies(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    git = f'bar = {{ git = {{ url = "https://x.org/bar.git", ref = "{SHA}" }} }}'
    upload(user, release, lepton_source(dependencies=git), kind="source")

    for other in [
        git.replace(SHA, "b" * 40),
        git.replace("{ git", "{ legacy-quark = true, git"),
        git.replace("{ git", "{ ignore-quark-deps = true, git"),
    ]:
        with pytest.raises(ValidationError, match="earlier artifacts in: dependencies"):
            upload(
                user,
                release,
                lepton_binary(dependencies=other),
                kind="lepton",
                abi="api3_0",
                platform="macos_arm64",
            )
    # the same, spelled differently
    upload(
        user,
        release,
        lepton_binary(dependencies=git.replace(SHA, SHA.upper())),
        kind="lepton",
        abi="api3_0",
        platform="macos_arm64",
    )


@pytest.mark.django_db
def test_table_without_version_accepts_any_version(user, alpha):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, quark(dependencies="alpha = {}"))
    assert release.dependencies_from.get().constraint == "*"


@pytest.mark.django_db
def test_only_owners_and_maintainers_publish(index, user):
    other = UserFactory.create()
    release, _ = create_draft(user, "foo-bar", "1.0.0")

    with pytest.raises(PermissionDenied):
        create_draft(other, "foo-bar", "1.0.1")
    with pytest.raises(PermissionDenied):
        upload(other, release, quark())
    with pytest.raises(PermissionDenied):
        finalise(other, release)
    with pytest.raises(PermissionDenied):
        delete_draft(other, release)

    ProjectMemberFactory.create(project=release.project, user=other)
    upload_quark(other, release)
    finalise(other, release)
    assert create_draft(other, "foo-bar", "1.0.1")[1]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, version, error",
    [
        ("_foo", "1.0.0", "start with a letter or digit"),
        ("foo-bar", "1.0", "MAJOR.MINOR.PATCH"),
        ("foo-bar", "1.0.0-beta", "MAJOR.MINOR.PATCH"),
        ("wslib", "1.0.0", "reserved"),
    ],
)
def test_invalid_drafts(user, name, version, error):
    with pytest.raises(ValidationError, match=error):
        create_draft(user, name, version)
    assert not Project.objects.exists()


@pytest.mark.django_db
def test_claimed_name(user):
    ReservedName.objects.filter(normalized_name="wslib").update(claimed_by=user)
    release, created = create_draft(user, "wslib", "1.0.0")
    assert created and release.project.owners().get() == user


@pytest.mark.django_db
def test_name_with_a_leading_digit(index, user):
    """The legacy quark 3Dj, once an admin assigned its reserved name."""
    ReservedName.objects.filter(normalized_name="3dj").update(claimed_by=user)
    release, _ = create_draft(user, "3dj", "1.0.0")
    upload_quark(user, release, name="3Dj")
    finalise(user, release)
    assert Project.objects.get(normalized_name="3dj").name == "3Dj"
    assert index_lines(index, "3dj")[0]["name"] == "3dj"
    assert show(index.repo_path, "3/3dj.jsonl")

    # and others can depend on it
    other, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, other, dependencies='"3Dj" = "^1"')
    finalise(user, other)
    assert index_lines(index, "foo-bar")[0]["dependencies"] == {"3dj": "^1"}


@pytest.mark.django_db
def test_deleted_version_is_refused(user):
    DeletedRelease.objects.create(normalized_name="foo-bar", version="1.0.0")

    with pytest.raises(ValidationError, match="was deleted"):
        create_draft(user, "Foo_Bar", "1.0.0")
    assert create_draft(user, "foo-bar", "1.0.1")[1]


@pytest.mark.django_db
def test_delete_draft(user, django_capture_on_commit_callbacks):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload(user, release, quark())

    with django_capture_on_commit_callbacks(execute=True):
        delete_draft(user, release)

    assert not Release.objects.exists() and not Asset.objects.exists()
    assert artifact_files() == []
    # the version can be drafted and uploaded again, also with another artifact
    release, created = create_draft(user, "foo-bar", "1.0.0")
    assert created
    assert upload(user, release, quark(content=b"Changed {}\n"))[1]


@pytest.mark.django_db
def test_final_version_cant_be_deleted(index, user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, release)
    finalise(user, release)

    with pytest.raises(Conflict, match="yank it instead"):
        delete_draft(user, release)


@pytest.mark.django_db
def test_finalise_needs_an_artifact(user):
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    with pytest.raises(ValidationError, match="has no artifacts yet"):
        finalise(user, release)


@pytest.mark.django_db
def test_concurrent_drafts_of_a_version(user, monkeypatch):
    project = create_draft(user, "foo-bar", "1.0.0")[0].project
    filter_tombstones = DeletedRelease.objects.filter

    def concurrent_request(**kwargs):
        # another job of a CI matrix creates the draft right after our lookup
        Release.objects.create(project=project, version="1.0.1", owner=user)
        return filter_tombstones(**kwargs)

    monkeypatch.setattr(DeletedRelease.objects, "filter", concurrent_request)
    release, created = create_draft(user, "foo-bar", "1.0.1")

    assert not created
    assert release.version == "1.0.1" and project.releases.count() == 2


@pytest.mark.django_db
def test_concurrent_new_package(user, monkeypatch):
    def concurrent_request(name, user):
        services.create_project(Project(name="Foo_Bar"), user)

    monkeypatch.setattr(services, "ensure_name_available", concurrent_request)
    release, created = create_draft(user, "foo-bar", "1.0.0")

    assert created and release.project.name == "Foo_Bar"
    assert Project.objects.count() == 1
