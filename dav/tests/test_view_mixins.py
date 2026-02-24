from django.http import HttpResponse
from django.test import SimpleTestCase

from dav.views.mixins import DavHeaderMixin, DavOptionsMixin


class _HeaderView(DavHeaderMixin):
    pass


class _ExplicitAllowedMethodsView(DavOptionsMixin):
    allowed_methods = ("REPORT", "PROPFIND")


class _IntrospectedAllowedMethodsView(DavOptionsMixin):
    http_method_names = ["get", "post", "trace"]

    def get(self, request, *args, **kwargs):
        return HttpResponse(status=200)

    def post(self, request, *args, **kwargs):
        return HttpResponse(status=200)


class DavViewMixinsTests(SimpleTestCase):
    def test_apply_dav_headers_sets_dav_header(self):
        response = HttpResponse(status=200)

        wrapped_response = _HeaderView().apply_dav_headers(response)

        self.assertIs(wrapped_response, response)
        self.assertEqual(
            wrapped_response["DAV"],
            "1, calendar-access, calendar-query-extended",
        )

    def test_get_allowed_methods_uses_explicit_allowed_methods(self):
        methods = _ExplicitAllowedMethodsView().get_allowed_methods()

        self.assertEqual(methods, ["REPORT", "PROPFIND"])

    def test_get_allowed_methods_introspects_http_method_handlers(self):
        methods = _IntrospectedAllowedMethodsView().get_allowed_methods()

        self.assertEqual(methods, ["GET", "POST"])

    def test_options_uses_allowed_methods_for_allow_header(self):
        response = _ExplicitAllowedMethodsView().options(request=None)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Allow"], "REPORT, PROPFIND")
