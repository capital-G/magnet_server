import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_owners_and_maintainers(apps, schema_editor):
    """The owner becomes an `owner` member, the maintainers `maintainer` members."""
    Project = apps.get_model("packages", "Project")
    ProjectMember = apps.get_model("packages", "ProjectMember")
    members = []
    for project in Project.objects.prefetch_related("maintainers"):
        members.append(
            ProjectMember(project=project, user_id=project.owner_id, role="owner")
        )
        members += [
            ProjectMember(project=project, user=user, role="maintainer")
            for user in project.maintainers.all()
            if user.pk != project.owner_id
        ]
    ProjectMember.objects.bulk_create(members)


class Migration(migrations.Migration):
    dependencies = [
        ("packages", "0009_artifact_kinds_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectMember",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("owner", "Owner"), ("maintainer", "Maintainer")],
                        max_length=20,
                    ),
                ),
                ("created", models.DateTimeField(auto_now_add=True)),
                (
                    "added_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="Who added the user; empty for the creator of the project",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="members",
                        to="packages.project",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Team member",
                "verbose_name_plural": "Team members",
                "ordering": ("project", "-role", "created"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("project", "user"), name="unique_project_member"
                    )
                ],
            },
        ),
        migrations.RunPython(copy_owners_and_maintainers, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="project",
            name="maintainers",
        ),
        migrations.RemoveField(
            model_name="project",
            name="owner",
        ),
    ]
