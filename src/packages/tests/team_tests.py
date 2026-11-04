"""The team of a package: owners and maintainers (`ProjectMember`)."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains

from packages.models import Project, ProjectMember
from packages.services import create_draft, get_or_create_project
from packages.team import add_member, remove_member, set_role
from packages.tests.factories import (
    ProjectFactory,
    ProjectMemberFactory,
    ReleaseFactory,
)
from packages.tests.publish_api_tests import Api
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


@pytest.fixture
def team():
    """foo-bar with the owner `olivia` and the maintainer `max`."""
    owner = UserFactory.create(username="olivia")
    maintainer = UserFactory.create(username="max")
    project = ProjectFactory.create(name="foo-bar", owner=owner)
    ProjectMemberFactory.create(project=project, user=maintainer)
    return SimpleNamespace(project=project, owner=owner, maintainer=maintainer)


def member(project, user):
    return project.members.get(user=user)


def roles(project):
    return {m.user.username: m.role for m in project.members.select_related("user")}


@pytest.mark.django_db
def test_owners_add_members(team, bob):
    added = add_member(team.owner, team.project, "Bob ", "owner")
    UserFactory.create(username="carol")
    add_member(team.owner, team.project, "carol", "maintainer")

    assert (added.user, added.added_by) == (
        User.objects.get(username="bob"),
        team.owner,
    )
    assert roles(team.project) == {
        "olivia": "owner",
        "max": "maintainer",
        "bob": "owner",
        "carol": "maintainer",
    }


@pytest.fixture
def bob():
    return UserFactory.create(username="bob")


@pytest.mark.django_db
@pytest.mark.usefixtures("bob")
@pytest.mark.parametrize(
    "username, role, error",
    [
        ("nobody", "maintainer", "There is no user nobody."),
        ("MAX", "owner", "max is already on the team of foo-bar as maintainer."),
        ("olivia", "maintainer", "olivia is already on the team of foo-bar as owner."),
        ("bob", "admin", "Unknown role admin."),
    ],
)
def test_add_member_refuses(team, username, role, error):
    with pytest.raises(ValidationError) as e:
        add_member(team.owner, team.project, username, role)
    assert e.value.messages == [error]


@pytest.mark.django_db
def test_only_owners_manage_the_team(team, bob):
    stranger = UserFactory.create()
    owner = member(team.project, team.owner)
    for user in [team.maintainer, stranger, AnonymousUser()]:
        with pytest.raises(PermissionDenied):
            add_member(user, team.project, "bob", "maintainer")
        with pytest.raises(PermissionDenied):
            set_role(user, owner, "maintainer")
        with pytest.raises(PermissionDenied):
            remove_member(user, owner)
    assert roles(team.project) == {"olivia": "owner", "max": "maintainer"}


@pytest.mark.django_db
def test_set_role(team):
    set_role(team.owner, member(team.project, team.maintainer), "owner")
    # with a second owner, the first may step down
    set_role(team.owner, member(team.project, team.owner), "maintainer")

    assert roles(team.project) == {"olivia": "maintainer", "max": "owner"}


@pytest.mark.django_db
def test_the_last_owner_stays(team):
    owner = member(team.project, team.owner)
    message = "foo-bar needs at least one owner, add another owner first."

    for change in [
        lambda: set_role(team.owner, owner, "maintainer"),
        lambda: remove_member(team.owner, owner),
    ]:
        with pytest.raises(ValidationError) as e:
            change()
        assert e.value.messages == [message]
    # an owner may set the owner role again
    set_role(team.owner, owner, "owner")
    assert roles(team.project) == {"olivia": "owner", "max": "maintainer"}


@pytest.mark.django_db
def test_remove_and_leave(team, bob):
    add_member(team.owner, team.project, "bob", "maintainer")

    remove_member(team.owner, member(team.project, bob))
    # maintainers leave themselves
    remove_member(team.maintainer, member(team.project, team.maintainer))

    assert roles(team.project) == {"olivia": "owner"}


@pytest.mark.django_db
def test_a_new_maintainer_publishes_through_the_api(client, team, bob):
    api = Api(client, user=bob)
    ReleaseFactory.create(project=team.project, version="1.0.0", final=True)
    assert api.draft(version="2.0.0").status_code == 403

    add_member(team.owner, team.project, "bob", "maintainer")

    assert api.draft(version="2.0.0").status_code == 201


@pytest.mark.django_db
def test_team_page_access(client, team):
    url = reverse("team", args=["foo-bar"])

    assert client.get(url).status_code == 302  # to the login
    client.force_login(UserFactory.create())
    assert client.get(url).status_code == 403

    client.force_login(team.maintainer)
    page = client.get(url)
    assertContains(page, "olivia")
    assertContains(page, "Leave")
    # maintainers see the team, owners change it
    assertNotContains(page, 'name="username"')
    assertNotContains(page, "Remove")
    assert client.post(url, {"username": "bob", "role": "owner"}).status_code == 403

    client.force_login(team.owner)
    page = client.get(url)
    assertContains(page, 'name="username"')
    assertContains(page, "Remove max from the team")


@pytest.mark.django_db
def test_project_page_links_the_team_for_members(client, team):
    ReleaseFactory.create(project=team.project, final=True)
    detail = reverse("project-detail", args=["foo-bar"])
    link = f'href="{reverse("team", args=["foo-bar"])}"'

    assertNotContains(client.get(detail), link)
    client.force_login(team.maintainer)
    assertContains(client.get(detail), link)


@pytest.mark.django_db
def test_change_the_team_on_the_website(client, team, bob):
    htmx = {"HX-Request": "true"}
    url = reverse("team", args=["foo-bar"])
    client.force_login(team.owner)

    response = client.post(url, {"username": "bob", "role": "owner"}, headers=htmx)
    assertContains(response, 'id="team"')
    assertNotContains(response, "<html")
    response = client.post(url, {"username": "nobody", "role": "owner"}, headers=htmx)
    assertContains(response, "There is no user nobody.")

    bob_member = member(team.project, bob)
    client.post(
        reverse("team-role", args=["foo-bar", bob_member.pk]),
        {"role": "maintainer"},
        headers=htmx,
    )
    max_member = member(team.project, team.maintainer)
    client.post(reverse("team-remove", args=["foo-bar", max_member.pk]), headers=htmx)
    assert roles(team.project) == {"olivia": "owner", "bob": "maintainer"}

    owner = member(team.project, team.owner)
    response = client.post(
        reverse("team-remove", args=["foo-bar", owner.pk]), headers=htmx
    )
    assertContains(response, "needs at least one owner")
    assert (
        client.get(reverse("team-remove", args=["foo-bar", owner.pk])).status_code
        == 405
    )


@pytest.mark.django_db
def test_leaving_leaves_the_page(client, team):
    client.force_login(team.maintainer)
    url = reverse(
        "team-remove", args=["foo-bar", member(team.project, team.maintainer).pk]
    )

    response = client.post(url, headers={"HX-Request": "true"})

    # foo-bar has no final version, so its page doesn't exist for others
    assert response.headers["HX-Redirect"] == reverse("home")
    assert roles(team.project) == {"olivia": "owner"}


@pytest.mark.django_db
def test_members_of_other_projects_are_not_found(client, team):
    other = ProjectMemberFactory.create()
    client.force_login(team.owner)

    url = reverse("team-remove", args=["foo-bar", other.pk])
    assert client.post(url).status_code == 404


@pytest.mark.django_db
def test_new_members_get_an_email(
    client, team, bob, mailoutbox, django_capture_on_commit_callbacks
):
    bob.email = "bob@example.com"
    bob.save()
    client.force_login(team.owner)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("team", args=["foo-bar"]), {"username": "bob", "role": "owner"}
        )

    [mail] = mailoutbox
    assert mail.to == ["bob@example.com"]
    assert mail.subject == "[magnet] You were added to the team of foo-bar"
    assert (
        "olivia added you to the team of the package foo-bar on magnet, as owner."
        in mail.body
    )
    assert "manage its team" in mail.body
    assert "http://testserver/project/foo-bar/team" in mail.body


@pytest.mark.django_db
def test_maintainers_mail_and_refused_adds(
    client, team, bob, mailoutbox, django_capture_on_commit_callbacks
):
    client.force_login(team.owner)
    url = reverse("team", args=["foo-bar"])

    with django_capture_on_commit_callbacks(execute=True):
        client.post(url, {"username": "max", "role": "owner"})
        client.post(url, {"username": "nobody", "role": "owner"})
        client.post(url, {"username": "bob", "role": "maintainer"})

    [mail] = mailoutbox
    assert mail.to == [bob.email]
    assert "as maintainer" in mail.body and "manage its team" not in mail.body


@pytest.mark.django_db
def test_a_failing_mail_doesnt_fail_the_add(
    client, team, bob, monkeypatch, django_capture_on_commit_callbacks
):
    def fail(*args, **kwargs):
        raise ConnectionRefusedError("no mail server")

    monkeypatch.setattr("packages.team.send_mail", fail)
    client.force_login(team.owner)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("team", args=["foo-bar"]), {"username": "bob", "role": "owner"}
        )

    assert response.status_code == 200
    assert roles(team.project)["bob"] == "owner"


@pytest.mark.django_db
def test_removed_members_get_an_email(
    client, team, bob, mailoutbox, django_capture_on_commit_callbacks
):
    add_member(team.owner, team.project, "bob", "maintainer")
    client.force_login(team.owner)

    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            reverse("team-remove", args=["foo-bar", member(team.project, bob).pk])
        )
        # refused: the last owner
        client.post(
            reverse(
                "team-remove", args=["foo-bar", member(team.project, team.owner).pk]
            )
        )

    [mail] = mailoutbox
    assert mail.to == [bob.email]
    assert mail.subject == "[magnet] You were removed from the team of foo-bar"
    assert "olivia removed you from the team of the package foo-bar" in mail.body


@pytest.mark.django_db
def test_no_email_for_leaving(
    client, team, mailoutbox, django_capture_on_commit_callbacks
):
    client.force_login(team.maintainer)
    url = reverse(
        "team-remove", args=["foo-bar", member(team.project, team.maintainer).pk]
    )

    with django_capture_on_commit_callbacks(execute=True):
        client.post(url)

    assert roles(team.project) == {"olivia": "owner"}
    assert mailoutbox == []
