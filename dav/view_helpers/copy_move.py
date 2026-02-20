from dav.views.helpers import copy_move as _impl
import sys

sys.modules[__name__] = _impl
