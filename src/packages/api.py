"""The publish API, which the CLI's `magnet publish` uses (see "Contract additions"
in `implementation_plan.md` and `../magnet_cli/src/http_client/publish.rs`):

    POST   packages/{name}/versions/                                   draft
    GET    packages/{name}/versions/{version}/
    DELETE packages/{name}/versions/{version}/                         drafts only
    PUT    packages/{name}/versions/{version}/artifacts/{abi}/{platform}/
    POST   packages/{name}/versions/{version}/finalise/

`{name}` may be in any spelling. Drafts of packages the caller doesn't own or
maintain don't exist for them (`404`). Errors are `{"detail": "…"}`."""

import re
import tempfile

from django.conf import settings
from django.core.files import File
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_field,
)
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.models import AccessToken
from rest_framework import exceptions, serializers, status
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from magnet.exceptions import PayloadTooLarge
from packages.models import Project, Release
from packages.services import add_artifact, create_draft, delete_draft, finalise
from packages.validators import normalize_name

SHA256_HEADER = "X-Magnet-Sha256"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK_SIZE = 64 * 1024


class LengthRequired(exceptions.APIException):
    status_code = status.HTTP_411_LENGTH_REQUIRED
    default_detail = "The upload needs a Content-Length header."
    default_code = "length_required"


class BearerTokenAuthentication(OAuth2Authentication):
    """`OAuth2Authentication` without reading the body: oauthlib also looks for
    credentials in `request.POST`, which on DRF's request parses the body (and
    refuses an `application/gzip` upload with `415`). Django's own request only
    reads form bodies there, so an upload stays in the stream for the view."""

    def authenticate(self, request):
        return super().authenticate(request._request)


class HasPublishScope(BasePermission):
    """Changes need the `publish` scope; reading a version also works with `read`."""

    message = "The token lacks the `publish` scope."

    def has_permission(self, request, view) -> bool:
        token = request.auth
        if not isinstance(token, AccessToken):
            return False
        if request.method in SAFE_METHODS and token.allow_scopes(["read"]):
            return True
        return token.allow_scopes(["publish"])


class ArtifactSerializer(serializers.Serializer):
    abi = serializers.CharField()
    platform = serializers.CharField()
    sha256 = serializers.CharField(source="sha256_checksum")


class VersionSerializer(serializers.Serializer):
    """A version as the CLI's `PublishedVersion` parses it."""

    name = serializers.CharField(source="project.normalized_name")
    version = serializers.CharField()
    state = serializers.SerializerMethodField()
    # in upload order, like the index
    artifacts = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ChoiceField(choices=["draft", "final"]))
    def get_state(self, release: Release) -> str:
        return "final" if release.is_final else "draft"

    @extend_schema_field(ArtifactSerializer(many=True))
    def get_artifacts(self, release: Release) -> list[dict]:
        assets = release.assets.order_by("created", "abi", "platform")
        return list(ArtifactSerializer(assets, many=True).data)


class DraftRequestSerializer(serializers.Serializer):
    version = serializers.CharField(help_text="MAJOR.MINOR.PATCH, e.g. 1.2.0")


def get_project(name: str) -> Project:
    project = Project.objects.filter(normalized_name=normalize_name(name)).first()
    if project is None:
        raise exceptions.NotFound(f"There is no package {name}.")
    return project


def get_release(request, name: str, version: str) -> Release:
    """The version, unless it's a draft the caller may not see."""
    project = get_project(name)
    release = project.releases.filter(version=version).first()
    if release is None or not (release.is_final or project.can_publish(request.user)):
        raise exceptions.NotFound(
            f"There is no version {version} of {project.normalized_name}."
        )
    return release


def version_response(release: Release, status_code=status.HTTP_200_OK) -> Response:
    return Response(VersionSerializer(release).data, status=status_code)


class PublishApiView(APIView):
    authentication_classes = [BearerTokenAuthentication]
    permission_classes = [HasPublishScope]


class VersionsView(PublishApiView):
    @extend_schema(
        request=DraftRequestSerializer,
        responses={
            201: OpenApiResponse(VersionSerializer, "a new draft"),
            200: OpenApiResponse(VersionSerializer, "the existing draft or version"),
        },
        summary="Create a draft",
        description="Creates a draft of the version (and the package, owned by the "
        "caller, if it's new), or returns the existing draft or final version.",
    )
    def post(self, request, name: str):
        serializer = DraftRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        release, created = create_draft(
            request.user, name, serializer.validated_data["version"]
        )
        return version_response(
            release, status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class VersionView(PublishApiView):
    @extend_schema(responses=VersionSerializer, summary="Get a version")
    def get(self, request, name: str, version: str):
        return version_response(get_release(request, name, version))

    @extend_schema(
        responses={204: None},
        summary="Delete a draft",
        description="Deletes a draft with its artifacts, which frees its version. "
        "Final versions can't be deleted (`409`).",
    )
    def delete(self, request, name: str, version: str):
        delete_draft(request.user, get_release(request, name, version))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ArtifactView(PublishApiView):
    @extend_schema(
        request={"application/gzip": OpenApiTypes.BINARY},
        parameters=[
            OpenApiParameter(
                SHA256_HEADER,
                location=OpenApiParameter.HEADER,
                required=True,
                description="sha256 of the body as 64 lowercase hex characters",
            )
        ],
        responses={
            201: OpenApiResponse(VersionSerializer, "stored"),
            200: OpenApiResponse(VersionSerializer, "the same artifact was there"),
        },
        summary="Upload an artifact",
        description="The raw `.tar.gz` as body, with a `Content-Length` (at most "
        "`MAGNET_MAX_UPLOAD_SIZE`). Artifacts are never replaced: another artifact "
        "for the same abi and platform is a `409`.",
    )
    def put(self, request, name: str, version: str, abi: str, platform: str):
        release = get_release(request, name, version)
        sha256 = request.headers.get(SHA256_HEADER, "")
        if not SHA256_RE.match(sha256):
            raise exceptions.ValidationError(
                f"{SHA256_HEADER} must be the sha256 of the upload "
                "as 64 lowercase hex characters."
            )
        with read_upload(request) as file:
            asset, created = add_artifact(
                request.user,
                release,
                abi=abi,
                platform=platform,
                file=file,
                sha256=sha256,
                uploaded_via=request.headers.get("User-Agent", "")[:255],
            )
        return version_response(
            asset.release,
            status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class FinaliseView(PublishApiView):
    @extend_schema(
        request=None,
        responses=VersionSerializer,
        summary="Finalise a draft",
        description="Writes the version into the index. It needs its `any`/`any` "
        "artifact (quarks) or `source`/`any` artifact (leptons, gluons). A final "
        "version is returned as is.",
    )
    def post(self, request, name: str, version: str):
        return version_response(
            finalise(request.user, get_release(request, name, version))
        )


def read_upload(request) -> File:
    """The raw body in a temporary file, read in chunks. The `Content-Length` is
    required and checked against the limit before reading; Django's request
    stream never yields more bytes than announced."""
    try:
        length = int(request.META.get("CONTENT_LENGTH") or -1)
    except ValueError:
        raise exceptions.ValidationError("Invalid Content-Length.") from None
    if length < 0:
        raise LengthRequired()
    if length > settings.MAGNET_MAX_UPLOAD_SIZE:
        raise PayloadTooLarge(
            f"The upload is larger than {settings.MAGNET_MAX_UPLOAD_SIZE} bytes."
        )

    temp = tempfile.TemporaryFile()
    try:
        remaining = length
        while remaining > 0:
            chunk = request.stream.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise exceptions.ValidationError(
                    f"The upload ended after {length - remaining} of {length} bytes."
                )
            temp.write(chunk)
            remaining -= len(chunk)
        temp.seek(0)
    except BaseException:
        temp.close()
        raise
    return File(temp, name="upload.tar.gz")
