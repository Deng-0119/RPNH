"""Transaction-local proposal and persisted-member lookup context."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..identities import TypedId
from ..models import PendingEvent, PreparedObject, TypedRelation


@dataclass(slots=True)
class TransactionValidationContext:
    """Hold authoritative validation state for one commit transaction.

    The context owns no connection and no process-wide cache. Its proposal
    indexes and lookup memos are valid only for the already-open
    ``BEGIN IMMEDIATE`` transaction that created it.
    """

    db: sqlite3.Connection
    objects: Sequence[PreparedObject]
    events: Sequence[PendingEvent]
    relations: Sequence[TypedRelation]
    event_store: Any | None = None
    task_id: TypedId | None = None
    branch_id: str = ""
    task_round_id: TypedId | None = None
    net_instance_id: TypedId | None = None
    transaction_id: TypedId | None = None
    idempotency_key: str = ""
    transaction_writer_epoch: int = 0
    new_by_logical: dict[tuple[str, str], PreparedObject] = field(init=False)
    new_by_version: dict[str, PreparedObject] = field(init=False)
    proposal_owner_roots: set[str] = field(init=False)
    canonical_upper: int = field(init=False)
    _member_visibility: dict[tuple[str, str], bool] = field(
        init=False, default_factory=dict,
    )
    _object_metadata: dict[
        tuple[str, str], Mapping[str, Any] | None
    ] = field(init=False, default_factory=dict)
    _version_metadata: dict[
        tuple[str, str], Mapping[str, Any] | None
    ] = field(init=False, default_factory=dict)
    _version_exists: dict[
        tuple[str, str | None], bool
    ] = field(init=False, default_factory=dict)
    _exact_refs: dict[
        tuple[str, str, str], bool
    ] = field(init=False, default_factory=dict)
    native_resume_superseded_firing_ids: set[str] = field(
        init=False, default_factory=set,
    )
    pending_firing_claims: list[
        tuple[str, set[str], set[str]]
    ] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        from ..event_store import RegistryCorruptError

        self.new_by_logical = {
            (str(item.logical_id), item.object_type): item
            for item in self.objects
        }
        self.new_by_version = {
            str(item.version_id): item for item in self.objects
        }
        producer_ids = {
            str(value.producer_invocation_id)
            for value in (*self.objects, *self.events)
            if value.producer_invocation_id is not None
        }
        self.proposal_owner_roots = set()
        for producer_id in producer_ids:
            rows = self.db.execute(
                "SELECT firing_version_id,state FROM firing_publications "
                "WHERE invocation_logical_id=? OR invocation_version_id=?",
                (producer_id, producer_id),
            ).fetchall()
            if len(rows) > 1:
                raise RegistryCorruptError(
                    "producer invocation resolves to multiple firing roots")
            if rows and rows[0]["state"] == "PROVISIONAL":
                self.proposal_owner_roots.add(
                    str(rows[0]["firing_version_id"]))
        maximum_row = self.db.execute(
            "SELECT COALESCE(MAX(ordinal),0) AS maximum FROM events"
        ).fetchone()
        self.canonical_upper = int(maximum_row["maximum"])

    @classmethod
    def referenced_version_ids(cls, value: object) -> set[str]:
        """Collect exact object-version references from typed payloads."""

        if isinstance(value, Mapping):
            found: set[str] = set()
            entity_type = value.get("entity_type")
            version_id = value.get("version_id")
            if (isinstance(entity_type, str)
                    and isinstance(version_id, str) and version_id):
                found.add(version_id)
            resource_version_id = value.get("resource_version_id")
            if isinstance(resource_version_id, str) and resource_version_id:
                found.add(resource_version_id)
            for nested in value.values():
                found.update(cls.referenced_version_ids(nested))
            return found
        if (isinstance(value, Sequence)
                and not isinstance(value, (str, bytes))):
            found = set()
            for nested in value:
                found.update(cls.referenced_version_ids(nested))
            return found
        return set()

    @classmethod
    def referenced_relation_ids(cls, value: object) -> set[str]:
        """Collect explicitly named Registry relation identities."""

        if isinstance(value, Mapping):
            found: set[str] = set()
            relation_id = value.get("relation_id")
            if isinstance(relation_id, str) and relation_id:
                found.add(relation_id)
            for nested in value.values():
                found.update(cls.referenced_relation_ids(nested))
            return found
        if (isinstance(value, Sequence)
                and not isinstance(value, (str, bytes))):
            found = set()
            for nested in value:
                found.update(cls.referenced_relation_ids(nested))
            return found
        return set()

    def persisted_member_visible(self, member_kind: str, identity: str) -> bool:
        """Apply canonical or exact proposal-firing visibility."""

        cache_key = (member_kind, identity)
        cached = self._member_visibility.get(cache_key)
        if cached is not None:
            return cached
        publication_queries = {
            "object": (
                "SELECT e.ordinal FROM objects value JOIN events e "
                "ON e.event_id=value.published_event_id "
                "WHERE value.version_id=?"),
            "event": "SELECT ordinal FROM events WHERE event_id=?",
            "relation": (
                "SELECT e.ordinal FROM relations value JOIN events e "
                "ON e.event_id=value.published_event_id "
                "WHERE value.relation_id=?"),
        }
        query = publication_queries.get(member_kind)
        if query is None:
            raise TypeError("unknown persisted member visibility kind")
        publication = self.db.execute(query, (identity,)).fetchone()
        if (publication is None
                or int(publication["ordinal"]) > self.canonical_upper):
            self._member_visibility[cache_key] = False
            return False
        roots = self.db.execute(
            "SELECT p.firing_version_id,p.state,"
            "p.published_transaction_id FROM firing_temporary_members m "
            "JOIN firing_publications p "
            "ON p.firing_version_id=m.firing_version_id "
            "WHERE m.member_kind=? AND m.member_identity=?",
            (member_kind, identity),
        ).fetchall()
        if not roots:
            self._member_visibility[cache_key] = True
            return True
        canonical = all(
            row["state"] == "PUBLISHED"
            and row["published_transaction_id"] is not None
            and self.db.execute(
                "SELECT 1 FROM events WHERE transaction_id=? "
                "AND event_type='transaction_committed/v1' AND ordinal<=?",
                (str(row["published_transaction_id"]), self.canonical_upper),
            ).fetchone() is not None
            for row in roots
        )
        root_ids = {str(row["firing_version_id"]) for row in roots}
        exact_proposal_firing = (
            bool(root_ids)
            and root_ids == self.proposal_owner_roots
            and all(row["state"] == "PROVISIONAL" for row in roots)
        )
        result = canonical or exact_proposal_firing
        self._member_visibility[cache_key] = result
        return result

    def validate_reference_visibility(self) -> None:
        """Reject references into another provisional firing's area."""

        from ..event_store import RegistryConflict

        referenced_versions: set[str] = set()
        referenced_relations: set[str] = set()
        for item in self.objects:
            referenced_versions.update(
                self.referenced_version_ids(item.metadata))
            referenced_relations.update(
                self.referenced_relation_ids(item.metadata))
        for pending in self.events:
            referenced_versions.update(
                self.referenced_version_ids(pending.payload))
            referenced_relations.update(
                self.referenced_relation_ids(pending.payload))
        for version_id in referenced_versions - set(self.new_by_version):
            roots = self.db.execute(
                "SELECT DISTINCT p.firing_version_id,p.state FROM "
                "firing_temporary_members m JOIN firing_publications p "
                "ON p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind='object' AND m.member_identity=?",
                (version_id,),
            ).fetchall()
            open_roots = {
                str(row["firing_version_id"]) for row in roots
                if row["state"] == "PROVISIONAL"
            }
            if (open_roots
                    and open_roots != self.proposal_owner_roots):
                raise RegistryConflict(
                    "authoritative payload references an object outside its "
                    "own provisional firing view")
        proposed_relation_ids = {
            str(relation.relation_id) for relation in self.relations
        }
        for relation_id in referenced_relations - proposed_relation_ids:
            roots = self.db.execute(
                "SELECT DISTINCT p.firing_version_id,p.state FROM "
                "firing_temporary_members m JOIN firing_publications p "
                "ON p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind='relation' AND m.member_identity=?",
                (relation_id,),
            ).fetchall()
            open_roots = {
                str(row["firing_version_id"]) for row in roots
                if row["state"] == "PROVISIONAL"
            }
            if (open_roots
                    and open_roots != self.proposal_owner_roots):
                raise RegistryConflict(
                    "authoritative payload references a relation outside its "
                    "own provisional firing view")

    def registered_refs_for_versions(
            self, version_ids: Iterable[str],
            ) -> set[tuple[str, str, str]]:
        versions = tuple(sorted({str(value) for value in version_ids
                                 if str(value)}))
        if not versions:
            return set()
        placeholders = ",".join("?" for _value in versions)
        return {
            (str(row["object_type"]), str(row["logical_id"]),
             str(row["version_id"]))
            for row in self.db.execute(
                "SELECT object_type,logical_id,version_id FROM objects "
                f"WHERE version_id IN ({placeholders})",
                versions,
            ).fetchall()
            if self.persisted_member_visible(
                "object", str(row["version_id"]))
        }

    def has_relation(self, relation_type: str, source_version: str,
                     target_version: str | None = None) -> bool:
        for relation in self.relations:
            if (relation.relation_type != relation_type
                    or str(getattr(
                        relation.source, "version_id", ""))
                    != source_version):
                continue
            if (target_version is None
                    or str(getattr(relation.target, "version_id", ""))
                    == target_version):
                return True
        if target_version is None:
            rows = self.db.execute(
                "SELECT relation_id FROM relations WHERE relation_type=? AND "
                "json_extract(source_json,'$.version_id')=?",
                (relation_type, source_version),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT relation_id FROM relations WHERE relation_type=? AND "
                "json_extract(source_json,'$.version_id')=? AND "
                "json_extract(target_json,'$.version_id')=?",
                (relation_type, source_version, target_version),
            ).fetchall()
        return any(self.persisted_member_visible(
            "relation", str(row["relation_id"])) for row in rows)

    def has_new_relation(self, relation_type: str, source_version: str,
                         target_version: str | None = None) -> bool:
        return any(
            relation.relation_type == relation_type
            and str(getattr(relation.source, "version_id", ""))
            == source_version
            and (target_version is None
                 or str(getattr(relation.target, "version_id", ""))
                 == target_version)
            for relation in self.relations)

    def object_metadata(
            self, logical_id: str,
            object_type: str) -> Mapping[str, Any] | None:
        cache_key = (logical_id, object_type)
        if cache_key in self._object_metadata:
            return self._object_metadata[cache_key]
        new = self.new_by_logical.get((logical_id, object_type))
        if new is not None:
            result: Mapping[str, Any] | None = dict(new.metadata)
            self._object_metadata[cache_key] = result
            return result
        rows = self.db.execute(
            "SELECT version_id,metadata_json FROM objects "
            "WHERE logical_id=? AND object_type=?",
            (logical_id, object_type)).fetchall()
        visible = [value for value in rows if self.persisted_member_visible(
            "object", str(value["version_id"]))]
        result = (json.loads(visible[0]["metadata_json"])
                  if len(visible) == 1 else None)
        self._object_metadata[cache_key] = result
        return result

    def version_exists(
            self, version_id: str, object_type: str | None = None) -> bool:
        cache_key = (version_id, object_type)
        if cache_key in self._version_exists:
            return self._version_exists[cache_key]
        new = self.new_by_version.get(version_id)
        if new is not None:
            result = object_type is None or new.object_type == object_type
        elif object_type is None:
            row = self.db.execute(
                "SELECT version_id FROM objects WHERE version_id=?",
                (version_id,)).fetchone()
            result = (row is not None and self.persisted_member_visible(
                "object", version_id))
        else:
            row = self.db.execute(
                "SELECT version_id FROM objects "
                "WHERE version_id=? AND object_type=?",
                (version_id, object_type)).fetchone()
            result = (row is not None and self.persisted_member_visible(
                "object", version_id))
        self._version_exists[cache_key] = result
        return result

    def version_metadata(
            self, version_id: str,
            object_type: str) -> Mapping[str, Any] | None:
        cache_key = (version_id, object_type)
        if cache_key in self._version_metadata:
            return self._version_metadata[cache_key]
        new = self.new_by_version.get(version_id)
        if new is not None:
            result = (dict(new.metadata)
                      if new.object_type == object_type else None)
        else:
            row = self.db.execute(
                "SELECT metadata_json FROM objects WHERE version_id=? "
                "AND object_type=?", (version_id, object_type)).fetchone()
            result = (json.loads(row["metadata_json"])
                      if row is not None and self.persisted_member_visible(
                          "object", version_id) else None)
        self._version_metadata[cache_key] = result
        return result

    def exact_ref_exists(
            self, value: object, expected_type: str | None = None) -> bool:
        if not isinstance(value, Mapping):
            return False
        version_id = str(value.get("version_id", ""))
        logical_id = str(value.get("logical_id", ""))
        entity_type = str(value.get("entity_type", ""))
        if expected_type is not None and entity_type != expected_type:
            return False
        cache_key = (entity_type, logical_id, version_id)
        if cache_key in self._exact_refs:
            return self._exact_refs[cache_key]
        new = self.new_by_version.get(version_id)
        if new is not None:
            result = (new.object_type == entity_type
                      and str(new.logical_id) == logical_id)
        else:
            row = self.db.execute(
                "SELECT logical_id,object_type FROM objects WHERE version_id=?",
                (version_id,),
            ).fetchone()
            result = (row is not None
                      and self.persisted_member_visible("object", version_id)
                      and row["logical_id"] == logical_id
                      and row["object_type"] == entity_type)
        self._exact_refs[cache_key] = result
        return result

    def exact_resource_ref_exists(self, value: object) -> bool:
        if (not isinstance(value, Mapping)
                or set(value) != {"resource_id", "resource_version_id"}):
            return False
        logical_id = str(value["resource_id"])
        version_id = str(value["resource_version_id"])
        entity_type = "resource_version/v1"
        cache_key = (entity_type, logical_id, version_id)
        if cache_key in self._exact_refs:
            return self._exact_refs[cache_key]
        new = self.new_by_version.get(version_id)
        if new is not None:
            result = (new.object_type == entity_type
                      and str(new.logical_id) == logical_id)
        else:
            row = self.db.execute(
                "SELECT logical_id,object_type FROM objects "
                "WHERE version_id=?", (version_id,)).fetchone()
            result = (row is not None
                      and self.persisted_member_visible("object", version_id)
                      and row["logical_id"] == logical_id
                      and row["object_type"] == entity_type)
        self._exact_refs[cache_key] = result
        return result

    def exact_relation_records(
            self, relation_type: str, *, source_version: str | None = None,
            target_version: str | None = None,
            ) -> list[tuple[Mapping[str, Any], Mapping[str, Any], str, str]]:
        from ..event_store import _ref_json

        records: list[
            tuple[Mapping[str, Any], Mapping[str, Any], str, str]
        ] = []
        for relation in self.relations:
            if relation.relation_type != relation_type:
                continue
            source = _ref_json(relation.source)
            target = _ref_json(relation.target)
            if (source_version is not None
                    and source.get("version_id") != source_version):
                continue
            if (target_version is not None
                    and target.get("version_id") != target_version):
                continue
            records.append((
                source, target, str(self.transaction_id), relation.strength))
        clauses = ["relation_type=?"]
        parameters: list[str] = [relation_type]
        if source_version is not None:
            clauses.append("json_extract(source_json,'$.version_id')=?")
            parameters.append(source_version)
        if target_version is not None:
            clauses.append("json_extract(target_json,'$.version_id')=?")
            parameters.append(target_version)
        rows = self.db.execute(
            "SELECT source_json,target_json,transaction_id,strength "
            "FROM relations WHERE " + " AND ".join(clauses),
            tuple(parameters),
        ).fetchall()
        for row in rows:
            source = json.loads(row["source_json"])
            target = json.loads(row["target_json"])
            if (source_version is not None
                    and source.get("version_id") != source_version):
                continue
            if (target_version is not None
                    and target.get("version_id") != target_version):
                continue
            records.append((
                source, target, str(row["transaction_id"]),
                str(row["strength"])))
        return records

    def disposition_records(
            self, event_types: set[str],
            ) -> list[tuple[str, Mapping[str, Any], str, str, str]]:
        records: list[tuple[str, Mapping[str, Any], str, str, str]] = []
        for pending in self.events:
            if pending.event_type in event_types:
                records.append((
                    pending.event_type, pending.payload,
                    str(pending.producer_invocation_id or ""),
                    str(self.task_id), pending.aggregate_id))
        placeholders = ",".join("?" for _ in event_types)
        rows = self.db.execute(
            "SELECT event_type,payload_json,producer_invocation_id,task_id,"
            "aggregate_id FROM events WHERE event_type IN ("
            f"{placeholders})", tuple(sorted(event_types)),
        ).fetchall()
        records.extend((
            str(row["event_type"]), json.loads(row["payload_json"]),
            str(row["producer_invocation_id"] or ""),
            str(row["task_id"]), str(row["aggregate_id"]),
        ) for row in rows)
        return records

    def version_transaction(self, version_id: str) -> str | None:
        if version_id in self.new_by_version:
            return str(self.transaction_id)
        row = self.db.execute(
            "SELECT transaction_id FROM objects WHERE version_id=?",
            (version_id,),
        ).fetchone()
        return str(row["transaction_id"]) if row is not None else None

    def current_version_ref_matches(
            self, value: object, object_type: str) -> bool:
        if not isinstance(value, Mapping):
            return False
        logical_id = str(value.get("logical_id", ""))
        version_id = str(value.get("version_id", ""))
        entity_type = str(value.get("entity_type", ""))
        if entity_type != object_type:
            return False
        row = self.db.execute(
            "SELECT version_id FROM objects WHERE logical_id=? AND "
            "object_type=? ORDER BY rowid DESC LIMIT 1",
            (logical_id, object_type),
        ).fetchone()
        current_version = str(row["version_id"]) if row is not None else None
        for item in self.objects:
            if (item.object_type == object_type
                    and str(item.logical_id) == logical_id):
                current_version = str(item.version_id)
        return current_version == version_id

    def unique_version_ref_matches(
            self, value: object, object_type: str) -> bool:
        if not isinstance(value, Mapping):
            return False
        logical_id = str(value.get("logical_id", ""))
        version_id = str(value.get("version_id", ""))
        entity_type = str(value.get("entity_type", ""))
        if entity_type != object_type:
            return False
        versions = [str(row["version_id"]) for row in self.db.execute(
            "SELECT version_id FROM objects WHERE logical_id=? AND "
            "object_type=? ORDER BY rowid",
            (logical_id, object_type),
        )]
        versions.extend(str(item.version_id) for item in self.objects
                        if item.object_type == object_type
                        and str(item.logical_id) == logical_id)
        return versions == [version_id]

    def latest_event_payload(
            self, event_type: str, *, event_task_id: str | None = None,
            event_net_id: str | None = None,
            include_pending: bool = True,
            ) -> Mapping[str, Any] | None:
        from ..event_store import _CANONICAL_EVENT_SQL

        clauses = ["e.event_type=?", _CANONICAL_EVENT_SQL]
        parameters: list[object] = [event_type]
        if event_task_id is not None:
            clauses.append("e.task_id=?")
            parameters.append(event_task_id)
        if event_net_id is not None:
            clauses.append("e.net_instance_id=?")
            parameters.append(event_net_id)
        row = self.db.execute(
            "SELECT e.payload_json FROM events e WHERE "
            + " AND ".join(clauses)
            + " ORDER BY e.task_control_sequence DESC, e.ordinal DESC "
            "LIMIT 1",
            tuple(parameters),
        ).fetchone()
        latest = json.loads(row["payload_json"]) if row is not None else None
        for pending in self.events if include_pending else ():
            if pending.event_type != event_type:
                continue
            if (event_task_id is not None
                    and str(self.task_id) != event_task_id):
                continue
            if (event_net_id is not None
                    and str(self.net_instance_id) != event_net_id):
                continue
            latest = pending.payload
        return latest


ProposalContext = TransactionValidationContext


__all__ = ("ProposalContext", "TransactionValidationContext")
