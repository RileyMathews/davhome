from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from unittest.mock import patch

from dav.views.dav_root import DavRootView
from dav.views.principals_collection import PrincipalsCollectionView


class RootAndPrincipalsCollectionViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="owner", password="pw-test-12345")

    def setUp(self):
        self.factory = RequestFactory()

    def _root_view(self, request):
        view = DavRootView()
        view.request = request
        return view

    def _principals_view(self, request):
        view = PrincipalsCollectionView()
        view.request = request
        return view

    def test_dav_root_options_get_head(self):
        request = self.factory.get("/dav/")
        request.user = self.user
        view = self._root_view(request)

        options = view.options(request)
        self.assertEqual(options.status_code, 204)
        self.assertIn("PROPFIND", options["Allow"])

        get_response = view.get(request)
        self.assertEqual(get_response.status_code, 200)

        head_response = view.head(request)
        self.assertEqual(head_response.status_code, 200)

    def test_dav_root_propfind_parse_error_none_and_depth_one(self):
        request = self.factory.generic(
            "PROPFIND",
            "/dav/",
            data="",
            content_type="application/xml",
            HTTP_DEPTH="1",
        )
        request.user = self.user

        with patch(
            "dav.views.dav_root._parse_propfind_payload",
            return_value=(None, HttpResponse(status=422)),
        ):
            parse_error = self._root_view(request).propfind(request)
        self.assertEqual(parse_error.status_code, 422)

        with patch(
            "dav.views.dav_root._parse_propfind_payload", return_value=(None, None)
        ):
            parse_none = self._root_view(request).propfind(request)
        self.assertEqual(parse_none.status_code, 400)

        with patch(
            "dav.views.dav_root._parse_propfind_payload",
            return_value=({"mode": "allprop", "requested": None}, None),
        ):
            ok = self._root_view(request).propfind(request)
        self.assertEqual(ok.status_code, 207)
        body = ok.content.decode("utf-8")
        self.assertIn("/dav/", body)
        self.assertIn(f"/dav/principals/users/{self.user.username}/", body)
        self.assertIn(f"/dav/calendars/users/{self.user.username}/", body)

    def test_principals_collection_options_get_head_propfind(self):
        request = self.factory.generic("GET", "/dav/principals/")
        request.user = self.user
        view = self._principals_view(request)

        options = view.options(request)
        self.assertEqual(options.status_code, 204)

        get_response = view.get(request)
        self.assertEqual(get_response.status_code, 200)

        head_response = view.head(request)
        self.assertEqual(head_response.status_code, 200)

        with patch(
            "dav.views.principals_collection._parse_propfind_payload",
            return_value=(None, HttpResponse(status=422)),
        ):
            parse_error = view.propfind(request)
        self.assertEqual(parse_error.status_code, 422)

        with patch(
            "dav.views.principals_collection._parse_propfind_payload",
            return_value=(None, None),
        ):
            parse_none = view.propfind(request)
        self.assertEqual(parse_none.status_code, 400)

        with patch(
            "dav.views.principals_collection._parse_propfind_payload",
            return_value=({"mode": "allprop", "requested": None}, None),
        ):
            ok = view.propfind(request)
        self.assertEqual(ok.status_code, 207)
        self.assertIn("/dav/principals/", ok.content.decode("utf-8"))
