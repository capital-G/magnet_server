from django.contrib import admin

from docs.models import DocBuild


@admin.register(DocBuild)
class DocBuildAdmin(admin.ModelAdmin):
    list_display = ("release", "status", "created_at", "finished_at", "sclang_version")
    list_filter = ("status",)
    search_fields = ("release__project__normalized_name",)
    list_select_related = ("release__project",)
    readonly_fields = [field.name for field in DocBuild._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False
