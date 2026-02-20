from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .mixins import DavAuthMixin, DavHeaderMixin, DavOptionsMixin


@method_decorator(csrf_exempt, name="dispatch")
class DavView(DavHeaderMixin, DavAuthMixin, DavOptionsMixin, View):
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
        auth_response = self.authenticate_dav_request(request)
        if auth_response is not None:
            return self.apply_dav_headers(auth_response)

        response = super().dispatch(request, *args, **kwargs)
        return self.apply_dav_headers(response)
