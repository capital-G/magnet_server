import gzip
import io
import tarfile

import factory.django
from django.core.files.base import ContentFile
from django.utils import timezone

from packages.models import ANY, Asset, Dependency, Project, Release
from users.tests.factories import UserFactory


def quark_tar_gz(files: dict[str, bytes] | None = None) -> bytes:
    """A deterministic artifact (fixed mtimes), so its sha256 is stable."""
    files = files or {"Classes/Foo.sc": b"Foo {}\n"}
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return gzip.compress(tar_buffer.getvalue(), mtime=0)


QUARK_MANIFEST = """\
magnet-version = 1

[package]
name = "foo"
version = "1.0.0"
kind = "quark"
sclang = ">=3.13"

[metadata]
summary = "A test quark"

[artifact]
global-paths = ["Classes/**.sc", "HelpSource/**/*"]
exclude = ["Classes/Private/**"]
"""

LEPTON_MANIFEST = """\
magnet-version = 1

[package]
name = "foo"
version = "1.0.0"
kind = "lepton"

[artifact]
global-paths = ["Classes/**.sc"]
platform-paths = { macos_arm64 = ["build/*.scx"] }
source-paths = ["Classes/**", "src/**", "CMakeLists.txt"]
"""


def tar_gz(*members: tuple[str, bytes] | tarfile.TarInfo) -> bytes:
    """An archive like the CLI writes it (ustar), with files `(path, content)`
    or raw members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tar.addfile(member)
            else:
                info = tarfile.TarInfo(member[0])
                info.size = len(member[1])
                tar.addfile(info, io.BytesIO(member[1]))
    return gzip.compress(buffer.getvalue(), mtime=0)


def quark(*extra: tuple[str, bytes] | tarfile.TarInfo, manifest=QUARK_MANIFEST):
    return tar_gz(
        ("Classes/Foo.sc", b"Foo {}\n"),
        ("HelpSource/Classes/Foo.schelp", b"title:: Foo\n"),
        ("README.md", b"# Foo\n"),
        ("magnet.toml", manifest.encode()),
        *extra,
    )


class ProjectFactory(factory.django.DjangoModelFactory[Project]):
    class Meta:
        model = Project

    owner = factory.SubFactory(UserFactory)

    name = factory.Sequence(lambda n: f"project-{n}")


class ReleaseFactory(factory.django.DjangoModelFactory[Release]):
    class Meta:
        model = Release

    project = factory.SubFactory(ProjectFactory)
    owner = factory.SubFactory(UserFactory)
    version = factory.Sequence(lambda n: f"1.0.{n}")
    kinds = factory.LazyFunction(lambda: ["quark"])

    class Params:
        # in the index (without writing it), the default is a draft
        final = factory.Trait(published_at=factory.LazyFunction(timezone.now))


class AssetFactory(factory.django.DjangoModelFactory[Asset]):
    class Meta:
        model = Asset

    release = factory.SubFactory(ReleaseFactory)
    uploader = factory.SelfAttribute("release.owner")
    kind = factory.LazyAttribute(lambda o: o.release.kinds[0])
    abi = ANY
    platform = ANY
    file = factory.LazyFunction(
        lambda: ContentFile(quark_tar_gz(), name="upload.tar.gz")
    )


class DependencyFactory(factory.django.DjangoModelFactory[Dependency]):
    class Meta:
        model = Dependency

    release = factory.SubFactory(ReleaseFactory)
    dependency = factory.SubFactory(ProjectFactory)
    constraint = "*"
