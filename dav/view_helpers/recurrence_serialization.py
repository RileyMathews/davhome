from dav.views.helpers import recurrence_serialization as _impl
import sys

sys.modules[__name__] = _impl
