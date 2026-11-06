"""The manifest schema for editors (`/schemas/magnet.v1.schema.json`)."""

import json

from django.urls import reverse

from packages.archive import SCHEMA_PATH


def test_schema_is_served(client):
    response = client.get(reverse("manifest-schema"))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response["Access-Control-Allow-Origin"] == "*"
    schema = json.loads(response.content)
    assert schema == json.loads(SCHEMA_PATH.read_text())
    # the URL the CLI writes into `#:schema` is the schema's `$id`
    assert schema["$id"] == (
        "https://magnet.supercollider.online" + reverse("manifest-schema")
    )


def test_schema_is_read_only(client):
    assert client.post(reverse("manifest-schema")).status_code == 405
