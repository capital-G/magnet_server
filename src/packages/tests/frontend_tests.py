import re

import pytest
from django.core.management import call_command
from django.urls import reverse

from packages.tests.factories import ReleaseFactory

# a <script> or <link> which loads from another host
EXTERNAL_ASSET = re.compile(r"<(script|link)[^>]+(src|href)=\"(https?:)?//")


@pytest.fixture
def manifest_static(settings, tmp_path):
    """Use the static files setup of the deployments (docker, prod), where
    templates only render after a `collectstatic` run."""
    settings.STATIC_ROOT = tmp_path / "staticfiles"
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
    call_command("collectstatic", interactive=False, verbosity=0)


@pytest.mark.django_db
@pytest.mark.usefixtures("manifest_static")
def test_pages_render_with_vendored_assets(client):
    release = ReleaseFactory(project__name="alpha", version="1.0.0", final=True)

    for url in [
        reverse("home"),
        reverse("project-detail", args=[release.project.slug]),
    ]:
        response = client.get(url)
        assert response.status_code == 200

        content = response.content.decode()
        assert EXTERNAL_ASSET.search(content) is None
        # hashed names from the manifest
        for asset in ["bulma", "htmx", "alpine"]:
            assert re.search(rf"/static/vendor/{asset}\.min\.[0-9a-f]{{12}}\.", content)
