from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .mixins import GuidToUsernameDispatchMixin
from .calendar_collection import CalendarCollectionView


@method_decorator(csrf_exempt, name="dispatch")
class CalendarCollectionUidView(GuidToUsernameDispatchMixin, CalendarCollectionView):
    pass
