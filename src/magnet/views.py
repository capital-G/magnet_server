from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, permissions, serializers
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.views import APIView


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
