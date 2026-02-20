from django.test import SimpleTestCase

from dav.view_helpers.copy_move import (
    _location_header,
    _parse_destination,
    _remap_uid_for_copied_object,
    _target_filename_for_entry,
)


class CopyMoveHelperTests(SimpleTestCase):
    def test_remap_uid_for_collection_and_dav_prefixes(self):
        self.assertEqual(
            _remap_uid_for_copied_object("collection:source/", "dest/"),
            "collection:dest/",
        )
        self.assertEqual(
            _remap_uid_for_copied_object("dav:source.ics", "dest.ics"),
            "dav:dest.ics",
        )
        self.assertEqual(
            _remap_uid_for_copied_object("plain-uid", "dest.ics"),
            "plain-uid",
        )

    def test_parse_destination_for_resource_and_collection(self):
        resource_destination = _parse_destination(
            "target.ics", source_is_collection=False
        )
        self.assertIsNotNone(resource_destination)
        assert resource_destination is not None
        self.assertIsNone(resource_destination.marker)
        self.assertEqual(resource_destination.lookup, "target.ics")

        collection_destination = _parse_destination(
            "folder/",
            source_is_collection=True,
        )
        self.assertIsNotNone(collection_destination)
        assert collection_destination is not None
        self.assertEqual(collection_destination.marker, "folder/")
        self.assertEqual(collection_destination.lookup, "folder/")

    def test_parse_destination_rejects_empty_path(self):
        self.assertIsNone(_parse_destination("/", source_is_collection=False))

    def test_target_filename_for_entry(self):
        destination = _parse_destination("copied-folder/", source_is_collection=True)
        self.assertIsNotNone(destination)
        assert destination is not None
        self.assertEqual(
            _target_filename_for_entry(
                entry_filename="source-folder/item.ics",
                source_is_collection=True,
                source_marker="source-folder/",
                destination=destination,
            ),
            "copied-folder/item.ics",
        )

        resource_destination = _parse_destination(
            "copy.ics", source_is_collection=False
        )
        self.assertIsNotNone(resource_destination)
        assert resource_destination is not None
        self.assertEqual(
            _target_filename_for_entry(
                entry_filename="ignored.ics",
                source_is_collection=False,
                source_marker=None,
                destination=resource_destination,
            ),
            "copy.ics",
        )

    def test_location_header_escapes_filename(self):
        self.assertEqual(
            _location_header("user01", "litmus", "folder/a b.ics"),
            "/dav/calendars/user01/litmus/folder/a%20b.ics",
        )
