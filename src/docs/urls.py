from django.urls import path

from . import views

urlpatterns = [
    path("project/<slug:slug>/docs", views.builds_view, name="docs-builds"),
    path("project/<slug:slug>/docs/rebuild", views.rebuild_view, name="docs-rebuild"),
]
