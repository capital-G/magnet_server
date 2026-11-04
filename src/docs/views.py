from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.static import serve

from docs import publish
from docs.models import DocBuild
from docs.status import docs_status
from docs.tasks import owner_rebuild
from packages.models import Project
from packages.views import team_project

# like nginx's docs server (nginx/default.conf): SCDoc's pages need inline scripts
# (helpRoot, onload), nothing else may be loaded from elsewhere or sent anywhere
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
    "frame-ancestors 'none'; form-action 'none'; base-uri 'none'"
)


def serve_docs(request: HttpRequest, path: str) -> HttpResponseBase:
    """`/docs/` for runserver (`DEBUG`), like nginx's docs server; set
    MAGNET_DOCS_BASE_URL to `http://localhost:8000/docs/` for it."""
    root = publish.docs_root()
    # the build folders and temporary links
    if any(part.startswith(".") for part in path.split("/")):
        raise Http404
    if path and not path.endswith("/") and (root / path).is_dir():
        return HttpResponseRedirect(f"{request.path}/")
    if not path or path.endswith("/"):
        path += "index.html"
    response = serve(request, path, document_root=str(root))
    response["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response["X-Content-Type-Options"] = "nosniff"
    return response


# builds the docs page lists
BUILDS_SHOWN = 10


@login_required
def builds_view(request: HttpRequest, slug: str) -> HttpResponse:
    """The recent docs builds of a package with the log of one (`?build=<id>`, the
    newest by default), for owners and maintainers; owners rebuild here."""
    project = team_project(request, slug)
    builds = list(
        DocBuild.objects.filter(release__project=project)
        .select_related("release")
        .order_by("-created_at", "-pk")[:BUILDS_SHOWN]
    )
    selected = builds[0] if builds else None
    if "build" in request.GET:
        selected = get_object_or_404(
            DocBuild.objects.select_related("release"),
            pk=request.GET["build"],
            release__project=project,
        )
    return render(
        request,
        "docs/builds.html",
        {
            "project": project,
            "builds": builds,
            "selected": selected,
            "status": docs_status(project),
            "can_manage": project.can_manage(request.user),
            "builds_on": settings.MAGNET_DOCS_BUILDS,
        },
    )


@require_POST
@login_required
def rebuild_view(request: HttpRequest, slug: str) -> HttpResponse:
    project = get_object_or_404(Project, slug=slug)
    try:
        build = owner_rebuild(request.user, project)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(request, f"Queued the docs of {build.release.version}.")
    return redirect("docs-builds", slug=project.slug)
