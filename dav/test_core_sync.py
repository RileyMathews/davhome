from django.test import SimpleTestCase

from dav.core import sync as core_sync


class DavCoreSyncTests(SimpleTestCase):
    def test_initial_selects_latest_change_per_filename(self):
        changes = [
            core_sync.SyncChange(revision=1, filename="a.ics", is_deleted=False),
            core_sync.SyncChange(revision=2, filename="b.ics", is_deleted=False),
            core_sync.SyncChange(revision=3, filename="a.ics", is_deleted=True),
        ]
        selection = core_sync.select_sync_collection_items(
            token_revision=None,
            latest_revision=3,
            changes=changes,
            current_filenames=["a.ics", "b.ics"],
            limit=None,
        )

        self.assertFalse(selection.invalid_token)
        self.assertEqual(selection.source, "initial-latest-by-filename")
        self.assertEqual(selection.next_revision, 3)
        self.assertEqual(
            [item.filename for item in selection.items],
            ["b.ics", "a.ics"],
        )
        self.assertEqual([item.is_deleted for item in selection.items], [False, True])

    def test_initial_without_changes_uses_current_objects(self):
        selection = core_sync.select_sync_collection_items(
            token_revision=None,
            latest_revision=0,
            changes=[],
            current_filenames=["a.ics", "b.ics"],
            limit=1,
        )

        self.assertFalse(selection.invalid_token)
        self.assertEqual(selection.source, "initial-current-objects")
        self.assertEqual(selection.next_revision, 0)
        self.assertEqual(len(selection.items), 1)
        self.assertEqual(selection.items[0].filename, "a.ics")
        self.assertIsNone(selection.items[0].revision)
        self.assertFalse(selection.items[0].is_deleted)

    def test_incremental_selects_latest_changes_after_token(self):
        changes = [
            core_sync.SyncChange(revision=1, filename="a.ics", is_deleted=False),
            core_sync.SyncChange(revision=2, filename="a.ics", is_deleted=True),
            core_sync.SyncChange(revision=3, filename="b.ics", is_deleted=False),
        ]
        selection = core_sync.select_sync_collection_items(
            token_revision=1,
            latest_revision=3,
            changes=changes,
            current_filenames=[],
            limit=None,
        )

        self.assertFalse(selection.invalid_token)
        self.assertEqual(selection.source, "incremental-latest-by-filename")
        self.assertEqual(selection.next_revision, 3)
        self.assertEqual(
            [
                (item.revision, item.filename, item.is_deleted)
                for item in selection.items
            ],
            [(2, "a.ics", True), (3, "b.ics", False)],
        )

    def test_future_token_is_marked_invalid(self):
        selection = core_sync.select_sync_collection_items(
            token_revision=10,
            latest_revision=3,
            changes=[],
            current_filenames=[],
            limit=None,
        )

        self.assertTrue(selection.invalid_token)
        self.assertEqual(selection.source, "invalid-token-future-revision")
        self.assertEqual(selection.next_revision, 3)
        self.assertEqual(selection.items, ())
