"""Registry mechanical identities and data; no default execution imports.

Transaction/execution migration is in progress. Importing this package never
opens a Registry, acquires a writer epoch, or loads optional components.
"""
from .identities import TypedId
from .models import VersionRef

__all__ = ("TypedId", "VersionRef")

_OBSERVER_EXPORTS = (
    'ObserverReadScope', 'RegistryReadObserverContext', 'issue_observer_access',
    'revoke_observer_access', 'observer_access_schema_data',
)
__all__ += _OBSERVER_EXPORTS


def __getattr__(name):
    if name not in _OBSERVER_EXPORTS:
        raise AttributeError(name)
    from . import observer_access
    value = getattr(observer_access, name)
    globals()[name] = value
    return value
