from django.urls import path

from . import views

urlpatterns = [
    path("projects", views.ProjectsList.as_view(), name="projects-list"),
    path("", views.HomeView.as_view(), name="home"),
]
