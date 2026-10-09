"""Bounded Codex location cursors over native main-thread history.

Cursors are not signed credentials or grants. The bound owner session is
rechecked separately, and every decoded native cut/position is revalidated.
This module does not open a Registry, read a body, or persist cursor state.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from cpn.rpnh.registry.event_store import CanonicalView
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.main_thread_history import MainThreadHistoryAnchor, MainThreadReadCut
from cpn.rpnh.registry.models import VersionRef

MAX_CURSOR_BYTES = 4096
PREFIX = "rpnh-history-v1."
INITIAL_VIEW = "initial-unbound"


def codex_thread_id(stable_session_id: str) -> str:
    """Render the existing session UUID in the stock client's UUID grammar.

    Only this Codex wire representation changes. The native Registry and the
    generic ses_ presentation identity keep exactly the same 128-bit value.
    Old ses_ wire IDs/cursors are not aliases or bearer capabilities.
    """
    if not isinstance(stable_session_id, str) or not re.fullmatch(r"ses_[a-f0-9]{32}", stable_session_id):
        raise ValueError("invalid stable session identity")
    return str(UUID(hex=stable_session_id[4:]))


def public_id(thread_id: str, ordinal: int, kind: str) -> str:
    """Use native ordinal, including gaps, and the existing live ID namespace."""
    return str(uuid5(NAMESPACE_URL, f"rpnh:codex:{thread_id}:{ordinal}:active-{kind}"))


def page_limit(value: object) -> int:
    if value is None:
        return 25
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError("history limit must be a uint32")
    return max(1, min(100, value))


def _fields(value: object, names: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("invalid history cursor")
    return value


def _text(value: object, maximum: int = 256) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError("invalid history cursor")
    return value


def _integer(value: object, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= 0x7FFFFFFFFFFFFFFF:
        raise ValueError("invalid history cursor")
    return value


def _ref(value: object, kind: str) -> VersionRef:
    raw = _fields(value, {"entity_type", "logical_id", "version_id"})
    if raw["entity_type"] != kind:
        raise ValueError("invalid history cursor")
    return VersionRef(kind,
                      TypedId.parse(_text(raw["logical_id"]), expected="resource"),
                      TypedId.parse(_text(raw["version_id"]), expected="resource_version"))


def _wire_ref(ref: VersionRef) -> dict:
    return {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id),
            "version_id": str(ref.version_id)}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid history cursor")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class HistoryCursor:
    thread_id: str
    cut: MainThreadReadCut
    query: str
    order: str
    items_view: str | None
    turn_filter: VersionRef | None
    anchor: MainThreadHistoryAnchor | None

    def encode(self) -> str:
        cut = self.cut
        anchor = self.anchor
        value = {
            "version": 1, "threadId": self.thread_id,
            "query": self.query, "order": self.order, "itemsView": self.items_view,
            "turnFilter": None if self.turn_filter is None else _wire_ref(self.turn_filter),
            "cut": {"taskId": str(cut.task_id), "branchId": cut.branch_id,
                    "ordinal": cut.view.through_ordinal,
                    "eventId": str(cut.boundary_event_id), "threadRef": _wire_ref(cut.thread_ref)},
            "anchor": None if anchor is None else {
                "turnRef": _wire_ref(anchor.turn_ref), "ordinal": anchor.turn_ordinal,
                "itemIndex": anchor.item_index, "inclusive": anchor.inclusive},
        }
        if anchor is not None and (anchor.cut != cut or anchor.query != self.query
                                   or anchor.order != self.order or anchor.turn_filter != self.turn_filter):
            raise ValueError("history anchor binding differs")
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                         allow_nan=False).encode("ascii")
        token = PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        if len(token) > MAX_CURSOR_BYTES:
            raise ValueError("history cursor exceeds bound")
        if self.decode(token) != self:
            raise ValueError("invalid history cursor")
        return token

    @classmethod
    def decode(cls, value: object) -> "HistoryCursor":
        try:
            if (not isinstance(value, str) or len(value) > MAX_CURSOR_BYTES
                    or not value.startswith(PREFIX)):
                raise ValueError("invalid")
            encoded = value[len(PREFIX):]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
                raise ValueError("invalid")
            raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != encoded:
                raise ValueError("invalid")
            data = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid")))
            _fields(data, {"version", "threadId", "query", "order", "itemsView", "turnFilter", "cut", "anchor"})
            if type(data["version"]) is not int or data["version"] != 1:
                raise ValueError("invalid")
            query, order, view = data["query"], data["order"], data["itemsView"]
            if query not in ("turns", "items") or order not in ("asc", "desc"):
                raise ValueError("invalid")
            if (query == "turns" and view not in ("summary", "full", "notLoaded", INITIAL_VIEW)
                    or query == "items" and view is not None):
                raise ValueError("invalid")
            source = _fields(data["cut"], {"taskId", "branchId", "ordinal", "eventId", "threadRef"})
            cut = MainThreadReadCut(
                TypedId.parse(_text(source["taskId"]), expected="task"), _text(source["branchId"]),
                CanonicalView(_integer(source["ordinal"])),
                TypedId.parse(_text(source["eventId"]), expected="event"),
                _ref(source["threadRef"], "main_thread/v1"))
            turn_filter = None if data["turnFilter"] is None else _ref(data["turnFilter"], "main_turn/v1")
            if query == "turns" and turn_filter is not None:
                raise ValueError("invalid")
            anchor = None
            if data["anchor"] is not None:
                position = _fields(data["anchor"], {"turnRef", "ordinal", "itemIndex", "inclusive"})
                index = position["itemIndex"]
                if (type(position["inclusive"]) is not bool
                        or query == "turns" and index is not None
                        or query == "items" and (type(index) is not int or index not in (0, 1))):
                    raise ValueError("invalid")
                anchor = MainThreadHistoryAnchor(
                    cut, query, order, turn_filter, _ref(position["turnRef"], "main_turn/v1"),
                    _integer(position["ordinal"]), index, position["inclusive"])
            if view == INITIAL_VIEW and (order != "desc" or anchor is not None and not anchor.inclusive):
                raise ValueError("invalid")
            return cls(_text(data["threadId"]), cut, query, order, view, turn_filter, anchor)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
            raise ValueError("invalid history cursor") from exc

    def bind(self, *, thread_id: str, query: str, order: str, items_view: str | None) -> None:
        if (self.thread_id != thread_id or self.query != query or self.order != order
                or self.items_view not in (items_view, INITIAL_VIEW)):
            raise ValueError("history cursor query differs")


def entry_anchor(cut, entry, *, query, order, turn_filter=None, inclusive=False):
    return MainThreadHistoryAnchor(
        cut, query, order, turn_filter, entry.turn_ref,
        entry.ordinal if query == "turns" else entry.turn_ordinal,
        None if query == "turns" else entry.item_index, inclusive)


def object_item_cursor_id(value: object, turn_id: object) -> str:
    """Validate the 0.161 new-query anchor, not an opaque continuation."""
    raw = _fields(value, {"type", "itemId"})
    if raw["type"] != "item":
        raise ValueError("invalid history cursor")
    _text(turn_id)
    return _text(raw["itemId"])


def object_item_anchor(cut, turn, thread_id, item_id, *, order):
    """Resolve only existing committed safe slots; no body read or new store."""
    matches = [entry for entry in turn.items
               if entry.item_index in (0, 1) and public_id(
                   thread_id, turn.ordinal, ("user", "agent")[entry.item_index]) == item_id]
    if len(matches) != 1:
        raise ValueError("history itemId is not present in this committed turn")
    return entry_anchor(cut, matches[0], query="items", order=order,
                        turn_filter=turn.turn_ref, inclusive=False)
