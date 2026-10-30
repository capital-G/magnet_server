"""The publish API against the contract the CLI implements
(`../magnet_cli/src/http_client/publish.rs`, "The contract (M3)" and "Contract
additions (M2)" in the plan)."""

import hashlib
import io
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from rest_framework.exceptions import ValidationError

from magnet.exceptions import PayloadTooLarge
from packages.api import LengthRequired, read_upload
from packages.models import DeletedRelease, Release
from packages.tests.factories import ReleaseFactory
from packages.tests.services_tests import lepton_binary, lepton_source, quark, source
from users.oauth import create_access_token
from users.tests.factories import UserFactory

VERSIONS = "/api/v1/packages/{name}/versions/"
VERSION = VERSIONS + "{version}/"
ARTIFACT = VERSION + "artifacts/{kind}/{abi}/{platform}/"
FINALISE = VERSION + "finalise/"


class Api:
    """A client with a token like the CLI's (`MAGNET_TOKEN` or `magnet login`)."""

    def __init__(self, client, user=None, scope="read publish"):
        self.client = client
        self.user = user or UserFactory.create()
        token = create_access_token(self.user, days=1, scope=scope)
        self.headers = {
            "Authorization": f"Bearer {token.token}",
            "User-Agent": "magnet/test",
        }

    def draft(self, name="foo-bar", version="1.0.0"):
        return self.client.post(
            VERSIONS.format(name=name),
            {"version": version},
            content_type="application/json",
            headers=self.headers,
        )

    def get(self, name="foo-bar", version="1.0.0"):
        return self.client.get(
            VERSION.format(name=name, version=version), headers=self.headers
        )

    def delete(self, name="foo-bar", version="1.0.0"):
        return self.client.delete(
            VERSION.format(name=name, version=version), headers=self.headers
        )

    def upload(
        self,
        data: bytes,
        kind="quark",
        abi="any",
        platform="any",
        name="foo-bar",
        version="1.0.0",
        sha256=None,
        **extra,
    ):
        url = ARTIFACT.format(
            name=name, version=version, kind=kind, abi=abi, platform=platform
        )
        return self.client.put(
            url,
            data,
            content_type="application/gzip",
            headers={
                **self.headers,
                "X-Magnet-Sha256": sha256 or hashlib.sha256(data).hexdigest(),
            },
            **extra,
        )

    def finalise(self, name="foo-bar", version="1.0.0"):
        return self.client.post(
            FINALISE.format(name=name, version=version), headers=self.headers
        )


@pytest.fixture
def index():
    call_command("init_index")


@pytest.fixture
def api(client):
    return Api(client)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_publish_a_quark(api):
    data = quark()

    response = api.draft(name="Foo_Bar")
    assert response.status_code == 201
    assert response.json() == {
        "name": "foo-bar",
        "version": "1.0.0",
        "state": "draft",
        "artifacts": [],
    }

    response = api.upload(data)
    assert response.status_code == 201
    assert response.json()["artifacts"] == [
        {"kind": "quark", "abi": "any", "platform": "any", "sha256": sha256(data)}
    ]

    response = api.finalise()
    assert response.status_code == 400
    assert response.json() == {
        "detail": "foo-bar 1.0.0 needs its source artifact before it can be finalised."
    }
    assert api.upload(source(), kind="source").status_code == 201
    response = api.finalise()
    assert response.status_code == 200
    assert response.json()["state"] == "final"
    assert [a["kind"] for a in response.json()["artifacts"]] == ["quark", "source"]
    asset = Release.objects.get().assets.get(kind="quark")
    assert asset.uploaded_via == "magnet/test"

    # every step again, like a second `magnet publish`
    assert api.draft().status_code == 200
    assert api.draft().json()["state"] == "final"
    assert api.upload(data).status_code == 200
    assert api.finalise().status_code == 200


@pytest.mark.django_db
def test_existing_draft(api):
    assert api.draft().status_code == 201
    response = api.draft(name="FOO_BAR")
    assert response.status_code == 200
    assert response.json()["state"] == "draft"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name, version, detail",
    [
        ("wslib", "1.0.0", "The name wslib is reserved (Legacy quark)."),
        ("_foo", "1.0.0", "Must start with a letter or digit"),
        # a valid name since S19, but reserved
        ("3dj", "1.0.0", "The name 3dj is reserved (Legacy quark)."),
        ("foo-bar", "1.0", "MAJOR.MINOR.PATCH"),
        ("foo-bar", "1.0.0", "foo-bar 1.0.0 was deleted"),
    ],
)
def test_refused_drafts(api, name, version, detail):
    DeletedRelease.objects.create(normalized_name="foo-bar", version="1.0.0")

    response = api.draft(name=name, version=version)

    assert response.status_code == 400
    assert detail in response.json()["detail"]


@pytest.mark.django_db
def test_draft_needs_a_version(api):
    response = api.client.post(
        VERSIONS.format(name="foo-bar"),
        {},
        content_type="application/json",
        headers=api.headers,
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "version: This field is required."}


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_others_cant_publish(client, api):
    api.draft()
    api.upload(quark())
    other = Api(client)

    # a foreign draft doesn't exist for others
    assert other.get().status_code == 404
    assert other.upload(quark()).status_code == 404
    assert other.finalise().status_code == 404
    assert other.delete().status_code == 404
    # the package does, its owner decides who publishes
    response = other.draft(version="1.0.1")
    assert response.status_code == 403
    assert response.json() == {
        "detail": "You are not an owner or maintainer of foo-bar."
    }

    api.upload(source(), kind="source")
    api.finalise()
    assert other.get().status_code == 200
    assert other.upload(quark(), kind="source").status_code == 403
    assert other.delete().status_code == 403


@pytest.mark.django_db
def test_get_version(client, api):
    api.draft()
    api.upload(quark())

    response = api.get(name="Foo_Bar")
    assert response.status_code == 200
    assert response.json()["artifacts"] == [
        {"kind": "quark", "abi": "any", "platform": "any", "sha256": sha256(quark())}
    ]
    # also with a read-only token
    reader = Api(client, user=api.user, scope="read")
    assert reader.get().status_code == 200
    assert api.get(version="2.0.0").status_code == 404
    assert api.get(name="unknown").json() == {"detail": "There is no package unknown."}


@pytest.mark.django_db
def test_read_scope_cant_publish(client):
    reader = Api(client, scope="read")
    response = reader.draft()
    assert response.status_code == 403
    assert response.json() == {"detail": "The token lacks the `publish` scope."}


@pytest.mark.django_db
@pytest.mark.parametrize("token", [None, "unknown"])
def test_unauthenticated(client, token):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for response in [
        client.post(VERSIONS.format(name="foo"), headers=headers),
        client.put(
            ARTIFACT.format(
                name="foo", version="1.0.0", kind="quark", abi="any", platform="any"
            ),
            b"data",
            content_type="application/gzip",
            headers=headers,
        ),
    ]:
        assert response.status_code == 401
        assert "detail" in response.json()
        assert response.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.django_db
def test_upload_conflict(api):
    api.draft()
    api.upload(quark())

    response = api.upload(quark(content=b"Changed {}\n"))

    assert response.status_code == 409
    assert "another sha256" in response.json()["detail"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data, kwargs, detail",
    [
        (quark(), {"sha256": "0" * 64}, "The sha256 of the upload is"),
        (quark(), {"sha256": "ABC"}, "X-Magnet-Sha256 must be the sha256"),
        (quark(name="other"), {}, "The manifest is for other, not foo-bar."),
        (b"not a tarball", {}, "not a valid .tar.gz"),
        (quark(), {"abi": "API3"}, "lowercase letters"),
        # unknown and undeclared kinds
        (quark(), {"kind": "gluon"}, "gluon is not an artifact kind"),
        (quark(), {"kind": "QUARK"}, "QUARK is not an artifact kind"),
        (
            quark(),
            {"kind": "lepton", "abi": "api3_0", "platform": "macos_arm64"},
            "There is no [artifact.lepton] in magnet.toml",
        ),
        # tags per kind
        (
            lepton_binary(),
            {"kind": "lepton", "abi": "any", "platform": "any"},
            "A lepton artifact needs an abi and a platform other than 'any'.",
        ),
        (
            lepton_binary(),
            {"kind": "lepton", "abi": "api3_0", "platform": "any"},
            "A lepton artifact needs an abi and a platform other than 'any'.",
        ),
        (
            quark(),
            {"abi": "api3_0", "platform": "macos_arm64"},
            "A quark artifact has the abi and platform 'any'.",
        ),
        (
            source(),
            {"kind": "source", "platform": "macos_arm64"},
            "A source artifact has the abi and platform 'any'.",
        ),
    ],
)
def test_upload_refused(api, data, kwargs, detail):
    api.draft()

    response = api.upload(data, **kwargs)

    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert not Release.objects.get().assets.exists()


@pytest.mark.django_db
def test_upload_per_kind(api):
    api.draft()
    kinds = ("quark", "lepton")
    uploads = [
        (source(kinds=kinds), {"kind": "source"}),
        (quark(kinds=kinds), {"kind": "quark"}),
        (
            lepton_binary(kinds=kinds),
            {"kind": "lepton", "abi": "api3_0", "platform": "macos_arm64"},
        ),
    ]
    for data, kwargs in uploads:
        assert api.upload(data, **kwargs).status_code == 201
    # again, e.g. a re-run of a CI job
    for data, kwargs in uploads:
        assert api.upload(data, **kwargs).status_code == 200
    # another archive for the same kind, abi and platform
    response = api.upload(
        lepton_binary(b"other", kinds=kinds),
        kind="lepton",
        abi="api3_0",
        platform="macos_arm64",
    )
    assert response.status_code == 409
    assert (
        "already has an artifact lepton-api3_0-macos_arm64"
        in (response.json()["detail"])
    )

    assert api.get().json()["artifacts"] == [
        {"kind": kind, "abi": abi, "platform": platform, "sha256": sha256(data)}
        for data, (kind, abi, platform) in [
            (uploads[0][0], ("source", "any", "any")),
            (uploads[1][0], ("quark", "any", "any")),
            (uploads[2][0], ("lepton", "api3_0", "macos_arm64")),
        ]
    ]


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_finalise_names_what_is_missing(api):
    api.draft()
    kinds = ("quark", "lepton")
    for data, kwargs, missing in [
        (
            quark(kinds=kinds),
            {},
            "its source artifact and a lepton artifact",
        ),
        (source(kinds=kinds), {"kind": "source"}, "a lepton artifact"),
        (
            lepton_binary(kinds=kinds),
            {"kind": "lepton", "abi": "api3_0", "platform": "macos_arm64"},
            None,
        ),
    ]:
        assert api.upload(data, **kwargs).status_code == 201
        response = api.finalise()
        if missing:
            assert response.status_code == 400
            assert response.json() == {
                "detail": f"foo-bar 1.0.0 needs {missing} before it can be finalised."
            }
    assert response.status_code == 200
    assert response.json()["state"] == "final"


@pytest.mark.django_db
def test_old_artifact_route_is_gone(api):
    api.draft()
    response = api.client.put(
        VERSION.format(name="foo-bar", version="1.0.0") + "artifacts/any/any/",
        quark(),
        content_type="application/gzip",
        headers={**api.headers, "X-Magnet-Sha256": sha256(quark())},
    )
    assert response.status_code == 404
    assert response.headers["Content-Type"] == "application/json"
    assert "detail" in response.json()


@pytest.mark.django_db
def test_upload_needs_a_content_length(api):
    api.draft()
    response = api.upload(quark(), CONTENT_LENGTH="")
    assert response.status_code == 411
    assert response.json() == {"detail": "The upload needs a Content-Length header."}


@pytest.mark.django_db
def test_upload_too_large(api, settings):
    settings.MAGNET_MAX_UPLOAD_SIZE = 100
    api.draft()

    response = api.upload(quark())

    assert response.status_code == 413
    assert response.json() == {"detail": "The upload is larger than 100 bytes."}


def fake_request(body: bytes, length: str):
    return SimpleNamespace(META={"CONTENT_LENGTH": length}, stream=io.BytesIO(body))


def test_read_upload():
    with read_upload(fake_request(b"0123456789", "10")) as file:
        assert file.read() == b"0123456789"
    # a request stream ends at the announced length (Django's `LimitedStream`)
    with pytest.raises(ValidationError, match="ended after 4 of 10 bytes"):
        read_upload(fake_request(b"0123", "10"))
    with pytest.raises(ValidationError, match="Invalid Content-Length"):
        read_upload(fake_request(b"", "ten"))
    with pytest.raises(LengthRequired):
        read_upload(fake_request(b"", ""))


def test_read_upload_checks_the_length_before_reading(settings):
    settings.MAGNET_MAX_UPLOAD_SIZE = 10
    request = fake_request(b"0" * 11, "11")
    with pytest.raises(PayloadTooLarge):
        read_upload(request)
    assert request.stream.tell() == 0


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_lepton_draft_needs_its_source(api):
    api.draft()
    assert api.upload(
        lepton_binary(), kind="lepton", abi="api3_0", platform="macos_arm64"
    ).status_code == (201)

    response = api.finalise()
    assert response.status_code == 400
    assert response.json() == {
        "detail": "foo-bar 1.0.0 needs its source artifact before it can be finalised."
    }

    assert api.upload(lepton_source(), kind="source").status_code == 201
    response = api.finalise()
    assert response.status_code == 200
    assert [(a["kind"], a["abi"]) for a in response.json()["artifacts"]] == [
        ("lepton", "api3_0"),
        ("source", "any"),
    ]


@pytest.mark.django_db
@pytest.mark.usefixtures("index")
def test_delete(api, django_capture_on_commit_callbacks):
    api.draft()
    api.upload(quark())
    with django_capture_on_commit_callbacks(execute=True):
        assert api.delete().status_code == 204
    assert not Release.objects.exists()

    api.draft()
    api.upload(quark())
    api.upload(source(), kind="source")
    api.finalise()
    response = api.delete()
    assert response.status_code == 409
    assert "yank it instead" in response.json()["detail"]


@pytest.mark.django_db
def test_foreign_final_version_of_a_maintained_package(client):
    # maintainers publish like owners
    release = ReleaseFactory.create(project__name="foo-bar", final=True)
    maintainer = Api(client)
    release.project.maintainers.add(maintainer.user)

    assert maintainer.draft(version="2.0.0").status_code == 201


def test_schema_documents_the_publish_api(client, db):
    schema = client.get("/api/schema/", headers={"Accept": "application/json"})
    paths = schema.json()["paths"]
    assert {
        "/api/v1/packages/{name}/versions/",
        "/api/v1/packages/{name}/versions/{version}/",
        "/api/v1/packages/{name}/versions/{version}/artifacts/{kind}/{abi}/{platform}/",
        "/api/v1/packages/{name}/versions/{version}/finalise/",
    } <= set(paths)
    assert "/api/v1/upload/release/" not in paths
    assert (
        "/api/v1/packages/{name}/versions/{version}/artifacts/{abi}/{platform}/"
        not in paths
    )
    upload = paths[
        "/api/v1/packages/{name}/versions/{version}/artifacts/{kind}/{abi}/{platform}/"
    ]["put"]
    [kind] = [p for p in upload["parameters"] if p["name"] == "kind"]
    assert sorted(kind["schema"]["enum"]) == ["lepton", "quark", "source"]
