from __future__ import annotations

from collections.abc import Sequence

from django.http import HttpResponse

from dav.common import _require_dav_user


class DavAuthMixin:
    require_dav_auth = True
    dav_user = None

    def authenticate_dav_request(self, request):
        if not self.require_dav_auth:
            self.dav_user = getattr(request, "user", None)
            return None

        user, auth_response = _require_dav_user(request)
        self.dav_user = user
        return auth_response


class DavHeaderMixin:
    def apply_dav_headers(self, response):
        from dav.common import _dav_common_headers

        return _dav_common_headers(response)


class DavOptionsMixin:
    allowed_methods: Sequence[str] | None = None

    def get_allowed_methods(self) -> list[str]:
        if self.allowed_methods is not None:
            return list(self.allowed_methods)

        methods = []
        for method in getattr(self, "http_method_names", []):
            if hasattr(self, method):
                methods.append(method.upper())
        return methods

    def options(self, request, *args, **kwargs):
        response = HttpResponse(status=204)
        response["Allow"] = ", ".join(self.get_allowed_methods())
        return response
