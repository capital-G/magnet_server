from django.apps import AppConfig


class DocsConfig(AppConfig):
    """Docs of the packages, built from their SCDoc help (docs_plan.md)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "docs"
