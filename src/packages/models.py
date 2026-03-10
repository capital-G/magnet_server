import hashlib
import logging
import uuid

import semver
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import validators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


def get_sentinel_user():
    return get_user_model().objects.get_or_create(username="deleted")[0]


def valid_semver(value: str) -> None:
    if not semver.VersionInfo.is_valid(value):
        raise ValidationError("Invalid semver format")


class Project(models.Model):
    """A basic model which acts as a housing for releases.
    This is more like an organization as all meta-data for the
    project are attached to the release."""

    class ProjectType(models.TextChoices):
        quark = "Quark"
        server_extension = "Server Extension"

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
        help_text=_("The name of the project"),
        unique=True,
    )

    project_type = models.CharField(
        max_length=255,
        choices=ProjectType.choices,
        blank=False,
    )

    slug = models.SlugField(
        max_length=255,
        blank=False,
        null=False,
        help_text=_("URL compatible name of the project"),
        unique=True,
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
        ordering = ("name",)

    def __str__(self):
        return self.name


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
        blank=False,
        null=False,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="unique_dependencies",
                fields=("release", "dependency"),
            ),
            # @todo add constraint of no self-dependencies!
        ]

    def __str__(self):
        return f"{self.release} requires {self.dependency} ({self.constraint})"


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

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="releases",
        help_text=_("The project this release is for"),
    )

    version = models.CharField(
        max_length=60,
        blank=False,
        null=False,
        help_text=_(
            "The version of the release. Semantic versioning is advised, i.e. 2.10.0"
        ),
        validators=[valid_semver],
    )

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

    is_prerelease = models.BooleanField(
        default=False,
        help_text=_("Whether the release is prerelease"),
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

    def make_git_entry(self):
        # avoid circular imports
        from git_db.db import git_database

        git_database.commit_release(self)

    def yank_git_entry(self):
        from git_db.db import git_database

        self.yanked = True
        self.save()

        git_database.yank_release(self)

    class Meta:
        ordering = (
            "-version",
            "-created",
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


class Asset(models.Model):
    class AssetFormat(models.TextChoices):
        quark_source = "quark_source", _("Quark source")
        server_extension_binary = (
            "server_extension_binary",
            _("Server extension binary"),
        )

    class Architecture(models.TextChoices):
        x86_64 = "x86_64", _("X86 64bit")
        x86 = "x86", _("X86 32bit")
        armv7 = "armv7", _("ARMv7")  # 32bit arm
        armv8 = "armv8", _("ARMv8")  # 64bit arm
        arm64 = "arm64", _("ARM64")  # arm from apple

    class OperatingSystem(models.TextChoices):
        windows = "windows", _("Windows")
        linux = "linux", _("Linux")
        # @todo make this lowercase!
        macos = "macOS", _("MacOS")

    class ServerAbiVersion(models.IntegerChoices):
        """Server extensions need to be compiled against a specific server API version.
        See <https://github.com/supercollider/supercollider/blob/217e94c53fbb82658e99fa734fd0d857eb9897c4/include/plugin_interface/SC_InterfaceTable.h#L26>
        """

        version_1 = 1  # 3.5 via 55cc46e (2011)
        version_2 = 2  # 3.9 via 0f39fea (2012)
        version_3 = 3  # 3.9 via 47a88a0 (2017)
        version_4 = 4  # 3.15 - unreleased - PR via https://github.com/supercollider/supercollider/pull/6702 (ca 2025)

    def get_upload_to(self, filename) -> str:
        # use a custom file format?
        return f"assets/{self.release.project.slug}/{self.identifier}.tar.gz"

    @staticmethod
    def is_tar_gz_file(filename: models.FileField):
        if not filename.name.endswith(".tar.gz"):
            raise ValidationError(_("File must be a .tar.gz"))

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
        blank=False,
        null=False,
        upload_to=get_upload_to,
        validators=[is_tar_gz_file],
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

    asset_format = models.CharField(
        max_length=32,
        choices=AssetFormat.choices,
        blank=False,
        null=False,
        help_text=_("Which kind of asset format this is targeting"),
    )

    architecture = models.CharField(
        max_length=16,
        choices=Architecture.choices,
        blank=True,
        null=True,  # null for quarks
    )

    operating_system = models.CharField(
        max_length=16,
        choices=OperatingSystem.choices,
        blank=True,
        null=True,  # null for quarks
    )

    server_abi_version = models.IntegerField(
        blank=True,
        choices=ServerAbiVersion.choices,
        null=True,  # null for quarks
        help_text=_(
            "The server runtime this extension is targeting. Leave empty for Quarks."
        ),
    )

    @property
    def identifier(self) -> str:
        if self.asset_format == Asset.AssetFormat.server_extension_binary:
            return f"{self.release.project.slug}-{self.release.version}-{self.architecture}-{self.operating_system}-abi{self.server_abi_version}"
        else:
            return f"{self.release.project.slug}-{self.release.version}-quark-source"

    def make_git_entry(self):
        from git_db.db import git_database

        git_database.commit_asset(self)

    class Meta:
        ordering = (
            "release",
            "-created",
        )
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        # should constraints be added for release/platform tuples?

        constraints = [
            models.CheckConstraint(
                name="mandatory_tuples",
                condition=(
                    Q(asset_format="server_extension_binary")
                    & Q(operating_system__isnull=False)
                    & Q(server_abi_version__isnull=False)
                    & Q(architecture__isnull=False)
                )
                | (
                    Q(asset_format="quark_source")
                    & Q(operating_system__isnull=True)
                    & Q(server_abi_version__isnull=True)
                    & Q(architecture__isnull=True)
                ),
            )
        ]

        unique_together = (
            "release",
            "server_abi_version",
            "architecture",
            "operating_system",
            "asset_format",
        )

    def clean(self, *args, **kwargs):
        # hack so we can also insert quark sources via the admin
        for a in ["server_abi_version", "architecture", "operating_system"]:
            if getattr(self, a) == "":
                setattr(self, a, None)
        return super().clean()

    def save(self, *args, **kwargs):
        if self.file and not self.sha256_checksum:
            hasher = hashlib.sha256()
            for chunk in self.file.chunks():
                hasher.update(chunk)
            self.sha256_checksum = hasher.hexdigest()
            logger.info(f"{self} obtained checksum {self.sha256_checksum}")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.identifier
