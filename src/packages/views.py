from collections.abc import Callable
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView, TemplateView
from django.views.static import serve
from django_htmx.http import HttpResponseClientRedirect
from drf_spectacular.utils import (
    extend_schema,
)
from rest_framework import status, viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from git_db.mirror import mirror_root
from packages.deletion import gone_marker
from packages.models import Asset, Project, Release
from packages.serializers import (
    ProjectDetailSerializer,
    ProjectSerializer,
)
from packages.services import Conflict, delete_draft, unyank, yank


class HomeView(TemplateView):
    template_name = "home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["project_count"] = Project.objects.public().count()
        context["release_count"] = Release.objects.final().count()
        return context


class ProjectsList(ListView):
    model = Project
    paginate_by = 50
    context_object_name = "projects"

    def get_queryset(self):
        qs = Project.objects.public().prefetch_related(
            Prefetch("releases", queryset=Release.objects.final())
        )
        if q := self.request.GET.get("q", "").strip():
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(normalized_name__icontains=q)
                | Q(
                    releases__summary__icontains=q,
                    releases__published_at__isnull=False,
                )
            ).distinct()
        return qs

    def get_template_names(self):
        if self.request.htmx:
            return ["packages/project_list.html#list"]
        return ["packages/project_list.html"]


class ProjectDetail(DetailView):
    model = Project
    context_object_name = "project"
    template_name = "packages/project_detail.html"

    def get_object(self, queryset=None):
        """Projects without a final release only exist for their owners and
        maintainers, who also see the drafts."""
        project = super().get_object(queryset)
        self.can_publish = project.can_publish(self.request.user)
        releases = project.releases.all()
        if not self.can_publish:
            releases = releases.final()
        self.releases = list(
            releases.prefetch_related("assets", "dependencies_from__dependency")
        )
        if not any(release.is_final for release in self.releases) and not (
            self.can_publish
        ):
            raise Http404
        return project

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        version = self.request.GET.get("version")
        final = [release for release in self.releases if release.is_final]
        context["releases"] = self.releases
        context["can_publish"] = self.can_publish
        context["release"] = next(
            (r for r in self.releases if r.version == version),
            # the newest final release, or the newest draft if there is none
            next(iter(final or self.releases), None),
        )
        return context


class AssetDetail(DetailView):
    model = Asset
    context_object_name = "asset"
    template_name = "packages/asset_detail.html"
    pk_url_kwarg = "uuid"

    def get_queryset(self):
        return Asset.objects.select_related("release__project", "uploader")

    def get_object(self, queryset=None):
        asset = super().get_object(queryset)
        release = asset.release
        if not release.is_final and not release.project.can_publish(self.request.user):
            raise Http404
        return asset


def serve_static(
    request, path: str, document_root: Path, **headers: str
) -> HttpResponseBase:
    """A file for runserver (`DEBUG`) with the CORS header nginx adds, also on
    `404` (so a browser client sees "not found", not a CORS error)."""
    try:
        response: HttpResponseBase = serve(
            request, path, document_root=str(document_root)
        )
        for header, value in headers.items():
            response[header] = value
    except Http404:
        response = HttpResponse(status=404)
    response["Access-Control-Allow-Origin"] = "*"
    return response


def serve_artifact(request, path: str) -> HttpResponseBase:
    """`/dl/` for runserver, like nginx: `410` for deleted artifacts."""
    if gone_marker(f"dl/{path}").is_file():
        response = HttpResponse(status=410)
        response["Access-Control-Allow-Origin"] = "*"
        return response
    return serve_static(request, path, settings.MAGNET_ARTIFACTS_ROOT / "dl")


def serve_index_mirror(request, path: str) -> HttpResponseBase:
    """`/index/`, the static HTTP mirror of the index, for runserver, like nginx."""
    return serve_static(
        request,
        path,
        mirror_root(),
        **{"Content-Type": "application/json", "Cache-Control": "no-cache"},
    )


def release_action(
    request, slug: str, version: str, action: Callable[[Release], object], url: str
) -> HttpResponse:
    """Runs a change of the owners/maintainers on a release, then redirects to
    `url` (htmx follows it with a full page load)."""
    release = get_object_or_404(Release, project__slug=slug, version=version)
    try:
        action(release)
    except PermissionDenied:
        return HttpResponse("You are not an owner or maintainer.", status=403)
    except ValidationError as e:
        return HttpResponse("; ".join(e.messages), status=400)
    except Conflict as e:
        return HttpResponse(str(e), status=409)
    if request.htmx:
        return HttpResponseClientRedirect(url)
    return redirect(url)


@require_POST
@login_required
def delete_draft_view(request, slug: str, version: str) -> HttpResponse:
    return release_action(
        request,
        slug,
        version,
        lambda release: delete_draft(request.user, release),
        reverse("project-detail", args=[slug]),
    )


@require_POST
@login_required
def yank_view(request, slug: str, version: str) -> HttpResponse:
    # the answer of `hx-prompt`, or a form field without htmx
    reason = (request.htmx.prompt if request.htmx else None) or request.POST.get(
        "reason", ""
    )
    return release_action(
        request,
        slug,
        version,
        lambda release: yank(request.user, release, reason.strip()),
        f"{reverse('project-detail', args=[slug])}?version={version}",
    )


@require_POST
@login_required
def unyank_view(request, slug: str, version: str) -> HttpResponse:
    return release_action(
        request,
        slug,
        version,
        lambda release: unyank(request.user, release),
        f"{reverse('project-detail', args=[slug])}?version={version}",
    )


class ProjectViewSet(viewsets.ViewSet):
    """Projects with their final releases (drafts are not public)."""

    queryset = Project.objects.public().prefetch_related(
        Prefetch(
            "releases", queryset=Release.objects.final().prefetch_related("assets")
        )
    )
    lookup_field = "slug"

    @extend_schema(
        responses={
            status.HTTP_200_OK: ProjectSerializer,
        }
    )
    def list(self, request):
        """List all available projects."""
        serializer = ProjectSerializer(self.queryset.all(), many=True)
        return Response(serializer.data)

    @extend_schema(
        responses={
            status.HTTP_200_OK: ProjectDetailSerializer,
        },
    )
    def retrieve(self, request, slug=None):
        """Retrieve a specific project and also returns its releases."""
        project = get_object_or_404(self.queryset.all(), slug=slug)
        serializer = ProjectDetailSerializer(project)
        return Response(serializer.data)
