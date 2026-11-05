import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from git_db.db import GIT_ENV
from packages.archive import SCHEMA_PATH, inspect_artifact
from packages.tests.cli import (
    MAGNET,
    MAGNET_CLI,
    needs_cli_with_artifact_kinds,
    needs_cli_with_quark_mappings,
)
from packages.tests.factories import (
    LEPTON_MANIFEST,
    QUARK_MANIFEST,
    lepton,
    quark,
    source,
    tar_gz,
)
from packages.validators import validate_artifact_archive


def inspect(data: bytes, kind="quark", abi="any", platform="any"):
    return inspect_artifact(
        ContentFile(data, name="upload.tar.gz"), kind, abi, platform
    )


def inspect_lepton(data: bytes, abi="api3_0", platform="macos_arm64"):
    return inspect(data, "lepton", abi, platform)


GIT_SOURCE_MANIFEST = QUARK_MANIFEST  # no [artifact.source]: `from = "git"`


def link(name: str, type=tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = type
    info.linkname = "/etc/passwd"
    return info


def test_valid_quark():
    artifact = inspect(quark())

    assert artifact.manifest["package"] == {
        "name": "foo",
        "version": "1.0.0",
        "sclang": ">=3.13",
    }
    assert artifact.kinds == ["quark"]
    assert artifact.readme == "# Foo\n"
    assert artifact.files == [
        "Classes/Foo.sc",
        "HelpSource/Classes/Foo.schelp",
        "README.md",
        "magnet.toml",
    ]


def test_manifest_with_crlf():
    # e.g. a Windows CI checkout
    artifact = inspect(quark(manifest=QUARK_MANIFEST.replace("\n", "\r\n")))
    assert artifact.manifest["package"]["name"] == "foo"


@pytest.mark.parametrize(
    "data, message",
    [
        (quark(link("Classes/Link.sc")), "neither a file nor a directory"),
        (quark(link("Classes/Hard.sc", tarfile.LNKTYPE)), "neither a file nor a"),
        (quark(("../x.sc", b"")), "outside of the package"),
        (quark(("Classes/../../x.sc", b"")), "outside of the package"),
        (quark(("/tmp/x.sc", b"")), "outside of the package"),
        (quark(("Classes/Foo.sc", b"again")), "more than once"),
        (quark(("classes/foo.sc", b"")), "more than once (ignoring the case)"),
        (quark(("./Classes/Foo.sc", b"")), "more than once"),
        (quark(("Classes/nul.sc", b"")), "reserved name on Windows"),
        (quark(("Classes/COM1", b"")), "reserved name on Windows"),
        (quark(("Classes/Foo./A.sc", b"")), "ends with a dot or a space"),
        (tar_gz(("Classes/Foo.sc", b"")), "no magnet.toml at its root"),
        (quark(manifest="magnet-version = 1\n[package\n"), "not valid TOML"),
        (
            quark(manifest="unknown = 1\n" + QUARK_MANIFEST),
            "invalid at top level: Additional properties are not allowed",
        ),
        (
            quark(
                manifest=QUARK_MANIFEST.replace(
                    'version = "1.0.0"', 'version = "1.0.0"\nkind = "quark"'
                )
            ),
            "invalid at package: Additional properties are not allowed ('kind' was unexpected)",
        ),
        (
            quark(
                manifest=QUARK_MANIFEST.replace(
                    "exclude = [", 'global-paths = ["Classes/**.sc"]\nexclude = ['
                )
            ),
            "invalid at artifact: Additional properties are not allowed ('global-paths' was unexpected)",
        ),
        (
            quark(
                manifest=QUARK_MANIFEST.replace("[artifact.quark]", "[artifact.gluon]")
            ),
            "invalid at artifact: Additional properties are not allowed ('gluon' was unexpected)",
        ),
        (
            quark(
                manifest=QUARK_MANIFEST.replace(
                    '[artifact.quark]\npaths = ["Classes/**.sc", "HelpSource/**/*"]\n',
                    "",
                )
            ),
            "magnet.toml has neither [artifact.quark] nor [artifact.lepton], so the package would only consist of its source",
        ),
        (
            quark(("Classes/notes.txt", b"")),
            "notes.txt (not selected by [artifact.quark] paths)",
        ),
        (quark(("Classes/Private/A.sc", b"")), "Private/A.sc (matched by exclude)"),
        (quark(("Classes/.DS_Store", b"")), "(hidden files are never packaged)"),
        (quark(("dist/foo.tar.gz", b"")), "(dist is never packaged)"),
        (quark(("sclang_conf.yaml", b"")), "(sclang_conf.yaml is never packaged)"),
        (
            tar_gz(
                ("Classes/Foo.sc", b""),
                (
                    "magnet.toml",
                    QUARK_MANIFEST.replace(
                        "\n[artifact]", 'readme = "X.md"\n\n[artifact]'
                    ).encode(),
                ),
            ),
            "The readme X.md of [metadata] is missing",
        ),
        (
            quark(manifest=QUARK_MANIFEST.replace("Classes/**.sc", "Classes/[a")),
            "`Classes/[a` is not a valid glob",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "archive",
)
def test_refused(data, message):
    with pytest.raises(ValidationError) as error:
        inspect(data)
    assert message in "; ".join(error.value.messages)


def test_refused_files_are_listed():
    data = quark(*[(f"Classes/{i}.txt", b"") for i in range(7)])

    with pytest.raises(ValidationError) as error:
        inspect(data)

    assert error.value.messages == [
        "The archive contains files which the manifest doesn't select: "
        "Classes/0.txt (not selected by [artifact.quark] paths), "
        "Classes/1.txt (not selected by [artifact.quark] paths), "
        "Classes/2.txt (not selected by [artifact.quark] paths), "
        "Classes/3.txt (not selected by [artifact.quark] paths), "
        "Classes/4.txt (not selected by [artifact.quark] paths) and 2 more"
    ]


MAPPING_MANIFEST = QUARK_MANIFEST.replace(
    'paths = ["Classes/**.sc", "HelpSource/**/*"]',
    'paths = [\n  "Classes/**.sc",\n  "HelpSource/**/*",\n'
    '  { from = "external/faust/libraries", as = "./externals/faustlibs/" },\n'
    '  { from = "LICENSE.txt", as = "Classes/LICENSE" },\n]',
)


def test_quark_paths_with_mappings():
    # `{ from, as }` entries select their `as` and everything below it
    data = quark(
        ("externals/faustlibs/stdfaust.lib", b""),
        ("externals/faustlibs/nested/more.lib", b""),
        ("Classes/LICENSE", b""),
        # `exclude` is matched against the path in the project by the CLI
        ("externals/faustlibs/Classes/Private/x.lib", b""),
        manifest=MAPPING_MANIFEST.replace(
            '"Classes/Private/**"', '"externals/faustlibs/Classes/Private/**"'
        ),
    )

    assert "externals/faustlibs/nested/more.lib" in inspect(data).files


def test_files_outside_of_globs_and_mappings_are_refused():
    data = quark(
        ("externals/faustlibs/stdfaust.lib", b""),
        ("externals/other.lib", b""),
        ("externals/faustlibs2/x.lib", b""),
        ("Classes/LICENSE.txt", b""),
        ("externals/faustlibs/.hidden", b""),
        manifest=MAPPING_MANIFEST,
    )

    with pytest.raises(ValidationError) as error:
        inspect(data)

    assert error.value.messages == [
        "The archive contains files which the manifest doesn't select: "
        "externals/other.lib (not selected by [artifact.quark] paths), "
        "externals/faustlibs2/x.lib (not selected by [artifact.quark] paths), "
        "Classes/LICENSE.txt (not selected by [artifact.quark] paths), "
        "externals/faustlibs/.hidden (hidden files are never packaged)"
    ]


def test_mapping_to_the_package_root_selects_every_file():
    manifest = QUARK_MANIFEST.replace(
        'paths = ["Classes/**.sc", "HelpSource/**/*"]',
        'paths = [{ from = "quark", as = "." }]',
    )

    assert "Foo.sc" in inspect(quark(("Foo.sc", b""), manifest=manifest)).files
    with pytest.raises(ValidationError, match=r"dist/Foo.sc \(dist is never"):
        inspect(quark(("dist/Foo.sc", b""), manifest=manifest))


def test_mapping_outside_of_the_package_selects_nothing():
    manifest = QUARK_MANIFEST.replace(
        'paths = ["Classes/**.sc", "HelpSource/**/*"]',
        'paths = ["Classes/**.sc", "HelpSource/**/*", '
        '{ from = "a", as = "../x" }, { from = "b", as = "/x" }]',
    )

    with pytest.raises(ValidationError, match="x/a.sc"):
        inspect(quark(("x/a.sc", b""), manifest=manifest))


def test_gzip_bomb_is_refused(settings, tmp_path, monkeypatch):
    settings.MAGNET_ARCHIVE_MAX_UNPACKED_SIZE = 1024 * 1024
    data = quark(("Classes/Zeros.sc", bytes(2 * 1024 * 1024)))
    assert len(data) < 10 * 1024
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="unpacks to more than 1048576 bytes"):
        inspect(data)
    assert list(tmp_path.iterdir()) == []


def test_too_many_entries(settings):
    settings.MAGNET_ARCHIVE_MAX_ENTRIES = 4

    with pytest.raises(ValidationError, match="more than 4 entries"):
        inspect(quark(("Classes/Bar.sc", b"")))


def test_too_large_manifest(settings):
    settings.MAGNET_ARCHIVE_MAX_TEXT_FILE_SIZE = 100

    with pytest.raises(ValidationError, match="magnet.toml in the archive is larger"):
        inspect(quark())


def test_readme_from_metadata_may_be_excluded():
    # `collect.rs: test_readme`: the readme is always packaged
    manifest = QUARK_MANIFEST.replace(
        "\n[artifact]", 'readme = "docs/intro.md"\n\n[artifact]'
    ).replace('exclude = ["Classes/Private/**"]', 'exclude = ["docs/**"]')
    data = tar_gz(
        ("Classes/Foo.sc", b""),
        ("docs/intro.md", b"Hi"),
        ("magnet.toml", manifest.encode()),
    )

    assert inspect(data).readme == "Hi"
    # README.md isn't the readme then
    with pytest.raises(ValidationError, match="README.md"):
        inspect(tar_gz(("README.md", b""), ("magnet.toml", manifest.encode())))


def test_quark_without_readme():
    data = tar_gz(("Classes/Foo.sc", b""), ("magnet.toml", QUARK_MANIFEST.encode()))
    assert inspect(data).readme is None


def test_quark_has_no_plugins():
    with pytest.raises(ValidationError, match="plugins/Foo.scx"):
        inspect(quark(("plugins/Foo.scx", b"")))


def test_hidden_file_in_quark_is_refused():
    with pytest.raises(ValidationError, match="Classes/.Foo.sc .hidden files"):
        inspect(quark(("Classes/.Foo.sc", b"")))


def test_quark_artifact_needs_quark_table():
    manifest = LEPTON_MANIFEST.replace(
        '[artifact.quark]\npaths = ["Classes/**.sc"]\n', ""
    )
    with pytest.raises(
        ValidationError,
        match=r"There is no \[artifact.quark\] in magnet.toml, so the package has no "
        "quark artifact",
    ):
        inspect(quark(manifest=manifest))


def test_lepton_artifact_needs_lepton_table():
    with pytest.raises(ValidationError, match=r"There is no \[artifact.lepton\]"):
        inspect_lepton(lepton(manifest=QUARK_MANIFEST))


def test_overrides_are_refused():
    manifest = QUARK_MANIFEST + '\n[overrides]\nbar = { path = "../bar" }\n'
    with pytest.raises(
        ValidationError, match=r"can't be published with \[overrides\]: bar"
    ):
        inspect(quark(manifest=manifest))


def test_valid_lepton():
    # `collect.rs: test_lepton_artifact`
    artifact = inspect_lepton(lepton(("README.md", b"# Foo\n")))

    assert artifact.kinds == ["quark", "lepton"]
    assert artifact.files == [
        "magnet.toml",
        "plugins/macos_arm64/api3_0/Foo.scx",
        "README.md",
    ]
    assert artifact.readme == "# Foo\n"


@pytest.mark.parametrize(
    "path",
    [
        "plugins/Foo.scx",
        "plugins/macos_arm64/Foo.scx",
        "plugins/macos_arm64/api3_0/sub/Foo.scx",
        "plugins/macos_arm64/api4_0/Foo.scx",
        "plugins/linux_x86_64/api3_0/Foo.so",
        "build/Foo.scx",
        "Classes/Foo.sc",
    ],
)
def test_lepton_files_outside_their_plugins_folder_are_refused(path):
    with pytest.raises(
        ValidationError, match=f"{path} .not a binary in plugins/macos_arm64/api3_0/"
    ):
        inspect_lepton(lepton((path, b"")))


@pytest.mark.parametrize("platform", ["linux_x86_64", "windows_x86_64"])
def test_lepton_needs_platform_paths_for_its_platform(platform):
    # `collect.rs: test_binaries_of_the_platform`
    with pytest.raises(
        ValidationError,
        match=f"There are no `platform-paths` for {platform} in \\[artifact.lepton\\]",
    ):
        inspect_lepton(
            tar_gz(
                ("magnet.toml", LEPTON_MANIFEST.encode()),
                (f"plugins/{platform}/api3_0/Foo.so", b""),
            ),
            platform=platform,
        )


@pytest.mark.parametrize(
    "kind, abi, platform",
    [
        ("lepton", "any", "macos_arm64"),
        ("lepton", "api3_0", "any"),
        ("quark", "api3_0", "macos_arm64"),
        ("source", "any", "macos_arm64"),
        ("gluon", "any", "any"),
    ],
)
def test_tags_per_kind(kind, abi, platform):
    with pytest.raises(ValidationError):
        inspect(lepton(), kind, abi, platform)


def test_unknown_platform_in_manifest():
    # `collect.rs: test_unknown_platform_in_manifest`
    manifest = LEPTON_MANIFEST.replace("macos_arm64 =", "macos =")
    with pytest.raises(ValidationError, match="platform-paths"):
        inspect(source(manifest=manifest), "source")


def test_source_of_paths():
    # `collect.rs: test_source_of_paths`
    artifact = inspect(source(("README.md", b"# Foo\n")), "source")

    assert artifact.kinds == ["quark", "lepton"]
    assert artifact.files == [
        "CMakeLists.txt",
        "Classes/Foo.sc",
        "magnet.toml",
        "src/Foo.cpp",
        "README.md",
    ]
    for path, reason in [
        ("build/Foo.scx", "not selected by [artifact.source] paths"),
        ("src/.cache", "hidden files are never packaged"),
        ("dist/foo.tar.gz", "dist is never packaged"),
    ]:
        with pytest.raises(ValidationError, match=re.escape(f"{path} ({reason})")):
            inspect(source((path, b"")), "source")


def test_source_of_paths_needs_paths():
    # `collect.rs: test_source_paths_required`
    manifest = LEPTON_MANIFEST.replace(
        'paths = ["Classes/**", "src/**", "CMakeLists.txt"]\n', ""
    )
    with pytest.raises(ValidationError, match=r"\[artifact.source\] needs `paths`"):
        inspect(source(manifest=manifest), "source")


def git_source(*extra: tuple[str, bytes]) -> bytes:
    return tar_gz(
        (".github/workflows/ci.yml", b"on: push\n"),
        (".gitignore", b"dist/\n"),
        ("Classes/Foo.sc", b"Foo {}\n"),
        ("notes.txt", b"anything git tracks\n"),
        ("magnet.toml", GIT_SOURCE_MANIFEST.encode()),
        *extra,
    )


def test_source_from_git():
    # any file git tracks, hidden ones too: they can't be checked against git
    artifact = inspect(git_source(), "source")
    assert artifact.files == [
        ".github/workflows/ci.yml",
        ".gitignore",
        "Classes/Foo.sc",
        "notes.txt",
        "magnet.toml",
    ]


@pytest.mark.parametrize(
    "path, reason",
    [
        (".git/config", ".git is never packaged"),
        ("sub/.git/HEAD", ".git is never packaged"),
        ("dist/foo.tar.gz", "dist is never packaged"),
        ("magnet_packages/bar/Bar.sc", "magnet_packages is never packaged"),
    ],
)
def test_source_from_git_refuses(path, reason):
    with pytest.raises(ValidationError, match=f"{path} .{reason}"):
        inspect(git_source((path, b"")), "source")


def test_source_from_git_respects_exclude():
    manifest = GIT_SOURCE_MANIFEST.replace(
        'exclude = ["Classes/Private/**"]', 'exclude = ["notes.txt"]'
    )
    data = tar_gz(("notes.txt", b""), ("magnet.toml", manifest.encode()))
    with pytest.raises(ValidationError, match=r"notes.txt \(matched by exclude\)"):
        inspect(data, "source")


@pytest.mark.parametrize(
    "data",
    [
        quark(("classes/foo.sc", b"")),
        quark(("Classes/aux.txt", b"")),
    ],
    ids=["case-collision", "reserved-name"],
)
def test_portability_checks_apply_to_all_artifacts(data):
    # the field validator of `Asset.file` (also used by the management commands)
    with pytest.raises(ValidationError):
        validate_artifact_archive(ContentFile(data, name="a.tar.gz"))


def test_m1_checks_need_no_manifest():
    validate_artifact_archive(ContentFile(tar_gz(("Classes/Foo.sc", b"")), name="a"))


@pytest.mark.skipif(not MAGNET_CLI.is_dir(), reason="needs ../magnet_cli")
@needs_cli_with_artifact_kinds
def test_schema_is_the_clis():
    vendored = json.loads(SCHEMA_PATH.read_text())
    cli = json.loads((MAGNET_CLI / "schemes" / "magnet.v1.schema.json").read_text())
    assert vendored == cli


def write_project(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(content)


def magnet_package(root: Path, **env: str) -> dict[str, bytes]:
    """The artifacts of `magnet package` by file name."""
    subprocess.run(
        [MAGNET, "package"],
        cwd=root,
        env={"HOME": str(root), "PATH": "/usr/bin:/bin", **env},
        check=True,
        capture_output=True,
    )
    return {path.name: path.read_bytes() for path in (root / "dist").iterdir()}


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@needs_cli_with_artifact_kinds
def test_artifacts_of_the_real_cli(tmp_path):
    write_project(
        tmp_path,
        {
            ".gitignore": "dist/\n",
            "Classes/Foo.sc": "Foo {}\n",
            "Classes/Nested/Bar.sc": "Bar {}\n",
            "Classes/Private/Secret.sc": "Secret {}\n",
            "HelpSource/Classes/Foo.schelp": "title:: Foo\n",
            "README.md": "# Foo\n",
            "notes.txt": "only in the source\n",
            # no [artifact.source]: the files git tracks
            "magnet.toml": QUARK_MANIFEST,
        },
    )
    git = {**os.environ, **GIT_ENV}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "1"]):
        subprocess.run(["git", *args], cwd=tmp_path, env=git, check=True)

    artifacts = magnet_package(tmp_path)

    assert sorted(artifacts) == [
        "foo-1.0.0-quark-any-any.tar.gz",
        "foo-1.0.0-source-any-any.tar.gz",
    ]
    quark_artifact = inspect(artifacts["foo-1.0.0-quark-any-any.tar.gz"])
    assert quark_artifact.files == [
        "Classes/Foo.sc",
        "Classes/Nested/Bar.sc",
        "HelpSource/Classes/Foo.schelp",
        "README.md",
        "magnet.toml",
    ]
    assert quark_artifact.manifest["package"]["sclang"] == ">=3.13"
    source_artifact = inspect(artifacts["foo-1.0.0-source-any-any.tar.gz"], "source")
    assert source_artifact.files == [
        ".gitignore",
        "Classes/Foo.sc",
        "Classes/Nested/Bar.sc",
        "HelpSource/Classes/Foo.schelp",
        "README.md",
        "magnet.toml",
        "notes.txt",
    ]


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@needs_cli_with_quark_mappings
def test_quark_with_mappings_of_the_real_cli(tmp_path):
    write_project(
        tmp_path,
        {
            "Classes/Foo.sc": "Foo {}\n",
            "HelpSource/Classes/Foo.schelp": "title:: Foo\n",
            "external/faust/libraries/stdfaust.lib": "// faust\n",
            "external/faust/libraries/nested/more.lib": "// faust\n",
            "external/faust/README.md": "not packaged\n",
            "LICENSE.txt": "MIT\n",
            "magnet.toml": MAPPING_MANIFEST.replace(
                "\n[artifact]\n",
                '\n[artifact]\nsource = { from = "paths", paths = ["**/*"] }\n',
            ),
        },
    )

    artifacts = magnet_package(tmp_path)

    assert inspect(artifacts["foo-1.0.0-quark-any-any.tar.gz"]).files == [
        "Classes/Foo.sc",
        "Classes/LICENSE",
        "HelpSource/Classes/Foo.schelp",
        "externals/faustlibs/nested/more.lib",
        "externals/faustlibs/stdfaust.lib",
        "magnet.toml",
    ]


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@needs_cli_with_artifact_kinds
def test_lepton_artifacts_of_the_real_cli(tmp_path):
    write_project(
        tmp_path,
        {
            "Classes/Foo.sc": "Foo {}\n",
            "src/Foo.cpp": "// Foo\n",
            "CMakeLists.txt": "# Foo\n",
            "build/Foo.scx": "binary\n",
            "build/CMakeCache.txt": "not packaged\n",
            "magnet.toml": LEPTON_MANIFEST,
        },
    )

    artifacts = magnet_package(
        tmp_path, MAGNET_ABI="api3_0", MAGNET_PLATFORM="macos_arm64"
    )

    assert sorted(artifacts) == [
        "foo-1.0.0-lepton-api3_0-macos_arm64.tar.gz",
        "foo-1.0.0-quark-any-any.tar.gz",
        "foo-1.0.0-source-any-any.tar.gz",
    ]
    binary = inspect_lepton(artifacts["foo-1.0.0-lepton-api3_0-macos_arm64.tar.gz"])
    assert binary.files == ["magnet.toml", "plugins/macos_arm64/api3_0/Foo.scx"]
    assert inspect(artifacts["foo-1.0.0-quark-any-any.tar.gz"]).files == [
        "Classes/Foo.sc",
        "magnet.toml",
    ]
    assert inspect(artifacts["foo-1.0.0-source-any-any.tar.gz"], "source").files == [
        "CMakeLists.txt",
        "Classes/Foo.sc",
        "magnet.toml",
        "src/Foo.cpp",
    ]
