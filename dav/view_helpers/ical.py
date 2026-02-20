from dav.views.helpers import ical as _impl
import sys

sys.modules[__name__] = _impl
