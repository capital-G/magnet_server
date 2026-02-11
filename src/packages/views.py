from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import parsers, permissions, status, viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from packages.models import Project
from packages.serializers import (
    AssetUploadSerializer,
    ProjectDetailSerializer,
    ProjectSerializer,
)


class ProjectViewSet(viewsets.ViewSet):
    queryset = Project.objects.all()
    lookup_field = "slug"

    @extend_schema(
        responses={
            status.HTTP_200_OK: ProjectSerializer,
        }
    )
    def list(self, request):
        """List all available projects."""
        serializer = ProjectSerializer(self.queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        responses={
            status.HTTP_200_OK: ProjectDetailSerializer,
        },
    )
    def retrieve(self, request, slug=None):
        """Retrieve a specific project and also returns its releases."""
        project = get_object_or_404(Project, slug=slug)
        serializer = ProjectDetailSerializer(project)
        return Response(serializer.data)


class UploadReleaseView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [
        # parsers.FormParser,
        parsers.MultiPartParser,
    ]

    @extend_schema(
        request=AssetUploadSerializer,
        responses={
            200: OpenApiResponse(
                description="Successfully uploaded a release",
            )
        },
        summary="Upload an asset",
        description="Upload a new asset for a given version.",
    )
    def put(self, request, *args, **kwargs):
        serializer = AssetUploadSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            # @todo respond w/ asset uuid?
            return Response({"message": "Asset received"}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
