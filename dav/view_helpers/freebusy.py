from dav.views.helpers import freebusy as _impl
import sys

sys.modules[__name__] = _impl
