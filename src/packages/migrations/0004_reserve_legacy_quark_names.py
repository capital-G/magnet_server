from pathlib import Path

from django.db import migrations

# A copy of `directory.txt` of https://github.com/supercollider-quarks/quarks
# (master f62039b, 2026-10-26), one `Name=URL` line per legacy quark.
DIRECTORY = Path(__file__).parent / "data" / "legacy_quarks_directory.txt"


def reserve_legacy_quark_names(apps, schema_editor):
    """Reserves the (normalized) names of all legacy quarks, also those which aren't
    valid package names (e.g. `Faust.quark`), so they stay reserved if the name
    rules get looser. Existing reservations are kept."""
    ReservedName = apps.get_model("packages", "ReservedName")
    existing = set(ReservedName.objects.values_list("normalized_name", flat=True))
    for line in DIRECTORY.read_text(encoding="utf-8").splitlines():
        name, separator, url = line.partition("=")
        # the same as `packages.validators.normalize_name`
        normalized_name = name.strip().lower().replace("_", "-")
        if separator and normalized_name and normalized_name not in existing:
            ReservedName.objects.create(
                normalized_name=normalized_name,
                reason="legacy_quark",
                note=url.strip(),
            )
            existing.add(normalized_name)


def remove_legacy_quark_names(apps, schema_editor):
    ReservedName = apps.get_model("packages", "ReservedName")
    ReservedName.objects.filter(reason="legacy_quark", claimed_by=None).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("packages", "0003_reserved_names_and_tombstones"),
    ]

    operations = [
        migrations.RunPython(reserve_legacy_quark_names, remove_legacy_quark_names),
    ]
