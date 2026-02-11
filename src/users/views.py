from django.http import HttpResponse
from django.views import View


class DisplayTokenView(View):
    """Simple view to display the generated oauth token via
    redirection. This should be styled at some point."""

    def get(self, request):
        return HttpResponse(self.request.GET.get("code", "No token received"))
