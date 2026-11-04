"""The legal notice and privacy policy (`/legal`)."""

import pytest
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains


@pytest.mark.django_db
def test_footer_links_the_legal_page(client):
    assertContains(client.get(reverse("home")), f'href="{reverse("legal")}"')


def test_legal_page_names_the_operator(client, settings):
    settings.MAGNET_LEGAL_NAME = "Jane Doe"
    settings.MAGNET_LEGAL_EMAIL = "contact@example.org"
    settings.MAGNET_LEGAL_HOSTING = "Example Hosting GmbH, Germany"
    settings.MAGNET_API_BASE_URL = "https://magnet.example.org/"
    settings.MAGNET_DOCS_BASE_URL = "https://package-docs.example.org/"

    response = client.get(reverse("legal"))

    assertContains(response, "run by Jane Doe")
    assertContains(response, 'href="mailto:contact@example.org"')
    assertContains(response, "a server of Example Hosting GmbH, Germany.")
    assertContains(response, "magnet.example.org")
    assertContains(response, "package-docs.example.org")


def test_legal_page_without_name_and_hosting(client, settings):
    settings.MAGNET_LEGAL_NAME = ""
    settings.MAGNET_LEGAL_HOSTING = ""

    response = client.get(reverse("legal"))

    assertNotContains(response, "run by")
    assertContains(response, "magnet runs on a server.")
    assertContains(response, 'href="mailto:mail@supercollider.online"')
