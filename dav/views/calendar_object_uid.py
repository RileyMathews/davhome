from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .mixins import GuidToUsernameDispatchMixin
from .calendar_object import CalendarObjectView


@method_decorator(csrf_exempt, name="dispatch")
class CalendarObjectUidView(GuidToUsernameDispatchMixin, CalendarObjectView):
    pass
