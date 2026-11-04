"""Classes and methods of versions, read from their quark artifacts."""

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from git_db.db import GitDatabase, get_git_database
from packages.classes import extract_classes, extract_classes_task
from packages.models import Asset, ClassDefinition, ClassFileError
from packages.services import create_draft, finalise
from packages.tests.factories import AssetFactory, ReleaseFactory, tar_gz
from packages.tests.services_tests import upload_quark
from users.tests.factories import UserFactory


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
