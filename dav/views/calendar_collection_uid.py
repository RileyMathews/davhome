from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from dav.views.helpers.identity import _dav_username_for_guid

from .mixins import GuidToUsernameDispatchMixin
from .calendar_collection import CalendarCollectionView


@method_decorator(csrf_exempt, name="dispatch")
class CalendarCollectionUidView(GuidToUsernameDispatchMixin, CalendarCollectionView):
    def guid_to_username(self, guid: str) -> str | None:
        return _dav_username_for_guid(guid)
