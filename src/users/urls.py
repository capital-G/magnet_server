from django.urls import path

from users.views import DisplayTokenView, revoke_token_view, tokens_view

urlpatterns = [
    path(r"display-token", DisplayTokenView.as_view(), name="display-token"),
    path("tokens", tokens_view, name="tokens"),
    path("tokens/<int:pk>/revoke", revoke_token_view, name="revoke-token"),
]
