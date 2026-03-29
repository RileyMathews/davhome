from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__all__ = [
    "CalendarCollectionUidView",
    "CalendarCollectionView",
    "CalendarHomeUidView",
    "CalendarHomeView",
    "CalendarObjectUidView",
    "CalendarObjectView",
    "DavRootView",
    "PrincipalUidView",
    "PrincipalView",
    "PrincipalsCollectionView",
]

_VIEW_MODULES = {
    "CalendarCollectionUidView": ".calendar_collection_uid",
    "CalendarCollectionView": ".calendar_collection",
    "CalendarHomeUidView": ".calendar_home_uid",
    "CalendarHomeView": ".calendar_home",
    "CalendarObjectUidView": ".calendar_object_uid",
    "CalendarObjectView": ".calendar_object",
    "DavRootView": ".dav_root",
    "PrincipalUidView": ".principal_uid",
    "PrincipalView": ".principal",
    "PrincipalsCollectionView": ".principals_collection",
}

if TYPE_CHECKING:
    from .calendar_collection import CalendarCollectionView
    from .calendar_collection_uid import CalendarCollectionUidView
    from .calendar_home import CalendarHomeView
    from .calendar_home_uid import CalendarHomeUidView
    from .calendar_object import CalendarObjectView
    from .calendar_object_uid import CalendarObjectUidView
    from .dav_root import DavRootView
    from .principal import PrincipalView
    from .principal_uid import PrincipalUidView
    from .principals_collection import PrincipalsCollectionView


def __getattr__(name: str):
    module_name = _VIEW_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
