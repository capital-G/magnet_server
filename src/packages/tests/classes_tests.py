"""Classes and methods of versions, read from their quark artifacts."""

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.tasks import TaskResultStatus
from django.urls import reverse
from django_tasks_db.models import DBTaskResult
from pytest_django.asserts import assertContains, assertNotContains

from git_db.db import GitDatabase, get_git_database
from packages.classes import (
    SearchResults,
    conflicts,
    conflicts_by_package,
    extract_classes,
    extract_classes_task,
    search,
)
from packages.models import Asset, ClassDefinition, ClassFileError, Project
from packages.services import create_draft, finalise
from packages.tests.factories import AssetFactory, ReleaseFactory, tar_gz
from packages.tests.services_tests import upload_quark
from users.tests.factories import UserFactory

DATABASE_BACKEND = "django_tasks_db.DatabaseBackend"


@pytest.fixture
def index() -> GitDatabase:
    call_command("init_index")
    return get_git_database()


FOO = b"""
Foo : Object {
    var <>volume;
    *new { ^super.new }
    play { }
}
"""


def release_with(*files, final=True, **kwargs):
    """A version whose quark artifact holds `files` (`(path, content)`)."""
    release = ReleaseFactory.create(final=final, **kwargs)
    AssetFactory.create(
        release=release,
        kind=Asset.Kind.quark,
        file=ContentFile(tar_gz(*files), name="upload.tar.gz"),
    )
    return release


def classes(release):
    return [
        (
            c.name,
            c.superclass,
            c.is_extension,
            c.path,
            c.line,
            c.platform,
            [(m.name, m.is_class_method) for m in c.methods.all()],
        )
        for c in release.class_definitions.order_by("path", "line")
    ]


@pytest.mark.django_db
def test_extract_classes():
    release = release_with(
        ("Classes/Foo.sc", FOO),
        ("Classes/extString.sc", b"+ String { shout { } }\n"),
        ("Classes/osx/MacOnly.sc", b"MacOnly { }\n"),
        # not compiled by sclang
        ("Classes/help/Example.sc", b"Example { }\n"),
        ("HelpSource/Classes/Foo.schelp", b"title:: Foo\n"),
        ("Classes/Broken.sc", b"Broken { open {\n"),
        ("magnet.toml", b"magnet-version = 1\n"),
    )

    extract_classes(release)

    assert classes(release) == [
        (
            "Foo",
            "Object",
            False,
            "Classes/Foo.sc",
            2,
            "",
            [("volume", False), ("volume_", False), ("new", True), ("play", False)],
        ),
        ("String", "", True, "Classes/extString.sc", 1, "", [("shout", False)]),
        ("MacOnly", "", False, "Classes/osx/MacOnly.sc", 1, "osx", []),
    ]
    [error] = release.class_file_errors.all()
    assert (error.path, error.message) == (
        "Classes/Broken.sc",
        "unmatched '{' at the end of the file",
    )
    assert release.classes_extracted_at is not None


@pytest.mark.django_db
def test_extracting_again_replaces_the_classes():
    release = release_with(("Classes/Foo.sc", FOO), ("Classes/Bad.sc", b"}"))

    extract_classes(release)
    extract_classes(release)

    assert ClassDefinition.objects.count() == 1
    assert ClassFileError.objects.count() == 1


@pytest.mark.django_db
def test_versions_without_a_quark_artifact_have_no_classes():
    release = ReleaseFactory.create(final=True, kinds=["lepton"])

    extract_classes(release)

    assert classes(release) == []
    assert release.classes_extracted_at is not None


@pytest.mark.django_db
def test_non_utf8_files():
    release = release_with(
        ("Classes/Latin1.sc", 'Foo { a { ^"\xe9" } }'.encode("latin-1"))
    )

    extract_classes(release)

    assert [c[0] for c in classes(release)] == ["Foo"]


@pytest.mark.django_db
def test_current_classes_are_those_of_the_newest_final_version():
    def version(project, number, name, **kwargs):
        if project is not None:
            kwargs["project"] = project
        return release_with(
            (f"Classes/{name}.sc", f"{name} {{ }}".encode()), version=number, **kwargs
        )

    old = version(None, "1.9.0", "Old")
    project = old.project
    for release in [
        old,
        # 1.10.0 is newer than 1.9.0
        version(project, "1.10.0", "Current"),
        version(project, "1.11.0", "Yanked", yanked=True),
        version(project, "2.0.0", "Draft", final=False),
        version(None, "0.1.0", "Other"),
    ]:
        extract_classes(release)

    current = ClassDefinition.objects.current()
    assert sorted(c.name for c in current) == ["Current", "Other"]


@pytest.mark.django_db
def test_finalising_extracts_the_classes(index, django_capture_on_commit_callbacks):
    user = UserFactory.create()
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, release)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        finalise(user, release)

    assert len(callbacks) >= 1
    assert [c[0] for c in classes(release)] == ["Foo"]


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_the_worker_extracts_the_classes(index, settings):
    """With the database backend of docker/prod, `db_worker` runs the task (the
    worker closes connections, so not inside a test transaction; serialized
    rollback keeps the reserved names of the data migration for later tests)."""
    settings.TASKS = {
        "default": {**settings.TASKS["default"], "BACKEND": DATABASE_BACKEND}
    }
    user = UserFactory.create()
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, release)

    finalise(user, release)

    task = DBTaskResult.objects.get()
    assert (task.queue_name, task.status) == ("default", TaskResultStatus.READY)
    assert classes(release) == []

    call_command("db_worker", batch=True, reload=False, startup_delay=False)

    task.refresh_from_db()
    assert task.status == TaskResultStatus.SUCCESSFUL
    assert [c[0] for c in classes(release)] == ["Foo"]


@pytest.mark.django_db
def test_a_failing_extraction_doesnt_fail_finalising(
    index, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    def fail(release):
        raise RuntimeError("parser bug")

    monkeypatch.setattr("packages.classes.extract_classes", fail)
    user = UserFactory.create()
    release, _ = create_draft(user, "foo-bar", "1.0.0")
    upload_quark(user, release)

    with django_capture_on_commit_callbacks(execute=True):
        release = finalise(user, release)

    assert release.is_final
    assert "parser bug" in caplog.text


@pytest.mark.django_db
def test_the_task_skips_deleted_versions():
    extract_classes_task.call("00000000-0000-0000-0000-000000000000")


@pytest.mark.django_db
def test_extract_classes_command():
    alpha = release_with(("Classes/Alpha.sc", b"Alpha { }"), project__name="alpha")
    beta = release_with(("Classes/Beta.sc", b"Beta { }"), project__name="Beta_Lib")
    draft = release_with(("Classes/Draft.sc", b"Draft { }"), final=False)

    call_command("extract_classes", "beta-lib")
    assert ClassDefinition.objects.get().release == beta

    call_command("extract_classes")
    assert {c.release for c in ClassDefinition.objects.all()} == {alpha, beta}
    assert draft.class_definitions.count() == 0


@pytest.mark.django_db
def test_admin_shows_the_classes(admin_client):
    release = release_with(("Classes/Foo.sc", FOO), ("Classes/Bad.sc", b"}"))
    extract_classes(release)
    definition = ClassDefinition.objects.get()

    changelist = admin_client.get(
        reverse("admin:packages_classdefinition_changelist"), {"q": "volume_"}
    )
    assert [c.name for c in changelist.context["cl"].result_list] == ["Foo"]
    page = admin_client.get(
        reverse("admin:packages_classdefinition_change", args=[definition.pk])
    )
    assert page.status_code == 200
    assert "volume_" in page.text
    errors = admin_client.get(reverse("admin:packages_classfileerror_changelist"))
    assert "Classes/Bad.sc" in errors.text
    assert (
        admin_client.get(reverse("admin:packages_classdefinition_add")).status_code
        == 403
    )


def published(name, *files, version="1.0.0", **kwargs):
    """A package `name` whose version has the class files `files`, extracted."""
    project = Project.objects.filter(name=name).first()
    if project is not None:
        kwargs["project"] = project
    else:
        kwargs["project__name"] = name
    release = release_with(*files, version=version, **kwargs)
    extract_classes(release)
    return release


def found(release):
    return [(c.kind, c.label, c.other.project.name) for c in conflicts(release)]


@pytest.mark.django_db
def test_class_and_method_conflicts():
    alpha = published(
        "alpha",
        ("Classes/Foo.sc", b"Foo { }"),
        ("Classes/ext.sc", b"+ String { shout { } *make { } quiet { } }"),
    )
    published("beta", ("Classes/Foo.sc", b"Foo : Bar { }"))
    published("gamma", ("Classes/ext.sc", b"+ String { shout { } make { } }"))
    published("delta", ("Classes/ext.sc", b"+ String { *make { } }"))
    # an extension of a class isn't a conflict with its definition
    published("epsilon", ("Classes/ext.sc", b"+ Foo { bar { } }"))

    assert found(alpha) == [
        ("class", "Foo", "beta"),
        ("method", "String:*make", "delta"),
        ("method", "String:-shout", "gamma"),
    ]


@pytest.mark.django_db
def test_conflicts_only_with_current_versions():
    alpha = published("alpha", ("Classes/Foo.sc", b"Foo { }"))
    # beta renamed its class in 2.0.0
    published("beta", ("Classes/Foo.sc", b"Foo { }"), version="1.0.0")
    published("beta", ("Classes/Bar.sc", b"Bar { }"), version="2.0.0")
    # gamma's version with Foo is yanked, delta's only a draft
    published("gamma", ("Classes/Foo.sc", b"Foo { }"), yanked=True)
    published("delta", ("Classes/Foo.sc", b"Foo { }"), final=False)
    # alpha's own older version doesn't count
    published("alpha", ("Classes/Foo.sc", b"Foo { }"), version="0.9.0")

    assert found(alpha) == []


@pytest.mark.django_db
def test_platform_folders_dont_conflict():
    alpha = published("alpha", ("Classes/osx/Foo.sc", b"Foo { }"))
    published("beta", ("Classes/linux/Foo.sc", b"Foo { }"))
    published("gamma", ("Classes/Foo.sc", b"Foo { }"))

    assert found(alpha) == [("class", "Foo", "gamma")]


@pytest.mark.django_db
def test_project_page_shows_conflicts_of_the_current_version(client):
    alpha = published("alpha", ("Classes/Foo.sc", b"Foo { }"), version="1.0.0")
    published("alpha", ("Classes/Foo.sc", b"Foo { }"), version="0.9.0")
    published("beta", ("Classes/Foo.sc", b"Foo { }"))
    url = reverse("project-detail", args=["alpha"])

    page = client.get(url)
    assertContains(page, "Naming conflicts")
    assertContains(page, "<li>class <code>Foo</code></li>", html=True)
    assertContains(page, reverse("project-detail", args=["beta"]))
    # older versions aren't what people install
    assertNotContains(client.get(url, {"version": "0.9.0"}), "Naming conflicts")
    assert alpha.version == "1.0.0"


@pytest.mark.django_db
def test_no_conflicts_box_without_conflicts(client):
    published("alpha", ("Classes/Foo.sc", b"Foo { }"))
    assertNotContains(
        client.get(reverse("project-detail", args=["alpha"])), "Naming conflicts"
    )


@pytest.mark.django_db
def test_classes_tab(client):
    release = published(
        "alpha",
        ("Classes/Foo.sc", FOO),
        ("Classes/osx/Mac.sc", b"Mac : Foo { }"),
        ("Classes/ext.sc", b"+ String { shout { } }"),
        ("Classes/Bad.sc", b"}"),
    )
    url = reverse("project-detail", args=["alpha"])

    page = client.get(url)

    assertContains(page, "Classes</a>")
    assertContains(page, '<strong class="is-family-monospace">Foo</strong>', html=True)
    # files with their classes, the classes with their methods
    assertContains(page, "<summary><code>Classes/Foo.sc</code></summary>", html=True)
    assertContains(page, "line 2")
    assertContains(
        page,
        '<ul class="is-family-monospace is-size-7 mt-1">'
        "<li>*new</li><li>volume</li><li>volume_</li><li>play</li></ul>",
        html=True,
    )
    assertContains(
        page, '<strong class="is-family-monospace">+ String</strong>', html=True
    )
    assertContains(page, "osx only")
    # parse errors only for the team
    assertNotContains(page, "Classes/Bad.sc")
    client.force_login(release.project.owners().get())
    assertContains(client.get(url), "<code>Classes/Bad.sc</code>")


@pytest.mark.django_db
def test_classes_tab_of_drafts_and_unread_versions(client):
    draft = release_with(("Classes/Foo.sc", FOO), final=False, project__name="alpha")
    client.force_login(draft.project.owners().get())
    url = reverse("project-detail", args=["alpha"])

    assertContains(client.get(url), "read once it's published")
    ReleaseFactory.create(project=draft.project, version="0.1.0", final=True)
    assertContains(client.get(url, {"version": "0.1.0"}), "haven't been read yet")


@pytest.mark.django_db
def test_conflicts_are_grouped_by_package(client):
    alpha = published(
        "alpha",
        ("Classes/Foo.sc", b"Foo { } Bar { }"),
        ("Classes/ext.sc", b"+ String { shout { } }"),
    )
    # a fork of alpha
    published(
        "alpha-fork",
        ("Classes/Foo.sc", b"Foo { } Bar { }"),
        ("Classes/ext.sc", b"+ String { shout { } }"),
    )
    published("beta", ("Classes/ext.sc", b"+ String { shout { } }"))

    groups = conflicts_by_package(alpha)

    assert [
        (
            g.other.project.name,
            [c.label for c in g.classes],
            [c.label for c in g.methods],
        )
        for g in groups
    ] == [
        ("alpha-fork", ["Bar", "Foo"], ["String:-shout"]),
        ("beta", [], ["String:-shout"]),
    ]
    page = client.get(reverse("project-detail", args=["alpha"]))
    assertContains(
        page, '<span class="tag is-danger is-light ml-1">2 classes</span>', html=True
    )
    assertContains(
        page,
        '<span class="tag is-warning is-light ml-1">1 method</span>',
        count=2,
        html=True,
    )


@pytest.mark.django_db
def test_classes_tab_folds_many_files(client):
    files = [(f"Classes/C{n}.sc", f"C{n} {{ }}".encode()) for n in range(11)]
    published("alpha", *files)

    page = client.get(reverse("project-detail", args=["alpha"])).text

    assert page.count('<details class="mb-3"') == 11
    assert '<details class="mb-3" open>' not in page


@pytest.fixture
def searchable():
    """Packages with classes, extensions and methods named like `Pbind`."""
    published(
        "alpha",
        ("Classes/Pbinder.sc", b"Pbinder { var <>pbind; *pbinds { } }"),
        ("Classes/ext.sc", b"+ Object { pbind { } }"),
    )
    published("beta", ("Classes/PBIND.sc", b"PBIND { }"))
    # not current: an older, a yanked and a draft version
    published("gamma", ("Classes/Pbind.sc", b"Pbind { }"), version="0.9.0")
    published("gamma", ("Classes/Other.sc", b"Other { }"), version="1.0.0")
    published("delta", ("Classes/Pbind.sc", b"Pbind { }"), yanked=True)
    published("epsilon", ("Classes/Pbind.sc", b"Pbind { }"), final=False)


@pytest.mark.django_db
@pytest.mark.usefixtures("searchable")
def test_search_classes_and_methods():
    results = search(" pbind ")

    # exact matches (ignoring case) first, then by name
    assert [
        (c.name, c.is_extension, c.release.project.name) for c in results.classes
    ] == [
        ("PBIND", False, "beta"),
        ("Pbinder", False, "alpha"),
    ]
    assert [
        (m.class_definition.name, m.name, m.is_class_method) for m in results.methods
    ] == [
        ("Object", "pbind", False),
        ("Pbinder", "pbind", False),
        ("Pbinder", "pbind_", False),
        ("Pbinder", "pbinds", True),
    ]
    assert not results.truncated
    assert search("") == search("  ") == SearchResults([], [], False)


@pytest.mark.django_db
@pytest.mark.usefixtures("searchable")
def test_search_is_limited(monkeypatch):
    monkeypatch.setattr("packages.classes.SEARCH_LIMIT", 2)

    results = search("p")

    assert (len(results.classes), len(results.methods), results.truncated) == (
        2,
        2,
        True,
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("searchable")
def test_class_search_page(client):
    url = reverse("class-search")

    assertContains(client.get(reverse("home")), f'href="{url}"')
    assertContains(client.get(url), 'name="q"')
    page = client.get(url, {"q": "pbind"})
    assertContains(
        page, '<td class="is-family-monospace">Object:-pbind</td>', html=True
    )
    assertContains(
        page, '<td class="is-family-monospace">Pbinder:*pbinds</td>', html=True
    )
    assertContains(page, f'href="{reverse("project-detail", args=["alpha"])}#classes"')
    assertNotContains(page, "gamma")

    partial = client.get(url, {"q": "zzz"}, headers={"HX-Request": "true"})
    assertNotContains(partial, "<html")
    assertContains(partial, "No class or method starting with <code>zzz</code>.")


@pytest.mark.django_db
def test_project_tabs_can_be_linked(client):
    published("alpha", ("Classes/Foo.sc", b"Foo { }"))
    page = client.get(reverse("project-detail", args=["alpha"]))
    assertContains(page, "location.hash.slice(1)")
