from urllib.parse import urlparse


def collection_marker(path):
    trimmed = path.strip("/")
    if not trimmed:
        return ""
    return f"{trimmed}/"


def split_filename_path(filename):
    clean = filename.strip("/")
    if not clean:
        return "", ""
    parts = [part for part in clean.split("/") if part]
    parent = "/".join(parts[:-1])
    leaf = parts[-1]
    return parent, leaf


def destination_filename_from_header(destination, username, slug):
    if not destination:
        return None

    parsed = urlparse(destination)
    destination_path = parsed.path if parsed.scheme else destination
    destination_path = (destination_path or "").strip()

    prefixes = (
        f"/dav/calendars/{username}/{slug}/",
        f"/dav/calendars/users/{username}/{slug}/",
    )
    for prefix in prefixes:
        if destination_path.startswith(prefix):
            return destination_path[len(prefix) :]
    return None


def is_ical_resource(filename, content_type):
    if filename.lower().endswith(".ics"):
        return True
    if content_type and "text/calendar" in content_type.lower():
        return True
    return False


def normalize_content_type(raw):
    value = (raw or "application/octet-stream").strip()
    return value.replace("; ", ";")


def normalize_href_path(href):
    parsed = urlparse(href)
    path = parsed.path if parsed.scheme else href
    if not path.startswith("/"):
        path = f"/{path}"
    return path
