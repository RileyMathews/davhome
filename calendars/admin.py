from django.contrib import admin

from .models import Calendar, CalendarObject, CalendarShare


@admin.register(Calendar)
class CalendarAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "timezone", "updated_at")
    search_fields = ("name", "slug", "owner__username")


@admin.register(CalendarShare)
class CalendarShareAdmin(admin.ModelAdmin):
    list_display = ("calendar", "user", "role", "updated_at")
    search_fields = ("calendar__name", "user__username")


@admin.register(CalendarObject)
class CalendarObjectAdmin(admin.ModelAdmin):
    list_display = ("calendar", "filename", "uid", "updated_at")
    search_fields = ("calendar__name", "filename", "uid")
