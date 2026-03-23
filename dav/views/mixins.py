from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

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


class GuidToUsernameDispatchMixin:
    def guid_to_username(self, guid: str) -> str | None:
        from dav.views.helpers.identity import _dav_username_for_guid

        return _dav_username_for_guid(guid)

    def dispatch(self, request, *args, **kwargs):
        guid = kwargs.get("guid")
        if not isinstance(guid, str):
            return HttpResponse(status=404)

        username = self.guid_to_username(guid)
        if username is None:
            return HttpResponse(status=404)

        kwargs = dict(kwargs)
        kwargs.pop("guid", None)
        kwargs["username"] = username
        return getattr(super(), "dispatch")(request, *args, **kwargs)
