from dav.views.helpers import identity as _impl
import sys

sys.modules[__name__] = _impl
