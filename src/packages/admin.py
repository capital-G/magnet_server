from collections.abc import Callable, Iterable

from django.contrib import admin, messages
from django.db.models import Model, QuerySet

from git_db.db import GitCommandError
from git_db.files import IndexWriteError
from packages.models import (
    Asset,
    DeletedRelease,
    Dependency,
    Project,
    Release,
    ReservedName,
)


def run_index_action[T: Model](
    model_admin: admin.ModelAdmin,
    request,
    objects: Iterable[T],
    action: Callable[[T], None],
    done: str,
) -> None:
    """Runs an index write per object and reports each result as a message."""
    for obj in objects:
        try:
            action(obj)
        except (IndexWriteError, GitCommandError) as e:
            model_admin.message_user(request, f"{obj}: {e}", messages.ERROR)
        else:
            model_admin.message_user(request, f"{obj}: {done}", messages.SUCCESS)


class ReleaseInlineAdmin(admin.TabularInline):
    show_change_link = True
    model = Release
    extra = 0

    fields = [
        "version",
        "created",
        "published_at",
        "yanked",
    ]

    readonly_fields = [
        "created",
        "published_at",
    ]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "normalized_name",
        "kind",
        "owner",
    ]

    readonly_fields = [
        "uuid",
        "normalized_name",
    ]

    inlines = [
        ReleaseInlineAdmin,
    ]

    list_filter = [
        "kind",
        "last_update",
        "created",
    ]

    filter_horizontal = [
        "maintainers",
    ]

    autocomplete_fields = [
        "owner",
    ]

    search_fields = [
        "name",
        "normalized_name",
    ]


class DependencyInline(admin.TabularInline):
    show_change_link = True
    model = Dependency
    extra = 0
    fields = [
        "dependency",
        "constraint",
    ]


class AssetInline(admin.TabularInline):
    show_change_link = True
    model = Asset
    extra = 0
    fields = [
        "abi",
        "platform",
        "sha256_checksum",
    ]

    readonly_fields = fields  # type: ignore


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = [
        "project",
        "version",
        "created",
        "published_at",
        "yanked",
        "owner",
    ]

    readonly_fields = [
        "uuid",
        "published_at",
    ]

    autocomplete_fields = [
        "project",
        "owner",
    ]

    inlines = [
        AssetInline,
        DependencyInline,
    ]

    list_filter = [
        "project__kind",
        "created",
        "last_update",
        "yanked",
    ]

    search_fields = [
        "project__name",
        "project__normalized_name",
        "version",
    ]

    actions = [
        "add_to_git_index",
        "yank_release_in_git_index",
    ]

    @admin.action(description="Add release to git index")
    def add_to_git_index(self, request, queryset: QuerySet[Release]):
        run_index_action(
            self, request, queryset, Release.make_git_entry, "published to the index"
        )

    @admin.action(description="Yank release in git index")
    def yank_release_in_git_index(self, request, queryset: QuerySet[Release]):
        run_index_action(
            self, request, queryset, Release.yank_git_entry, "yanked in the index"
        )


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = [
        "file",
        "created",
        "release",
        "abi",
        "platform",
    ]

    readonly_fields = [
        "uuid",
        "sha256_checksum",
    ]

    list_filter = [
        "created",
        "abi",
        "platform",
    ]

    autocomplete_fields = [
        "release",
    ]

    search_fields = [
        "uuid",
        "file",
        "release__project__name",
    ]

    actions = [
        "add_to_git_index",
    ]

    @admin.action(description="Enter selected assets to git index")
    def add_to_git_index(self, request, queryset: QuerySet[Asset]):
        run_index_action(
            self, request, queryset, Asset.make_git_entry, "added to the index"
        )


@admin.register(ReservedName)
class ReservedNameAdmin(admin.ModelAdmin):
    list_display = [
        "normalized_name",
        "reason",
        "claimed_by",
        "created",
    ]

    list_filter = [
        "reason",
    ]

    autocomplete_fields = [
        "claimed_by",
    ]

    search_fields = [
        "normalized_name",
        "note",
    ]


@admin.register(DeletedRelease)
class DeletedReleaseAdmin(admin.ModelAdmin):
    """Read-only: tombstones are written by the hard delete, and removing one would
    allow publishing different contents under a deleted version."""

    list_display = [
        "normalized_name",
        "version",
        "deleted_at",
        "reason",
    ]

    search_fields = [
        "normalized_name",
        "version",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
