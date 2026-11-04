"""Post-processing of rendered docs (`docs.rewrite`) on SCDoc's output of the package
in fixtures/docdemo: rendered by docs_builder/render.scd (SC 3.14.1) from a package
`docdemo` with a guide, an undocumented class and an image, which links to the
magnet package `docdep`, the legacy quark `legacything` and SC's documents; its
docmap.json keeps only the entries that aren't SC's (and SinOsc)."""

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from docs import publish
from docs.rewrite import Context, Rewriter, postprocess, repository_file_url

FIXTURE = Path(__file__).parent / "fixtures" / "docdemo"
CONTEXT = Context(
    name="docdemo",
    version="1.2.0",
    summary="A demo package",
    sclang=">=3.13",
    sclang_version="3.14.1",
    sclang_mismatch=False,
    project_url="http://magnet.test/project/docdemo",
    repository_url="https://github.com/me/docdemo.git",
    docs_base_url="http://docs.test/",
    hosted_dependencies=frozenset({"docdep"}),
)


@pytest.fixture
def rendered(tmp_path) -> Path:
    """The fixture plus what SCDoc copies from SC's HelpSource."""
    rendered = tmp_path / "rendered"
    shutil.copytree(FIXTURE, rendered)
    for path, content in [
        ("static/scdoc.css", "/* sc */"),
        ("images/plugin.png", "png"),
        ("Browse.html", "<html></html>"),
        ("Guides/scEn.png", "png"),
        ("docmap.js", "docmap = {}"),
    ]:
        (rendered / path).parent.mkdir(parents=True, exist_ok=True)
        (rendered / path).write_text(content)
    return rendered


def docmap(rendered: Path) -> dict:
    return json.loads((rendered / "docmap.json").read_text())


def publish_docs(rendered: Path, context: Context = CONTEXT, build_id: int = 1) -> Path:
    target = publish.build_dir("docdemo", build_id)
    postprocess(rendered, target, docmap(rendered), context)
    return target


def links(html: str) -> set[str]:
    return set(re.findall(r"""\s(?:href|src)=['"]([^'"]*)['"]""", html))


@pytest.mark.django_db
def test_links_by_owner(rendered):
    target = publish_docs(rendered)

    found = links((target / "Classes" / "DemoThing.html").read_text())

    expected = {
        # the package's own pages and files stay relative
        "./../Guides/DemoGuide.html",
        "./../Guides/DemoGuide.html#Usage",
        "demo.png",
        "./../docmap.js",
        # a dependency on magnet: its docs
        "http://docs.test/docdep/Classes/DepThing.html",
        # SC's documents, also Browse and the Overviews
        "https://docs.supercollider.online/Classes/SinOsc.html#*ar",
        "https://docs.supercollider.online/Classes/Object.html",
        "https://docs.supercollider.online/Browse.html#UGens",
        "https://docs.supercollider.online/Overviews/Methods.html#shout",
        # SC's static files, shared per version
        "../../_static/3.14.1/static/scdoc.css",
        "../../_static/3.14.1/images/plugin.png",
        "../../_static/magnet.css",
        # source links into the repository
        "https://github.com/me/docdemo/blob/HEAD/Classes/DemoThing.sc",
        "https://github.com/me/docdemo/blob/HEAD/HelpSource/Classes/DemoThing.schelp",
        # the magnet bar
        "http://magnet.test/project/docdemo",
        "../index.html",
        "https://github.com/me/docdemo.git",
        # untouched
        "https://supercollider.github.io",
        "#-shout",
    }
    assert expected <= found
    unexpected = [
        link
        for link in found
        if any(
            part in link
            for part in (
                "LegacyThing",  # a legacy quark: no docs on magnet
                "DemoHelper",  # undocumented, no page
                "custom.css",  # nothing the package provides
                "frontend",
                "qrc:",
                "file:",
                "./../static/",
            )
        )
    ]
    assert unexpected == []


@pytest.mark.django_db
def test_links_without_a_target_become_text(rendered):
    target = publish_docs(rendered)

    html = (target / "Classes" / "DemoThing.html").read_text()

    assert "<a>LegacyThing</a>" in html
    assert "<a>DemoHelper</a>" in html


@pytest.mark.django_db
def test_sc_menu_bar_points_to_the_sc_docs(rendered):
    target = publish_docs(rendered)

    html = (target / "Guides" / "DemoGuide.html").read_text()

    assert "var helpRoot = 'https://docs.supercollider.online';" in html
    assert '<div id="magnet-bar">' in html
    assert "../_static/magnet.css" in html


@pytest.mark.django_db
def test_only_the_package_is_published(rendered):
    target = publish_docs(rendered)

    published = sorted(
        str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()
    )

    assert published == [
        "Classes/DemoThing.html",
        "Classes/demo.png",
        "Guides/DemoGuide.html",
        "docmap.js",
        "index.html",
    ]


@pytest.mark.django_db
def test_static_files_are_shared_once_per_version(rendered):
    publish_docs(rendered)
    (rendered / "static" / "scdoc.css").write_text("/* changed */")

    publish_docs(rendered, build_id=2)

    static = publish.docs_root() / "_static"
    assert (static / "3.14.1" / "static" / "scdoc.css").read_text() == "/* sc */"
    assert (static / "3.14.1" / "images" / "plugin.png").is_file()
    assert "#magnet-bar" in (static / "magnet.css").read_text()
    assert sorted(p.name for p in static.iterdir()) == ["3.14.1", "magnet.css"]


@pytest.mark.django_db
def test_failed_documents_arent_linked(rendered):
    (rendered / "Guides" / "DemoGuide.html").unlink()

    target = publish_docs(rendered)

    html = (target / "Classes" / "DemoThing.html").read_text()
    assert "DemoGuide.html" not in html
    assert "DemoGuide" not in (target / "index.html").read_text()


@pytest.mark.django_db
def test_dependencies_outside_magnet_arent_linked(rendered):
    target = publish_docs(rendered, replace(CONTEXT, hosted_dependencies=frozenset()))

    found = links((target / "Classes" / "DemoThing.html").read_text())

    assert not any("DepThing" in link for link in found)


@pytest.mark.django_db
def test_the_bar(rendered):
    context = replace(CONTEXT, repository_url="", sclang=">=3.15", sclang_mismatch=True)

    html = (publish_docs(rendered, context) / "Classes" / "DemoThing.html").read_text()

    bar = html[html.index('<div id="magnet-bar">') :]
    bar = bar[: bar.index("</div>")]
    assert ">docdemo 1.2.0</a>" in bar
    assert "Repository" not in bar
    assert "Built with SuperCollider 3.14.1, which this version doesn" in bar
    assert "(&gt;=3.15)" in bar


@pytest.mark.django_db
def test_index_lists_the_documents_by_folder(rendered):
    html = (publish_docs(rendered) / "index.html").read_text()

    assert "<title>docdemo 1.2.0 | magnet docs</title>" in html
    assert html.index("<h2>Guides</h2>") < html.index("<h2>Classes</h2>")
    assert '<a href="Classes/DemoThing.html">DemoThing</a>: A demo of magnet' in html
    assert '<a href="Guides/DemoGuide.html">DemoGuide</a>' in html
    assert 'href="../_static/3.14.1/static/scdoc.css"' in html
    assert 'id="magnet-bar"' in html


@pytest.mark.django_db
def test_index_of_documents_without_folder(rendered):
    data = docmap(rendered)
    data["documents"] = {"Intro": {"owner": "package", "title": "Intro", "summary": ""}}
    (rendered / "Intro.html").write_text("<html><body></body></html>")
    target = publish.build_dir("docdemo", 1)

    postprocess(rendered, target, data, CONTEXT)

    html = (target / "index.html").read_text()
    assert '<a href="Intro.html">Intro</a>' in html
    assert "<h2></h2>" not in html


@pytest.mark.django_db
def test_index_without_documents(rendered):
    data = docmap(rendered)
    data["documents"] = {}
    target = publish.build_dir("docdemo", 1)

    postprocess(rendered, target, data, CONTEXT)

    assert "This version has no help files" in (target / "index.html").read_text()


def rewriter() -> Rewriter:
    return Rewriter({}, set(), set(), CONTEXT)


@pytest.mark.parametrize(
    "url, expected",
    [
        # class files of SC at the builder's version
        (
            "file:///usr/local/share/SuperCollider/SCClassLibrary/Common/Core/Object.sc",
            "https://github.com/supercollider/supercollider/blob/Version-3.14.1/"
            "SCClassLibrary/Common/Core/Object.sc",
        ),
        # class files of dependencies aren't linked
        ("file:///packages/docdep/Classes/DepThing.sc", None),
        ("file:///somewhere/else.sc", None),
        ("mailto:me@example.org", "mailto:me@example.org"),
        ("/absolute.html", "/absolute.html"),
    ],
)
def test_other_urls(url, expected):
    assert rewriter().url(url, "Classes/DemoThing.html", 1) == expected


@pytest.mark.parametrize(
    "repository, expected",
    [
        (
            "https://github.com/me/foo",
            "https://github.com/me/foo/blob/HEAD/Classes/A%20B.sc",
        ),
        (
            "https://github.com/me/foo.git",
            "https://github.com/me/foo/blob/HEAD/Classes/A%20B.sc",
        ),
        (
            "https://gitlab.com/me/foo/",
            "https://gitlab.com/me/foo/-/blob/HEAD/Classes/A%20B.sc",
        ),
        ("https://codeberg.org/me/foo", None),
        ("", None),
    ],
)
def test_repository_file_url(repository, expected):
    assert repository_file_url(repository, "Classes/A B.sc") == expected
