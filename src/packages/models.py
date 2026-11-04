import hashlib
import logging
import os
import uuid
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from packages.validators import (
    ANY,
    SOURCE_KIND,
    normalize_name,
    parse_version,
    validate_artifact_archive,
    validate_artifact_kind_tags,
    validate_artifact_tag,
    validate_git_ref,
    validate_git_url,
    validate_name,
    validate_release_kinds,
    validate_version,
    validate_version_req,
)

logger = logging.getLogger(__name__)


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
        kind=asset.kind,
        abi=asset.abi,
        platform=asset.platform,
    )


class ProjectQuerySet(models.QuerySet):
    def public(self) -> "ProjectQuerySet":
        """Projects with at least one final release; the others are only visible
        to their owners and maintainers."""
        return self.filter(releases__published_at__isnull=False).distinct()

    def of_user(self, user) -> "ProjectQuerySet":
        """Projects `user` owns or maintains, also those with only drafts."""
        return self.filter(members__user=user)


class Project(models.Model):
    """A basic model which acts as a housing for releases.
    This is more like an organization as all meta-data for the
    project are attached to the release. The kinds are per release, too."""

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

    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True,
        help_text=_(
            "URL compatible name of the project. Defaults to the normalized name."
        ),
    )

    objects = ProjectQuerySet.as_manager()

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

    def can_publish(self, user) -> bool:
        """Whether `user` may create, upload to, finalise and delete drafts, and
        yank: owners and maintainers."""
        return user.is_authenticated and self.members.filter(user=user).exists()

    def can_manage(self, user) -> bool:
        """Whether `user` may change the team: owners only."""
        return (
            user.is_authenticated
            and self.members.filter(user=user, role=ProjectMember.Role.owner).exists()
        )

    def owners(self) -> models.QuerySet:
        return get_user_model().objects.filter(
            project_memberships__project=self,
            project_memberships__role=ProjectMember.Role.owner,
        )

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


class ProjectMember(models.Model):
    """A user on the team of a project. Owners and maintainers publish; owners
    also manage the team, which always keeps at least one owner (`packages.team`)."""

    class Role(models.TextChoices):
        owner = "owner", _("Owner")
        maintainer = "maintainer", _("Maintainer")

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="members"
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="project_memberships",
    )

    role = models.CharField(max_length=20, choices=Role.choices)

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Who added the user; empty for the creator of the project"),
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Team member")
        verbose_name_plural = _("Team members")
        # owners first
        ordering = ("project", "-role", "created")
        constraints = [
            models.UniqueConstraint(
                name="unique_project_member", fields=("project", "user")
            ),
        ]

    def __str__(self):
        return f"{self.user} ({self.role} of {self.project})"


class Dependency(models.Model):
    release = models.ForeignKey(
        "Release",
        on_delete=models.CASCADE,
        related_name="dependencies_from",
    )

    # PROTECT: the index lines of other packages name it, so a package with
    # dependents can't be deleted (see `packages/deletion.py`)
    dependency = models.ForeignKey(
        "Project",
        on_delete=models.PROTECT,
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


def git_index_entry(
    url: str, ref: str, legacy_quark: bool, ignore_quark_deps: bool
) -> dict[str, Any]:
    """A git dependency in an index line, as the CLI reads it
    (`GitIndexDependency::Git`); the flags are omitted when false."""
    entry: dict[str, Any] = {}
    if legacy_quark:
        entry["legacy-quark"] = True
    if ignore_quark_deps:
        entry["ignore-quark-deps"] = True
    entry["git"] = {"url": url, "ref": ref}
    return entry


class GitDependency(models.Model):
    """A git or legacy quark dependency of a release, pinned by a commit (published
    packages can't use branches or tags, they could move)."""

    release = models.ForeignKey(
        "Release",
        on_delete=models.CASCADE,
        related_name="git_dependencies",
    )

    # normalized, the key in the index line
    name = models.CharField(max_length=255, validators=[validate_name])

    url = models.CharField(max_length=2048, validators=[validate_git_url])

    ref = models.CharField(
        max_length=40, validators=[validate_git_ref], help_text=_("Commit SHA")
    )

    legacy_quark = models.BooleanField(default=False)

    ignore_quark_deps = models.BooleanField(
        default=False,
        help_text=_("Don't use the dependencies of the quark's .quark file"),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                name="unique_git_dependencies",
                fields=("release", "name"),
            ),
        ]

    def __str__(self):
        return f"{self.release} requires {self.name} ({self.url} {self.ref})"

    def index_entry(self) -> dict[str, Any]:
        return git_index_entry(
            self.url, self.ref, self.legacy_quark, self.ignore_quark_deps
        )


class ReleaseQuerySet(models.QuerySet):
    def final(self) -> "ReleaseQuerySet":
        """Releases which are in the index (D2.3), the others are drafts."""
        return self.filter(published_at__isnull=False)

    def drafts(self) -> "ReleaseQuerySet":
        return self.filter(published_at__isnull=True)


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
        help_text=_(
            "The date and time the release was written to the index. "
            "Releases without are drafts."
        ),
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

    # from the manifest of the first artifact, empty for a draft without artifacts.
    # A list, so it's filtered with `kinds__icontains` (sqlite has no JSON contains).
    kinds = models.JSONField(
        blank=True,
        default=list,
        validators=[validate_release_kinds],
        help_text=_("The kinds the version declares, in the order quark, lepton"),
    )

    sclang = models.CharField(
        max_length=255,
        blank=True,
        default="",
        validators=[validate_version_req],
        help_text=_("Supported SuperCollider versions, e.g. >=3.13"),
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

    readme = models.TextField(
        blank=True,
        default="",
        help_text=_("The readme of the package as Markdown"),
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
    # when `packages.classes.extract_classes` last read the quark artifact; null
    # until then (a version without a quark artifact has no classes)
    classes_extracted_at = models.DateTimeField(null=True, blank=True, editable=False)

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

    objects = ReleaseQuerySet.as_manager()

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

    @property
    def is_final(self) -> bool:
        return self.published_at is not None

    def index_dependencies(self) -> dict[str, Any]:
        """The `dependencies` of the version's index line: normalized name ->
        version requirement, or the git dependency's entry, sorted."""
        dependencies: dict[str, Any] = {
            d.dependency.normalized_name: d.constraint
            for d in self.dependencies_from.select_related("dependency")
        }
        for git_dependency in self.git_dependencies.all():
            dependencies[git_dependency.name] = git_dependency.index_entry()
        return dict(sorted(dependencies.items()))

    def yank_git_entry(self, reason: str = ""):
        from git_db.db import get_git_database

        # the index first, so the database only says yanked once the index does
        get_git_database().yank_release(self)
        self.yanked = True
        self.yanked_reason = reason
        self.save(update_fields=["yanked", "yanked_reason"])
        self._update_docs()

    def unyank_git_entry(self):
        from git_db.db import get_git_database

        get_git_database().unyank_release(self)
        self.yanked = False
        self.yanked_reason = ""
        self.save(update_fields=["yanked", "yanked_reason"])
        self._update_docs()

    def _update_docs(self):
        """The current version may have changed (docs_plan.md)."""
        from docs.tasks import update_docs

        update_docs(self.project)


class Asset(models.Model):
    """An artifact of a release for one (kind, abi, platform), see D1.5.
    Every release has a `source` artifact, and one per kind it declares: a single
    `quark` artifact, and a `lepton` artifact per (abi, platform). The source and
    quark artifacts have the abi and platform `any`."""

    class Kind(models.TextChoices):
        source = SOURCE_KIND, _("Source")
        quark = "quark", _("Quark")
        lepton = "lepton", _("Lepton")

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
        validators=[validate_artifact_archive],
        help_text=_("The artifact as .tar.gz, stored at its download path"),
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

    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        help_text=_("The kind of the artifact, a kind of its release or 'source'"),
    )

    # The vocabulary of abi and platform for leptons is not decided yet (D3.2),
    # e.g. the server plugin API version (`api3_0`) and `linux_x86_64`.
    abi = models.CharField(
        max_length=64,
        default=ANY,
        validators=[validate_artifact_tag],
        help_text=_("ABI the artifact targets, 'any' for sources and quarks"),
    )

    platform = models.CharField(
        max_length=64,
        default=ANY,
        validators=[validate_artifact_tag],
        help_text=_("Platform the artifact targets, 'any' for sources and quarks"),
    )

    class Meta:
        ordering = (
            "release",
            "kind",
            "abi",
            "platform",
        )
        verbose_name = _("Asset")
        verbose_name_plural = _("Assets")
        constraints = [
            models.UniqueConstraint(
                name="unique_release_kind_abi_platform",
                fields=("release", "kind", "abi", "platform"),
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
        if self.kind:
            validate_artifact_kind_tags(self.kind, self.abi, self.platform)
        if (
            self.release_id
            and self.kind
            and self.kind != SOURCE_KIND
            and self.kind not in self.release.kinds
        ):
            raise ValidationError(
                {
                    "kind": _("%(release)s doesn't declare the kind %(kind)s.")
                    % {"release": self.release, "kind": self.kind}
                }
            )
        # a new upload must not replace an artifact, e.g. of a deleted asset (D2.3)
        if self.release_id and self.file and not self.file._committed:
            path = asset_upload_to(self, self.file.name)
            if self.file.storage.exists(path):
                raise ValidationError(
                    {
                        "file": _("The artifact %(path)s already exists.")
                        % {"path": path}
                    }
                )

    @property
    def filename(self) -> str:
        return os.path.basename(self.file.name)

    @property
    def identifier(self) -> str:
        return artifact_filename(
            name=self.release.project.normalized_name,
            version=self.release.version,
            kind=self.kind,
            abi=self.abi,
            platform=self.platform,
        ).removesuffix(".tar.gz")

    def make_git_entry(self):
        from git_db.db import get_git_database

        get_git_database().commit_asset(self)


class ReservedName(models.Model):
    """A package name nobody but `claimed_by` may register, e.g. the name of a
    legacy quark, which belongs to its author, or of a hard-deleted package.
    Projects which already exist under a reserved name are not affected."""

    class Reason(models.TextChoices):
        legacy_quark = "legacy_quark", _("Legacy quark")
        deleted = "deleted", _("Deleted package")

    normalized_name = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("The reserved name, normalized, e.g. ddw-common"),
    )

    reason = models.CharField(max_length=32, choices=Reason.choices)

    note = models.TextField(
        blank=True,
        default="",
        help_text=_("e.g. the repository URL of the legacy quark"),
    )

    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reserved_names",
        help_text=_("The user who may register a package with this name"),
    )

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("normalized_name",)
        verbose_name = _("Reserved name")
        verbose_name_plural = _("Reserved names")

    def __str__(self):
        return self.normalized_name

    def save(self, *args, **kwargs):
        self.normalized_name = normalize_name(self.normalized_name)
        super().save(*args, **kwargs)


class DeletedRelease(models.Model):
    """Tombstone of a hard-deleted version, so its version number is never
    published again: a lock file may still refer to it (DESIGN.md "Deletion")."""

    normalized_name = models.CharField(max_length=255)

    version = models.CharField(max_length=60)

    deleted_at = models.DateTimeField(auto_now_add=True)

    reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("normalized_name", "-deleted_at")
        verbose_name = _("Deleted release")
        verbose_name_plural = _("Deleted releases")
        constraints = [
            models.UniqueConstraint(
                name="unique_deleted_release",
                fields=("normalized_name", "version"),
            )
        ]

    def __str__(self):
        return f"{self.normalized_name} ({self.version})"


class ClassDefinitionQuerySet(models.QuerySet):
    def current(self) -> "ClassDefinitionQuerySet":
        """The classes and extensions of each package's newest final, non-yanked
        version, which search and conflicts compare (older versions would only add
        clutter)."""
        newest = (
            Release.objects.final()
            .filter(project=models.OuterRef("release__project"), yanked=False)
            .order_by("-version_major", "-version_minor", "-version_patch")
            .values("pk")[:1]
        )
        return self.filter(release=models.Subquery(newest))


class ClassDefinition(models.Model):
    """A class or a class extension in a `.sc` file of a version's quark artifact
    (`packages.classes.extract_classes`)."""

    release = models.ForeignKey(
        Release, on_delete=models.CASCADE, related_name="class_definitions"
    )

    name = models.CharField(max_length=255, db_index=True)

    # "" for extensions and for classes without `: Superclass` (sclang uses Object)
    superclass = models.CharField(max_length=255, blank=True, default="")

    is_extension = models.BooleanField(default=False)

    # the file in the quark artifact, and the line of the class header
    path = models.CharField(max_length=1024)

    line = models.PositiveIntegerField()

    # `osx`, `linux`, `windows`, `iphone` if the file is only compiled on that
    # platform (its folder), else ""
    platform = models.CharField(max_length=20, blank=True, default="")

    objects = ClassDefinitionQuerySet.as_manager()

    class Meta:
        ordering = ("release", "is_extension", "name", "path", "line")
        verbose_name = _("Class definition")
        verbose_name_plural = _("Class definitions")

    def __str__(self):
        return f"{'+' if self.is_extension else ''}{self.name} ({self.release})"


class MethodDefinition(models.Model):
    """A method of a class or extension, incl. the accessors of its variables."""

    class_definition = models.ForeignKey(
        ClassDefinition, on_delete=models.CASCADE, related_name="methods"
    )

    name = models.CharField(max_length=255, db_index=True)

    is_class_method = models.BooleanField(default=False)

    class Meta:
        # in the order of the file
        ordering = ("class_definition", "id")
        verbose_name = _("Method definition")
        verbose_name_plural = _("Method definitions")

    def __str__(self):
        return f"{self.class_definition.name}:{'*' if self.is_class_method else ''}{self.name}"


class ClassFileError(models.Model):
    """A `.sc` file of a version's quark artifact which doesn't parse (sclang would
    refuse it too); its classes are missing from the definitions."""

    release = models.ForeignKey(
        Release, on_delete=models.CASCADE, related_name="class_file_errors"
    )

    path = models.CharField(max_length=1024)

    message = models.TextField()

    class Meta:
        ordering = ("release", "path")

    def __str__(self):
        return f"{self.path}: {self.message}"
