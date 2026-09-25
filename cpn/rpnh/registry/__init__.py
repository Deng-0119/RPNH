"""Registry mechanical identities and data; no default execution imports.

Transaction/execution migration is in progress. Importing this package never
opens a Registry, acquires a writer epoch, or loads optional components.
"""
from .identities import TypedId
from .models import VersionRef

__all__ = ("TypedId", "VersionRef")
