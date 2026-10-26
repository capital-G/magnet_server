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
    path("assets/<uuid:uuid>", views.AssetDetail.as_view(), name="asset-detail"),
    path("", views.HomeView.as_view(), name="home"),
]
