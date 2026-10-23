import gzip
import io
import tarfile

import factory.django
from django.core.files.base import ContentFile

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


class ProjectFactory(factory.django.DjangoModelFactory[Project]):
    class Meta:
        model = Project

    owner = factory.SubFactory(UserFactory)

    name = factory.Sequence(lambda n: f"project-{n}")
    kind = Project.Kind.quark


class ReleaseFactory(factory.django.DjangoModelFactory[Release]):
    class Meta:
        model = Release

    project = factory.SubFactory(ProjectFactory)
    owner = factory.SubFactory(UserFactory)
    version = factory.Sequence(lambda n: f"1.0.{n}")


class AssetFactory(factory.django.DjangoModelFactory[Asset]):
    class Meta:
        model = Asset

    release = factory.SubFactory(ReleaseFactory)
    uploader = factory.SelfAttribute("release.owner")
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
