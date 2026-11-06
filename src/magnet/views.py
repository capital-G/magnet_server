from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, permissions, serializers
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from packages.archive import SCHEMA_PATH


class MagnetServerVersion(Serializer):
    version = serializers.CharField()


magnet_server_version = MagnetServerVersion(data={"version": settings.MAGNET_VERSION})
assert magnet_server_version.is_valid()


class VersionApiView(APIView):
    permission_classes = (permissions.AllowAny,)

    @extend_schema(
        description="Returns the version of the server",
        responses=MagnetServerVersion,
    )
    def get(self, request, *args, **kwargs):
        return Response(magnet_server_version.data)


class ApiNotFoundView(APIView):
    """Unknown paths below `/api/` get a JSON `404` instead of Django's HTML page."""

    authentication_classes = []
    permission_classes = (permissions.AllowAny,)
    schema = None

    def initial(self, request, *args, **kwargs):
        # before the method lookup, so every method gets the `404`
        raise exceptions.NotFound()


@require_safe
def manifest_schema_view(request: HttpRequest) -> HttpResponse:
    """The JSON schema of `magnet.toml` (vendored from the CLI, its `$id` is this URL).
    `magnet init` references it via `#:schema`, so the TOML extensions of editors offer
    completion and validation; any origin may fetch it, e.g. editors in the browser."""
    response = HttpResponse(SCHEMA_PATH.read_bytes(), content_type="application/json")
    response["Access-Control-Allow-Origin"] = "*"
    response["Cache-Control"] = "public, max-age=3600"
    return response


def legal_view(request: HttpRequest) -> HttpResponse:
    """The legal notice and privacy policy."""
    return render(
        request,
        "legal.html",
        {
            "name": settings.MAGNET_LEGAL_NAME,
            "email": settings.MAGNET_LEGAL_EMAIL,
            "hosting": settings.MAGNET_LEGAL_HOSTING,
            "site_host": urlsplit(settings.MAGNET_API_BASE_URL).hostname,
            "docs_host": urlsplit(settings.MAGNET_DOCS_BASE_URL).hostname,
        },
    )
