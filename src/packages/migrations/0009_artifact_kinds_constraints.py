from django.db import migrations, models

import packages.validators


class Migration(migrations.Migration):
    """The schema changes after `0008`'s data migration, in their own transaction
    (postgres can't alter a table with pending trigger events)."""

    dependencies = [
        ("packages", "0008_artifact_kinds"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="asset",
            constraint=models.UniqueConstraint(
                fields=("release", "kind", "abi", "platform"),
                name="unique_release_kind_abi_platform",
            ),
        ),
        migrations.RemoveField(
            model_name="project",
            name="kind",
        ),
        migrations.RemoveField(
            model_name="release",
            name="kind",
        ),
        migrations.AlterModelOptions(
            name="asset",
            options={
                "ordering": ("release", "kind", "abi", "platform"),
                "verbose_name": "Asset",
                "verbose_name_plural": "Assets",
            },
        ),
        migrations.AlterField(
            model_name="asset",
            name="abi",
            field=models.CharField(
                default="any",
                help_text="ABI the artifact targets, 'any' for sources and quarks",
                max_length=64,
                validators=[packages.validators.validate_artifact_tag],
            ),
        ),
        migrations.AlterField(
            model_name="asset",
            name="platform",
            field=models.CharField(
                default="any",
                help_text="Platform the artifact targets, 'any' for sources and quarks",
                max_length=64,
                validators=[packages.validators.validate_artifact_tag],
            ),
        ),
    ]
