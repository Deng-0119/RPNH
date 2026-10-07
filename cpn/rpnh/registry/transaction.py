"""D1-C prewrite/validate/commit boundary."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping, Sequence

from .event_store import EventStore
from .identities import TypedId
from .models import (
    EventEnvelope,
    PendingEvent,
    PreparedObject,
    TypedRelation,
    VersionRef,
)
from .object_store import ObjectStore


class RegistryTransaction:
    def __init__(self, *, event_store: EventStore, object_store: ObjectStore,
                 task_id: TypedId, branch_id: str, task_round_id: TypedId | None,
                 net_instance_id: TypedId | None, idempotency_key: str,
                 writer_epoch: int) -> None:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        self.event_store = event_store
        self.object_store = object_store
        self.task_id = task_id
        self.branch_id = branch_id
        self.task_round_id = task_round_id
        self.net_instance_id = net_instance_id
        self.idempotency_key = idempotency_key
        self.writer_epoch = writer_epoch
        # The transaction identity is part of a few authoritative object
        # contracts (for example address bindings).  It therefore has to be
        # stable across an idempotent command retry, just like the command
        # identity itself.
        self.transaction_id = TypedId(
            "transaction",
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"d1-c:transaction:{task_id}:{idempotency_key}",
            ).hex,
        )
        self._objects: list[PreparedObject] = []
        self._events: list[PendingEvent] = []
        self._relations: list[TypedRelation] = []
        self._precommit_validators: list[
            Callable[[Sequence[PreparedObject]], None]] = []
        self._expected_snapshot_predecessors: dict[
            str, Mapping[str, Any] | None] = {}
        self._expected_dependency_root_predecessor: dict[str, str | None] | None = None
        self._firing_publications: list[dict[str, object]] = []
        self._workspace_head_advances: list[dict[str, str]] = []
        self._initial_ordinal = event_store.max_ordinal()
        self._initial_heads: dict[str, int] = {}
        self._closed = False
        self._expected_registry_ordinal: int | None = None

    def _initial_stream_head(self, stream_id: str) -> int:
        head = self._initial_heads.get(stream_id)
        if head is None:
            head = self.event_store.stream_head_at(
                stream_id, through_ordinal=self._initial_ordinal)
            self._initial_heads[stream_id] = head
        return head

    def prewrite(self, *, object_type: str, logical_id: TypedId,
                 version_id: TypedId, payload: bytes, metadata: Mapping[str, Any],
                 media_type: str, schema_ref: str,
                 producer_invocation_id: TypedId | None = None) -> PreparedObject:
        if self._closed:
            raise RuntimeError("transaction is closed")
        prepared = self.object_store.prewrite(
            object_type=object_type, logical_id=logical_id, version_id=version_id,
            payload=payload, metadata=metadata, media_type=media_type,
            schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id)
        self._objects.append(prepared)
        return prepared

    def prewrite_descriptor(
            self, *, object_type: str, logical_id: TypedId,
            version_id: TypedId, descriptor: int,
            expected_size: int, metadata: Mapping[str, Any], media_type: str,
            schema_ref: str,
            producer_invocation_id: TypedId | None = None) -> PreparedObject:
        if self._closed:
            raise RuntimeError("transaction is closed")
        prepared = self.object_store.prewrite_descriptor(
            object_type=object_type, logical_id=logical_id,
            version_id=version_id, descriptor=descriptor,
            expected_size=expected_size,
            metadata=metadata, media_type=media_type,
            schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id)
        self._objects.append(prepared)
        return prepared

    def prewrite_with_metadata_factory(
            self, *, object_type: str, logical_id: TypedId,
            version_id: TypedId, payload: bytes,
            metadata_factory: Callable[[int], Mapping[str, Any]],
            media_type: str, schema_ref: str,
            producer_invocation_id: TypedId | None = None) -> PreparedObject:
        if self._closed:
            raise RuntimeError("transaction is closed")
        prepared = self.object_store.prewrite_with_metadata_factory(
            object_type=object_type, logical_id=logical_id,
            version_id=version_id, payload=payload,
            metadata_factory=metadata_factory, media_type=media_type,
            schema_ref=schema_ref,
            producer_invocation_id=producer_invocation_id)
        self._objects.append(prepared)
        return prepared

    def append(self, event: PendingEvent) -> None:
        if self._closed:
            raise RuntimeError("transaction is closed")
        self._events.append(event)

    def relate(
            self, relation: TypedRelation, *,
            producer_invocation_id: TypedId | None = None,
            system_owned: bool = False) -> None:
        if self._closed:
            raise RuntimeError("transaction is closed")
        if (not isinstance(relation, TypedRelation)
                or (producer_invocation_id is not None
                    and producer_invocation_id.kind != "invocation")
                or (relation.producer_invocation_id is not None
                    and producer_invocation_id is not None
                    and relation.producer_invocation_id
                    != producer_invocation_id)
                or not isinstance(system_owned, bool)):
            raise TypeError(
                "relation ownership declaration is malformed")
        owner = (relation.producer_invocation_id
                 if relation.producer_invocation_id is not None
                 else producer_invocation_id)
        exact_system_owned = relation.system_owned or system_owned
        if ((owner is None and not exact_system_owned)
                or (owner is not None and exact_system_owned)):
            raise TypeError(
                "relation requires exactly one invocation or system owner")
        self._relations.append(
            relation if (owner == relation.producer_invocation_id
                         and exact_system_owned == relation.system_owned)
            else TypedRelation(
                relation.relation_id, relation.relation_type,
                relation.source, relation.target, relation.strength,
                relation.metadata, owner, exact_system_owned))

    def next_stream_sequence(self, stream_id: str) -> int:
        """Return the sequence the next append will receive in this transaction."""
        return self._initial_stream_head(stream_id) + sum(
            event.stream_id == stream_id for event in self._events) + 1

    def validate_before_commit(
            self, validator: Callable[[Sequence[PreparedObject]], None]) -> None:
        if self._closed or not callable(validator):
            raise RuntimeError("transaction precommit validator is unavailable")
        self._precommit_validators.append(validator)

    def expect_registry_ordinal(self, ordinal: int) -> None:
        """Require a complete target head under the native write transaction.

        This is an admission CAS, not a lease or a new permission. Exact command
        replay retains the normal idempotent semantics after subsequent writes.
        """
        if self._closed or type(ordinal) is not int or ordinal < 0:
            raise ValueError("expected Registry ordinal must be nonnegative")
        if self._expected_registry_ordinal is not None:
            raise RuntimeError("expected Registry ordinal is already fixed")
        self._expected_registry_ordinal = ordinal

    def expect_snapshot_predecessor(
            self, stream_id: str,
            snapshot_ref: Mapping[str, Any] | None) -> None:
        if self._closed or not stream_id:
            raise RuntimeError("transaction snapshot predecessor is unavailable")
        if stream_id in self._expected_snapshot_predecessors:
            raise RuntimeError("transaction snapshot predecessor was already fixed")
        self._expected_snapshot_predecessors[stream_id] = (
            dict(snapshot_ref) if snapshot_ref is not None else None)

    def expect_current_stream_head(self, stream_id: str) -> None:
        """Refresh one late-bound CAS stream before its first staged append."""

        if (self._closed or not isinstance(stream_id, str) or not stream_id
                or any(event.stream_id == stream_id for event in self._events)):
            raise RuntimeError(
                "transaction stream-head expectation is unavailable")
        self._initial_heads[stream_id] = self.event_store.stream_heads().get(
            stream_id, 0)

    def _expect_dependency_root_predecessor(
            self, *, root_ref: VersionRef,
            predecessor: VersionRef | None) -> None:
        """Fix the private dependency-root CAS condition for this commit."""

        if self._closed or self._expected_dependency_root_predecessor is not None:
            raise RuntimeError(
                "transaction dependency root predecessor is unavailable")
        if (root_ref.entity_type != "dependency_root_index/v1"
                or root_ref.entity_id.kind != "dependency_root_index"
                or root_ref.version_id.kind != "dependency_root_index_version"):
            raise TypeError("dependency root CAS requires one exact root ref")
        if (predecessor is not None
                and (predecessor.entity_type != root_ref.entity_type
                     or predecessor.entity_id != root_ref.entity_id
                     or predecessor.version_id.kind
                     != "dependency_root_index_version")):
            raise TypeError(
                "dependency root predecessor belongs to another lineage")
        self._expected_dependency_root_predecessor = {
            "logical_id": str(root_ref.entity_id),
            "expected_version_id": (
                str(predecessor.version_id)
                if predecessor is not None else None),
        }

    def publish_firing(
            self, *, firing_ref: VersionRef,
            invocation_ref: VersionRef,
            admission_checkpoint_ref: VersionRef,
            expected_parent_checkpoint_ref: VersionRef,
            operation_result_ref: VersionRef,
            workspace_lineage_ref: VersionRef | None,
            expected_workspace_head_ref: VersionRef | None,
            workspace_revision_ref: VersionRef | None,
            marking_checkpoint_ref: VersionRef,
            marking_net_ref: VersionRef) -> None:
        """CAS-promote one exact provisional firing at this commit."""

        refs = (
            (firing_ref, "transition_firing/v1"),
            (invocation_ref, "invocation/v1"),
            (admission_checkpoint_ref, "marking_checkpoint/v1"),
            (expected_parent_checkpoint_ref, "marking_checkpoint/v1"),
            (operation_result_ref, "operation_result/v1"),
            (marking_checkpoint_ref, "marking_checkpoint/v1"),
        )
        workspace_refs = (
            workspace_lineage_ref,
            expected_workspace_head_ref,
            workspace_revision_ref,
        )
        has_workspace = workspace_lineage_ref is not None
        if (self._closed
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != expected
                       for ref, expected in refs)
                or any((ref is not None) != has_workspace
                       for ref in workspace_refs)
                or (has_workspace and (
                    any(not isinstance(ref, VersionRef)
                        or ref.entity_type != "workspace_revision/v1"
                        for ref in workspace_refs)
                    or workspace_lineage_ref.entity_id
                    != expected_workspace_head_ref.entity_id
                    or workspace_lineage_ref.entity_id
                    != workspace_revision_ref.entity_id))
                or not isinstance(marking_net_ref, VersionRef)
                or marking_net_ref.entity_type != "net_instance/v1"
                or self._firing_publications
                or self._workspace_head_advances):
            raise RuntimeError(
                "transaction permits exactly one firing publication root")
        self._firing_publications.append({
            "firing_version_id": str(firing_ref.version_id),
            "invocation_version_id": str(invocation_ref.version_id),
            "admission_checkpoint_version_id": str(
                admission_checkpoint_ref.version_id),
            "expected_parent_checkpoint_version_id": str(
                expected_parent_checkpoint_ref.version_id),
            "operation_result_version_id": str(operation_result_ref.version_id),
            "workspace_lineage_id": (
                str(workspace_lineage_ref.entity_id)
                if workspace_lineage_ref is not None else None),
            "expected_workspace_head_version_id": (
                str(expected_workspace_head_ref.version_id)
                if expected_workspace_head_ref is not None else None),
            "workspace_revision_version_id": (
                str(workspace_revision_ref.version_id)
                if workspace_revision_ref is not None else None),
            "marking_checkpoint_version_id": str(
                marking_checkpoint_ref.version_id),
            "marking_net_version_id": str(
                marking_net_ref.version_id),
        })

    def advance_workspace_head(
            self, *, lineage_ref: VersionRef, expected_head_ref: VersionRef,
            successor_ref: VersionRef, authority_ref: VersionRef) -> None:
        """Atomically advance one non-firing workspace lineage head."""

        workspace_refs = (lineage_ref, expected_head_ref, successor_ref)
        if (self._closed
                or self._firing_publications
                or any(not isinstance(ref, VersionRef)
                       or ref.entity_type != "workspace_revision/v1"
                       or ref.entity_id.kind != "workspace_lineage"
                       or ref.version_id.kind != "workspace_revision"
                       for ref in workspace_refs)
                or any(ref.entity_id != lineage_ref.entity_id
                       for ref in workspace_refs)
                or expected_head_ref.version_id == successor_ref.version_id
                or not isinstance(authority_ref, VersionRef)
                or not isinstance(authority_ref.entity_type, str)
                or not authority_ref.entity_type
                or not isinstance(authority_ref.entity_id, TypedId)
                or not isinstance(authority_ref.version_id, TypedId)):
            raise TypeError(
                "workspace head advance requires exact workspace and authority refs")
        if any(item["workspace_lineage_id"] == str(lineage_ref.entity_id)
               for item in self._workspace_head_advances):
            raise RuntimeError(
                "transaction may advance each workspace lineage only once")
        self._workspace_head_advances.append({
            "workspace_lineage_id": str(lineage_ref.entity_id),
            "expected_workspace_head_version_id": str(expected_head_ref.version_id),
            "workspace_revision_version_id": str(successor_ref.version_id),
            "authority_entity_type": authority_ref.entity_type,
            "authority_logical_id": str(authority_ref.entity_id),
            "authority_version_id": str(authority_ref.version_id),
        })

    def commit(self) -> tuple[EventEnvelope, ...]:
        if self._closed:
            raise RuntimeError("transaction is closed")
        business_settlements = sum(
            event.event_type == "transition_firing_settled/v1"
            for event in self._events)
        if (business_settlements > 1
                or bool(business_settlements)
                != bool(self._firing_publications)
                or (self._firing_publications and business_settlements != 1)):
            raise RuntimeError(
                "transaction may settle and publish exactly one firing")
        for item in self._objects:
            self.object_store.verify_prepared(item)
            self._events.append(PendingEvent(
                event_type="object_version_published/v1", criticality="authoritative",
                stream_id=f"object:{item.logical_id}", aggregate_id=str(item.logical_id),
                aggregate_type=item.object_type, idempotency_key=self.idempotency_key,
                command_id=self.idempotency_key,
                payload={
                    "logical_id": str(item.logical_id), "version_id": str(item.version_id),
                    "object_type": item.object_type, "size": item.size,
                    "media_type": item.media_type, "schema_ref": item.schema_ref,
                    "storage_locator": item.storage_locator, "metadata": dict(item.metadata),
                }, payload_schema_ref="registry_v1/object_version_published/v1",
                producer_invocation_id=item.producer_invocation_id))
        for relation in self._relations:
            def _endpoint(ref):
                value = {"entity_type": ref.entity_type, "entity_id": str(ref.entity_id)}
                if hasattr(ref, "version_id"):
                    value["version_id"] = str(ref.version_id)
                return value
            self._events.append(PendingEvent(
                event_type="relation_published/v1", criticality="authoritative",
                stream_id=f"relation:{relation.relation_id}",
                aggregate_id=str(relation.relation_id), aggregate_type="typed_relation/v1",
                idempotency_key=self.idempotency_key, command_id=self.idempotency_key,
                payload={"relation_id": str(relation.relation_id),
                         "relation_type": relation.relation_type,
                         "source": _endpoint(relation.source),
                         "target": _endpoint(relation.target),
                         "strength": relation.strength,
                         "metadata": dict(relation.metadata)},
                payload_schema_ref="registry_v1/relation_published/v1",
                producer_invocation_id=relation.producer_invocation_id))
        self._events.append(PendingEvent(
            event_type="transaction_committed/v1", criticality="authoritative",
            stream_id=f"transaction:{self.transaction_id}",
            aggregate_id=str(self.transaction_id), aggregate_type="transaction",
            idempotency_key=self.idempotency_key, command_id=self.idempotency_key,
            payload={"object_count": len(self._objects), "relation_count": len(self._relations),
                     "fact_count": len(self._events)},
            payload_schema_ref="registry_v1/transaction_committed/v1"))
        for validator in self._precommit_validators:
            validator(tuple(self._objects))
        expected = {event.stream_id: self._initial_stream_head(event.stream_id)
                    for event in self._events}
        result = self.event_store.publish_batch(
            task_id=self.task_id, branch_id=self.branch_id,
            task_round_id=self.task_round_id, net_instance_id=self.net_instance_id,
            transaction_id=self.transaction_id, idempotency_key=self.idempotency_key,
            writer_epoch=self.writer_epoch, objects=tuple(self._objects),
            events=tuple(self._events), relations=tuple(self._relations),
            expected_heads=expected,
            expected_snapshot_predecessors=(
                self._expected_snapshot_predecessors),
            expected_dependency_root_predecessor=(
                self._expected_dependency_root_predecessor),
            firing_publications=tuple(self._firing_publications),
            workspace_head_advances=tuple(self._workspace_head_advances),
            expected_registry_ordinal=self._expected_registry_ordinal)
        self._closed = True
        return result

    def abort(self, reason: str) -> tuple[EventEnvelope, ...]:
        if self._closed:
            raise RuntimeError("transaction is closed")
        result = self.event_store.abort(
            task_id=self.task_id, branch_id=self.branch_id,
            transaction_id=self.transaction_id,
            idempotency_key=self.idempotency_key, writer_epoch=self.writer_epoch,
            reason=reason)
        self._closed = True
        return result
