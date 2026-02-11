from django.contrib import admin
from django.db.models import QuerySet

from packages.models import Asset, Dependency, Project, Release


class ReleaseInlineAdmin(admin.TabularInline):
    show_change_link = True
    model = Release
    extra = 0

    readonly_fields = [
        "created",
    ]

    fields = [
        "version",
        "created",
        "is_prerelease",
        "yanked",
    ]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "owner",
        "project_type",
    ]

    readonly_fields = [
        "uuid",
    ]

    prepopulated_fields = {"slug": ["name"]}

    inlines = [
        ReleaseInlineAdmin,
    ]

    list_filter = [
        "project_type",
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
        "asset_format",
        "architecture",
        "operating_system",
        "server_abi_version",
    ]

    readonly_fields = fields  # type: ignore


@admin.register(Release)
class ReleaseAdmin(admin.ModelAdmin):
    list_display = [
        "project",
        "version",
        "created",
        "is_prerelease",
        "yanked",
        "owner",
    ]

    readonly_fields = [
        "uuid",
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
        "is_prerelease",
        "created",
        "last_update",
        "yanked",
    ]

    search_fields = [
        "project__name",
        "project__slug",
        "version",
    ]

    actions = [
        "add_to_git_index",
        "yank_release_in_git_index",
        "make_git_entry",
    ]

    @admin.action(description="Add release to git index")
    def add_to_git_index(self, request, queryset: QuerySet[Release]):
        for release in queryset:
            release.make_git_entry()

    @admin.action(description="Yank release in git index")
    def yank_release_in_git_index(self, request, queryset: QuerySet[Release]):
        for release in queryset:
            release.yank_git_entry()


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = [
        "file",
        "created",
        "release",
        "asset_format",
        "architecture",
        "operating_system",
        "server_abi_version",
    ]

    readonly_fields = [
        "uuid",
        "sha256_checksum",
    ]

    list_filter = [
        "created",
        "asset_format",
        "architecture",
        "operating_system",
        "server_abi_version",
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
        for asset in queryset:
            asset.make_git_entry()
