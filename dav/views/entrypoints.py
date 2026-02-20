"""DAV entrypoints shim: thin alias surface for URL imports."""

from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt

from . import (
    CalendarCollectionUidView,
    CalendarCollectionView,
    CalendarHomeUidView,
    CalendarHomeView,
    CalendarObjectUidView,
    CalendarObjectView,
    CalendarsCollectionView,
    CalendarsUidsCollectionView,
    CalendarsUsersCollectionView,
    DavRootView,
    PrincipalUidView,
    PrincipalView,
    PrincipalsCollectionView,
    PrincipalsUsersCollectionView,
)


@csrf_exempt
def well_known_caldav(request):
    return redirect("/dav/", permanent=False)


dav_root = DavRootView.as_view()

principals_collection_view = PrincipalsCollectionView.as_view()
principals_users_collection_view = PrincipalsUsersCollectionView.as_view()

principal_view = PrincipalView.as_view()
principal_users_view = principal_view
principal_uid_view = PrincipalUidView.as_view()

calendar_home_view = CalendarHomeView.as_view()
calendar_home_users_view = calendar_home_view
calendar_home_uid_view = CalendarHomeUidView.as_view()

calendars_collection_view = CalendarsCollectionView.as_view()
calendars_uids_collection_view = CalendarsUidsCollectionView.as_view()
calendars_users_collection_view = CalendarsUsersCollectionView.as_view()

calendar_collection_view = CalendarCollectionView.as_view()
calendar_collection_users_view = calendar_collection_view
calendar_collection_uid_view = CalendarCollectionUidView.as_view()

calendar_object_view = CalendarObjectView.as_view()
calendar_object_users_view = calendar_object_view
calendar_object_uid_view = CalendarObjectUidView.as_view()
