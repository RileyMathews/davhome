from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse

from .mixins import DavHeaderMixin, DavOptionsMixin, DavPropfindResponseMixin


@method_decorator(csrf_exempt, name="dispatch")
class DavView(DavHeaderMixin, DavOptionsMixin, DavPropfindResponseMixin, View):
    http_method_names = [
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "trace",
        "propfind",
        "proppatch",
        "report",
        "mkcalendar",
        "mkcol",
        "copy",
        "move",
    ]

    def dispatch(self, request, *args, **kwargs):
        if request.method.upper() not in self.get_allowed_methods():
            response = View.dispatch(self, request, *args, **kwargs)
            return self.apply_dav_headers(response)

        auth_response = self.authenticate_dav_request(request)
        if auth_response is not None:
            return self.apply_dav_headers(auth_response)

        response = View.dispatch(self, request, *args, **kwargs)
        return self.apply_dav_headers(response)

    def authenticate_dav_request(self, request):
        # DAV clients expect OPTIONS to succeed without auth challenge.
        if request.method == "OPTIONS":
            return None

        if request.user.is_authenticated:
            return None

        # DAV auth failures must be HTTP 401 + WWW-Authenticate, not redirects.
        response = HttpResponse(status=401)
        response["WWW-Authenticate"] = 'Basic realm="davhome"'
        return response
