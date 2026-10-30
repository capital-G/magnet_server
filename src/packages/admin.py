from collections.abc import Callable, Iterable
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError
from django.db.models import Model, QuerySet
from django.template.response import TemplateResponse

from git_db.db import GitCommandError
from git_db.files import IndexWriteError
from packages.deletion import delete_project_permanently, delete_release_permanently
from packages.models import (
    Asset,
    DeletedRelease,
    Dependency,
    GitDependency,
    Project,
    Release,
    ReservedName,
)
from packages.services import finalise_release


def run_index_action[T: Model](
    model_admin: admin.ModelAdmin,
    request,
    objects: Iterable[T],
    action: Callable[[T], object],
    done: str,
) -> None:
    """Runs an index write per object and reports each result as a message."""
    for obj in objects:
        try:
            action(obj)
        except ValidationError as e:
            model_admin.message_user(
                request, f"{obj}: {'; '.join(e.messages)}", messages.ERROR
            )
        except (IndexWriteError, GitCommandError) as e:
            model_admin.message_user(request, f"{obj}: {e}", messages.ERROR)
        else:
            model_admin.message_user(request, f"{obj}: {done}", messages.SUCCESS)


class PermanentDeleteForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60}),
        help_text="Stored with the tombstones, e.g. malware or a leaked secret.",
    )


class PermanentDeleteMixin:
    """Replaces Django's delete, which would leave the index, the storage and the
    tombstones behind, with "delete permanently" (`packages/deletion.py`)
    for superusers, after a confirmation page which asks for the reason."""

    delete_permanently_function: Callable[[Any, str], None]

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_hard_delete_permission(self, request) -> bool:
        return request.user.is_superuser

    @admin.action(
        description="Delete permanently (index, artifacts; versions never reused)",
        permissions=["hard_delete"],
    )
    def delete_permanently(self, request, queryset):
        form = PermanentDeleteForm(request.POST if "confirm" in request.POST else None)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            run_index_action(
                self,  # type: ignore[arg-type]
                request,
                queryset,
                lambda obj: self.delete_permanently_function(obj, reason),
                "deleted permanently",
            )
            return None
        return TemplateResponse(
            request,
            "admin/packages/delete_permanently.html",
            {
                **self.admin_site.each_context(request),  # type: ignore[attr-defined]
                "title": "Delete permanently",
                "objects": queryset,
                "form": form,
                "opts": queryset.model._meta,
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            },
        )


class ReleaseInlineAdmin(admin.TabularInline):
    show_change_link = True
    model = Release
    extra = 0
    can_delete = False

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
class ProjectAdmin(PermanentDeleteMixin, admin.ModelAdmin):
    delete_permanently_function = staticmethod(delete_project_permanently)
    actions = ["delete_permanently"]

    list_display = [
        "name",
        "normalized_name",
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


class GitDependencyInline(admin.TabularInline):
    model = GitDependency
    extra = 0
    fields = [
        "name",
        "url",
        "ref",
        "legacy_quark",
        "ignore_quark_deps",
    ]


class AssetInline(admin.TabularInline):
    show_change_link = True
    model = Asset
    extra = 0
    can_delete = False
    fields = [
        "kind",
        "abi",
        "platform",
        "sha256_checksum",
    ]

    readonly_fields = fields  # type: ignore


class ReleaseStateFilter(admin.SimpleListFilter):
    """Final releases by default, drafts on request."""

    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return [("final", "Final"), ("draft", "Draft"), ("all", "All")]

    def queryset(self, request, queryset):
        match self.value():
            case "draft":
                return queryset.drafts()
            case "all":
                return queryset
            case _:
                return queryset.final()

    def choices(self, changelist):
        # without Django's own "All" entry, which would mean "final" here
        for lookup, title in self.lookup_choices:
            yield {
                "selected": (self.value() or "final") == lookup,
                "query_string": changelist.get_query_string(
                    {self.parameter_name: lookup}
                ),
                "display": title,
            }


@admin.register(Release)
class ReleaseAdmin(PermanentDeleteMixin, admin.ModelAdmin):
    delete_permanently_function = staticmethod(delete_release_permanently)

    list_display = [
        "project",
        "version",
        "kinds",
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
        GitDependencyInline,
    ]

    list_filter = [
        ReleaseStateFilter,
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
        "unyank_release_in_git_index",
        "delete_permanently",
    ]

    @admin.action(description="Finalise (add release to git index)")
    def add_to_git_index(self, request, queryset: QuerySet[Release]):
        run_index_action(
            self, request, queryset, finalise_release, "published to the index"
        )

    @admin.action(description="Yank release in git index")
    def yank_release_in_git_index(self, request, queryset: QuerySet[Release]):
        run_index_action(
            self, request, queryset, Release.yank_git_entry, "yanked in the index"
        )

    @admin.action(description="Un-yank release in git index")
    def unyank_release_in_git_index(self, request, queryset: QuerySet[Release]):
        run_index_action(
            self, request, queryset, Release.unyank_git_entry, "un-yanked in the index"
        )


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None) -> bool:
        # an artifact goes with its version (`ReleaseAdmin.delete_permanently`)
        return False

    list_display = [
        "file",
        "created",
        "release",
        "kind",
        "abi",
        "platform",
    ]

    readonly_fields = [
        "uuid",
        "sha256_checksum",
    ]

    list_filter = [
        "created",
        "kind",
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
