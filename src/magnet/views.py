from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, serializers
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
