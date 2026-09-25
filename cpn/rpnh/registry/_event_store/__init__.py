"""Internal functional implementation modules for :mod:`registry.event_store`.

The public facade remains ``registry.event_store.EventStore``.  These modules
contain behavior only and never construct or retain a store of their own.
"""

__all__ = (
    "accounting",
    "backend",
    "commit",
    "net_lineage",
    "proposal",
    "provenance",
    "queries",
    "views",
)
