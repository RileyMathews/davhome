from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .mixins import GuidToUsernameDispatchMixin
from .principal import PrincipalView


@method_decorator(csrf_exempt, name="dispatch")
class PrincipalUidView(GuidToUsernameDispatchMixin, PrincipalView):
    pass
