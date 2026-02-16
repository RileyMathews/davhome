import base64

from django.contrib.auth import authenticate


def get_dav_user(request):
    if request.user.is_authenticated:
        return request.user

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Basic "):
        return None

    encoded = auth_header.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return None

    return authenticate(request, username=username, password=password)


def unauthorized_response():
    from django.http import HttpResponse

    response = HttpResponse(status=401)
    response["WWW-Authenticate"] = 'Basic realm="davhome"'
    return response
