import hashlib
import logging
import os
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from packages.validators import (
    normalize_name,
    parse_version,
    validate_artifact_tag,
    validate_name,
    validate_version,
    validate_version_req,
)

logger = logging.getLogger(__name__)

# abi and platform of artifacts which run everywhere, e.g. the sources of a quark
ANY = "any"


def get_sentinel_user():
    return get_user_model().objects.get_or_create(username="deleted")[0]


def artifact_filename(
    name: str, version: str, kind: str, abi: str, platform: str
) -> str:
    return f"{name}-{version}-{kind}-{abi}-{platform}.tar.gz"


def artifact_path(name: str, version: str, kind: str, abi: str, platform: str) -> str:
    """Storage path of an artifact relative to the storage root, which is also its
    download path (the `dl` template of the index `config.json`)."""
    return (
        f"dl/{name}/{version}/{artifact_filename(name, version, kind, abi, platform)}"
    )


def asset_upload_to(asset: "Asset", filename: str) -> str:
    release = asset.release
    return artifact_path(
        name=release.project.normalized_name,
        version=release.version,
        kind=release.project.kind,
        abi=asset.abi,
        platform=asset.platform,
    )


def validate_tar_gz_name(file: models.FileField) -> None:
    if not file.name or not file.name.endswith(".tar.gz"):
        raise ValidationError(_("File must be a .tar.gz"))


class Project(models.Model):
    """A basic model which acts as a housing for releases.
    This is more like an organization as all meta-data for the
    project are attached to the release."""

    class Kind(models.TextChoices):
        """See D1.4. The kind of a project can't change after its first release."""

        quark = "quark", _("Quark")
        lepton = "lepton", _("Lepton")
        gluon = "gluon", _("Gluon")

    uuid = models.UUIDField(
        editable=False,
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
    )

    created = models.DateTimeField(
        auto_now_add=True,
        help_text=_("The date and time the project was first submitted"),
    )

    last_update = models.DateTimeField(
        blank=False,
        auto_now=True,
        help_text=_("The date and time the project was last updated"),
    )

    name = models.CharField(
        max_length=255,
        help_text=_("The name of the project, e.g. DDW_Common"),
        validators=[validate_name],
    )

    normalized_name = models.CharField(
        max_length=255,
        unique=True,
        editable=False,
        help_text=_(
            "Lowercase name with '_' replaced by '-', e.g. ddw-common. "
            "Used by the index and download URLs."
        ),
    )

    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
    )

    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True,
        help_text=_(
            "URL compatible name of the project. Defaults to the normalized name."
        ),
    )

    # should this be many-to-many?
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET(get_sentinel_user),
        related_name="projects_owned",
    )

    maintainers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        help_text=_("Users who are allowed to publish releases of this project"),
        related_name="projects_maintained",
        blank=True,
    )

    class Meta:
        verbose_name = _("Project")
        verbose_name_plural = _("Projects")
        ordering = ("normalized_name",)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_name(self.name)
        if not self.slug:
            self.slug = self.normalized_name
        super().save(*args, **kwargs)

    def clean(self):
        # Model forms exclude non-editable fields from the unique checks,
        # so check the normalized name here to get an error instead of an IntegrityError.
        self.normalized_name = normalize_name(self.name)
        collision = (
            Project.objects.filter(normalized_name=self.normalized_name)
            .exclude(pk=self.pk)
            .first()
        )
        if collision:
            raise ValidationError(
                {
                    "name": _("The name collides with the existing project %(name)s.")
                    % {"name": collision.name}
                }
            )


class Dependency(models.Model):
    release = models.ForeignKey(
        "Release",
        on_delete=models.CASCADE,
        related_name="dependencies_from",
    )

    dependency = models.ForeignKey(
        "Project",
        on_delete=models.CASCADE,
        related_name="dependency_to",
    )

    constraint = models.CharField(
        max_length=255,
        validators=[validate_version_req],
        help_text=_(
            "Version requirement in Cargo syntax, e.g. ^1.2.3, ~1.2 or >=1, <2"
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="unique_dependencies",
                fields=("release", "dependency"),
            ),
        ]

    def __str__(self):
        return f"{self.release} requires {self.dependency} ({self.constraint})"

    def clean(self):
        if (
            self.release_id
            and self.dependency_id
            and self.release.project_id == self.dependency_id
        ):
            raise ValidationError(
                {"dependency": _("A project can't depend on itself.")}
            )


class Release(models.Model):
    """A release acts as a version of a project.
    It is also possible to attach multiple assets to a release.
    A release also stores all kind of metadata for a project, so
    any project information can also be versionized."""

    uuid = models.UUIDField(
        editable=False,
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
    )

    created = models.DateTimeField(
        auto_now_add=True,
        help_text=_("The date and time the project was submitted"),
    )

    last_update = models.DateTimeField(
        blank=False,
        auto_now=True,
        help_text=_("The date and time the project was last updated"),
    )

    published_at = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
        help_text=_("The date and time the release was written to the index"),
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="releases",
        help_text=_("The project this release is for"),
    )

    version = models.CharField(
        max_length=60,
        help_text=_("The version of the release as MAJOR.MINOR.PATCH, e.g. 2.10.0"),
        validators=[validate_version],
    )

    # set from version on save, so releases can be ordered by version
    version_major = models.PositiveIntegerField(editable=False)
    version_minor = models.PositiveIntegerField(editable=False)
    version_patch = models.PositiveIntegerField(editable=False)

    summary = models.TextField(
        blank=True,
        default="",
        help_text=_("A short summary of the project"),
    )

    description = models.TextField(
        blank=True,
        default="",
        help_text=_("A description of the project. Supports markdown formatting."),
    )

    release_note = models.TextField(
        blank=True,
        default="",
        help_text=_("Comment about the release"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET(get_sentinel_user),
        help_text=_("Users who created the release"),
    )

    # @todo rename to homepage?
    home_page_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Optional home page URL of the project."),
    )

    bug_tracker_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Optional bug tracker URL of the project."),
    )

    repository_url = models.URLField(
        blank=True, default="", help_text=_("Optional repository URL of the project.")
    )

    license = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=_("License of the project."),
    )

    # @todo add generic tags
    # keywords = models.ManyToManyField()
    yanked = models.BooleanField(
        default=False,
        help_text=_("Whether the project is yanked"),
    )

    yanked_reason = models.TextField(
        blank=True,
        default="",
        help_text=_("Reason for yanked"),
    )

    dependencies = models.ManyToManyField(
        Project,
        blank=True,
        through=Dependency,
        related_name="dependants",
        symmetrical=False,
    )

    class Meta:
        # newest version first
        ordering = (
            "-version_major",
            "-version_minor",
            "-version_patch",
        )
        verbose_name = _("Release")
        verbose_name_plural = _("Releases")
        constraints = [
            models.UniqueConstraint(
                name="unique_project_version",
                fields=("project", "version"),
            )
        ]

    def __str__(self):
        return f"{self.project} ({self.version})"

    def save(self, *args, **kwargs):
        self.version_major, self.version_minor, self.version_patch = parse_version(
            self.version
        )
        super().save(*args, **kwargs)

    def make_git_entry(self):
        # avoid circular imports
        from git_db.db import git_database

        git_database.commit_release(self)

    def yank_git_entry(self):
        from git_db.db import git_database

        self.yanked = True
        self.save()

        git_database.yank_release(self)


class Asset(models.Model):
    """The artifact of a release for one (abi, platform), see D1.5.
    Quarks have a single artifact for `any`/`any`. Leptons have one per
    (abi, platform) plus their sources as `source`/`any`."""

    uuid = models.UUIDField(
        editable=False,
        default=uuid.uuid4,
        unique=True,
        primary_key=True,
    )

    created = models.DateTimeField(
        auto_now_add=True,
        help_text=_("The date and time the asset was submitted"),
    )

    last_update = models.DateTimeField(
        blank=False,
        auto_now=True,
        help_text=_("The date and time the asset was last updated"),
    )

    file = models.FileField(
        max_length=512,
        upload_to=asset_upload_to,
        validators=[validate_tar_gz_name],
        help_text=_("The uploaded file in .tar.gz format"),
    )

    sha256_checksum = models.CharField(
        max_length=64,
        help_text=_(
            "The SHA256 checksum of the file. Verified by the server upon upload"
        ),
        blank=True,
        editable=False,
    )

    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET(get_sentinel_user),
        related_name="extension_uploads",
        help_text=_("The uploader of this asset"),
    )

    uploaded_via = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Identifier of upload client"),
    )

    comment = models.TextField(
        default="",
        blank=True,
        help_text=_("Additional comments about this asset"),
    )

    release = models.ForeignKey(
        Release,
        on_delete=models.CASCADE,
        related_name="assets",
        help_text=_("The release this extension belongs to"),
    )

    # The vocabulary of abi and platform for leptons is not decided yet (D3.2),
    # e.g. the server plugin API version (`api3`) and `linux_x86_64`.
    abi = models.CharField(
        max_length=64,
        default=ANY,
        validators=[validate_artifact_tag],
        help_text=_("ABI the artifact targets, 'any' for quarks"),
    )

    platform = models.CharField(
        max_length=64,
        default=ANY,
        validators=[validate_artifact_tag],
        help_text=_("Platform the artifact targets, 'any' for quarks"),
    )

    class Meta:
        ordering = (
            "release",
            "abi",
            "platform",
        )
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        constraints = [
            models.UniqueConstraint(
                name="unique_release_abi_platform",
                fields=("release", "abi", "platform"),
            ),
        ]

    def __str__(self):
        return self.identifier

    def save(self, *args, **kwargs):
        if self.file and not self.sha256_checksum:
            hasher = hashlib.sha256()
            for chunk in self.file.chunks():
                hasher.update(chunk)
            self.sha256_checksum = hasher.hexdigest()
            logger.info(f"{self} obtained checksum {self.sha256_checksum}")
        super().save(*args, **kwargs)

    def clean(self):
        if (
            self.release_id
            and self.release.project.kind == Project.Kind.quark
            and (self.abi, self.platform) != (ANY, ANY)
        ):
            raise ValidationError(
                _("A quark has a single artifact with abi and platform 'any'.")
            )

    @property
    def filename(self) -> str:
        return os.path.basename(self.file.name)

    @property
    def identifier(self) -> str:
        project = self.release.project
        return artifact_filename(
            name=project.normalized_name,
            version=self.release.version,
            kind=project.kind,
            abi=self.abi,
            platform=self.platform,
        ).removesuffix(".tar.gz")

    def make_git_entry(self):
        from git_db.db import git_database

        git_database.commit_asset(self)
