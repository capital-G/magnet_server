"""Changes of a package's team (`ProjectMember`): owners add people, change roles
and remove them; anyone can leave. A package always keeps at least one owner."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils.translation import gettext as _

from packages.models import Project, ProjectMember

Role = ProjectMember.Role


def ensure_can_manage(user: User, project: Project) -> None:
    if not project.can_manage(user):
        raise PermissionDenied(
            _("You are not an owner of %(project)s.")
            % {"project": project.normalized_name}
        )


def _ensure_another_owner(member: ProjectMember) -> None:
    """Refuses to take the owner role from the last owner. Locks the owners, so two
    owners can't remove each other at the same time."""
    owners = ProjectMember.objects.select_for_update().filter(
        project_id=member.project_id, role=Role.owner
    )
    if member.role == Role.owner and not owners.exclude(pk=member.pk).exists():
        raise ValidationError(
            _("%(project)s needs at least one owner, add another owner first.")
            % {"project": member.project.normalized_name}
        )


def add_member(
    actor: User, project: Project, username: str, role: str
) -> ProjectMember:
    """Adds the user `username` (any case) with `role`; for owners."""
    ensure_can_manage(actor, project)
    if role not in Role.values:
        raise ValidationError(_("Unknown role %(role)s.") % {"role": role})
    user = get_user_model().objects.filter(username__iexact=username.strip()).first()
    if user is None:
        raise ValidationError(
            _("There is no user %(username)s.") % {"username": username.strip()}
        )
    if member := project.members.filter(user=user).first():
        raise ValidationError(
            _("%(username)s is already on the team of %(project)s as %(role)s.")
            % {
                "username": user.username,
                "role": member.get_role_display().lower(),
                "project": project.normalized_name,
            }
        )
    return ProjectMember.objects.create(
        project=project, user=user, role=role, added_by=actor
    )


def set_role(actor: User, member: ProjectMember, role: str) -> ProjectMember:
    """Changes the role of `member`; for owners. The last owner stays one."""
    ensure_can_manage(actor, member.project)
    if role not in Role.values:
        raise ValidationError(_("Unknown role %(role)s.") % {"role": role})
    with transaction.atomic():
        if role != Role.owner:
            _ensure_another_owner(member)
        member.role = role
        member.save(update_fields=["role"])
    return member


def remove_member(actor: User, member: ProjectMember) -> None:
    """Removes `member`: owners remove anyone, everyone can leave. The last owner
    can't."""
    if member.user_id != actor.pk:
        ensure_can_manage(actor, member.project)
    with transaction.atomic():
        _ensure_another_owner(member)
        member.delete()


def _send_mail(template: str, user: User, context: dict) -> None:
    """Sends `templates/packages/email/<template>_{subject,message}.txt` to `user`
    after the commit; Django logs a failure (`robust`), the change stays."""
    context = {"user": user, **context}
    subject = render_to_string(f"packages/email/{template}_subject.txt", context)
    message = render_to_string(f"packages/email/{template}_message.txt", context)

    def send():
        send_mail(
            settings.ACCOUNT_EMAIL_SUBJECT_PREFIX + " ".join(subject.split()),
            message,
            None,  # DEFAULT_FROM_EMAIL
            [user.email],
        )

    transaction.on_commit(send, robust=True)


def send_added_mail(member: ProjectMember, team_url: str) -> None:
    """Tells a new member who added them, as what, and where the team is."""
    _send_mail(
        "added_to_team",
        member.user,
        {
            "added_by": member.added_by,
            "project": member.project,
            "role": member.get_role_display().lower(),
            "is_owner": member.role == Role.owner,
            "team_url": team_url,
        },
    )


def send_removed_mail(member: ProjectMember, removed_by: User) -> None:
    """Tells a removed member who removed them; not sent to those who left."""
    if member.user_id != removed_by.pk:
        _send_mail(
            "removed_from_team",
            member.user,
            {"removed_by": removed_by, "project": member.project},
        )
