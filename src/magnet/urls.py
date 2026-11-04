"""
URL configuration for magnet project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path, re_path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from oauth2_provider import urls as oauth2_urls
from rest_framework_nested import routers

from docs.views import serve_docs
from magnet.views import ApiNotFoundView, VersionApiView, legal_view
from packages.api import ArtifactView, FinaliseView, VersionsView, VersionView
from packages.classes_api import (
    ClassCheckView,
    ClassSearchView,
    PackageConflictsView,
)
from packages.views import ProjectViewSet, serve_artifact, serve_index_mirror

router = routers.DefaultRouter()
router.register(r"projects", ProjectViewSet, basename="project")

urlpatterns = [
    path("o/", include(oauth2_urls)),
    path("api/v1/", include(router.urls)),
    path("api/version/", VersionApiView.as_view()),
    path(
        "api/v1/packages/<str:name>/versions/",
        VersionsView.as_view(),
        name="api-versions",
    ),
    path(
        "api/v1/packages/<str:name>/versions/<str:version>/",
        VersionView.as_view(),
        name="api-version",
    ),
    path(
        "api/v1/packages/<str:name>/versions/<str:version>/artifacts/<str:kind>/<str:abi>/<str:platform>/",
        ArtifactView.as_view(),
        name="api-artifact",
    ),
    path(
        "api/v1/packages/<str:name>/versions/<str:version>/finalise/",
        FinaliseView.as_view(),
        name="api-finalise",
    ),
    path("api/v1/classes/", ClassSearchView.as_view(), name="api-classes"),
    path("api/v1/classes/check/", ClassCheckView.as_view(), name="api-class-check"),
    path(
        "api/v1/packages/<str:name>/conflicts/",
        PackageConflictsView.as_view(),
        name="api-conflicts",
    ),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path(
        "api/swagger/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger",
    ),
    # after all API routes: JSON instead of HTML for unknown API paths
    re_path(r"^api/", ApiNotFoundView.as_view()),
    path("admin/", admin.site.urls),
    path("legal", legal_view, name="legal"),
    path("accounts/", include("allauth.urls")),
    path("users/", include("users.urls")),
    path("", include("docs.urls")),
    path("", include("packages.urls")),
]

if settings.DEBUG:
    # artifact downloads, the index mirror and the docs for runserver; deployments
    # serve them via nginx
    urlpatterns += [
        re_path(r"^dl/(?P<path>.*)$", serve_artifact),
        re_path(r"^index/(?P<path>.*)$", serve_index_mirror),
        re_path(r"^docs/(?P<path>.*)$", serve_docs),
    ]
