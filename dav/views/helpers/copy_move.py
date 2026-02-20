from dataclasses import dataclass
from urllib.parse import quote

from django.http import HttpResponse
from django.utils import timezone

from dav.core import paths as core_paths


@dataclass(frozen=True)
class _CopyMoveDestination:
    marker: str | None
    lookup: str


def _remap_uid_for_copied_object(uid, target_filename):
    if uid.startswith("collection:"):
        return f"collection:{target_filename}"
    if uid.startswith("dav:"):
        return f"dav:{target_filename}"
    return uid


def _parse_destination(destination, source_is_collection):
    destination_clean = destination.strip("/")
    if not destination_clean:
        return None
    if source_is_collection:
        marker = core_paths.collection_marker(destination)
        return _CopyMoveDestination(marker=marker, lookup=marker)
    return _CopyMoveDestination(marker=None, lookup=destination_clean)


def _resolve_source(writable, filename):
    source = writable.calendar_objects.filter(filename=filename).first()
    if source is None and filename.endswith("/"):
        source = writable.calendar_objects.filter(
            filename=core_paths.collection_marker(filename)
        ).first()
    return source


def _destination_entries(writable, source_is_collection, destination):
    if source_is_collection:
        return list(
            writable.calendar_objects.filter(
                filename__startswith=destination.marker
            ).values(
                "filename",
                "uid",
            )
        )

    destination_obj = writable.calendar_objects.filter(
        filename=destination.lookup
    ).first()
    if destination_obj is None:
        return []
    return [{"filename": destination_obj.filename, "uid": destination_obj.uid}]


def _source_entries(
    writable, source, source_is_collection, source_marker, is_move, depth
):
    if not source_is_collection:
        return [source]
    if not is_move and depth == "0":
        return [source]
    return list(writable.calendar_objects.filter(filename__startswith=source_marker))


def _target_filename_for_entry(
    entry_filename, source_is_collection, source_marker, destination
):
    if not source_is_collection:
        return destination.lookup
    marker_value = source_marker or ""
    suffix = entry_filename[len(marker_value) :]
    return f"{destination.marker}{suffix}"


def _location_header(username, slug, filename):
    escaped_filename = quote(filename, safe="/")
    return f"/dav/calendars/{username}/{slug}/{escaped_filename}"


def _delete_destination_entries(writable, source_is_collection, destination):
    if source_is_collection:
        writable.calendar_objects.filter(
            filename__startswith=destination.marker
        ).delete()
        return
    writable.calendar_objects.filter(filename=destination.lookup).delete()


def copy_or_move_calendar_object(
    writable,
    request,
    username,
    slug,
    filename,
    next_revision,
    is_move,
    collection_exists,
    create_calendar_change,
    dav_common_headers,
):
    source = _resolve_source(writable, filename)
    if source is None:
        return HttpResponse(status=404)

    raw_destination = core_paths.destination_filename_from_header(
        request.headers.get("Destination"),
        username,
        slug,
    )
    if raw_destination is None:
        return HttpResponse(status=400)

    source_is_collection = source.filename.endswith("/")
    source_marker = source.filename if source_is_collection else None
    destination = _parse_destination(raw_destination, source_is_collection)
    if destination is None:
        return HttpResponse(status=403)

    if source.filename == destination.lookup:
        return HttpResponse(status=204)

    destination_parent, _ = core_paths.split_filename_path(destination.lookup)
    if not collection_exists(writable, destination_parent):
        return HttpResponse(status=409)

    overwrite = request.headers.get("Overwrite", "T").strip().upper() != "F"
    destination_entries = _destination_entries(
        writable,
        source_is_collection,
        destination,
    )

    if destination_entries and not overwrite:
        return HttpResponse(status=412)

    copy_depth = (request.headers.get("Depth") or "infinity").strip().lower()
    source_entries = _source_entries(
        writable,
        source,
        source_is_collection,
        source_marker,
        is_move,
        copy_depth,
    )

    now = timezone.now()

    if destination_entries and overwrite:
        _delete_destination_entries(writable, source_is_collection, destination)
        for item in destination_entries:
            create_calendar_change(
                writable,
                next_revision,
                item["filename"],
                item["uid"],
                True,
            )
            next_revision += 1

    copied_filenames = []
    for entry in source_entries:
        target_filename = _target_filename_for_entry(
            entry.filename,
            source_is_collection,
            source_marker,
            destination,
        )
        target_uid = _remap_uid_for_copied_object(entry.uid, target_filename)

        writable.calendar_objects.create(
            uid=target_uid,
            filename=target_filename,
            etag=entry.etag,
            ical_blob=entry.ical_blob,
            content_type=entry.content_type,
            size=entry.size,
            dead_properties=(entry.dead_properties or {}).copy(),
            updated_at=now,
        )
        create_calendar_change(
            writable,
            next_revision,
            target_filename,
            target_uid,
            False,
        )
        next_revision += 1
        copied_filenames.append(target_filename)

    if is_move:
        for entry in source_entries:
            create_calendar_change(
                writable,
                next_revision,
                entry.filename,
                entry.uid,
                True,
            )
            next_revision += 1
        if source_is_collection:
            writable.calendar_objects.filter(
                filename__startswith=source_marker
            ).delete()
        else:
            source.delete()

    writable.updated_at = now
    writable.save(update_fields=["updated_at"])

    response = HttpResponse(status=204 if destination_entries else 201)
    if copied_filenames:
        response["Location"] = _location_header(username, slug, copied_filenames[0])
    return dav_common_headers(response)
