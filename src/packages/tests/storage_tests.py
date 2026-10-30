import hashlib
import io
import tarfile

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from packages.models import ANY, Asset
from packages.storage import ArtifactExistsError
from packages.tests.factories import ReleaseFactory
from packages.validators import validate_artifact_archive


def tar_gz(*members: tarfile.TarInfo, content: bytes = b"Foo {}\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for member in members:
            if member.isfile():
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            else:
                archive.addfile(member)
    return buffer.getvalue()


def entry(name: str, type=tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = type
    info.linkname = linkname
    return info


QUARK = tar_gz(entry("Classes", tarfile.DIRTYPE), entry("Classes/Foo.sc"))


def upload(release, data: bytes = QUARK, kind="quark", **kwargs) -> Asset:
    asset = Asset(
        release=release,
        uploader=release.owner,
        file=ContentFile(data, name="upload.tar.gz"),
        kind=kind,
        **kwargs,
    )
    asset.full_clean()
    asset.save()
    return asset


@pytest.mark.django_db
def test_quark_artifact_is_stored_at_its_download_path():
    release = ReleaseFactory(project__name="DDW_Common", version="0.1.0")
    asset = upload(release)

    path = "dl/ddw-common/0.1.0/ddw-common-0.1.0-quark-any-any.tar.gz"
    assert asset.file.name == path
    assert (settings.MAGNET_ARTIFACTS_ROOT / path).read_bytes() == QUARK
    assert asset.file.url == f"http://localhost:8000/{path}"
    assert asset.sha256_checksum == hashlib.sha256(QUARK).hexdigest()


@pytest.mark.django_db
def test_second_upload_for_same_kind_abi_and_platform_is_rejected():
    release = ReleaseFactory()
    upload(release)

    with pytest.raises(ValidationError) as e:
        upload(release, data=tar_gz(entry("Classes/Other.sc")))
    # unique (release, kind, abi, platform)
    assert "__all__" in e.value.message_dict

    # the same abi and platform for another kind
    upload(release, data=tar_gz(entry("Classes/Other.sc")), kind="source")


@pytest.mark.django_db
def test_existing_artifact_file_is_never_replaced():
    release = ReleaseFactory()
    asset = upload(release)
    path = settings.MAGNET_ARTIFACTS_ROOT / asset.file.name
    asset.delete()  # Django keeps the file of a deleted asset

    with pytest.raises(ValidationError) as e:
        upload(release, data=tar_gz(entry("Classes/Other.sc")))
    assert "already exists" in e.value.message_dict["file"][0]

    # the storage refuses as well, e.g. for a concurrent upload which passed validation
    replacement = Asset(
        release=release,
        uploader=release.owner,
        kind="quark",
        file=ContentFile(tar_gz(entry("Classes/Other.sc")), name="upload.tar.gz"),
    )
    with pytest.raises(ArtifactExistsError):
        replacement.save()
    assert path.read_bytes() == QUARK


@pytest.mark.parametrize(
    "data",
    [
        tar_gz(entry("../evil.sc")),
        tar_gz(entry("Classes/../../evil.sc")),
        tar_gz(entry("/etc/evil.sc")),
        tar_gz(entry("Classes/link.sc", tarfile.SYMTYPE, linkname="/etc/passwd")),
        tar_gz(
            entry("Classes/Foo.sc"),
            entry("Classes/hard.sc", tarfile.LNKTYPE, "Classes/Foo.sc"),
        ),
        tar_gz(entry("dev", tarfile.FIFOTYPE)),
        tar_gz(),  # empty
        b"not a tarball",
        QUARK[:-4],  # truncated gzip trailer, the tar itself is complete
        QUARK[: len(QUARK) // 2],
        QUARK + b"trailing garbage",
    ],
    ids=[
        "parent",
        "nested-parent",
        "absolute",
        "symlink",
        "hardlink",
        "fifo",
        "empty",
        "garbage",
        "truncated-trailer",
        "truncated-half",
        "trailing-garbage",
    ],
)
def test_invalid_archive_is_rejected(data):
    with pytest.raises(ValidationError):
        validate_artifact_archive(ContentFile(data, name="a.tar.gz"))


def test_valid_archive_is_accepted_and_rewound():
    file = ContentFile(
        tar_gz(entry("./Classes/Foo.sc"), entry("HelpSource", tarfile.DIRTYPE))
    )
    validate_artifact_archive(file)
    assert file.tell() == 0


@pytest.mark.django_db
def test_invalid_archive_is_not_stored():
    release = ReleaseFactory()
    with pytest.raises(ValidationError):
        upload(release, data=tar_gz(entry("../evil.sc")))
    assert not settings.MAGNET_ARTIFACTS_ROOT.exists()


@pytest.mark.django_db
def test_artifacts_are_stored_with_their_kind():
    release = ReleaseFactory(
        project__name="gamma", kinds=["quark", "lepton"], version="0.3.0"
    )
    upload(release, kind="lepton", abi="api3", platform="linux_x86_64")
    upload(release, kind="source", abi=ANY, platform=ANY)
    upload(release, kind="quark", abi=ANY, platform=ANY)

    assert sorted(a.file.name for a in release.assets.all()) == [
        "dl/gamma/0.3.0/gamma-0.3.0-lepton-api3-linux_x86_64.tar.gz",
        "dl/gamma/0.3.0/gamma-0.3.0-quark-any-any.tar.gz",
        "dl/gamma/0.3.0/gamma-0.3.0-source-any-any.tar.gz",
    ]
