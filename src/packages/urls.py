from django.urls import path

from . import views

urlpatterns = [
    path("projects", views.ProjectsList.as_view(), name="projects-list"),
    path("project/<slug:slug>", views.ProjectDetail.as_view(), name="project-detail"),
    path(
        "project/<slug:slug>/drafts/<str:version>/delete",
        views.delete_draft_view,
        name="delete-draft",
    ),
    path(
        "project/<slug:slug>/versions/<str:version>/yank",
        views.yank_view,
        name="yank",
    ),
    path(
        "project/<slug:slug>/versions/<str:version>/unyank",
        views.unyank_view,
        name="unyank",
    ),
    path("assets/<uuid:uuid>", views.AssetDetail.as_view(), name="asset-detail"),
    path("", views.HomeView.as_view(), name="home"),
]
