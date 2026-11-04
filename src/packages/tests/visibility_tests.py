"""Drafts are only visible to the owners and maintainers of their package."""

import pytest
from django.urls import reverse
from pytest_django.asserts import assertContains, assertNotContains

from packages.models import Release
from packages.tests.factories import AssetFactory, ProjectMemberFactory, ReleaseFactory
from users.tests.factories import UserFactory


@pytest.fixture
def published():
    """A package with a final version 1.0.0 and a draft 2.0.0."""
    release = ReleaseFactory.create(
        project__name="alpha", version="1.0.0", summary="Final summary", final=True
    )
    ReleaseFactory.create(
        project=release.project, version="2.0.0", summary="Draft summary"
    )
    return release.project


@pytest.fixture
def draft_only():
    """A new package with nothing but a draft."""
    return ReleaseFactory.create(project__name="beta", version="1.0.0").project


def detail_url(project, version=None):
    url = reverse("project-detail", args=[project.slug])
    return f"{url}?version={version}" if version else url


@pytest.mark.django_db
def test_api_lists_only_final_releases(client, published, draft_only):
    projects = client.get("/api/v1/projects/").json()
    assert [p["name"] for p in projects] == ["alpha"]
    assert projects[0]["summary"] == "Final summary"

    detail = client.get(f"/api/v1/projects/{published.slug}/").json()
    assert [r["version"] for r in detail["releases"]] == ["1.0.0"]
    assert client.get(f"/api/v1/projects/{draft_only.slug}/").status_code == 404


@pytest.mark.django_db
def test_api_shows_the_kinds_of_the_latest_final_version(client, published):
    ReleaseFactory.create(
        project=published, version="1.1.0", kinds=["quark", "lepton"], final=True
    )

    [project] = client.get("/api/v1/projects/").json()
    assert project["kinds"] == ["quark", "lepton"]
    detail = client.get(f"/api/v1/projects/{published.slug}/").json()
    assert detail["kinds"] == ["quark", "lepton"]
    assert [(r["version"], r["kinds"]) for r in detail["releases"]] == [
        ("1.1.0", ["quark", "lepton"]),
        ("1.0.0", ["quark"]),
    ]


@pytest.fixture
def quark_and_lepton(published):
    """1.1.0 of `published` adds a lepton to the quark of 1.0.0."""
    release = ReleaseFactory.create(
        project=published, version="1.1.0", kinds=["quark", "lepton"], final=True
    )
    for kind, abi, platform in [
        ("source", "any", "any"),
        ("quark", "any", "any"),
        ("lepton", "api3_0", "macos_arm64"),
    ]:
        AssetFactory.create(release=release, kind=kind, abi=abi, platform=platform)
    return release


def tag(kind, classes="tag"):
    return f'<span class="{classes}">{kind}</span>'


@pytest.mark.django_db
def test_pages_show_the_kinds_of_each_version(client, published, quark_and_lepton):
    page = client.get(detail_url(published))
    # the header and the versions table
    assertContains(page, tag("lepton", "tag is-link is-light"), html=True)
    assertContains(page, tag("lepton", "tag is-small"), count=1, html=True)
    assertContains(page, tag("quark", "tag is-small"), count=2, html=True)
    # the artifacts table
    for kind in ["source", "quark", "lepton"]:
        assertContains(page, f"<td>{kind}</td>", count=1, html=True)

    page = client.get(detail_url(published, "1.0.0"))
    assertContains(page, tag("quark", "tag is-link is-light"), html=True)
    assertNotContains(page, tag("lepton", "tag is-link is-light"), html=True)

    # the kinds of the latest final version, not of the draft 2.0.0
    listing = client.get(reverse("projects-list"))
    assertContains(listing, tag("quark"), count=1, html=True)
    assertContains(listing, tag("lepton"), count=1, html=True)

    lepton = quark_and_lepton.assets.get(kind="lepton")
    page = client.get(reverse("asset-detail", args=[lepton.uuid]))
    assertContains(page, tag("lepton", "tag is-link is-light"), html=True)
    assertContains(page, "Lepton artifact for alpha v1.1.0")


@pytest.mark.django_db
def test_admin_filters_versions_by_kind(admin_client, published, quark_and_lepton):
    url = reverse("admin:packages_release_changelist")

    def versions(query):
        response = admin_client.get(url + query)
        return [str(r.version) for r in response.context["cl"].result_list]

    assert versions("?kind=lepton") == ["1.1.0"]
    assert versions("?kind=quark") == ["1.1.0", "1.0.0"]
    assert versions("?kind=lepton&state=all") == ["1.1.0"]


@pytest.mark.django_db
def test_list_and_search_show_only_public_projects(client, published, draft_only):
    content = client.get(reverse("projects-list")).text
    assert "alpha" in content and "beta" not in content
    assert "Final summary" in content and "Draft summary" not in content

    def search(q):
        # the htmx partial, without the search field which repeats `q`
        return client.get(
            reverse("projects-list"), {"q": q}, headers={"HX-Request": "true"}
        ).text

    assert "alpha" in search("final summ")
    assert "alpha" not in search("draft summ")
    assert "beta" not in search("beta")


@pytest.mark.django_db
def test_home_counts_only_final_releases(client, published, draft_only):
    assert "1 package and 1 release available" in client.get(reverse("home")).text


@pytest.mark.django_db
def test_others_dont_see_drafts(client, published, draft_only):
    client.force_login(UserFactory.create())

    assert client.get(detail_url(draft_only)).status_code == 404
    page = client.get(detail_url(published, "2.0.0")).text
    # the newest final release instead of the draft
    assert "v1.0.0" in page and "2.0.0" not in page
    assert "draft" not in page


@pytest.mark.django_db
def test_owners_and_maintainers_see_drafts(client, published, draft_only):
    maintainer = UserFactory.create()
    ProjectMemberFactory.create(project=draft_only, user=maintainer)

    client.force_login(published.owners().get())
    page = client.get(detail_url(published, "2.0.0")).text
    assert "v2.0.0" in page and "draft" in page
    assert reverse("delete-draft", args=["alpha", "2.0.0"]) in page

    client.force_login(maintainer)
    page = client.get(detail_url(draft_only)).text
    assert "v1.0.0" in page and "draft" in page
    # nothing to install yet
    assert "magnet add beta" not in page


@pytest.mark.django_db
def test_assets_of_drafts_are_hidden(client, draft_only):
    asset = AssetFactory.create(release=draft_only.releases.get())
    url = reverse("asset-detail", args=[asset.uuid])

    assert client.get(url).status_code == 404
    client.force_login(draft_only.owners().get())
    assert client.get(url).status_code == 200


@pytest.mark.django_db
def test_delete_draft_on_the_website(
    client, published, django_capture_on_commit_callbacks
):
    url = reverse("delete-draft", args=["alpha", "2.0.0"])

    assert client.post(url).status_code == 302  # to the login
    client.force_login(UserFactory.create())
    assert client.post(url).status_code == 403
    client.force_login(published.owners().get())
    assert client.get(url).status_code == 405
    assert client.post(
        reverse("delete-draft", args=["alpha", "1.0.0"])
    ).status_code == (409)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url, headers={"HX-Request": "true"})

    assert response.headers["HX-Redirect"] == detail_url(published)
    assert list(Release.objects.values_list("version", flat=True)) == ["1.0.0"]


@pytest.mark.django_db
def test_admin_lists_final_releases_by_default(admin_client, published):
    url = reverse("admin:packages_release_changelist")

    def versions(query=""):
        response = admin_client.get(url + query)
        return [str(r.version) for r in response.context["cl"].result_list]

    assert versions() == ["1.0.0"]
    assert versions("?state=draft") == ["2.0.0"]
    assert sorted(versions("?state=all")) == ["1.0.0", "2.0.0"]


@pytest.mark.django_db
def test_readme_is_sanitized(client):
    release = ReleaseFactory.create(
        project__name="alpha",
        final=True,
        readme='# Alpha\n<script>alert("readme")</script>\n[x](javascript:alert(1))',
    )

    page = client.get(detail_url(release.project)).text

    assert "<h1>Alpha</h1>" in page
    assert '<script>alert("readme")' not in page and "javascript:" not in page
