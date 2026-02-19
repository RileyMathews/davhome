from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SyncChange:
    revision: int
    filename: str
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class SyncSelectedItem:
    revision: int | None
    filename: str
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class SyncSelection:
    source: str
    next_revision: int
    items: tuple[SyncSelectedItem, ...]
    invalid_token: bool = False


def _latest_changes_by_filename(changes):
    latest_by_filename = {}
    for change in changes:
        latest_by_filename[change.filename] = change
    return latest_by_filename


def _limit_items(items, limit):
    if limit is None:
        return list(items)
    return list(items)[:limit]


def select_sync_collection_items(
    *,
    token_revision,
    latest_revision,
    changes,
    current_filenames,
    limit,
):
    if token_revision is not None and token_revision > latest_revision:
        return SyncSelection(
            source="invalid-token-future-revision",
            next_revision=latest_revision,
            items=(),
            invalid_token=True,
        )

    if token_revision is None:
        if changes:
            latest_by_filename = _latest_changes_by_filename(changes)
            selected_changes = sorted(
                latest_by_filename.values(),
                key=lambda change: change.revision,
            )
            selected_changes = _limit_items(selected_changes, limit)
            next_revision = latest_revision
            if selected_changes:
                next_revision = selected_changes[-1].revision
            return SyncSelection(
                source="initial-latest-by-filename",
                next_revision=next_revision,
                items=tuple(
                    SyncSelectedItem(
                        revision=change.revision,
                        filename=change.filename,
                        is_deleted=change.is_deleted,
                    )
                    for change in selected_changes
                ),
            )

        selected_filenames = _limit_items(current_filenames, limit)
        return SyncSelection(
            source="initial-current-objects",
            next_revision=latest_revision,
            items=tuple(
                SyncSelectedItem(
                    revision=None,
                    filename=filename,
                    is_deleted=False,
                )
                for filename in selected_filenames
            ),
        )

    changed_after_token = [
        change for change in changes if change.revision > token_revision
    ]
    latest_by_filename = _latest_changes_by_filename(changed_after_token)
    ordered_changes = sorted(
        latest_by_filename.values(),
        key=lambda change: change.revision,
    )
    ordered_changes = _limit_items(ordered_changes, limit)
    next_revision = latest_revision
    if ordered_changes:
        next_revision = ordered_changes[-1].revision
    return SyncSelection(
        source="incremental-latest-by-filename",
        next_revision=next_revision,
        items=tuple(
            SyncSelectedItem(
                revision=change.revision,
                filename=change.filename,
                is_deleted=change.is_deleted,
            )
            for change in ordered_changes
        ),
    )
