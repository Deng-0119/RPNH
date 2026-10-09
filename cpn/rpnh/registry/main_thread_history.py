"""Native, refs-only main-thread history values. None of these grant access.

These values are deliberately not a public transcript DTO. A content field is
an exact reference into a committed main-turn object, not permission to expose
its arbitrary JSON or follow its child-resource references. Public renderers
must retain their own existing disclosure rules and access checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from .event_store import CanonicalView
from .identities import TypedId
from .models import VersionRef


@dataclass(frozen=True, slots=True)
class MainThreadReadCut:
    """Re-readable source identity and exact canonical boundary, not a grant."""

    task_id: TypedId
    branch_id: str
    view: CanonicalView
    boundary_event_id: TypedId
    thread_ref: VersionRef


@dataclass(frozen=True, slots=True)
class MainThreadHistoryItem:
    """One immutable field slot; no raw prompt or body is returned."""

    turn_ref: VersionRef
    turn_ordinal: int
    item_index: int
    field: Literal["user_input", "answer"]


@dataclass(frozen=True, slots=True)
class MainThreadHistoryTurn:
    ordinal: int
    turn_ref: VersionRef
    items: tuple[MainThreadHistoryItem, ...]


@dataclass(frozen=True, slots=True)
class MainThreadHistoryChildLink:
    """At-cut launch fact, without granting child Registry traversal."""

    link_ref: VersionRef
    origin_turn_ref: VersionRef | None
    task_control_id: str
    task_kind: str
    state: str


@dataclass(frozen=True, slots=True)
class MainThreadHistoryProjection:
    """Committed history only. Current TaskControl annotations are excluded.

    `child_links` contains only immutable Registry facts at this cut. It cannot
    stand in for the current TaskControl fallback used by display_history.
    """

    cut: MainThreadReadCut
    state: str
    next_turn_ordinal: int
    active_turn_ref: VersionRef | None
    latest_turn_ref: VersionRef | None
    latest_committed_turn_ref: VersionRef | None
    turns: tuple[MainThreadHistoryTurn, ...]
    child_links: tuple[MainThreadHistoryChildLink, ...]


@dataclass(frozen=True, slots=True)
class MainThreadHistoryAnchor:
    """Native query position. Transport encoding and authorization are separate.

    Initial anchors may include their target; continuation anchors exclude it.
    Changing the cut, query, order, or filter requires a fresh query.
    """

    cut: MainThreadReadCut
    query: Literal["turns", "items"]
    order: Literal["asc", "desc"]
    turn_filter: VersionRef | None
    turn_ref: VersionRef
    turn_ordinal: int
    item_index: int | None
    inclusive: bool = False


_Entry = TypeVar("_Entry", MainThreadHistoryTurn, MainThreadHistoryItem)


@dataclass(frozen=True, slots=True)
class MainThreadHistoryPage(Generic[_Entry]):
    cut: MainThreadReadCut
    entries: tuple[_Entry, ...]
    next_anchor: MainThreadHistoryAnchor | None


__all__ = (
    "MainThreadReadCut", "MainThreadHistoryItem", "MainThreadHistoryTurn",
    "MainThreadHistoryChildLink", "MainThreadHistoryProjection",
    "MainThreadHistoryAnchor", "MainThreadHistoryPage",
)
