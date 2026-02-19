from uuid import UUID
from urllib.parse import urlparse


_SYNC_TOKEN_PATH_PREFIX = "/sync/"
_SYNC_TOKEN_DATA_PREFIX = "data:,"


def _build_sync_token(calendar_id, revision):
    return f"{_SYNC_TOKEN_DATA_PREFIX}{calendar_id}/{revision}"


def _sync_token_revision_from_parts(parts, expected_calendar_id):
    if len(parts) != 2:
        return None
    try:
        token_calendar_id = UUID(parts[0])
        revision = int(parts[1])
    except (ValueError, TypeError):
        return None
    if revision < 0 or token_calendar_id != expected_calendar_id:
        return None
    return revision


def _parse_sync_token_for_calendar(token, calendar, valid_sync_token_error_response):
    value = (token or "").strip()
    if not value:
        return None, valid_sync_token_error_response()

    if value.startswith(_SYNC_TOKEN_DATA_PREFIX):
        payload = value[len(_SYNC_TOKEN_DATA_PREFIX) :]
        revision = _sync_token_revision_from_parts(payload.split("/"), calendar.id)
        if revision is None:
            return None, valid_sync_token_error_response()
        return revision, None

    parsed = urlparse(value)
    path = parsed.path or ""
    if (
        parsed.params
        or parsed.query
        or parsed.fragment
        or not path.startswith(_SYNC_TOKEN_PATH_PREFIX)
    ):
        return None, valid_sync_token_error_response()

    revision = _sync_token_revision_from_parts(
        path.removeprefix(_SYNC_TOKEN_PATH_PREFIX).split("/"),
        calendar.id,
    )
    if revision is None:
        return None, valid_sync_token_error_response()

    return revision, None
