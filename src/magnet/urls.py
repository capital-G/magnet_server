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
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from oauth2_provider import urls as oauth2_urls
from rest_framework_nested import routers

from magnet.views import ApiNotFoundView, VersionApiView
from packages.views import ProjectViewSet, UploadReleaseView

router = routers.DefaultRouter()
router.register(r"projects", ProjectViewSet, basename="project")
# router.register("upload", UploadReleaseView, basename="upload")

urlpatterns = [
    path("o/", include(oauth2_urls)),
    path("api/v1/", include(router.urls)),
    path("api/version/", VersionApiView.as_view()),
    path("api/v1/upload/release/", UploadReleaseView.as_view(), name="upload-release"),
    # path('api/', include(release_router.urls)),
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
    path("accounts/", include("allauth.urls")),
    path("users/", include("users.urls")),
    path("", include("packages.urls")),
]

if settings.DEBUG:
    # artifact downloads for runserver; deployments serve them via nginx
    urlpatterns += static("dl/", document_root=settings.MAGNET_ARTIFACTS_ROOT / "dl")
