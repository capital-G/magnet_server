"""The team of a package: owners and maintainers (`ProjectMember`)."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from packages.models import Project, ProjectMember
from packages.services import create_draft, get_or_create_project
from packages.tests.factories import ProjectFactory, ProjectMemberFactory
from users.tests.factories import UserFactory


@pytest.mark.django_db
def test_roles():
    owner, maintainer, stranger = UserFactory.create_batch(3)
    project = ProjectFactory.create(owner=owner)
    ProjectMemberFactory.create(project=project, user=maintainer)

    assert [project.can_publish(u) for u in (owner, maintainer, stranger)] == [
        True,
        True,
        False,
    ]
    assert [project.can_manage(u) for u in (owner, maintainer, stranger)] == [
        True,
        False,
        False,
    ]
    assert not project.can_publish(AnonymousUser())
    assert not project.can_manage(AnonymousUser())
    assert list(project.owners()) == [owner]


@pytest.mark.django_db
def test_several_owners():
    first, second = UserFactory.create_batch(2)
    project = ProjectFactory.create(owner=first)
    ProjectMemberFactory.create(
        project=project, user=second, role=ProjectMember.Role.owner
    )

    assert set(project.owners()) == {first, second}
    assert project.can_manage(second)


@pytest.mark.django_db
def test_projects_of_a_user():
    user = UserFactory.create()
    owned = ProjectFactory.create(name="owned", owner=user)
    maintained = ProjectFactory.create(name="maintained")
    ProjectMemberFactory.create(project=maintained, user=user)
    ProjectFactory.create(name="foreign")

    assert set(Project.objects.of_user(user)) == {owned, maintained}


@pytest.mark.django_db
def test_the_creator_of_a_package_owns_it():
    user = UserFactory.create()

    release, _ = create_draft(user, "foo-bar", "1.0.0")
    created = get_or_create_project(name="baz", owner=user)

    for project in [release.project, created]:
        [member] = project.members.all()
        assert (member.user, member.role, member.added_by) == (user, "owner", None)


@pytest.mark.django_db
def test_deleting_a_user_removes_the_memberships():
    user = UserFactory.create()
    project = ProjectFactory.create(owner=user)

    user.delete()

    assert not project.members.exists()


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_migration_copies_owner_and_maintainers():
    before = [("packages", "0009_artifact_kinds_constraints")]
    after = [("packages", "0010_project_members")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    apps = executor.loader.project_state(before).apps
    User = apps.get_model("auth", "User")
    owner, maintainer = (
        User.objects.create(username="owner"),
        User.objects.create(username="maintainer"),
    )
    project = apps.get_model("packages", "Project").objects.create(
        name="foo", normalized_name="foo", slug="foo", owner_id=owner.pk
    )
    # the owner as maintainer too only becomes an owner
    project.maintainers.add(owner, maintainer)

    executor.loader.build_graph()
    executor.migrate(after)
    apps = executor.loader.project_state(after).apps
    members = apps.get_model("packages", "ProjectMember").objects.order_by("role")

    assert [(m.user.username, m.role) for m in members] == [
        ("maintainer", "maintainer"),
        ("owner", "owner"),
    ]

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
