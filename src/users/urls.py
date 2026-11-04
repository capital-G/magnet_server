from django.urls import path

from users.views import (
    DisplayTokenView,
    my_packages_view,
    profile_view,
    revoke_token_view,
    settings_view,
    tokens_view,
)

urlpatterns = [
    path(r"display-token", DisplayTokenView.as_view(), name="display-token"),
    path("tokens", tokens_view, name="tokens"),
    path("packages", my_packages_view, name="my-packages"),
    path("settings", settings_view, name="profile-settings"),
    path("tokens/<int:pk>/revoke", revoke_token_view, name="revoke-token"),
    # after the other routes, their names are reserved usernames
    path("<str:username>", profile_view, name="profile"),
]
