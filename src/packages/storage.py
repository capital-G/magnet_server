from django.conf import settings
from django.core.files.storage import FileSystemStorage


class ArtifactExistsError(Exception):
    pass


class ArtifactStorage(FileSystemStorage):
    """Stores artifacts at their download path (see `artifact_path()`) below
    `MAGNET_ARTIFACTS_ROOT`, which nginx serves. Their URL is the download URL.

    Artifacts are never replaced (D2.3), because clients verify them against the
    sha256 in the index: storing a name which already exists raises
    :class:`ArtifactExistsError` instead of picking another name, as Django does
    by default. This also holds for concurrent uploads, since the file system
    storage creates files exclusively and asks for another name on a collision."""

    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(
            location=settings.MAGNET_ARTIFACTS_ROOT if location is None else location,
            base_url=settings.MAGNET_DOWNLOAD_BASE_URL
            if base_url is None
            else base_url,
            allow_overwrite=False,
            **kwargs,
        )

    def get_available_name(self, name, max_length=None):
        if self.exists(name):
            raise ArtifactExistsError(f"Artifact {name} already exists")
        return super().get_available_name(name, max_length=max_length)
