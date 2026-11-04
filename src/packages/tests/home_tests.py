"""The home page: counts and the columns of new, random and updated packages."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from packages.tests.factories import ReleaseFactory


def published(name, version, days_ago, **kwargs):
    return ReleaseFactory.create(
        project__name=name,
        version=version,
        published_at=timezone.now() - timedelta(days=days_ago),
        **kwargs,
    )


@pytest.fixture
def packages():
    """alpha is the oldest package but was updated last; beta is the newest."""
    alpha = published("alpha", "1.0.0", days_ago=30, summary="First of all").project
    ReleaseFactory.create(
        project=alpha,
        version="1.1.0",
        published_at=timezone.now(),
        summary="Alpha, updated",
    )
    published("gamma", "0.1.0", days_ago=10)
    published("beta", "2.0.0", days_ago=5)
    # drafts aren't public
    ReleaseFactory.create(project__name="delta", version="1.0.0")


def charts(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    return {
        title: [project.name for project in projects]
        for title, projects in response.context["charts"]
    }


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_home_shows_new_random_and_updated_packages(client):
    result = charts(client)

    assert result["New packages"] == ["beta", "gamma", "alpha"]
    assert result["Just updated"] == ["alpha", "beta", "gamma"]
    assert sorted(result["Random packages"]) == ["alpha", "beta", "gamma"]


@pytest.mark.django_db
@pytest.mark.usefixtures("packages")
def test_tiles_show_the_latest_final_version(client):
    page = client.get(reverse("home")).text

    assert "Alpha, updated" in page and "1.1.0" in page
    assert "First of all" not in page
    for title in ["New packages", "Random packages", "Just updated"]:
        assert title in page


@pytest.mark.django_db
def test_columns_hold_six_packages(client):
    for n in range(8):
        published(f"p{n}", "1.0.0", days_ago=n)

    assert all(len(names) == 6 for names in charts(client).values())


@pytest.mark.django_db
def test_no_columns_without_packages(client):
    assert "New packages" not in client.get(reverse("home")).text
