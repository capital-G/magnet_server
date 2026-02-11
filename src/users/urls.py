from django.urls import path

from users.views import DisplayTokenView

urlpatterns = [
    path(r"display-token", DisplayTokenView.as_view(), name="display-token"),
]
