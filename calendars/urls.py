from django.urls import path

from . import views

app_name = "calendars"

urlpatterns = [
    path("", views.calendar_list, name="list"),
    path("new/", views.calendar_create, name="create"),
    path("<uuid:calendar_id>/", views.calendar_edit, name="edit"),
    path("<uuid:calendar_id>/delete/", views.calendar_delete, name="delete"),
    path("<uuid:calendar_id>/sharing/", views.calendar_sharing, name="sharing"),
    path("<uuid:calendar_id>/sharing/add/", views.calendar_share_add, name="share-add"),
    path(
        "<uuid:calendar_id>/sharing/<int:share_id>/update/",
        views.calendar_share_update,
        name="share-update",
    ),
    path(
        "<uuid:calendar_id>/sharing/<int:share_id>/delete/",
        views.calendar_share_delete,
        name="share-delete",
    ),
]
