from django.urls import path

from . import views

urlpatterns = [
    path("projects", views.ProjectsList.as_view(), name="projects-list"),
    path("project/<slug:slug>", views.ProjectDetail.as_view(), name="project-detail"),
    path("", views.HomeView.as_view(), name="home"),
]
