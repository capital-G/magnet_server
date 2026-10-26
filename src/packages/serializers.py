from typing import Any

from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.fields import SerializerMethodField

from packages.models import Asset, Project, Release


class AssetSerializer(serializers.ModelSerializer):
    uploader = serializers.SlugRelatedField(
        queryset=User.objects.all(),
        slug_field="username",
        # style={'base_template': 'input.html'}
    )

    class Meta:
        model = Asset
        fields = [
            "uuid",
            "created",
            "file",
            "sha256_checksum",
            "uploader",
            "uploaded_via",
            "comment",
            "abi",
            "platform",
        ]


class ProjectSerializer(serializers.ModelSerializer):
    # maybe use methods that match the type?
    description = SerializerMethodField()
    summary = SerializerMethodField()
    repository_url = SerializerMethodField()
    home_page_url = SerializerMethodField()
    bug_tracker_url = SerializerMethodField()
    license = SerializerMethodField()

    # project description are stored in releases, so we have take these attributes
    # from the latest release (releases are ordered newest first; the views
    # prefetch only the final ones)
    def get_release_attribute(self, project: Project, attribute: str) -> Any | None:
        if release := next(iter(project.releases.all()), None):
            return release.__getattribute__(attribute)
        return None

    def get_description(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="description")

    def get_summary(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="summary")

    def get_repository_url(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="repository_url")

    def get_home_page_url(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="home_page_url")

    def get_bug_tracker_url(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="bug_tracker_url")

    def get_license(self, project: Project) -> str | None:
        return self.get_release_attribute(project, attribute="license")

    class Meta:
        model = Project
        fields = [
            "uuid",
            "created",
            "summary",
            "description",
            "repository_url",
            "home_page_url",
            "bug_tracker_url",
            "license",
            "last_update",
            "name",
            "normalized_name",
            "slug",
            "kind",
        ]


class ReleaseSerializer(serializers.ModelSerializer):
    assets = AssetSerializer(many=True, read_only=True)

    class Meta:
        model = Release
        fields = [
            "version",
            "uuid",
            "created",
            "release_note",
            "published_at",
            "yanked",
            "assets",
        ]


class ProjectDetailSerializer(ProjectSerializer):
    releases = ReleaseSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = [
            "uuid",
            "created",
            "summary",
            "description",
            "repository_url",
            "home_page_url",
            "bug_tracker_url",
            "license",
            "last_update",
            "name",
            "normalized_name",
            "slug",
            "kind",
            "releases",
        ]
