from django.http import HttpRequest, HttpResponse

from dav.core.contracts import ProtocolError, WritePrecondition


def _split_header_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def write_precondition_from_request(
    request: HttpRequest,
    existing_etag: str | None,
) -> WritePrecondition:
    if_match = _split_header_values(request.headers.get("If-Match"))
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match is not None:
        if_none_match = if_none_match.strip()
    if if_none_match == "":
        if_none_match = None

    return WritePrecondition(
        if_match=if_match,
        if_none_match=if_none_match,
        existing_etag=existing_etag,
    )


def protocol_error_to_http_response(error: ProtocolError) -> HttpResponse:
    return HttpResponse(status=error.http_status)
