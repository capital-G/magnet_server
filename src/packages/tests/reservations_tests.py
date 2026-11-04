import importlib

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from packages.models import DeletedRelease, Project, ReservedName
from packages.services import ensure_name_available, get_or_create_project
from packages.tests.factories import ProjectFactory
from users.tests.factories import UserFactory

legacy_quarks_migration = importlib.import_module(
    "packages.migrations.0004_reserve_legacy_quark_names"
)


def reserve_legacy_quark_names():
    legacy_quarks_migration.reserve_legacy_quark_names(apps, None)


@pytest.mark.django_db
def test_legacy_quark_names_are_reserved():
    reservations = ReservedName.objects.filter(reason="legacy_quark")

    assert reservations.count() == 321
    assert {
        r.normalized_name: r.note
        for r in reservations.filter(
            normalized_name__in=["3dj", "ddwcommon", "faust.quark", "wslib"]
        )
    } == {
        "3dj": "https://github.com/supercollider-quarks/3Dj",
        "ddwcommon": "https://github.com/jamshark70/ddwCommon",
        "faust.quark": "https://github.com/madskjeldgaard/faust.quark",
        "wslib": "https://github.com/supercollider-quarks/wslib",
    }


@pytest.mark.django_db
def test_reserving_legacy_quark_names_again_keeps_existing_reservations():
    owner = UserFactory.create()
    ReservedName.objects.filter(normalized_name="wslib").update(
        claimed_by=owner, note="edited by an admin"
    )
    ReservedName.objects.filter(normalized_name="ddwcommon").delete()

    reserve_legacy_quark_names()

    assert ReservedName.objects.count() == 321
    wslib = ReservedName.objects.get(normalized_name="wslib")
    assert (wslib.claimed_by, wslib.note) == (owner, "edited by an admin")
    assert ReservedName.objects.filter(normalized_name="ddwcommon").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["foolib", "FooLib", "foo_lib"])
def test_reserved_name_is_refused_for_others(name: str):
    ReservedName.objects.create(normalized_name="foo-lib", reason="legacy_quark")
    ReservedName.objects.create(normalized_name="foolib", reason="deleted")

    with pytest.raises(ValidationError, match="is reserved"):
        ensure_name_available(name, UserFactory.create())


@pytest.mark.django_db
def test_reserved_name_is_allowed_for_its_claimant():
    claimant = UserFactory.create()
    ReservedName.objects.create(
        normalized_name="foo-lib", reason="legacy_quark", claimed_by=claimant
    )

    ensure_name_available("Foo_Lib", claimant)
    with pytest.raises(ValidationError, match=r"Foo_Lib is reserved \(Legacy quark\)"):
        ensure_name_available("Foo_Lib", UserFactory.create())


@pytest.mark.django_db
def test_unreserved_name_is_available():
    ReservedName.objects.create(normalized_name="foolib", reason="legacy_quark")
    ensure_name_available("foo-lib", UserFactory.create())


@pytest.mark.django_db
def test_new_projects_check_reservations():
    claimant = UserFactory.create()
    ReservedName.objects.filter(normalized_name="wslib").update(claimed_by=claimant)

    with pytest.raises(ValidationError, match="is reserved"):
        get_or_create_project(name="wslib", owner=UserFactory.create())
    project = get_or_create_project(name="wslib", owner=claimant)
    assert project.owners().get() == claimant


@pytest.mark.django_db
def test_existing_projects_with_a_reserved_name_keep_working():
    project = ProjectFactory.create(name="foolib")
    ReservedName.objects.create(normalized_name="foolib", reason="legacy_quark")

    assert get_or_create_project(name="FooLib", owner=project.owners().get()) == project
    assert Project.objects.count() == 1


@pytest.mark.django_db
def test_reserved_names_are_stored_normalized():
    reservation = ReservedName.objects.create(
        normalized_name="Foo_Lib", reason="deleted"
    )
    assert reservation.normalized_name == "foo-lib"


@pytest.mark.django_db
def test_a_version_has_a_single_tombstone():
    DeletedRelease.objects.create(normalized_name="foo", version="1.0.0")
    DeletedRelease.objects.create(normalized_name="foo", version="1.0.1")
    with pytest.raises(IntegrityError):
        DeletedRelease.objects.create(normalized_name="foo", version="1.0.0")


@pytest.mark.django_db
def test_admin_lists_reservations_and_tombstones(admin_client):
    DeletedRelease.objects.create(normalized_name="foo", version="1.0.0")

    reserved = admin_client.get("/admin/packages/reservedname/?q=wslib")
    deleted = admin_client.get("/admin/packages/deletedrelease/")

    assert reserved.status_code == deleted.status_code == 200
    assert "wslib" in reserved.text
    assert "foo" in deleted.text and "1.0.0" in deleted.text
    assert admin_client.get("/admin/packages/reservedname/add/").status_code == 200
    # tombstones are read-only
    assert admin_client.get("/admin/packages/deletedrelease/add/").status_code == 403
