from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .mixins import GuidToUsernameDispatchMixin
from .calendar_home import CalendarHomeView


@method_decorator(csrf_exempt, name="dispatch")
class CalendarHomeUidView(GuidToUsernameDispatchMixin, CalendarHomeView):
    pass
