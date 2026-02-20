from __future__ import annotations

from collections.abc import Sequence

from django.http import HttpResponse


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
