from django.urls import path

from . import views

app_name = "dav"

urlpatterns = [
    path("", views.dav_root, name="root"),
    path("principals/<str:username>/", views.principal_view, name="principal"),
    path("calendars/<str:username>/", views.calendar_home_view, name="calendar-home"),
    path(
        "calendars/<str:username>/<slug:slug>/",
        views.calendar_collection_view,
        name="calendar-collection",
    ),
    path(
        "calendars/<str:username>/<slug:slug>/<path:filename>",
        views.calendar_object_view,
        name="calendar-object",
    ),
]
