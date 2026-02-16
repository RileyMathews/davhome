from django.urls import path

from . import views

app_name = "dav"

urlpatterns = [
    path("", views.dav_root, name="root"),
    path("principals/", views.principals_collection_view, name="principals"),
    path(
        "principals/users/",
        views.principals_users_collection_view,
        name="principals-users",
    ),
    path(
        "principals/users/<str:username>/",
        views.principal_users_view,
        name="principal-user",
    ),
    path(
        "principals/__uids__/<str:guid>/",
        views.principal_uid_view,
        name="principal-uid",
    ),
    path("principals/<str:username>/", views.principal_view, name="principal"),
    path("calendars/", views.calendars_collection_view, name="calendars"),
    path(
        "calendars/__uids__/",
        views.calendars_uids_collection_view,
        name="calendars-uids",
    ),
    path(
        "calendars/users/",
        views.calendars_users_collection_view,
        name="calendars-users",
    ),
    path(
        "calendars/users/<str:username>/",
        views.calendar_home_users_view,
        name="calendar-home-users",
    ),
    path(
        "calendars/__uids__/<str:guid>/",
        views.calendar_home_uid_view,
        name="calendar-home-uid",
    ),
    path("calendars/<str:username>/", views.calendar_home_view, name="calendar-home"),
    path(
        "calendars/users/<str:username>/<slug:slug>/",
        views.calendar_collection_users_view,
        name="calendar-collection-users",
    ),
    path(
        "calendars/__uids__/<str:guid>/<slug:slug>/",
        views.calendar_collection_uid_view,
        name="calendar-collection-uid",
    ),
    path(
        "calendars/<str:username>/<slug:slug>/",
        views.calendar_collection_view,
        name="calendar-collection",
    ),
    path(
        "calendars/users/<str:username>/<slug:slug>/<path:filename>",
        views.calendar_object_users_view,
        name="calendar-object-users",
    ),
    path(
        "calendars/__uids__/<str:guid>/<slug:slug>/<path:filename>",
        views.calendar_object_uid_view,
        name="calendar-object-uid",
    ),
    path(
        "calendars/<str:username>/<slug:slug>/<path:filename>",
        views.calendar_object_view,
        name="calendar-object",
    ),
]
