import pytest
from django.views.generic.dates import timezone_today

from users.models import UserProfile
from users.tests.factories import UserProfileFactory


@pytest.mark.django_db
def test_user_profile() -> None:
    user_profile: UserProfile = UserProfileFactory()  # type: ignore
    assert user_profile.created.date() == timezone_today()
