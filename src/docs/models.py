from django.db import models
from django.utils.translation import gettext_lazy as _

from packages.models import Release


class DocBuild(models.Model):
    """A build of the docs of a version, see `docs.tasks`. The docs of a package are
    those of its latest built build; failed builds keep the previous docs."""

    class Status(models.TextChoices):
        queued = "queued", _("Queued")
        building = "building", _("Building")
        built = "built", _("Built")
        failed = "failed", _("Failed")
        # the version wasn't the package's current one anymore when its turn came
        skipped = "skipped", _("Skipped")

    release = models.ForeignKey(
        Release, on_delete=models.CASCADE, related_name="doc_builds"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.queued
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # the builder's sclang, which may not meet the version's `sclang` (built anyway)
    sclang_version = models.CharField(max_length=32, blank=True)
    sclang_mismatch = models.BooleanField(default=False)
    # why a build failed, in a few words; the details are in the log
    error = models.CharField(max_length=255, blank=True)
    # output of the build containers, shortened in the middle (`LOG_LIMIT`)
    log = models.TextField(blank=True)
    # the documents a built build published, e.g. ["Classes/Foo", "Guides/Intro"]
    documents = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"docs of {self.release} ({self.status})"
