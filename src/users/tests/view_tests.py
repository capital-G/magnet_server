import pytest
from django.urls import reverse
from pytest_django.asserts import assertContains


@pytest.mark.django_db
def test_oauth_token_view_no_token(client):
    url = reverse("display-token")
    response = client.get(url)
    assert response.status_code == 200
    assertContains(response, "No token received")


@pytest.mark.django_db
def test_oauth_token_view_with_token(client):
    token = "1234567-89"
    url = f"{reverse('display-token')}?code={token}"
    response = client.get(url)
    assert response.status_code == 200
    assertContains(response, token)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name", ["account_login", "account_signup", "account_reset_password"]
)
def test_account_pages_use_the_site_layout(client, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code == 200
    assertContains(response, 'class="navbar')
    assertContains(response, 'class="box"')
    assertContains(response, 'class="input"')
    assertContains(response, 'class="button is-link"')


@pytest.mark.django_db
def test_account_management_pages_use_the_site_layout(admin_client):
    for url_name in ["account_email", "account_change_password", "account_logout"]:
        response = admin_client.get(reverse(url_name))
        assert response.status_code == 200
        assertContains(response, 'class="navbar')
        assertContains(response, 'class="box"')


@pytest.mark.django_db
def test_failed_login_shows_the_error(client):
    response = client.post(
        reverse("account_login"),
        {"login": "nobody@example.com", "password": "wrong"},
    )
    assert response.status_code == 200
    assertContains(response, 'class="notification is-danger"')
