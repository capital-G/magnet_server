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
