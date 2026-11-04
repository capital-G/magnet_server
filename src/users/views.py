from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST

from packages.models import Project, ProjectMember, Release
from users.models import CiToken
from users.oauth import create_ci_token


class DisplayTokenView(View):
    """Simple view to display the generated oauth token via
    redirection. This should be styled at some point."""

    def get(self, request):
        return HttpResponse(self.request.GET.get("code", "No token received"))


class CiTokenForm(forms.Form):
    name = forms.CharField(max_length=100)
    days = forms.TypedChoiceField(
        label="Expires after",
        choices=[(30, "30 days"), (90, "90 days"), (365, "365 days")],
        coerce=int,
        initial=90,
    )


def render_tokens(request, form: CiTokenForm, new_token: str | None = None):
    """The tokens page; htmx requests get only its `tokens` partial."""
    template = "users/tokens.html"
    return render(
        request,
        f"{template}#tokens" if request.htmx else template,
        {
            "form": form,
            "new_token": new_token,
            "tokens": CiToken.objects.filter(
                access_token__user=request.user
            ).select_related("access_token"),
            "now": timezone.now(),
        },
    )


@login_required
def tokens_view(request) -> HttpResponse:
    """Create and list the user's tokens for scripts and CI. A new token is shown
    only in the answer to its creation."""
    if request.method != "POST":
        return render_tokens(request, CiTokenForm())
    form = CiTokenForm(request.POST)
    if not form.is_valid():
        return render_tokens(request, form)
    token = create_ci_token(
        request.user, form.cleaned_data["name"], form.cleaned_data["days"]
    )
    return render_tokens(request, CiTokenForm(), new_token=token.token)


@require_POST
@login_required
def revoke_token_view(request, pk: int) -> HttpResponse:
    ci_token = get_object_or_404(CiToken, pk=pk, access_token__user=request.user)
    # deletes the access token, and with it the name
    ci_token.access_token.revoke()
    return render_tokens(request, CiTokenForm())


def profile_view(request, username: str) -> HttpResponse:
    """The public page of a user: the packages they own or maintain. Packages with
    only drafts are listed only for members of their team."""
    user = get_object_or_404(
        get_user_model(), username__iexact=username, is_active=True
    )
    visible = Q(project__in=Project.objects.public().values("pk"))
    if request.user.is_authenticated:
        visible |= Q(project__in=Project.objects.of_user(request.user).values("pk"))
    memberships = (
        ProjectMember.objects.filter(visible, user=user)
        .select_related("project")
        .prefetch_related(
            Prefetch("project__releases", queryset=Release.objects.final())
        )
        .order_by("project__normalized_name")
    )
    return render(
        request,
        "users/profile.html",
        {"profile_user": user, "memberships": memberships},
    )
