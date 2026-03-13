from django.views.generic import DetailView, ListView, TemplateView
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

from packages.models import Asset, Project, Release
from packages.serializers import (
    AssetUploadSerializer,
    ProjectDetailSerializer,
    ProjectSerializer,
)


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super(HomeView, self).get_context_data(**kwargs)
        context["project_count"] = Project.objects.all().count()
        context["release_count"] = Release.objects.all().count()
        return context


class ProjectsList(ListView):
    model = Project
    paginate_by = 50
    context_object_name = "projects"

    def get_queryset(self):
        qs = Project.objects.prefetch_related("releases")
        if q := self.request.GET.get("q", "").strip():
            qs = qs.filter(name__icontains=q)
        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ["packages/project_list.html#list"]
        return ["packages/project_list.html"]


class ProjectDetail(DetailView):
    model = Project
    context_object_name = "project"
    template_name = "packages/project_detail.html"

    def get_queryset(self):
        return Project.objects.prefetch_related(
            "releases",
            "releases__assets",
            "releases__dependencies_from__dependency",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["release"] = None
        if version := self.request.GET.get("version"):
            context["release"] = self.object.releases.filter(
                version=version,
            ).first()
        if not context["release"]:
            context["release"] = (
                self.object.releases.first() if self.object.releases.exists() else None
            )
        return context


class AssetDetail(DetailView):
    model = Asset
    context_object_name = "asset"
    template_name = "packages/asset_detail.html"
    pk_url_kwarg = "uuid"

    def get_queryset(self):
        return Asset.objects.select_related("release__project", "uploader")


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
