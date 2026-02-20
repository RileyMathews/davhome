import logging
import base64
import binascii

from django.contrib.auth import authenticate


logger = logging.getLogger("dav.audit")


def _client_ip(request):
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def _reason_code_for_status(status_code):
    reason_codes = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "unsupported_method",
        409: "conflict",
        412: "precondition_failed",
        415: "unsupported_media_type",
        501: "not_implemented",
    }
    return reason_codes.get(status_code, "http_error")


def _allow_values(raw_allow):
    if not raw_allow:
        return None
    return [value.strip() for value in raw_allow.split(",") if value.strip()]


class DavAuditRejectLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith("/dav/"):
            return response
        if response.status_code < 400:
            return response
        if response.status_code == 405:
            allowed = _allow_values(response.headers.get("Allow"))
            logger.warning(
                "dav_method_not_allowed reason_code=%s method=%s path=%s status=%s allowed=%r user_agent=%r content_type=%r content_length=%r depth=%r destination=%r overwrite=%r if_none_match=%r if_match=%r remote_ip=%r body=%r extra=%r",
                "unsupported_method",
                request.method,
                request.path,
                response.status_code,
                allowed,
                request.headers.get("User-Agent"),
                request.META.get("CONTENT_TYPE") or request.content_type,
                request.META.get("CONTENT_LENGTH"),
                request.headers.get("Depth"),
                request.headers.get("Destination"),
                request.headers.get("Overwrite"),
                request.headers.get("If-None-Match"),
                request.headers.get("If-Match"),
                _client_ip(request),
                request.body,
                {},
            )
            return response

        logger.warning(
            "dav_reject reason_code=%s method=%s path=%s status=%s user_agent=%r content_type=%r content_length=%r depth=%r destination=%r overwrite=%r if_none_match=%r if_match=%r remote_ip=%r allow=%r body=%r",
            _reason_code_for_status(response.status_code),
            request.method,
            request.path,
            response.status_code,
            request.headers.get("User-Agent"),
            request.META.get("CONTENT_TYPE") or request.content_type,
            request.META.get("CONTENT_LENGTH"),
            request.headers.get("Depth"),
            request.headers.get("Destination"),
            request.headers.get("Overwrite"),
            request.headers.get("If-None-Match"),
            request.headers.get("If-Match"),
            _client_ip(request),
            response.headers.get("Allow"),
            request.body,
        )
        return response


class DavBasicAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/dav/") and request.method != "OPTIONS":
            user = self._resolve_dav_user(request)
            if user is not None:
                request.user = user

        return self.get_response(request)

    def _resolve_dav_user(self, request):
        if request.user.is_authenticated:
            return request.user

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Basic "):
            return None

        encoded = auth_header.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None

        return authenticate(request, username=username, password=password)
