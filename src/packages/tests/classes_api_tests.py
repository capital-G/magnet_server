"""The API of classes and methods: search, conflicts, the check of an artifact."""

import pytest

from packages.models import ClassDefinition
from packages.tests.classes_tests import published
from packages.tests.factories import tar_gz
from users.oauth import create_access_token
from users.tests.factories import UserFactory

CHECK = "/api/v1/classes/check/"


@pytest.fixture
def packages():
    published(
        "alpha",
        ("Classes/Foo.sc", b"Foo { *new { } play { } }"),
        ("Classes/ext.sc", b"+ String { shout { } }"),
    )
    published(
        "Beta_Lib",
        ("Classes/Foo.sc", b"Foo { }"),
        ("Classes/ext.sc", b"+ String { shout { } *make { } }"),
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_search(client):
    response = client.get("/api/v1/classes/", {"q": "foo"})

    assert response.status_code == 200
    assert response.json() == {
        "classes": [
            {
                "name": "Foo",
                "extension": False,
                "package": "alpha",
                "version": "1.0.0",
                "path": "Classes/Foo.sc",
            },
            {
                "name": "Foo",
                "extension": False,
                "package": "beta-lib",
                "version": "1.0.0",
                "path": "Classes/Foo.sc",
            },
        ],
        "methods": [],
        "truncated": False,
    }
    [method] = client.get("/api/v1/classes/", {"q": "MAKE"}).json()["methods"]
    assert method == {
        "name": "make",
        "class": "String",
        "class_method": True,
        "extension": True,
        "package": "beta-lib",
        "version": "1.0.0",
        "path": "Classes/ext.sc",
    }
    assert client.get("/api/v1/classes/").json()["classes"] == []


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_conflicts_of_a_package(client):
    response = client.get("/api/v1/packages/Alpha/conflicts/")

    assert response.status_code == 200
    assert response.json() == {
        "package": "alpha",
        "version": "1.0.0",
        "conflicts": [
            {
                "package": "beta-lib",
                "version": "1.0.0",
                "classes": ["Foo"],
                "methods": [
                    {"class": "String", "name": "shout", "class_method": False}
                ],
            }
        ],
    }


@pytest.mark.django_db
def test_conflicts_of_packages_without_a_current_version(client):
    published("yanked", ("Classes/Foo.sc", b"Foo { }"), yanked=True)
    published("draft", ("Classes/Foo.sc", b"Foo { }"), final=False)

    assert client.get("/api/v1/packages/yanked/conflicts/").json() == {
        "package": "yanked",
        "version": None,
        "conflicts": [],
    }
    for name in ["draft", "missing"]:
        response = client.get(f"/api/v1/packages/{name}/conflicts/")
        assert response.status_code == 404
        assert "detail" in response.json()


def check(client, data, token=None, **params):
    user = UserFactory.create()
    token = token or create_access_token(user, days=1, scope="publish")
    query = "".join(f"&{k}={v}" for k, v in params.items()).replace("&", "?", 1)
    return client.post(
        CHECK + query,
        data,
        content_type="application/gzip",
        headers={"Authorization": f"Bearer {token.token}"},
    )


QUARK = tar_gz(
    ("magnet.toml", b"magnet-version = 1\n"),
    ("Classes/Foo.sc", b"Foo : Bar { play { } }"),
    ("Classes/linux/Lin.sc", b"Lin { }"),
    ("Classes/ext.sc", b"+ String { shout { } }"),
    ("Classes/Broken.sc", b"Broken {"),
    ("Classes/help/Example.sc", b"Example { }"),
)


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_check_an_artifact(client):
    before = ClassDefinition.objects.count()

    response = check(client, QUARK)

    assert response.status_code == 200
    body = response.json()
    assert [
        (c["name"], c["superclass"], c["extension"], c["platform"])
        for c in body["classes"]
    ] == [
        ("Foo", "Bar", False, ""),
        ("Lin", "", False, "linux"),
        ("String", "", True, ""),
    ]
    assert body["classes"][0]["methods"] == [{"name": "play", "class_method": False}]
    assert body["classes"][0]["line"] == 1
    assert body["errors"] == [
        {"path": "Classes/Broken.sc", "message": "unmatched '{' at the end of the file"}
    ]
    assert [(c["package"], c["classes"], c["methods"]) for c in body["conflicts"]] == [
        (
            "alpha",
            ["Foo"],
            [{"class": "String", "name": "shout", "class_method": False}],
        ),
        (
            "beta-lib",
            ["Foo"],
            [{"class": "String", "name": "shout", "class_method": False}],
        ),
    ]
    # nothing is stored
    assert ClassDefinition.objects.count() == before


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_the_own_package_doesnt_conflict(client):
    body = check(client, QUARK, package="ALPHA").json()

    assert [c["package"] for c in body["conflicts"]] == ["beta-lib"]


@pytest.mark.django_db
def test_check_needs_a_token_with_the_publish_scope(client):
    assert client.post(CHECK, QUARK, content_type="application/gzip").status_code == 401
    read_only = create_access_token(UserFactory.create(), days=1, scope="read")
    assert check(client, QUARK, token=read_only).status_code == 403


@pytest.mark.django_db
def test_check_refuses_broken_archives(client):
    response = check(client, b"not a tar.gz")

    assert response.status_code == 400
    assert response.json() == {"detail": "The file is not a valid .tar.gz archive."}


@pytest.mark.django_db
def test_check_has_the_upload_limit(client, settings):
    settings.MAGNET_MAX_UPLOAD_SIZE = 10

    assert check(client, QUARK).status_code == 413


@pytest.mark.django_db
def test_classes_api_is_in_the_schema(client):
    schema = client.get("/api/schema/").text
    for path in [
        "/api/v1/classes/",
        "/api/v1/classes/check/",
        "/api/v1/packages/{name}/conflicts/",
    ]:
        assert path in schema
