import packages.validators
from django.db import migrations, models


def map_kinds(apps, schema_editor):
    """Best effort for the test data before artifact kinds (nothing was released):
    a quark release has the kinds `["quark"]` and a quark artifact, a lepton release
    `["lepton"]`, its `source`/`any` artifact becomes the source (`any`/`any`, its file
    keeps its old path) and the others lepton artifacts."""
    Release = apps.get_model("packages", "Release")
    Asset = apps.get_model("packages", "Asset")
    for release in Release.objects.all():
        release.kinds = [release.kind] if release.kind in ("quark", "lepton") else []
        release.save(update_fields=["kinds"])
    for asset in Asset.objects.select_related("release"):
        if asset.abi == "source":
            asset.kind, asset.abi, asset.platform = "source", "any", "any"
        elif asset.release.kind == "quark":
            asset.kind = "quark"
        else:
            asset.kind = "lepton"
        asset.save(update_fields=["kind", "abi", "platform"])


class Migration(migrations.Migration):
    dependencies = [
        ("packages", "0007_git_dependency"),
    ]

    operations = [
        migrations.AddField(
            model_name="release",
            name="kinds",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="The kinds the version declares, in the order quark, lepton",
                validators=[packages.validators.validate_release_kinds],
            ),
        ),
        migrations.AddField(
            model_name="asset",
            name="kind",
            field=models.CharField(
                choices=[
                    ("source", "Source"),
                    ("quark", "Quark"),
                    ("lepton", "Lepton"),
                ],
                default="",
                help_text="The kind of the artifact, a kind of its release or 'source'",
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.RemoveConstraint(
            model_name="asset",
            name="unique_release_abi_platform",
        ),
        migrations.RunPython(map_kinds, migrations.RunPython.noop),
    ]
