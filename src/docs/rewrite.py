"""Post-processing of rendered docs (build step 4, docs_plan.md): publishes the package's
own pages and help files, with their links rewritten by the owner of the target
(docmap.json of render.scd), SuperCollider's static files shared per version in
`docs/_static/<sclang version>/`, and the magnet bar. Best effort: plain string
rewriting of SCDoc's output, which may change with SuperCollider versions."""

import os
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from django.template.loader import render_to_string

from docs import publish

SC_DOCS = "https://docs.supercollider.online/"
SC_SOURCE = "https://github.com/supercollider/supercollider/blob/Version-{version}/"
# where class files are in the build containers (docs_builder/, docs/tasks.py)
CLASS_LIBRARY = "file:///usr/local/share/SuperCollider/"
PACKAGES = "file:///packages/"
# below the docs root, shared by all packages
STATIC = "_static"
MAGNET_CSS = "magnet.css"
# SuperCollider's own help files the pages use (style sheets, scripts, icons)
SHARED = ("static/", "images/")
# kept next to the pages: the data of scdoc.js' inherited methods
DOCMAP_JS = "docmap.js"
# folders of the index page in this order, others after them
FOLDERS = ("Overviews", "Guides", "Tutorials", "Reference", "Classes")

ATTRIBUTE_RE = re.compile(r"""\s(href|src)=(['"])(.*?)\2""")
HELP_ROOT_RE = re.compile(r"var helpRoot = '[^']*';")
BODY_RE = re.compile(r"<body[^>]*>")
HEAD_END = "</head>"


@dataclass(frozen=True)
class Context:
    """What the pages say about the package beyond docmap.json."""

    name: str
    version: str
    summary: str
    sclang: str
    sclang_version: str
    sclang_mismatch: bool
    # the package's page on the website and its repository
    project_url: str
    repository_url: str
    # MAGNET_DOCS_BASE_URL, with a trailing slash
    docs_base_url: str
    # the dependencies (normalised names) that are packages of magnet, which host
    # their docs; legacy quarks and git dependencies have none
    hosted_dependencies: frozenset[str]


def postprocess(
    rendered: Path, target: Path, docmap: dict[str, Any], context: Context
) -> list[str]:
    """Writes the docs to publish into `target` (which doesn't exist yet) from the
    output of render.scd in `rendered`; shares the static files next to it. Returns
    the published documents (paths without `.html`)."""
    documents: dict[str, dict[str, Any]] = docmap["documents"]
    # documents that failed to render are missing
    pages = {
        path
        for path, document in documents.items()
        if "title" in document and (rendered / f"{path}.html").is_file()
    }
    files = set(docmap["files"])
    share_static(rendered, publish.docs_root() / STATIC / context.sclang_version)

    target.mkdir(parents=True)
    for path in sorted(files | {DOCMAP_JS}):
        if (rendered / path).is_file():
            (target / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rendered / path, target / path)
    rewriter = Rewriter(documents, pages, files, context)
    for path in sorted(pages):
        html = (rendered / f"{path}.html").read_text(errors="replace")
        (target / f"{path}.html").parent.mkdir(parents=True, exist_ok=True)
        (target / f"{path}.html").write_text(rewriter.page(html, f"{path}.html"))
    (target / "index.html").write_text(index_page(documents, pages, context))
    return sorted(pages)


def share_static(rendered: Path, folder: Path) -> None:
    """SuperCollider's static files once per version, and magnet's style sheet."""
    root = folder.parent
    root.mkdir(parents=True, exist_ok=True)
    css = root / f".{MAGNET_CSS}.{os.getpid()}"
    shutil.copyfile(Path(__file__).parent / "assets" / MAGNET_CSS, css)
    os.replace(css, root / MAGNET_CSS)
    if folder.exists():
        return
    temporary = root / f".{folder.name}.{os.getpid()}"
    shutil.rmtree(temporary, ignore_errors=True)
    for shared in SHARED:
        if (rendered / shared).is_dir():
            shutil.copytree(rendered / shared, temporary / shared)
    try:
        temporary.rename(folder)
    except OSError:
        # another build shared them meanwhile
        shutil.rmtree(temporary, ignore_errors=True)


class Rewriter:
    def __init__(
        self,
        documents: dict[str, dict[str, Any]],
        pages: set[str],
        files: set[str],
        context: Context,
    ) -> None:
        self.documents = documents
        self.pages = pages
        self.files = files
        self.context = context

    def page(self, html: str, path: str) -> str:
        """A rendered page (`path` relative to the docs) with rewritten links, SC's
        menu bar pointing to its online docs and the magnet bar."""
        depth = path.count("/")
        up = "../" * depth

        def attribute(match: re.Match[str]) -> str:
            url = self.url(match[3], path, depth)
            if url is None:
                # links become plain text, references to nothing
                return ""
            return f" {match[1]}={match[2]}{url}{match[2]}"

        html = ATTRIBUTE_RE.sub(attribute, html)
        html = HELP_ROOT_RE.sub(f"var helpRoot = '{SC_DOCS.rstrip('/')}';", html, 1)
        css = f"../{up}{STATIC}/{MAGNET_CSS}"
        html = html.replace(
            HEAD_END,
            f"<link rel='stylesheet' href='{css}' type='text/css' />\n" + HEAD_END,
            1,
        )
        bar = bar_html(self.context, index_url=f"{up}index.html")
        return BODY_RE.sub(lambda body: f"{body[0]}\n{bar}", html, 1)

    def url(self, url: str, page: str, depth: int) -> str | None:
        """The rewritten `url` of an attribute of `page`, None to drop it."""
        if url.startswith(PACKAGES):
            return self.source_url(url.removeprefix(PACKAGES))
        if url.startswith(CLASS_LIBRARY):
            source = SC_SOURCE.format(version=self.context.sclang_version)
            return source + url.removeprefix(CLASS_LIBRARY)
        scheme = urlsplit(url).scheme
        if scheme in ("file", "qrc"):
            return None
        if scheme or not url or url.startswith(("#", "/")):
            return url

        path, hash_mark, fragment = url.partition("#")
        anchor = hash_mark + fragment
        target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
        if target.startswith("../"):
            return url
        if target.endswith(".html"):
            return self.document_url(url, target, anchor)
        if target in self.files or target == DOCMAP_JS:
            return url
        if target.startswith(SHARED):
            version = self.context.sclang_version
            return f"{'../' * (depth + 1)}{STATIC}/{version}/{target}{anchor}"
        # e.g. frontend.js and custom.css, which no package provided
        return None

    def document_url(self, url: str, target: str, anchor: str) -> str | None:
        document = self.documents.get(target.removesuffix(".html"))
        if target.removesuffix(".html") in self.pages or target in self.files:
            return url
        if document is None or document["owner"] == "sc":
            # SC's documents, also Browse, Search and the Overviews
            return f"{SC_DOCS}{target}{anchor}"
        dependency = document.get("dependency")
        if (
            document["owner"] == "dependency"
            and not document.get("undocumented")
            and dependency in self.context.hosted_dependencies
        ):
            return f"{self.context.docs_base_url}{dependency}/{target}{anchor}"
        # undocumented or failed documents of the package, legacy/git dependencies
        return None

    def source_url(self, path: str) -> str | None:
        """A class file of the package in its repository, if the host is known.
        Files of dependencies aren't linked."""
        name, _, path = path.partition("/")
        if name != self.context.name:
            return None
        return repository_file_url(self.context.repository_url, path)


def repository_file_url(repository_url: str, path: str) -> str | None:
    """The file at `path` on the default branch, for GitHub and GitLab."""
    parts = urlsplit(repository_url)
    base = repository_url.rstrip("/").removesuffix(".git")
    path = quote(path)
    if parts.hostname == "github.com":
        return f"{base}/blob/HEAD/{path}"
    if parts.hostname == "gitlab.com":
        return f"{base}/-/blob/HEAD/{path}"
    return None


def bar_html(context: Context, index_url: str) -> str:
    return render_to_string(
        "docs/bar.html",
        {
            "name": context.name,
            "version": context.version,
            "project_url": context.project_url,
            "repository_url": context.repository_url,
            "sclang": context.sclang,
            "sclang_version": context.sclang_version,
            "sclang_mismatch": context.sclang_mismatch,
            "index_url": index_url,
        },
    )


def index_page(
    documents: dict[str, dict[str, Any]], pages: set[str], context: Context
) -> str:
    """The package's documents by folder."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in pages:
        folder = path.split("/")[0] if "/" in path else ""
        groups.setdefault(folder, []).append({**documents[path], "path": path})
    order = {folder: i for i, folder in enumerate(FOLDERS)}
    return render_to_string(
        "docs/index.html",
        {
            "name": context.name,
            "version": context.version,
            "summary": context.summary,
            "groups": [
                (folder, sorted(group, key=lambda d: d["title"].lower()))
                for folder, group in sorted(
                    groups.items(), key=lambda g: (order.get(g[0], len(order)), g[0])
                )
            ],
            "static_url": f"../{STATIC}/{context.sclang_version}/",
            "magnet_css_url": f"../{STATIC}/{MAGNET_CSS}",
            "project_url": context.project_url,
            "repository_url": context.repository_url,
            "sclang": context.sclang,
            "sclang_version": context.sclang_version,
            "sclang_mismatch": context.sclang_mismatch,
        },
    )
