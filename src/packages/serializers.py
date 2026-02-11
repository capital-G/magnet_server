from typing import Any, Dict, Optional

from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.fields import SerializerMethodField
from rest_framework.relations import SlugRelatedField

from packages.models import Asset, Dependency, Project, Release, valid_semver


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
            "asset_format",
            "architecture",
            "operating_system",
            "server_abi_version",
        ]


class ProjectSerializer(serializers.ModelSerializer):
    # maybe use methods that match the type?
    description = SerializerMethodField()
    summary = SerializerMethodField()
    repository_url = SerializerMethodField()
    home_page_url = SerializerMethodField()
    bug_tracker_url = SerializerMethodField()
    license = SerializerMethodField()

    queryset = Project.objects.all().prefetch_related("releases")

    # project description are stored in releases, so we have take these attributes
    # from the latest release
    def get_release_attribute(self, project: Project, attribute: str) -> Optional[Any]:
        if release := project.releases.last():
            return release.__getattribute__(attribute)
        return None

    def get_description(self, project: Project) -> Optional[str]:
        return self.get_release_attribute(project, attribute="description")

    def get_summary(self, project: Project) -> Optional[str]:
        return self.get_release_attribute(project, attribute="summary")

    def get_repository_url(self, project: Project) -> Optional[str]:
        return self.get_release_attribute(project, attribute="repository_url")

    def get_home_page_url(self, project: Project) -> Optional[str]:
        return self.get_release_attribute(project, attribute="home_page_url")

    def get_bug_tracker_url(self, project: Project) -> Optional[str]:
        return self.get_release_attribute(project, attribute="bug_tracker_url")

    def get_license(self, project: Project) -> Optional[str]:
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
            "slug",
            "project_type",
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
            "is_prerelease",
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
            "slug",
            "project_type",
            "releases",
        ]


class ReleaseUploadSerializer(serializers.ModelSerializer):
    project = SlugRelatedField(slug_field="slug", read_only=True)  # type: ignore

    class Meta:
        model = Release
        fields = [
            "project",
            "version",
            "license",
        ]

    def to_internal_value(self, data: Dict[str, Any]) -> Release:
        project = Project.objects.get(slug__exact=data["project"])
        release, _ = Release.objects.get_or_create(
            version=data["version"],
            project=project,
            defaults={
                "license": data["license"],
            },
        )
        return release


class AssetUploadSerializer(serializers.ModelSerializer):
    release: ReleaseUploadSerializer = ReleaseUploadSerializer(
        read_only=False,
        many=False,
    )

    class Meta:
        model = Asset
        fields = [
            # "uuid",
            "file",
            "sha256_checksum",
            "uploaded_via",
            "comment",
            "asset_format",
            "architecture",
            "operating_system",
            "server_abi_version",
            "release",
        ]
