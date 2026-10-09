"""Standalone Registry authority for the persistent interactive main thread.

The objects in this module are the authority.  Any JSON/UI transcript built
from them is only a projection and can always be rebuilt from Registry facts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal, Mapping, NamedTuple

from ._registry import _RegistryCore
from .event_store import CanonicalView, RegistryConflict
from .identities import TypedId
from .main_thread_history import (
    MainThreadHistoryAnchor,
    MainThreadHistoryChildLink,
    MainThreadHistoryItem,
    MainThreadHistoryPage,
    MainThreadHistoryProjection,
    MainThreadHistoryTurn,
    MainThreadReadCut,
)
from .models import PreparedObject, VersionRef
from .schema_catalog import canonical_json
from ..path_refs import relative_child_registry, resolve_child_registry


class MainThreadAuthorityError(RuntimeError):
    """The persisted main-thread authority is absent, corrupt, or mismatched."""


class ThreadTurnAdvance(NamedTuple):
    thread_ref: VersionRef
    turn_ref: VersionRef


class AttemptAttachment(NamedTuple):
    thread_ref: VersionRef
    turn_ref: VersionRef
    attempt_relative_path: str
    attempt_path: Path


class ChildRegistryLink(NamedTuple):
    link_ref: VersionRef
    registry_relative_path: str
    registry_path: Path


_MISSING = object()


def _stable_id(kind: str, *parts: object) -> TypedId:
    material = ":".join(str(part) for part in parts)
    return TypedId(  # type: ignore[arg-type]
        kind,
        uuid.uuid5(uuid.NAMESPACE_URL, f"rpnh:main-thread:{material}").hex,
    )


def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _parse_ref(value: Mapping[str, Any]) -> VersionRef:
    if set(value) != {"entity_type", "logical_id", "version_id"}:
        raise MainThreadAuthorityError("exact Registry ref is malformed")
    try:
        return VersionRef(
            str(value["entity_type"]),
            TypedId.parse(str(value["logical_id"])),
            TypedId.parse(str(value["version_id"])),
        )
    except (TypeError, ValueError) as exc:
        raise MainThreadAuthorityError("exact Registry ref is malformed") from exc


def _coerce_ref(value: VersionRef | Mapping[str, Any] | None) -> VersionRef | None:
    if value is None:
        return None
    if isinstance(value, VersionRef):
        return value
    if isinstance(value, Mapping):
        return _parse_ref(value)
    raise TypeError("optional authority ref must be an exact VersionRef")


def _exact_json_value(value: Any, *, label: str) -> Any:
    try:
        encoded = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} must be an exact JSON value") from exc


class MainThreadRegistry:
    """Own one persistent logical main-thread lineage in a Registry core."""

    CHILD_PATH_BASE_META = "main_thread_child_path_base"
    REGISTRY_ROOT_PATH_BASE = "registry_root/v1"
    LEGACY_SESSION_PATH_BASE = "session_root/v1"
    HISTORY_PAGE_MAX_ENTRIES = 100

    def __init__(
            self, core: _RegistryCore, *, session_root: Path | str,
            initialize_path_base: str | None = None,
    ) -> None:
        if not isinstance(core, _RegistryCore):
            raise TypeError("main-thread authority requires one Registry core")
        root = Path(session_root)
        if not root.is_dir():
            raise ValueError("declared main-thread session root must exist")
        self.core = core
        self.session_root = root.resolve(strict=True)
        stored = self.core.event_store.get_meta(self.CHILD_PATH_BASE_META)
        if initialize_path_base is not None:
            if initialize_path_base != self.REGISTRY_ROOT_PATH_BASE:
                raise ValueError("unknown main-thread child path base")
            stored = self.core.event_store.get_or_create_meta(
                self.CHILD_PATH_BASE_META, initialize_path_base)
        if stored is None:
            # Registries written before the marker used session-root paths.
            stored = self.LEGACY_SESSION_PATH_BASE
        if stored not in {
                self.REGISTRY_ROOT_PATH_BASE,
                self.LEGACY_SESSION_PATH_BASE,
        }:
            raise MainThreadAuthorityError(
                "main-thread child path base is unknown")
        self.child_path_base = stored
        self.child_path_root = (
            self.core.run_dir.resolve(strict=True)
            if stored == self.REGISTRY_ROOT_PATH_BASE
            else self.session_root)

    @staticmethod
    def _idempotency_key(command: str, key: str, material: Mapping[str, Any]) -> str:
        if not isinstance(key, str) or not key:
            raise TypeError("main-thread command requires a nonempty idempotency key")
        return canonical_json({
            "authority": "main_thread/v1",
            "command": command,
            "caller_idempotency_key": key,
            "material": dict(material),
        }).decode("ascii")

    def _read_registered_bytes(
            self, core: _RegistryCore, ref: VersionRef, *,
            expected_type: str | None = None,
            view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> tuple[Any, bytes]:
        if expected_type is not None and ref.entity_type != expected_type:
            raise MainThreadAuthorityError(
                f"expected {expected_type}, received {ref.entity_type}")
        try:
            if view is None:
                prepared = core.get_version(ref.version_id)
            else:
                row = core.event_store.object_row_for_view(view, ref.version_id)
                if row is None:
                    raise MainThreadAuthorityError(
                        "exact Registry object is not canonical at the read cut")
                if max_bytes is not None and row["size"] > max_bytes:
                    raise ValueError("registered payload exceeds reader byte bound")
                # Use the row admitted by this view, not get_version's ambient
                # positive memo. The ObjectStore still verifies its envelope,
                # exact locator, size, and bytes below.
                prepared = PreparedObject(
                    row["object_type"], TypedId.parse(row["logical_id"]),
                    TypedId.parse(row["version_id"]), row["size"],
                    row["media_type"], row["schema_ref"],
                    (TypedId.parse(row["producer_invocation_id"], expected="invocation")
                     if row["producer_invocation_id"] else None),
                    row["storage_locator"], json.loads(row["metadata_json"]),
                )
            if (prepared.object_type != ref.entity_type
                    or prepared.logical_id != ref.entity_id):
                raise MainThreadAuthorityError(
                    "exact Registry ref resolves to another object")
            core.catalog.validate_instance(
                prepared.object_type, category="object",
                instance=prepared.metadata)
            payload = core.object_store.read_registered(
                prepared, max_bytes=(
                    prepared.size if view is not None
                    else max_bytes))
        except MainThreadAuthorityError:
            raise
        except Exception as exc:
            raise MainThreadAuthorityError(
                f"exact Registry object is unavailable: {ref}") from exc
        return prepared, payload

    def _read_exact(
            self, core: _RegistryCore, ref: VersionRef, *,
            expected_type: str | None = None,
            view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> dict[str, Any]:
        prepared, payload = self._read_registered_bytes(
            core, ref, expected_type=expected_type, view=view,
            max_bytes=max_bytes)
        try:
            document = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MainThreadAuthorityError(
                "Registry authority object is not canonical JSON") from exc
        if document != dict(prepared.metadata):
            raise MainThreadAuthorityError(
                "Registry object bytes differ from registered authority")
        return document

    def _ordered_documents(
            self, core: _RegistryCore, object_type: str,
            *, view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> list[tuple[VersionRef, dict[str, Any]]]:
        rows = core.event_store.canonical_object_rows(
            object_type=object_type,
            through_ordinal=None if view is None else view.through_ordinal)
        try:
            event_ordinals = {}
            for row in rows:
                published_event_id = str(row["published_event_id"])
                publication = core.event_store.event_by_id(
                    TypedId.parse(published_event_id, expected="event"))
                if publication is None:
                    raise KeyError(published_event_id)
                event_ordinals[published_event_id] = publication.ordinal
            ordered = sorted(
                rows,
                key=lambda row: event_ordinals[str(row["published_event_id"])],
            )
        except KeyError as exc:
            raise MainThreadAuthorityError(
                f"{object_type} lacks its publication event") from exc
        result = []
        for row in ordered:
            ref = VersionRef(
                object_type,
                TypedId.parse(str(row["logical_id"])),
                TypedId.parse(str(row["version_id"])),
            )
            result.append((ref, self._read_exact(
                core, ref, view=view, max_bytes=max_bytes)))
        return result

    def _thread_versions(
            self, *, view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> list[tuple[VersionRef, dict[str, Any]]]:
        versions = self._ordered_documents(
            self.core, "main_thread/v1", view=view, max_bytes=max_bytes)
        if not versions:
            raise MainThreadAuthorityError("main-thread authority is missing")
        if len({ref.entity_id for ref, _document in versions}) != 1:
            raise MainThreadAuthorityError(
                "Registry contains multiple main-thread lineages")
        previous: VersionRef | None = None
        for ref, document in versions:
            if document.get("main_thread_ref") != _ref_payload(ref):
                raise MainThreadAuthorityError(
                    "main-thread version does not identify itself")
            expected = _ref_payload(previous) if previous is not None else None
            if document.get("predecessor_version_ref") != expected:
                raise MainThreadAuthorityError(
                    "main-thread predecessor chain is broken")
            previous = ref
        return versions

    def _turn_lineages(
            self, thread_id: TypedId, *, view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> list[list[tuple[VersionRef, dict[str, Any]]]]:
        grouped: dict[TypedId, list[tuple[VersionRef, dict[str, Any]]]] = {}
        for ref, document in self._ordered_documents(
                self.core, "main_turn/v1", view=view, max_bytes=max_bytes):
            if document.get("main_thread_id") != str(thread_id):
                raise MainThreadAuthorityError(
                    "main-turn belongs to another thread lineage")
            grouped.setdefault(ref.entity_id, []).append((ref, document))
        lineages = sorted(
            grouped.values(), key=lambda items: int(items[0][1]["ordinal"]))
        previous_turn: VersionRef | None = None
        for ordinal, lineage in enumerate(lineages, 1):
            if int(lineage[0][1]["ordinal"]) != ordinal:
                raise MainThreadAuthorityError(
                    "main-turn ordinals are not contiguous")
            previous_version: VersionRef | None = None
            accepted_predecessor = (
                _ref_payload(previous_turn) if previous_turn is not None else None)
            for index, (ref, document) in enumerate(lineage):
                if (document.get("main_turn_ref") != _ref_payload(ref)
                        or int(document.get("ordinal", 0)) != ordinal):
                    raise MainThreadAuthorityError(
                        "main-turn version identity or ordinal differs")
                expected_version = (
                    _ref_payload(previous_version)
                    if previous_version is not None else None)
                if document.get("previous_version_ref") != expected_version:
                    raise MainThreadAuthorityError(
                        "main-turn version predecessor chain is broken")
                if document.get("predecessor_turn_ref") != accepted_predecessor:
                    raise MainThreadAuthorityError(
                        "main-turn conversational predecessor chain is broken")
                expected_state = (
                    "accepted" if index == 0 else
                    "running" if index == 1 else document.get("state"))
                if document.get("state") != expected_state or index > 2:
                    raise MainThreadAuthorityError(
                        "main-turn state lineage is invalid")
                previous_version = ref
            current_ref, current = lineage[-1]
            if len(lineage) == 1 and current["state"] != "accepted":
                raise MainThreadAuthorityError("main-turn lacks accepted origin")
            if len(lineage) == 2 and current["state"] != "running":
                raise MainThreadAuthorityError("main-turn lacks running successor")
            if len(lineage) == 3 and current["state"] not in {
                    "committed", "interrupted", "failed"}:
                raise MainThreadAuthorityError(
                    "main-turn terminal successor is invalid")
            previous_turn = current_ref
        return lineages

    def _child_registry_link_lineages(
            self, thread_id: TypedId, *, view: CanonicalView | None = None,
            max_bytes: int | None = None,
    ) -> list[list[tuple[VersionRef, dict[str, Any]]]]:
        grouped: dict[TypedId, list[tuple[VersionRef, dict[str, Any]]]] = {}
        for ref, document in self._ordered_documents(
                self.core, "main_child_registry_link/v1", view=view,
                max_bytes=max_bytes):
            if document.get("main_thread_id") != str(thread_id):
                raise MainThreadAuthorityError(
                    "child Registry link belongs to another main thread")
            grouped.setdefault(ref.entity_id, []).append((ref, document))
        lineages = sorted(
            grouped.values(),
            key=lambda items: str(items[0][1]["task_control_id"]),
        )
        seen_task_ids: set[str] = set()
        seen_paths: set[str] = set()
        for lineage in lineages:
            if len(lineage) > 2:
                raise MainThreadAuthorityError(
                    "child Registry link has too many lifecycle versions")
            previous: VersionRef | None = None
            identity: tuple[object, ...] | None = None
            for index, (ref, document) in enumerate(lineage):
                expected_previous = (
                    _ref_payload(previous) if previous is not None else None)
                if (document.get("main_child_registry_link_ref")
                        != _ref_payload(ref)
                        or document.get("previous_version_ref")
                        != expected_previous
                        or document.get("state") != (
                            "launch_registered"
                            if index == 0 else "registry_attached")):
                    raise MainThreadAuthorityError(
                        "child Registry link lifecycle is malformed")
                relative_path, _path = self._validated_attempt_path(
                    str(document.get("registry_relative_path", "")))
                current_identity = (
                    document.get("main_thread_id"),
                    document.get("origin_main_turn_ref"),
                    document.get("task_control_id"),
                    document.get("task_kind"),
                    relative_path,
                )
                if identity is None:
                    identity = current_identity
                elif current_identity != identity:
                    raise MainThreadAuthorityError(
                        "child Registry link changed its launch identity")
                previous = ref
            current = lineage[-1][1]
            task_id = str(current["task_control_id"])
            relative_path = str(current["registry_relative_path"])
            if task_id in seen_task_ids or relative_path in seen_paths:
                raise MainThreadAuthorityError(
                    "main thread contains duplicate child Registry indexes")
            seen_task_ids.add(task_id)
            seen_paths.add(relative_path)
            origin = current.get("origin_main_turn_ref")
            if origin is not None:
                origin_ref = _parse_ref(origin)
                origin_turn = self._read_exact(
                    self.core, origin_ref, expected_type="main_turn/v1",
                    view=view, max_bytes=max_bytes)
                if (origin_turn.get("state") != "committed"
                        or origin_turn.get("main_thread_id")
                        != str(thread_id)):
                    raise MainThreadAuthorityError(
                        "child Registry link origin is not a committed turn")
        return lineages

    def _current_thread(self) -> tuple[VersionRef, dict[str, Any]]:
        return self._thread_versions()[-1]

    def _require_current_thread(self, ref: VersionRef) -> dict[str, Any]:
        current_ref, current = self._current_thread()
        if ref != current_ref:
            raise RegistryConflict("main-thread predecessor is not current")
        return current

    def _require_current_turn(
            self, ref: VersionRef, *, states: set[str], thread_id: TypedId,
    ) -> dict[str, Any]:
        for lineage in self._turn_lineages(thread_id):
            if lineage[-1][0].entity_id == ref.entity_id:
                current_ref, current = lineage[-1]
                if current_ref != ref:
                    raise RegistryConflict("main-turn predecessor is not current")
                if current.get("state") not in states:
                    raise RegistryConflict(
                        f"main-turn state {current.get('state')!r} is not admissible")
                return current
        raise RegistryConflict("main-turn does not belong to the current thread")

    def _prewrite(
            self, transaction: Any, ref: VersionRef,
            document: Mapping[str, Any],
    ) -> None:
        transaction.prewrite(
            object_type=ref.entity_type,
            logical_id=ref.entity_id,
            version_id=ref.version_id,
            payload=canonical_json(document),
            metadata=document,
            media_type="application/json",
            schema_ref=f"registry_v1/{ref.entity_type}",
        )

    def _matching_existing(
            self, expected: tuple[tuple[VersionRef, Mapping[str, Any]], ...],
    ) -> bool:
        present = [
            self.core.event_store.object_row(ref.version_id) is not None
            for ref, _document in expected
        ]
        if not any(present):
            return False
        if not all(present):
            raise MainThreadAuthorityError(
                "idempotent main-thread command is only partly registered")
        for ref, document in expected:
            if self._read_exact(self.core, ref) != dict(document):
                raise RegistryConflict(
                    "idempotent main-thread command differs from registered facts")
        return True

    def create_thread(self, *, idempotency_key: str) -> VersionRef:
        logical_id = _stable_id("resource", "thread", idempotency_key)
        ref = VersionRef(
            "main_thread/v1", logical_id,
            _stable_id("resource_version", logical_id, "created", idempotency_key),
        )
        document = {
            "main_thread_ref": _ref_payload(ref),
            "predecessor_version_ref": None,
            "state": "idle",
            "next_turn_ordinal": 1,
            "active_turn_ref": None,
            "latest_turn_ref": None,
            "latest_committed_turn_ref": None,
        }
        if self._matching_existing(((ref, document),)):
            return ref
        if self.core.event_store.canonical_object_rows(
                object_type="main_thread/v1"):
            raise RegistryConflict(
                "one persistent main-thread lineage already exists")
        command_key = self._idempotency_key(
            "create_thread", idempotency_key, {"thread_ref": _ref_payload(ref)})
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, ref, document)
        transaction.commit()
        return ref

    def accept_turn(
            self, *, thread_ref: VersionRef, user_input: Any,
            idempotency_key: str, expected_ordinal: int | None = None,
    ) -> ThreadTurnAdvance:
        normalized_input = _exact_json_value(user_input, label="main-turn user input")
        current = self._read_exact(self.core, thread_ref, expected_type="main_thread/v1")
        ordinal = int(current["next_turn_ordinal"])
        if expected_ordinal is not None and expected_ordinal != ordinal:
            raise RegistryConflict("main-turn ordinal differs from thread authority")
        turn_id = _stable_id(
            "resource", thread_ref.entity_id, "turn", ordinal, idempotency_key)
        accepted_ref = VersionRef(
            "main_turn/v1", turn_id,
            _stable_id("resource_version", turn_id, "accepted", idempotency_key),
        )
        successor_ref = VersionRef(
            "main_thread/v1", thread_ref.entity_id,
            _stable_id(
                "resource_version", thread_ref.version_id,
                accepted_ref.version_id, "turn-active", idempotency_key),
        )
        turn_document = {
            "main_turn_ref": _ref_payload(accepted_ref),
            "main_thread_id": str(thread_ref.entity_id),
            "ordinal": ordinal,
            "state": "accepted",
            "previous_version_ref": None,
            "predecessor_turn_ref": current["latest_turn_ref"],
            "user_input": normalized_input,
            "attempt_relative_path": None,
            "execution_receipt_ref": None,
            "answer_ref": None,
            "decision_ref": None,
        }
        thread_document = {
            **current,
            "main_thread_ref": _ref_payload(successor_ref),
            "predecessor_version_ref": _ref_payload(thread_ref),
            "state": "turn_active",
            "next_turn_ordinal": ordinal + 1,
            "active_turn_ref": _ref_payload(accepted_ref),
        }
        expected = ((accepted_ref, turn_document), (successor_ref, thread_document))
        if self._matching_existing(expected):
            return ThreadTurnAdvance(successor_ref, accepted_ref)
        current = self._require_current_thread(thread_ref)
        if (current["state"] not in {"idle", "stopped"}
                or current["active_turn_ref"] is not None
                or int(current["next_turn_ordinal"]) != ordinal):
            raise RegistryConflict("main thread cannot accept this turn")
        command_key = self._idempotency_key("accept_turn", idempotency_key, {
            "thread_ref": _ref_payload(thread_ref),
            "turn_ref": _ref_payload(accepted_ref),
            "user_input": normalized_input,
        })
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, accepted_ref, turn_document)
        self._prewrite(transaction, successor_ref, thread_document)
        transaction.commit()
        return ThreadTurnAdvance(successor_ref, accepted_ref)

    def _validated_attempt_path(self, relative_path: str) -> tuple[str, Path]:
        try:
            candidate = resolve_child_registry(
                self.child_path_root, relative_path)
        except ValueError as exc:
            raise ValueError(
                "attempt path escapes its parent Registry") from exc
        return relative_path, candidate

    def relative_child_path(self, child_registry_root: Path) -> str:
        """Return the direct edge from this Registry to one child Registry."""
        return relative_child_registry(
            self.child_path_root, child_registry_root)

    def resolve_child_path(self, relative_path: str) -> Path:
        """Resolve one stored edge without traversing any grandchild link."""
        return self._validated_attempt_path(relative_path)[1]

    def attach_attempt(
            self, *, thread_ref: VersionRef, turn_ref: VersionRef,
            idempotency_key: str, attempt_relative_path: str | None = None,
    ) -> AttemptAttachment:
        turn = self._read_exact(self.core, turn_ref, expected_type="main_turn/v1")
        generated = (
            f"attempts/turn-{int(turn['ordinal']):06d}-{turn_ref.entity_id.value}"
        )
        relative_path, attempt_path = self._validated_attempt_path(
            generated if attempt_relative_path is None else attempt_relative_path)
        running_ref = VersionRef(
            "main_turn/v1", turn_ref.entity_id,
            _stable_id(
                "resource_version", turn_ref.version_id, "running",
                relative_path, idempotency_key),
        )
        successor_ref = VersionRef(
            "main_thread/v1", thread_ref.entity_id,
            _stable_id(
                "resource_version", thread_ref.version_id,
                running_ref.version_id, "attempt-attached", idempotency_key),
        )
        running = {
            **turn,
            "main_turn_ref": _ref_payload(running_ref),
            "state": "running",
            "previous_version_ref": _ref_payload(turn_ref),
            "attempt_relative_path": relative_path,
        }
        thread = self._read_exact(
            self.core, thread_ref, expected_type="main_thread/v1")
        successor = {
            **thread,
            "main_thread_ref": _ref_payload(successor_ref),
            "predecessor_version_ref": _ref_payload(thread_ref),
            "active_turn_ref": _ref_payload(running_ref),
        }
        expected = ((running_ref, running), (successor_ref, successor))
        if self._matching_existing(expected):
            attempt_path.mkdir(parents=True, exist_ok=True)
            return AttemptAttachment(
                successor_ref, running_ref, relative_path, attempt_path)
        current_thread = self._require_current_thread(thread_ref)
        current_turn = self._require_current_turn(
            turn_ref, states={"accepted"}, thread_id=thread_ref.entity_id)
        if (current_thread["state"] != "turn_active"
                or current_thread["active_turn_ref"] != _ref_payload(turn_ref)
                or current_turn != turn):
            raise RegistryConflict("attempt does not match the active main turn")
        attempt_path.mkdir(parents=True, exist_ok=True)
        command_key = self._idempotency_key("attach_attempt", idempotency_key, {
            "thread_ref": _ref_payload(thread_ref),
            "turn_ref": _ref_payload(turn_ref),
            "attempt_relative_path": relative_path,
        })
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, running_ref, running)
        self._prewrite(transaction, successor_ref, successor)
        transaction.commit()
        return AttemptAttachment(
            successor_ref, running_ref, relative_path, attempt_path)

    def register_child_registry_link(
            self, *, task_control_id: str, task_kind: str,
            registry_relative_path: str,
            origin_main_turn_ref: VersionRef | None,
            idempotency_key: str,
    ) -> ChildRegistryLink:
        """Index one independently owned task Registry by a relative link."""

        if (not isinstance(task_control_id, str)
                or re.fullmatch(
                    r"task-[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
                    task_control_id) is None):
            raise ValueError("child Registry link requires a valid task id")
        if task_kind not in {"single_agent", "workflow"}:
            raise ValueError("child Registry link requires a valid task kind")
        relative_path, registry_path = self._validated_attempt_path(
            registry_relative_path)
        thread_ref, _thread = self._current_thread()
        origin_payload = None
        if origin_main_turn_ref is not None:
            if not isinstance(origin_main_turn_ref, VersionRef):
                raise TypeError("child Registry link origin must be a VersionRef")
            origin = self._read_exact(
                self.core, origin_main_turn_ref,
                expected_type="main_turn/v1")
            if (origin.get("state") != "committed"
                    or origin.get("main_thread_id")
                    != str(thread_ref.entity_id)):
                raise RegistryConflict(
                    "child Registry link origin is not a committed main turn")
            origin_payload = _ref_payload(origin_main_turn_ref)
        logical_id = _stable_id(
            "resource", thread_ref.entity_id, task_control_id,
            "child-registry-link")
        link_ref = VersionRef(
            "main_child_registry_link/v1", logical_id,
            _stable_id(
                "resource_version", logical_id, "launch-registered",
                task_kind, relative_path, origin_payload),
        )
        document = {
            "main_child_registry_link_ref": _ref_payload(link_ref),
            "previous_version_ref": None,
            "main_thread_id": str(thread_ref.entity_id),
            "origin_main_turn_ref": origin_payload,
            "task_control_id": task_control_id,
            "task_kind": task_kind,
            "registry_relative_path": relative_path,
            "state": "launch_registered",
            "child_task_ref": None,
            "child_run_ref": None,
        }
        existing = [
            lineage for lineage in self._child_registry_link_lineages(
                thread_ref.entity_id)
            if lineage[-1][0].entity_id == logical_id
        ]
        if existing:
            first_ref, first = existing[0][0]
            if first_ref != link_ref or first != document:
                raise RegistryConflict(
                    "task id is already linked to another child Registry")
            current_ref = existing[0][-1][0]
            return ChildRegistryLink(current_ref, relative_path, registry_path)
        command_key = self._idempotency_key(
            "register_child_registry_link", idempotency_key, document)
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, link_ref, document)
        transaction.commit()
        return ChildRegistryLink(link_ref, relative_path, registry_path)

    def _inspect_linked_child_identity(
            self, registry_relative_path: str,
    ) -> tuple[VersionRef, VersionRef] | None:
        _relative_path, registry_path = self._validated_attempt_path(
            registry_relative_path)
        database = registry_path / ".registry_v1" / "registry.sqlite3"
        if not database.is_file():
            return None
        try:
            uri = f"{database.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(
                    uri, timeout=0, isolation_level=None, uri=True,
            ) as connection:
                connection.execute("PRAGMA query_only=ON")
                meta_table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='registry_meta'",
                ).fetchone()
                if meta_table is None:
                    return None
                pointer = connection.execute(
                    "SELECT value FROM registry_meta "
                    "WHERE key='native_run_ref'",
                ).fetchone()
        except sqlite3.OperationalError as exc:
            error_code = getattr(exc, "sqlite_errorcode", None)
            primary_code = (
                error_code & 0xff if isinstance(error_code, int) else None)
            if primary_code in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_CANTOPEN,
                    sqlite3.SQLITE_LOCKED,
            }:
                return None
            raise MainThreadAuthorityError(
                "linked child Registry metadata is malformed") from exc
        except sqlite3.DatabaseError as exc:
            raise MainThreadAuthorityError(
                "linked child Registry metadata is malformed") from exc
        if pointer is None:
            return None
        raw_run_ref = str(pointer[0])
        try:
            child = _RegistryCore(
                registry_path, create=False, read_only=True)
        except Exception as exc:
            raise MainThreadAuthorityError(
                "linked child Registry identity is malformed") from exc
        try:
            run_ref = _parse_ref(json.loads(raw_run_ref))
            run = self._read_exact(
                child, run_ref, expected_type="native_run_identity/v1")
            task_ref = _parse_ref(run["task_ref"])
            if task_ref.entity_id != child.task_id:
                raise MainThreadAuthorityError(
                    "linked child run belongs to another task identity")
            self._read_exact(child, task_ref, expected_type="task/v1")
            return task_ref, run_ref
        except MainThreadAuthorityError:
            raise
        except Exception as exc:
            raise MainThreadAuthorityError(
                "linked child Registry identity is malformed") from exc

    def attach_child_registry_link(
            self, *, task_control_id: str, idempotency_key: str,
    ) -> ChildRegistryLink:
        """Attach exact child task/run refs once its Registry is readable."""

        thread_ref, _thread = self._current_thread()
        matches = [
            lineage for lineage in self._child_registry_link_lineages(
                thread_ref.entity_id)
            if lineage[-1][1]["task_control_id"] == task_control_id
        ]
        if len(matches) != 1:
            raise RegistryConflict(
                "child Registry attachment requires one registered link")
        current_ref, current = matches[0][-1]
        relative_path, registry_path = self._validated_attempt_path(
            current["registry_relative_path"])
        identity = self._inspect_linked_child_identity(relative_path)
        if identity is None:
            if current["state"] == "registry_attached":
                raise MainThreadAuthorityError(
                    "attached child Registry is no longer readable")
            return ChildRegistryLink(
                current_ref, relative_path, registry_path)
        task_ref, run_ref = identity
        if current["state"] == "registry_attached":
            if (current["child_task_ref"] != _ref_payload(task_ref)
                    or current["child_run_ref"] != _ref_payload(run_ref)):
                raise MainThreadAuthorityError(
                    "attached child Registry changed identity")
            return ChildRegistryLink(
                current_ref, relative_path, registry_path)
        attached_ref = VersionRef(
            "main_child_registry_link/v1", current_ref.entity_id,
            _stable_id(
                "resource_version", current_ref.version_id,
                task_ref.version_id, run_ref.version_id,
                "registry-attached"),
        )
        attached = {
            **current,
            "main_child_registry_link_ref": _ref_payload(attached_ref),
            "previous_version_ref": _ref_payload(current_ref),
            "state": "registry_attached",
            "child_task_ref": _ref_payload(task_ref),
            "child_run_ref": _ref_payload(run_ref),
        }
        if not self._matching_existing(((attached_ref, attached),)):
            command_key = self._idempotency_key(
                "attach_child_registry_link", idempotency_key, attached)
            transaction = self.core.begin(idempotency_key=command_key)
            self._prewrite(transaction, attached_ref, attached)
            transaction.commit()
        return ChildRegistryLink(
            attached_ref, relative_path, registry_path)

    def reconcile_child_registry_links(self) -> tuple[dict[str, Any], ...]:
        """Attach every currently readable independent child Registry."""

        thread_ref, _thread = self._current_thread()
        task_ids = [
            str(lineage[-1][1]["task_control_id"])
            for lineage in self._child_registry_link_lineages(
                thread_ref.entity_id)
        ]
        for task_id in task_ids:
            self.attach_child_registry_link(
                task_control_id=task_id,
                idempotency_key=f"child-registry:{task_id}:attach",
            )
        lineages = self._child_registry_link_lineages(thread_ref.entity_id)
        return tuple(dict(lineage[-1][1]) for lineage in lineages)

    def _inspect_child_registry(
            self, *, turn_ref: VersionRef, turn: Mapping[str, Any],
            allow_running: bool = False,
    ) -> dict[str, Any]:
        raw_path = turn.get("attempt_relative_path")
        if not isinstance(raw_path, str):
            raise MainThreadAuthorityError("running turn has no attempt path")
        relative_path, attempt_path = self._validated_attempt_path(raw_path)
        try:
            child = _RegistryCore(
                attempt_path, create=False, read_only=True)
        except Exception as exc:
            raise MainThreadAuthorityError(
                "attempt is not an exact readable child Registry") from exc
        try:
            raw_run_ref = child.event_store.get_meta("native_run_ref")
            if raw_run_ref is None:
                raise MainThreadAuthorityError(
                    "child Registry has no exact native run identity")
            run_ref = _parse_ref(json.loads(raw_run_ref))
            run = self._read_exact(
                child, run_ref, expected_type="native_run_identity/v1")
            task_ref = _parse_ref(run["task_ref"])
            if task_ref.entity_id != child.task_id:
                raise MainThreadAuthorityError(
                    "child run belongs to another task identity")
            self._read_exact(child, task_ref, expected_type="task/v1")

            authorities = self._ordered_documents(
                child, "run_execution_authority/v1")
            if (not authorities
                    or len({ref.entity_id for ref, _ in authorities}) != 1):
                raise MainThreadAuthorityError(
                    "child Registry lacks one run-execution authority lineage")
            authority_ref, authority = authorities[-1]
            if (authority.get("run_execution_authority_ref")
                    != _ref_payload(authority_ref)
                    or authority.get("run_ref") != _ref_payload(run_ref)
                    or authority.get("task_ref") != _ref_payload(task_ref)):
                raise MainThreadAuthorityError(
                    "child execution authority differs from its run/task")
            checkpoint_ref = _parse_ref(authority["latest_checkpoint_ref"])
            self._read_exact(
                child, checkpoint_ref, expected_type="marking_checkpoint/v1")
            outcome = authority.get("status")
            admissible = {"terminal", "stopped_by_owner"}
            if allow_running:
                admissible.add("running")
            if outcome not in admissible:
                raise MainThreadAuthorityError(
                    "child execution has no admissible Registry status")

            observed: dict[str, Any] = {
                "main_turn_ref": _ref_payload(turn_ref),
                "attempt_relative_path": relative_path,
                "validated_through_ordinal": child.event_store.max_ordinal(),
                "child_task_ref": _ref_payload(task_ref),
                "child_run_ref": _ref_payload(run_ref),
                "run_execution_authority_ref": _ref_payload(authority_ref),
                "final_checkpoint_ref": _ref_payload(checkpoint_ref),
                "outcome": outcome,
                "run_outcome": None,
                "terminal_evidence_ref": None,
                "terminal_result_ref": None,
                "final_result_index_ref": None,
            }
            if outcome == "running":
                if authority.get("terminal_evidence_ref") is not None:
                    raise MainThreadAuthorityError(
                        "running child Registry contains terminal result authority")
                return observed
            if outcome == "stopped_by_owner":
                if authority.get("terminal_evidence_ref") is not None:
                    raise MainThreadAuthorityError(
                        "stopped child Registry contains terminal result authority")
                return observed

            if authority.get("terminal_evidence_ref") is None:
                raise MainThreadAuthorityError(
                    "terminal child Registry lacks current terminal evidence")
            evidence_ref = _parse_ref(authority["terminal_evidence_ref"])
            evidence = self._read_exact(
                child, evidence_ref, expected_type="run_terminal_evidence/v1")
            terminal_result_ref = _parse_ref(evidence["terminal_result_ref"])
            final_index_ref = _parse_ref(evidence["final_result_index_ref"])
            final_checkpoint_ref = _parse_ref(evidence["final_checkpoint_ref"])
            if (evidence.get("terminal_evidence_ref")
                    != _ref_payload(evidence_ref)
                    or evidence.get("run_ref") != _ref_payload(run_ref)
                    or final_checkpoint_ref != checkpoint_ref):
                raise MainThreadAuthorityError(
                    "terminal evidence differs from run/checkpoint authority")
            self._read_registered_bytes(
                child, terminal_result_ref,
                expected_type="resource_version/v1")
            final_index = self._read_exact(
                child, final_index_ref, expected_type="final_result_index/v1")
            if (final_index.get("final_result_index_ref")
                    != _ref_payload(final_index_ref)
                    or final_index.get("terminal_result_ref")
                    != _ref_payload(terminal_result_ref)
                    or final_index.get("terminal_outcome")
                    != evidence.get("run_outcome")):
                raise MainThreadAuthorityError(
                    "child final result index differs from terminal evidence")
            observed.update({
                "run_outcome": evidence["run_outcome"],
                "terminal_evidence_ref": _ref_payload(evidence_ref),
                "terminal_result_ref": _ref_payload(terminal_result_ref),
                "final_result_index_ref": _ref_payload(final_index_ref),
            })
            return observed
        except MainThreadAuthorityError:
            raise
        except Exception as exc:
            raise MainThreadAuthorityError(
                "child Registry terminal authority is malformed") from exc

    def observe_turn_execution(self, *, turn_ref: VersionRef) -> dict[str, Any]:
        """Observe one active turn using only its registered child authority.

        ``pending_start`` means the main-thread Registry has attached the exact
        attempt path but that path does not yet contain a child Registry.  Once
        a child Registry exists, malformed or mismatched facts are errors; they
        are never collapsed into a process-derived status.
        """

        turn = self._read_exact(
            self.core, turn_ref, expected_type="main_turn/v1")
        thread_id = TypedId.parse(
            str(turn["main_thread_id"]), expected="resource")
        self._require_current_turn(
            turn_ref, states={"accepted", "running"}, thread_id=thread_id)
        base = {
            "main_turn_ref": _ref_payload(turn_ref),
            "ordinal": int(turn["ordinal"]),
            "user_input": turn["user_input"],
            "attempt_relative_path": turn.get("attempt_relative_path"),
            "output": None,
        }
        if turn["state"] == "accepted":
            return {**base, "state": "accepted", "observation": None}
        raw_path = turn.get("attempt_relative_path")
        if not isinstance(raw_path, str):
            raise MainThreadAuthorityError("running turn has no attempt path")
        _relative_path, attempt_path = self._validated_attempt_path(raw_path)
        database = attempt_path / ".registry_v1" / "registry.sqlite3"
        if not database.is_file():
            return {**base, "state": "pending_start", "observation": None}
        observed = self._inspect_child_registry(
            turn_ref=turn_ref, turn=turn, allow_running=True)
        output: Any = None
        if observed["outcome"] == "terminal":
            result_ref = _parse_ref(observed["terminal_result_ref"])
            child = _RegistryCore(
                attempt_path, create=False, read_only=True)
            _prepared, payload = self._read_registered_bytes(
                child, result_ref, expected_type="resource_version/v1")
            try:
                output = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MainThreadAuthorityError(
                    "child terminal result is not exact JSON") from exc
        return {
            **base,
            "state": observed["outcome"],
            "observation": observed,
            "output": output,
        }

    def record_execution_receipt(
            self, *, turn_ref: VersionRef, idempotency_key: str,
            allow_running: bool = False,
    ) -> VersionRef:
        if not isinstance(allow_running, bool):
            raise TypeError("allow_running must be a bool")
        turn = self._read_exact(self.core, turn_ref, expected_type="main_turn/v1")
        thread_id = TypedId.parse(str(turn["main_thread_id"]), expected="resource")
        self._require_current_turn(
            turn_ref, states={"running"}, thread_id=thread_id)
        observed = (
            self._inspect_child_registry(
                turn_ref=turn_ref, turn=turn, allow_running=True)
            if allow_running else
            self._inspect_child_registry(turn_ref=turn_ref, turn=turn)
        )
        receipt_id = _stable_id(
            "resource", turn_ref.entity_id,
            observed["run_execution_authority_ref"]["version_id"], "receipt")
        receipt_ref = VersionRef(
            "main_turn_execution_receipt/v1", receipt_id,
            _stable_id(
                "resource_version", receipt_id,
                observed["validated_through_ordinal"], idempotency_key),
        )
        document = {
            "main_turn_execution_receipt_ref": _ref_payload(receipt_ref),
            **observed,
        }
        if self._matching_existing(((receipt_ref, document),)):
            return receipt_ref
        command_key = self._idempotency_key(
            "record_execution_receipt", idempotency_key, document)
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, receipt_ref, document)
        transaction.commit()
        return receipt_ref

    def _validated_receipt(
            self, *, turn_ref: VersionRef, turn: Mapping[str, Any],
            receipt_ref: VersionRef,
    ) -> dict[str, Any]:
        receipt = self._read_exact(
            self.core, receipt_ref,
            expected_type="main_turn_execution_receipt/v1")
        if (receipt.get("main_turn_execution_receipt_ref")
                != _ref_payload(receipt_ref)
                or receipt.get("main_turn_ref") != _ref_payload(turn_ref)
                or receipt.get("attempt_relative_path")
                != turn.get("attempt_relative_path")):
            raise RegistryConflict(
                "execution receipt does not belong to the active main turn")
        observed = (
            self._inspect_child_registry(
                turn_ref=turn_ref, turn=turn, allow_running=True)
            if receipt.get("outcome") == "running" else
            self._inspect_child_registry(turn_ref=turn_ref, turn=turn)
        )
        comparable = {
            key: value for key, value in receipt.items()
            if key != "main_turn_execution_receipt_ref"
        }
        if comparable != observed:
            raise RegistryConflict(
                "execution receipt no longer matches the exact child Registry")
        return receipt

    def _validate_optional_ref(
            self, ref: VersionRef | None, *, turn: Mapping[str, Any],
    ) -> dict[str, str] | None:
        if ref is None:
            return None
        try:
            self._read_exact(self.core, ref)
        except MainThreadAuthorityError:
            relative_path, attempt_path = self._validated_attempt_path(
                str(turn["attempt_relative_path"]))
            del relative_path
            child = _RegistryCore(
                attempt_path, create=False, read_only=True)
            self._read_exact(child, ref)
        return _ref_payload(ref)

    def commit_terminal_answer(
            self, *, thread_ref: VersionRef, turn_ref: VersionRef,
            receipt_ref: VersionRef, answer: Any,
            idempotency_key: str,
            answer_ref: VersionRef | Mapping[str, Any] | None = None,
            decision_ref: VersionRef | Mapping[str, Any] | None = None,
    ) -> ThreadTurnAdvance:
        normalized_answer = _exact_json_value(answer, label="main-turn answer")
        turn = self._read_exact(self.core, turn_ref, expected_type="main_turn/v1")
        receipt = self._read_exact(
            self.core, receipt_ref,
            expected_type="main_turn_execution_receipt/v1")
        answer_reference = _coerce_ref(answer_ref)
        decision_reference = _coerce_ref(decision_ref)
        committed_ref = VersionRef(
            "main_turn/v1", turn_ref.entity_id,
            _stable_id(
                "resource_version", turn_ref.version_id,
                receipt_ref.version_id, "committed", idempotency_key),
        )
        successor_ref = VersionRef(
            "main_thread/v1", thread_ref.entity_id,
            _stable_id(
                "resource_version", thread_ref.version_id,
                committed_ref.version_id, "idle", idempotency_key),
        )
        committed = {
            **turn,
            "main_turn_ref": _ref_payload(committed_ref),
            "state": "committed",
            "previous_version_ref": _ref_payload(turn_ref),
            "execution_receipt_ref": _ref_payload(receipt_ref),
            "answer": normalized_answer,
            "answer_ref": (
                _ref_payload(answer_reference)
                if answer_reference is not None else None),
            "decision_ref": (
                _ref_payload(decision_reference)
                if decision_reference is not None else None),
        }
        thread = self._read_exact(
            self.core, thread_ref, expected_type="main_thread/v1")
        successor = {
            **thread,
            "main_thread_ref": _ref_payload(successor_ref),
            "predecessor_version_ref": _ref_payload(thread_ref),
            "state": "idle",
            "active_turn_ref": None,
            "latest_turn_ref": _ref_payload(committed_ref),
            "latest_committed_turn_ref": _ref_payload(committed_ref),
        }
        expected = ((committed_ref, committed), (successor_ref, successor))
        if self._matching_existing(expected):
            return ThreadTurnAdvance(successor_ref, committed_ref)
        current_thread = self._require_current_thread(thread_ref)
        current_turn = self._require_current_turn(
            turn_ref, states={"running"}, thread_id=thread_ref.entity_id)
        if (current_thread["state"] != "turn_active"
                or current_thread["active_turn_ref"] != _ref_payload(turn_ref)
                or current_turn != turn):
            raise RegistryConflict("terminal answer does not match active main turn")
        validated = self._validated_receipt(
            turn_ref=turn_ref, turn=turn, receipt_ref=receipt_ref)
        if (validated != receipt or validated.get("outcome") != "terminal"
                or validated.get("terminal_evidence_ref") is None
                or validated.get("terminal_result_ref") is None
                or validated.get("final_result_index_ref") is None):
            raise RegistryConflict(
                "main-turn answer requires exact terminal child evidence")
        answer_payload = self._validate_optional_ref(
            answer_reference, turn=turn)
        decision_payload = self._validate_optional_ref(
            decision_reference, turn=turn)
        if (answer_payload != committed["answer_ref"]
                or decision_payload != committed["decision_ref"]):
            raise RegistryConflict("answer/decision ref validation differs")
        command_key = self._idempotency_key(
            "commit_terminal_answer", idempotency_key, {
                "thread_ref": _ref_payload(thread_ref),
                "turn_ref": _ref_payload(turn_ref),
                "receipt_ref": _ref_payload(receipt_ref),
                "answer": normalized_answer,
                "answer_ref": answer_payload,
                "decision_ref": decision_payload,
            })
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, committed_ref, committed)
        self._prewrite(transaction, successor_ref, successor)
        transaction.commit()
        return ThreadTurnAdvance(successor_ref, committed_ref)

    def commit_interruption(
            self, *, thread_ref: VersionRef, turn_ref: VersionRef,
            receipt_ref: VersionRef, idempotency_key: str,
    ) -> ThreadTurnAdvance:
        turn = self._read_exact(self.core, turn_ref, expected_type="main_turn/v1")
        interrupted_ref = VersionRef(
            "main_turn/v1", turn_ref.entity_id,
            _stable_id(
                "resource_version", turn_ref.version_id,
                receipt_ref.version_id, "interrupted", idempotency_key),
        )
        successor_ref = VersionRef(
            "main_thread/v1", thread_ref.entity_id,
            _stable_id(
                "resource_version", thread_ref.version_id,
                interrupted_ref.version_id, "stopped", idempotency_key),
        )
        interrupted = {
            **turn,
            "main_turn_ref": _ref_payload(interrupted_ref),
            "state": "interrupted",
            "previous_version_ref": _ref_payload(turn_ref),
            "execution_receipt_ref": _ref_payload(receipt_ref),
        }
        thread = self._read_exact(
            self.core, thread_ref, expected_type="main_thread/v1")
        successor = {
            **thread,
            "main_thread_ref": _ref_payload(successor_ref),
            "predecessor_version_ref": _ref_payload(thread_ref),
            "state": "stopped",
            "active_turn_ref": None,
            "latest_turn_ref": _ref_payload(interrupted_ref),
        }
        expected = ((interrupted_ref, interrupted), (successor_ref, successor))
        if self._matching_existing(expected):
            return ThreadTurnAdvance(successor_ref, interrupted_ref)
        current_thread = self._require_current_thread(thread_ref)
        current_turn = self._require_current_turn(
            turn_ref, states={"running"}, thread_id=thread_ref.entity_id)
        if (current_thread["state"] != "turn_active"
                or current_thread["active_turn_ref"] != _ref_payload(turn_ref)
                or current_turn != turn):
            raise RegistryConflict("interruption does not match active main turn")
        receipt = self._validated_receipt(
            turn_ref=turn_ref, turn=turn, receipt_ref=receipt_ref)
        if (receipt.get("outcome") != "stopped_by_owner"
                or any(receipt.get(name) is not None for name in (
                    "run_outcome", "terminal_evidence_ref",
                    "terminal_result_ref", "final_result_index_ref"))):
            raise RegistryConflict(
                "main-turn interruption requires a stopped child receipt")
        command_key = self._idempotency_key(
            "commit_interruption", idempotency_key, {
                "thread_ref": _ref_payload(thread_ref),
                "turn_ref": _ref_payload(turn_ref),
                "receipt_ref": _ref_payload(receipt_ref),
            })
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, interrupted_ref, interrupted)
        self._prewrite(transaction, successor_ref, successor)
        transaction.commit()
        return ThreadTurnAdvance(successor_ref, interrupted_ref)

    def commit_execution_failure(
            self, *, thread_ref: VersionRef, turn_ref: VersionRef,
            receipt_ref: VersionRef, idempotency_key: str,
    ) -> ThreadTurnAdvance:
        """Close one main turn whose child returned without terminal evidence.

        The child Registry remains independent and nonterminal.  This method
        records that exact boundary instead of manufacturing child terminal
        evidence or leaving the conversational thread permanently active.
        """

        turn = self._read_exact(self.core, turn_ref, expected_type="main_turn/v1")
        failed_ref = VersionRef(
            "main_turn/v1", turn_ref.entity_id,
            _stable_id(
                "resource_version", turn_ref.version_id,
                receipt_ref.version_id, "failed", idempotency_key),
        )
        successor_ref = VersionRef(
            "main_thread/v1", thread_ref.entity_id,
            _stable_id(
                "resource_version", thread_ref.version_id,
                failed_ref.version_id, "idle", idempotency_key),
        )
        failed = {
            **turn,
            "main_turn_ref": _ref_payload(failed_ref),
            "state": "failed",
            "previous_version_ref": _ref_payload(turn_ref),
            "execution_receipt_ref": _ref_payload(receipt_ref),
        }
        thread = self._read_exact(
            self.core, thread_ref, expected_type="main_thread/v1")
        successor = {
            **thread,
            "main_thread_ref": _ref_payload(successor_ref),
            "predecessor_version_ref": _ref_payload(thread_ref),
            "state": "idle",
            "active_turn_ref": None,
            "latest_turn_ref": _ref_payload(failed_ref),
        }
        expected = ((failed_ref, failed), (successor_ref, successor))
        if self._matching_existing(expected):
            return ThreadTurnAdvance(successor_ref, failed_ref)
        current_thread = self._require_current_thread(thread_ref)
        current_turn = self._require_current_turn(
            turn_ref, states={"running"}, thread_id=thread_ref.entity_id)
        if (current_thread["state"] != "turn_active"
                or current_thread["active_turn_ref"] != _ref_payload(turn_ref)
                or current_turn != turn):
            raise RegistryConflict(
                "execution failure does not match active main turn")
        receipt = self._validated_receipt(
            turn_ref=turn_ref, turn=turn, receipt_ref=receipt_ref)
        if (receipt.get("outcome") != "running"
                or any(receipt.get(name) is not None for name in (
                    "run_outcome", "terminal_evidence_ref",
                    "terminal_result_ref", "final_result_index_ref"))):
            raise RegistryConflict(
                "main-turn execution failure requires a nonterminal child receipt")
        command_key = self._idempotency_key(
            "commit_execution_failure", idempotency_key, {
                "thread_ref": _ref_payload(thread_ref),
                "turn_ref": _ref_payload(turn_ref),
                "receipt_ref": _ref_payload(receipt_ref),
            })
        transaction = self.core.begin(idempotency_key=command_key)
        self._prewrite(transaction, failed_ref, failed)
        self._prewrite(transaction, successor_ref, successor)
        transaction.commit()
        return ThreadTurnAdvance(successor_ref, failed_ref)

    def project_current_thread(self) -> dict[str, Any]:
        """Preserve the trusted-host dictionary projection at one current cut."""

        return self._project_thread_at_view(self.core.event_store.canonical_view())

    def _project_thread_at_view(
            self, view: CanonicalView, *, max_bytes: int | None = None,
    ) -> dict[str, Any]:
        thread_ref, thread = self._thread_versions(
            view=view, max_bytes=max_bytes)[-1]
        lineages = self._turn_lineages(
            thread_ref.entity_id, view=view, max_bytes=max_bytes)
        child_link_lineages = self._child_registry_link_lineages(
            thread_ref.entity_id, view=view, max_bytes=max_bytes)
        if int(thread["next_turn_ordinal"]) != len(lineages) + 1:
            raise MainThreadAuthorityError(
                "thread next ordinal differs from registered turns")
        committed_history: list[dict[str, Any]] = []
        current_turns: list[tuple[VersionRef, dict[str, Any]]] = []
        for lineage in lineages:
            current_ref, current = lineage[-1]
            current_turns.append((current_ref, current))
            if current["state"] == "committed":
                committed_history.append({
                    "ordinal": current["ordinal"],
                    "turn_ref": _ref_payload(current_ref),
                    "user_input": current["user_input"],
                    "answer": current["answer"],
                    "answer_ref": current["answer_ref"],
                    "decision_ref": current["decision_ref"],
                    "execution_receipt_ref": current["execution_receipt_ref"],
                })
        active = [
            (ref, value) for ref, value in current_turns
            if value["state"] in {"accepted", "running"}
        ]
        if len(active) > 1:
            raise MainThreadAuthorityError("thread has multiple active turns")
        expected_active = _ref_payload(active[0][0]) if active else None
        if thread["active_turn_ref"] != expected_active:
            raise MainThreadAuthorityError(
                "thread active-turn pointer differs from turn authority")
        latest = current_turns[-1][0] if current_turns else None
        latest_state = current_turns[-1][1]["state"] if current_turns else None
        expected_latest = (
            _ref_payload(latest)
            if latest is not None and latest_state in {
                "committed", "interrupted", "failed"} else
            (_ref_payload(current_turns[-2][0])
             if len(current_turns) > 1 else None)
        )
        if thread["latest_turn_ref"] != expected_latest:
            raise MainThreadAuthorityError(
                "thread latest-turn pointer differs from turn authority")
        latest_committed = next((
            ref for ref, value in reversed(current_turns)
            if value["state"] == "committed"
        ), None)
        expected_committed = (
            _ref_payload(latest_committed)
            if latest_committed is not None else None)
        if thread["latest_committed_turn_ref"] != expected_committed:
            raise MainThreadAuthorityError(
                "thread committed-turn pointer differs from history")
        return {
            "thread_ref": _ref_payload(thread_ref),
            "thread_id": str(thread_ref.entity_id),
            "state": thread["state"],
            "next_turn_ordinal": thread["next_turn_ordinal"],
            "active_turn_ref": thread["active_turn_ref"],
            "latest_turn_ref": thread["latest_turn_ref"],
            "latest_committed_turn_ref": thread["latest_committed_turn_ref"],
            "committed_history": committed_history,
            "child_registry_links": [
                dict(lineage[-1][1]) for lineage in child_link_lineages
            ],
        }

    def capture_read_cut(
            self, *, max_object_bytes: int | None = None,
    ) -> MainThreadReadCut:
        """Capture native history identity without a lease or execution action.

        A CanonicalView alone has no source identity. The event and exact
        thread ref bind it to this Registry and survive reopening the reader.
        Neither this cut nor a pagination anchor is a public read grant.
        """

        self._validate_history_budget(max_object_bytes)
        _first, boundary = self.core.event_store.first_and_last_events()
        if boundary is None:
            raise MainThreadAuthorityError("main-thread authority is missing")
        view = self.core.event_store.canonical_view(
            through_ordinal=boundary.ordinal)
        projection = self._project_thread_at_view(
            view, max_bytes=max_object_bytes)
        cut = MainThreadReadCut(
            self.core.task_id, self.core.branch_id, view, boundary.event_id,
            _parse_ref(projection["thread_ref"]))
        self._validate_read_cut(cut)
        return cut

    @staticmethod
    def _validate_history_budget(max_object_bytes: int | None) -> None:
        if max_object_bytes is not None and (
                type(max_object_bytes) is not int or max_object_bytes < 0):
            raise TypeError("history object budget must be a nonnegative integer")

    def _validate_read_cut(self, cut: MainThreadReadCut) -> None:
        if not isinstance(cut, MainThreadReadCut):
            raise TypeError("history requires a source-bound MainThreadReadCut")
        if (not isinstance(cut.view, CanonicalView)
                or not isinstance(cut.boundary_event_id, TypedId)
                or cut.boundary_event_id.kind != "event"
                or not isinstance(cut.thread_ref, VersionRef)
                or cut.thread_ref.entity_type != "main_thread/v1"
                or not isinstance(cut.thread_ref.entity_id, TypedId)
                or cut.thread_ref.entity_id.kind != "resource"
                or not isinstance(cut.thread_ref.version_id, TypedId)
                or cut.thread_ref.version_id.kind != "resource_version"):
            raise MainThreadAuthorityError("main-thread read cut is malformed")
        if (cut.task_id != self.core.task_id
                or cut.branch_id != self.core.branch_id
                or self.core.event_store.get_meta("task_id") != str(cut.task_id)
                or self.core.event_store.get_meta("branch_id") != cut.branch_id):
            raise MainThreadAuthorityError("read cut belongs to another Registry")
        try:
            self.core.event_store.canonical_view(
                through_ordinal=cut.view.through_ordinal)
        except (TypeError, RegistryConflict) as exc:
            raise MainThreadAuthorityError("read cut ordinal is invalid") from exc
        boundary = self.core.event_store.event_by_id(cut.boundary_event_id)
        if (boundary is None or boundary.ordinal != cut.view.through_ordinal
                or boundary.task_id != cut.task_id
                or boundary.branch_id != cut.branch_id):
            raise MainThreadAuthorityError("read cut boundary identity differs")

    def project_thread_at(
            self, cut: MainThreadReadCut, *, max_object_bytes: int | None = None,
    ) -> MainThreadHistoryProjection:
        """Read refs-only committed history at a revalidated canonical cut.

        Only existing main-thread display membership is projected: active,
        interrupted and failed turns do not produce conversational items.
        Raw input/answer JSON, provider profiles, child paths and child bodies
        are not returned. TaskControl is never queried or reconciled.
        """

        self._validate_history_budget(max_object_bytes)
        self._validate_read_cut(cut)
        projection = self._project_thread_at_view(
            cut.view, max_bytes=max_object_bytes)
        if _parse_ref(projection["thread_ref"]) != cut.thread_ref:
            raise MainThreadAuthorityError("read cut exact thread identity differs")
        turns = []
        for item in projection["committed_history"]:
            ref = _parse_ref(item["turn_ref"])
            ordinal = int(item["ordinal"])
            turns.append(MainThreadHistoryTurn(ordinal, ref, (
                MainThreadHistoryItem(ref, ordinal, 0, "user_input"),
                MainThreadHistoryItem(ref, ordinal, 1, "answer"),
            )))
        links = tuple(MainThreadHistoryChildLink(
            _parse_ref(link["main_child_registry_link_ref"]),
            _coerce_ref(link["origin_main_turn_ref"]),
            link["task_control_id"], link["task_kind"], link["state"],
        ) for link in projection["child_registry_links"])
        return MainThreadHistoryProjection(
            cut, projection["state"], projection["next_turn_ordinal"],
            _coerce_ref(projection["active_turn_ref"]),
            _coerce_ref(projection["latest_turn_ref"]),
            _coerce_ref(projection["latest_committed_turn_ref"]),
            tuple(turns), links,
        )

    def page_turns_at(
            self, cut: MainThreadReadCut, *, limit: int = 50,
            order: Literal["asc", "desc"] = "asc",
            anchor: MainThreadHistoryAnchor | None = None,
            max_object_bytes: int | None = None,
    ) -> MainThreadHistoryPage[MainThreadHistoryTurn]:
        """Page native committed turn refs; this is not a transport RPC."""

        return self._page_history(
            cut, query="turns", limit=limit, order=order,
            turn_filter=None, anchor=anchor, max_object_bytes=max_object_bytes)

    def _committed_turn_documents_at(
            self, cut: MainThreadReadCut, exact_refs: tuple[VersionRef, ...], *,
            max_object_bytes: int | None = None,
    ) -> tuple[MainThreadHistoryProjection, tuple[dict[str, Any], ...]]:
        """Existing owner-host hydration seam; never a public body/grant API.

        Membership and exact body validation stay together here. Only the
        MainSession safe renderer may forward a projection to a frontend.
        """
        if (not isinstance(exact_refs, tuple)
                or len(exact_refs) > self.HISTORY_PAGE_MAX_ENTRIES
                or any(not isinstance(ref, VersionRef) for ref in exact_refs)
                or len(set(exact_refs)) != len(exact_refs)):
            raise ValueError("history requires at most 100 unique exact turn refs")
        projection = self.project_thread_at(cut, max_object_bytes=max_object_bytes)
        members = {turn.turn_ref for turn in projection.turns}
        if any(ref not in members for ref in exact_refs):
            raise MainThreadAuthorityError("history turn is not committed at this cut")
        documents = tuple(self._read_exact(
            self.core, ref, expected_type="main_turn/v1", view=cut.view,
            max_bytes=max_object_bytes,
        ) for ref in exact_refs)
        return projection, documents

    def page_items_at(
            self, cut: MainThreadReadCut, *, limit: int = 50,
            order: Literal["asc", "desc"] = "asc",
            turn_filter: VersionRef | None = None,
            anchor: MainThreadHistoryAnchor | None = None,
            max_object_bytes: int | None = None,
    ) -> MainThreadHistoryPage[MainThreadHistoryItem]:
        """Page exact field slots in (turn ordinal, field position) order."""

        return self._page_history(
            cut, query="items", limit=limit, order=order,
            turn_filter=turn_filter, anchor=anchor, max_object_bytes=max_object_bytes)

    def _page_history(
            self, cut: MainThreadReadCut, *, query: str, limit: int,
            order: str, turn_filter: VersionRef | None,
            anchor: MainThreadHistoryAnchor | None,
            max_object_bytes: int | None,
    ) -> MainThreadHistoryPage:
        if type(limit) is not int or not 1 <= limit <= self.HISTORY_PAGE_MAX_ENTRIES:
            raise ValueError("history page limit must be an integer from 1 to 100")
        if order not in {"asc", "desc"}:
            raise ValueError("history page order must be asc or desc")
        if turn_filter is not None and not isinstance(turn_filter, VersionRef):
            raise TypeError("history turn filter requires one exact VersionRef")
        if anchor is not None:
            if not isinstance(anchor, MainThreadHistoryAnchor):
                raise TypeError("history position requires a native anchor")
            if (anchor.cut != cut or anchor.query != query
                    or anchor.order != order or anchor.turn_filter != turn_filter):
                raise MainThreadAuthorityError("history anchor query or cut differs")
            if (type(anchor.turn_ordinal) is not int or anchor.turn_ordinal < 1
                    or type(anchor.inclusive) is not bool
                    or not isinstance(anchor.turn_ref, VersionRef)
                    or (query == "turns" and anchor.item_index is not None)
                    or (query == "items" and (
                        type(anchor.item_index) is not int
                        or anchor.item_index not in {0, 1}))):
                raise MainThreadAuthorityError("history anchor position is malformed")
        projection = self.project_thread_at(cut, max_object_bytes=max_object_bytes)
        if (turn_filter is not None and not any(
                turn.turn_ref == turn_filter for turn in projection.turns)):
            raise MainThreadAuthorityError("turn filter is not committed at this cut")
        entries = (projection.turns if query == "turns" else tuple(
            item for turn in projection.turns
            if turn_filter is None or turn.turn_ref == turn_filter
            for item in turn.items))
        ordered = entries if order == "asc" else reversed(entries)
        found = anchor is None
        selected = []
        more = False
        for entry in ordered:
            ordinal = entry.ordinal if query == "turns" else entry.turn_ordinal
            index = None if query == "turns" else entry.item_index
            if not found:
                if (entry.turn_ref != anchor.turn_ref
                        or ordinal != anchor.turn_ordinal
                        or index != anchor.item_index):
                    continue
                found = True
                if not anchor.inclusive:
                    continue
            if len(selected) == limit:
                more = True
                break
            selected.append(entry)
        if not found:
            raise MainThreadAuthorityError("history anchor is not present at this cut")
        next_anchor = None
        if more:
            last = selected[-1]
            next_anchor = MainThreadHistoryAnchor(
                cut, query, order, turn_filter, last.turn_ref,
                last.ordinal if query == "turns" else last.turn_ordinal,
                None if query == "turns" else last.item_index,
            )
        return MainThreadHistoryPage(cut, tuple(selected), next_anchor)

    def recover_thread(self) -> dict[str, Any]:
        """Rebuild current authority and committed history from Registry only."""

        return self.project_current_thread()


__all__ = (
    "AttemptAttachment",
    "ChildRegistryLink",
    "MainThreadAuthorityError",
    "MainThreadRegistry",
    "ThreadTurnAdvance",
)
