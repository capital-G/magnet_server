import pytest
from django.core.exceptions import ValidationError
from django.urls import path
from oauth2_provider.contrib.rest_framework import OAuth2Authentication, TokenHasScope
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from git_db.files import DuplicateIndexEntry
from magnet import urls
from users.oauth import create_access_token
from users.tests.factories import UserFactory

"""Error responses of the API: always JSON `{"detail": …}` (the CLI prints `detail`)."""


class PublishView(APIView):
    authentication_classes = [OAuth2Authentication]
    permission_classes = [TokenHasScope]
    required_scopes = ["publish"]

    def post(self, request):
        return Response({"user": request.user.email})


class ExampleSerializer(serializers.Serializer):
    version = serializers.CharField(max_length=3)


class FailingView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, error):
        if error == "model":
            raise ValidationError(["Invalid version", "Name is reserved"])
        if error == "index":
            raise DuplicateIndexEntry("alpha 1.0.0 is already in the index")
        ExampleSerializer(data=request.data).is_valid(raise_exception=True)
        return Response({})


urlpatterns = [
    path("api/test/publish/", PublishView.as_view()),
    path("api/test/fail/<str:error>/", FailingView.as_view()),
    *urls.urlpatterns,
]

pytestmark = [pytest.mark.django_db, pytest.mark.urls("magnet.tests.api_tests")]


def bearer(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def test_token_with_publish_scope_is_accepted(client):
    user = UserFactory.create()
    token = create_access_token(user, days=1, scope="read publish")

    response = client.post("/api/test/publish/", **bearer(token.token))

    assert response.status_code == 200
    assert response.json() == {"user": user.email}


def test_token_without_publish_scope_is_forbidden(client):
    token = create_access_token(UserFactory.create(), days=1, scope="read")

    response = client.post("/api/test/publish/", **bearer(token.token))

    assert response.status_code == 403
    assert "detail" in response.json()


@pytest.mark.parametrize("headers", [{}, bearer("not-a-token")])
def test_missing_or_unknown_token_is_401_json(client, headers):
    response = client.post("/api/test/publish/", **headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert "detail" in response.json()


def test_django_validation_error_is_400_detail(client):
    response = client.post("/api/test/fail/model/")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid version; Name is reserved"}


def test_index_write_error_is_400_detail(client):
    response = client.post("/api/test/fail/index/")

    assert response.status_code == 400
    assert response.json() == {"detail": "alpha 1.0.0 is already in the index"}


def test_serializer_errors_are_joined_into_detail(client):
    response = client.post(
        "/api/test/fail/serializer/", {"version": "1.0.0"}, "application/json"
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "version: Ensure this field has no more than 3 characters."
    }


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
def test_unknown_api_path_is_404_json(client, method):
    response = getattr(client, method)("/api/v1/does-not-exist/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}


def test_method_not_allowed_is_405_json(client):
    response = client.post("/api/version/")

    assert response.status_code == 405
    assert "detail" in response.json()


def test_too_large_request_is_413_json(client, settings):
    # Django only limits form bodies, DRF reads JSON from the stream
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 10

    response = client.post("/api/test/fail/serializer/", {"version": "x" * 100})

    assert response.status_code == 413
    assert "detail" in response.json()


def test_server_version(client):
    response = client.get("/api/version/")

    assert response.status_code == 200
    assert set(response.json()) == {"version"}
