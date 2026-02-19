from types import SimpleNamespace
from uuid import uuid4

from django.test import SimpleTestCase

from dav.view_helpers import identity, sync_tokens


class ViewHelpersIdentitySyncTokenTests(SimpleTestCase):
    def test_identity_helpers(self):
        self.assertEqual(
            identity._dav_guid_for_username("user01"),
            "10000000-0000-0000-0000-000000000001",
        )
        self.assertIsNone(identity._dav_guid_for_username("alice"))
        self.assertEqual(
            identity._dav_username_for_guid("10000000-0000-0000-0000-000000000001"),
            "user01",
        )
        self.assertIsNone(
            identity._dav_username_for_guid("10000000-0000-0000-0000-000000000000")
        )
        self.assertIsNone(
            identity._dav_username_for_guid("10000000-0000-0000-0000-000000000100")
        )

        user_guid = SimpleNamespace(username="user01")
        user_plain = SimpleNamespace(username="alice")
        self.assertEqual(
            identity._principal_href_for_user(user_guid),
            "/dav/principals/__uids__/10000000-0000-0000-0000-000000000001/",
        )
        self.assertEqual(
            identity._principal_href_for_user(user_plain),
            "/dav/principals/users/alice/",
        )
        self.assertEqual(
            identity._calendar_home_href_for_user(user_guid),
            "/dav/calendars/__uids__/10000000-0000-0000-0000-000000000001/",
        )
        self.assertEqual(
            identity._calendar_home_href_for_user(user_plain),
            "/dav/calendars/users/alice/",
        )

    def test_sync_token_helpers(self):
        calendar_id = uuid4()
        calendar = SimpleNamespace(id=calendar_id)
        error_calls = []

        def valid_sync_token_error_response():
            error_calls.append(True)
            return {"error": "invalid"}

        self.assertEqual(
            sync_tokens._build_sync_token(calendar_id, 3), f"data:,{calendar_id}/3"
        )
        self.assertIsNone(
            sync_tokens._sync_token_revision_from_parts(["x"], calendar_id)
        )
        self.assertIsNone(
            sync_tokens._sync_token_revision_from_parts(
                [str(calendar_id), "-1"], calendar_id
            )
        )
        self.assertIsNone(
            sync_tokens._sync_token_revision_from_parts(
                [str(uuid4()), "1"], calendar_id
            )
        )
        self.assertEqual(
            sync_tokens._sync_token_revision_from_parts(
                [str(calendar_id), "1"], calendar_id
            ),
            1,
        )

        revision, error = sync_tokens._parse_sync_token_for_calendar(
            "",
            calendar,
            valid_sync_token_error_response,
        )
        self.assertIsNone(revision)
        self.assertEqual(error, {"error": "invalid"})

        revision, error = sync_tokens._parse_sync_token_for_calendar(
            f"data:,{calendar_id}/2",
            calendar,
            valid_sync_token_error_response,
        )
        self.assertEqual(revision, 2)
        self.assertIsNone(error)

        revision, error = sync_tokens._parse_sync_token_for_calendar(
            "data:,bad-token",
            calendar,
            valid_sync_token_error_response,
        )
        self.assertIsNone(revision)
        self.assertEqual(error, {"error": "invalid"})

        revision, error = sync_tokens._parse_sync_token_for_calendar(
            f"https://example.test/sync/{calendar_id}/9",
            calendar,
            valid_sync_token_error_response,
        )
        self.assertEqual(revision, 9)
        self.assertIsNone(error)

        revision, error = sync_tokens._parse_sync_token_for_calendar(
            f"https://example.test/sync/{calendar_id}/9?x=1",
            calendar,
            valid_sync_token_error_response,
        )
        self.assertIsNone(revision)
        self.assertEqual(error, {"error": "invalid"})
        self.assertGreaterEqual(len(error_calls), 3)
