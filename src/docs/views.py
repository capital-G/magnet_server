from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.views.static import serve

from docs import publish

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
