import json
import subprocess
import tarfile

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from packages.archive import SCHEMA_PATH, inspect_artifact
from packages.tests.cli import MAGNET, MAGNET_CLI, needs_cli_without_artifact_kinds
from packages.tests.factories import LEPTON_MANIFEST, QUARK_MANIFEST, quark, tar_gz
from packages.validators import validate_artifact_archive


def inspect(data: bytes, abi: str = "any", platform: str = "any"):
    return inspect_artifact(ContentFile(data, name="upload.tar.gz"), abi, platform)


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
        "kind": "quark",
        "sclang": ">=3.13",
    }
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
            quark(manifest=QUARK_MANIFEST.replace('"quark"', '"plugin"')),
            "invalid at package.kind",
        ),
        (quark(("Classes/notes.txt", b"")), "notes.txt (not selected by global-paths)"),
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
                        "[artifact]", 'readme = "X.md"\n[artifact]'
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
        "Classes/0.txt (not selected by global-paths), "
        "Classes/1.txt (not selected by global-paths), "
        "Classes/2.txt (not selected by global-paths), "
        "Classes/3.txt (not selected by global-paths), "
        "Classes/4.txt (not selected by global-paths) and 2 more"
    ]


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
        "[artifact]", 'readme = "docs/intro.md"\n[artifact]'
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


def test_quark_has_no_source_artifact():
    with pytest.raises(ValidationError, match="A quark has no source artifact"):
        inspect(quark(), abi="source")


def test_quark_has_no_plugins():
    with pytest.raises(ValidationError, match="plugins/Foo.scx"):
        inspect(quark(("plugins/Foo.scx", b"")))
    with pytest.raises(ValidationError, match="A quark can not have `platform-paths`"):
        inspect(
            quark(
                manifest=QUARK_MANIFEST
                + '\n[artifact.platform-paths]\nmacos_arm64 = ["build/*.scx"]\n'
            )
        )


def test_overrides_are_refused():
    manifest = QUARK_MANIFEST + '\n[overrides]\nbar = { path = "../bar" }\n'
    with pytest.raises(
        ValidationError, match=r"can't be published with \[overrides\]: bar"
    ):
        inspect(quark(manifest=manifest))


def test_lepton_artifacts():
    # `collect.rs: test_lepton_artifact` and `test_lepton_source`
    manifest = ("magnet.toml", LEPTON_MANIFEST.encode())
    binary = tar_gz(("Classes/Foo.sc", b""), manifest, ("plugins/Foo.scx", b""))
    source = tar_gz(
        ("CMakeLists.txt", b""), ("Classes/Foo.sc", b""), manifest, ("src/Foo.cpp", b"")
    )

    assert inspect(binary, abi="api3_0", platform="macos_arm64").manifest["package"][
        "kind"
    ] == ("lepton")
    inspect(source, abi="source", platform="any")
    with pytest.raises(ValidationError, match="src/Foo.cpp"):
        inspect(source, abi="api3_0", platform="macos_arm64")
    with pytest.raises(ValidationError, match="plugins/Foo.scx"):
        inspect(binary, abi="source", platform="any")
    with pytest.raises(ValidationError, match="plugins/sub/Foo.scx"):
        inspect(
            tar_gz(manifest, ("plugins/sub/Foo.scx", b"")),
            abi="api3_0",
            platform="macos_arm64",
        )


@pytest.mark.parametrize("platform", ["linux_x86_64", "any"])
def test_lepton_binaries_need_platform_paths_for_their_platform(platform):
    # `collect.rs: test_binaries_of_the_platform`
    binary = tar_gz(
        ("Classes/Foo.sc", b""),
        ("magnet.toml", LEPTON_MANIFEST.encode()),
        ("plugins/Foo.so", b""),
    )
    with pytest.raises(
        ValidationError, match=f"There are no `platform-paths` for {platform}"
    ):
        inspect(binary, abi="api3_0", platform=platform)


def test_unknown_platform_in_manifest():
    # `collect.rs: test_unknown_platform_in_manifest`
    manifest = LEPTON_MANIFEST.replace("macos_arm64 =", "macos =")
    with pytest.raises(ValidationError, match="platform-paths"):
        inspect(tar_gz(("magnet.toml", manifest.encode())), abi="source")


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
@needs_cli_without_artifact_kinds
def test_schema_is_the_clis():
    vendored = json.loads(SCHEMA_PATH.read_text())
    cli = json.loads((MAGNET_CLI / "schemes" / "magnet.v1.schema.json").read_text())
    assert vendored == cli


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@needs_cli_without_artifact_kinds
def test_artifact_of_the_real_cli(tmp_path):
    for path, content in {
        "Classes/Foo.sc": "Foo {}\n",
        "Classes/Nested/Bar.sc": "Bar {}\n",
        "Classes/Private/Secret.sc": "Secret {}\n",
        "HelpSource/Classes/Foo.schelp": "title:: Foo\n",
        "README.md": "# Foo\n",
        "notes.txt": "not packaged\n",
        "magnet.toml": QUARK_MANIFEST,
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    subprocess.run(
        [MAGNET, "package"],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
    )

    data = (tmp_path / "dist" / "foo-1.0.0-quark-any-any.tar.gz").read_bytes()
    artifact = inspect(data)

    assert artifact.files == [
        "Classes/Foo.sc",
        "Classes/Nested/Bar.sc",
        "HelpSource/Classes/Foo.schelp",
        "README.md",
        "magnet.toml",
    ]
    assert artifact.manifest["package"]["sclang"] == ">=3.13"


@pytest.mark.skipif(not MAGNET.is_file(), reason="needs a debug build of ../magnet_cli")
@needs_cli_without_artifact_kinds
def test_lepton_artifacts_of_the_real_cli(tmp_path):
    for path, content in {
        "Classes/Foo.sc": "Foo {}\n",
        "src/Foo.cpp": "// Foo\n",
        "CMakeLists.txt": "# Foo\n",
        "build/Foo.scx": "binary\n",
        "build/CMakeCache.txt": "not packaged\n",
        "magnet.toml": LEPTON_MANIFEST,
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)
    subprocess.run(
        [MAGNET, "package"],
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "MAGNET_ABI": "api3_0",
            "MAGNET_PLATFORM": "macos_arm64",
        },
        check=True,
        capture_output=True,
    )

    dist = tmp_path / "dist"
    binary = inspect(
        (dist / "foo-1.0.0-lepton-api3_0-macos_arm64.tar.gz").read_bytes(),
        abi="api3_0",
        platform="macos_arm64",
    )
    source = inspect(
        (dist / "foo-1.0.0-lepton-source-any.tar.gz").read_bytes(),
        abi="source",
        platform="any",
    )

    assert binary.files == ["Classes/Foo.sc", "magnet.toml", "plugins/Foo.scx"]
    assert source.files == [
        "CMakeLists.txt",
        "Classes/Foo.sc",
        "magnet.toml",
        "src/Foo.cpp",
    ]
