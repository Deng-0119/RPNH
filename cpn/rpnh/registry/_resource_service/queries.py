"""Queries operations for the live Registry resource kernel."""

from __future__ import annotations
from cpn.rpnh.registry.publication import (_NO_CONTENT_SCHEMA_INSTANCE, _content_schema_instance, _content_schema_source_from_payload, _effective_operation_binding_metadata, _fresh_reference_resource_ref, _effective_firing_resource_access_events, _origin_payload, _fresh_reference_resource_metadata, _fresh_bootstrap_reference_resource_metadata, _append_direct_resource_version_publication, _ref_payload, _resource_from_payload, _stable_id, _version_from_payload)
from cpn.rpnh.resource_access import (ResourceReadContract)
import json
import os
import threading
import traceback
import uuid
from dataclasses import (asdict)
from datetime import (datetime, timedelta, timezone)
from pathlib import (Path, PurePath)
from typing import (Any, Iterable, Mapping, Sequence)
from jsonschema import (Draft7Validator)
from cpn.rpnh.registry._registry import (RegistryReadError, _RegistryCore)
from cpn.rpnh.registry.errors import (DeliveryAcknowledgementConflict, DeliveryOutcomeUnknown, MissingResourceAddress, PathPublicationFault, ReleaseAuthorizationDenied, ReleaseWitnessConsumed, ResourceAddressConflict, ResourceIdempotencyConflict, ResourceIntegrityFault, ResourcePayloadSchemaViolation, ResourceServiceError, ResourceSchemaViolation, StaleAuthorityHead, StaleInvocationContext, StaleQueryContext, StaleWriterFence, UnauthorizedResourceDelivery, UndeclaredPublicationOrigin, UnknownResourceVersion, UnmonitorableDeliveryBoundary, WrongNetOutputBinding)
from cpn.rpnh.registry.event_store import (FiringView, RegistryConflict, RegistryAuthorityView, StaleWriterError)
from cpn.rpnh.registry.content_schemas import (hydrate_registered_content_schema)
from cpn.rpnh.registry.identities import (TypedId, fresh_bootstrap_resource_id)
from cpn.rpnh.registry.invocations import (InvocationAdmissionError, InvocationContext, InvocationLifecycle)
from cpn.rpnh.registry.models import (PendingEvent, TypedRelation, VersionRef)
from cpn.rpnh.registry.operations import (RegisteredContentSchemaAuthority)
from cpn.rpnh.registry.resources import (AcknowledgeResourceDelivery, AddressBindingIntent, AuthorizedResourceRelease, AuthorizeResourceRelease, BindResourceAddress, CheckpointRepairOrigin, OpaqueBrokerChannelRef, PreparedResourceDelivery, PrepareResourceDelivery, PrivateSystemOrigin, PublishPathResource, PublicationOrigin, PublishResource, PublishResourceWithoutPayload, QueryCursor, RegistryHead, RegistryObserverContext, RelationQuery, RelationQueryResult, ResourceAddress, ResourceAddressBindingRef, ResourceAddressResult, ResourceDeliveryReceipt, ResourceHeader, ResourceQuery, ResourceQueryResult, ResourceRelation, ResourceVersionRef, SafeDescriptor, UnbindResourceAddress)
from cpn.rpnh.registry.schema_catalog import (canonical_json, canonical_text)
from .common import (
    _OBSERVER_SAFE_FIELDS,
    _fresh_checkpoint_repair_resource_metadata,
    _parse_timestamp,
    _resolve_registry_workspace_root,
    _resource_payload,
    _timestamp,
    _utc_now,
)

def _head(self, *, ordinal: int | None = None) -> RegistryHead:
    core = self._ResourceServiceKernel__core
    current_ordinal = core.event_store.max_ordinal()
    chosen = current_ordinal if ordinal is None else ordinal
    if chosen < 0 or chosen > current_ordinal:
        raise StaleAuthorityHead("registry head is outside committed history")
    writer_epoch = core.event_store.writer_epoch
    if chosen == current_ordinal:
        cached = self._ResourceServiceKernel__current_head_cache
        if (cached is not None and cached.ordinal == current_ordinal
                and cached.writer_fencing_epoch == writer_epoch):
            return cached
        with core.event_store.connect() as db:
            task_row = db.execute(
                "SELECT sequence FROM task_control_heads WHERE task_id=?",
                (str(core.task_id),),
            ).fetchone()
            stream_rows = db.execute(
                "SELECT stream_id,sequence FROM stream_heads "
                "ORDER BY stream_id",
            ).fetchall()
        head = RegistryHead(
            ordinal=current_ordinal,
            writer_fencing_epoch=writer_epoch,
            task_control_sequence=(
                int(task_row["sequence"]) if task_row is not None else 0),
            stream_heads={
                str(row["stream_id"]): int(row["sequence"])
                for row in stream_rows
            },
        )
        self._ResourceServiceKernel__current_head_cache = head
        return head
    with core.event_store.connect() as db:
        task_row = db.execute(
            "SELECT MAX(task_control_sequence) FROM events "
            "WHERE task_id=? AND ordinal<=?",
            (str(core.task_id), chosen),
        ).fetchone()
        writer_row = db.execute(
            "SELECT writer_fencing_epoch FROM events WHERE ordinal<=? "
            "ORDER BY ordinal DESC LIMIT 1",
            (chosen,),
        ).fetchone()
        stream_rows = db.execute(
            "SELECT stream_id,MAX(stream_sequence) AS sequence FROM events "
            "WHERE ordinal<=? GROUP BY stream_id ORDER BY stream_id",
            (chosen,),
        ).fetchall()
    return RegistryHead(
        ordinal=chosen,
        writer_fencing_epoch=(
            int(writer_row["writer_fencing_epoch"])
            if writer_row is not None else writer_epoch),
        task_control_sequence=int(task_row[0] or 0) if task_row else 0,
        stream_heads={
            str(row["stream_id"]): int(row["sequence"])
            for row in stream_rows
        },
    )

def _historical_heads(
        self, ordinals: Iterable[int],
) -> Mapping[int, RegistryHead]:
    """Build several historical heads from one exact committed-history pass."""

    requested = tuple(dict.fromkeys(ordinals))
    if any(type(ordinal) is not int for ordinal in requested):
        raise TypeError("historical Registry head ordinals must be integers")
    if not requested:
        return {}
    core = self._ResourceServiceKernel__core
    current_ordinal = core.event_store.max_ordinal()
    if min(requested) < 0 or max(requested) > current_ordinal:
        raise StaleAuthorityHead("registry head is outside committed history")
    current_writer_epoch = core.event_store.writer_epoch
    historical = set(requested)
    heads: dict[int, RegistryHead] = {}
    if current_ordinal in historical:
        heads[current_ordinal] = self._head()
        historical.remove(current_ordinal)
    if not historical:
        return heads

    stream_heads: dict[str, int] = {}
    task_control_sequence = 0
    historical_writer_epoch = current_writer_epoch

    def capture(ordinal: int) -> None:
        heads[ordinal] = RegistryHead(
            ordinal=ordinal,
            writer_fencing_epoch=historical_writer_epoch,
            task_control_sequence=task_control_sequence,
            stream_heads=dict(stream_heads),
        )

    if 0 in historical:
        capture(0)
    highest = max(historical)
    with core.event_store.connect() as db:
        rows = db.execute(
            "SELECT ordinal,stream_id,stream_sequence,task_id,"
            "task_control_sequence,writer_fencing_epoch "
            "FROM events WHERE ordinal<=? "
            "ORDER BY ordinal",
            (highest,),
        )
        for row in rows:
            ordinal = int(row["ordinal"])
            historical_writer_epoch = int(
                row["writer_fencing_epoch"])
            stream_heads[str(row["stream_id"])] = int(
                row["stream_sequence"])
            if (str(row["task_id"]) == str(core.task_id)
                    and row["task_control_sequence"] is not None):
                task_control_sequence = max(
                    task_control_sequence,
                    int(row["task_control_sequence"]),
                )
            if ordinal in historical:
                capture(ordinal)
    if (len(heads) != len(requested)
            or core.event_store.writer_epoch != current_writer_epoch):
        raise StaleAuthorityHead(
            "historical Registry head snapshot changed during hydration")
    return heads

def _canonical_heads(
        self, ordinals: Iterable[int | None],
) -> Mapping[int, RegistryHead]:
    """Build each head from facts canonically visible at that exact cut."""

    requested_values = tuple(dict.fromkeys(ordinals))
    if any(value is not None and type(value) is not int
           for value in requested_values):
        raise TypeError("canonical Registry head ordinals must be integers")
    if not requested_values:
        return {}
    store = self._ResourceServiceKernel__core.event_store
    maximum_ordinal = store.max_ordinal()
    current = store.canonical_events(through_ordinal=maximum_ordinal)
    current_ordinal = current[-1].ordinal if current else 0
    requested = tuple(dict.fromkeys(
        current_ordinal if value is None else value
        for value in requested_values))
    if min(requested) < 0 or max(requested) > current_ordinal:
        raise StaleAuthorityHead(
            "canonical Registry head is outside published history")
    writer_epoch = store.writer_epoch
    heads: dict[int, RegistryHead] = {}
    for ordinal in requested:
        # Promotion publishes earlier events at the Success commit, not at their
        # original event ordinals. A prefix of today's canonical events would
        # therefore rewrite a head captured before that promotion.
        events = store.canonical_events(through_ordinal=ordinal)
        if ordinal and (not events or events[-1].ordinal != ordinal):
            raise StaleAuthorityHead(
                "canonical Registry head snapshot changed during hydration")
        stream_heads: dict[str, int] = {}
        task_control_sequence = 0
        for event in events:
            stream_heads[event.stream_id] = event.stream_sequence
            if (event.task_id == self._ResourceServiceKernel__core.task_id
                    and event.task_control_sequence is not None):
                task_control_sequence = max(
                    task_control_sequence, event.task_control_sequence)
        heads[ordinal] = RegistryHead(
            ordinal=ordinal,
            writer_fencing_epoch=writer_epoch,
            task_control_sequence=task_control_sequence,
            stream_heads=dict(stream_heads),
        )

    if store.writer_epoch != writer_epoch:
        raise StaleAuthorityHead(
            "canonical Registry head snapshot changed during hydration")
    return heads

def _canonical_head(self, *, ordinal: int | None = None) -> RegistryHead:
    """Build a resource-query head from permanently visible facts only."""

    heads = self._canonical_heads((ordinal,))
    return next(iter(heads.values()))

def head(
        self, context: InvocationContext | RegistryObserverContext, *,
        ordinal: int | None = None) -> RegistryHead:
    self._query_authority(
        context, observer_fields=("projection_head",))
    return self._canonical_head(ordinal=ordinal)

def _observer_authority(
        self, context: RegistryObserverContext, *,
        required_fields: Iterable[str] = ()) -> VersionRef:
    material = {
        "observer_principal_ref": _ref_payload(context.observer_principal_ref),
        "observer_profile_ref": _ref_payload(context.observer_profile_ref),
        "task_ref": _ref_payload(context.task_ref),
        "grant_ref": _ref_payload(context.grant_ref),
        "issued_writer_fencing_epoch": context.issued_writer_fencing_epoch,
        "issued_task_control_sequence": context.issued_task_control_sequence,
        "reader_fence": context.reader_fence, "expires_at": context.expires_at,
        "purpose": context.purpose,
    }
    grant = self._exact_object(
        context.grant_ref, expected_type="registry_observer_grant/v1")
    safe_fields = grant.metadata.get("safe_fields")
    if (not isinstance(safe_fields, list)
            or not safe_fields
            or any(not isinstance(field, str) for field in safe_fields)
            or len(set(safe_fields)) != len(safe_fields)
            or not set(safe_fields).issubset(_OBSERVER_SAFE_FIELDS)):
        raise StaleQueryContext("observer grant has an invalid safe-field profile")
    missing = set(required_fields) - set(safe_fields)
    if missing:
        raise UnauthorizedResourceDelivery(
            f"observer grant lacks safe fields: {sorted(missing)}")
    if (grant.metadata.get("observer_principal_ref")
            != _ref_payload(context.observer_principal_ref)
            or grant.metadata.get("observer_profile_ref")
            != _ref_payload(context.observer_profile_ref)
            or grant.metadata.get("task_ref") != _ref_payload(context.task_ref)
            or grant.metadata.get("reader_fence") != context.reader_fence
            or grant.metadata.get("expires_at") != context.expires_at
            or grant.metadata.get("purpose") != context.purpose
            or context.task_ref.entity_id != self._ResourceServiceKernel__core.task_id):
        raise StaleQueryContext("observer grant does not match its context")
    current = self._head()
    if (context.issued_writer_fencing_epoch
            != current.writer_fencing_epoch
            or context.issued_task_control_sequence
            != current.task_control_sequence):
        raise StaleQueryContext("observer governance head changed")
    if _parse_timestamp(context.expires_at) <= _utc_now():
        raise StaleQueryContext("observer grant expired")
    if self._ResourceServiceKernel__core.writer_epoch != self._ResourceServiceKernel__core.event_store.writer_epoch:
        raise StaleWriterFence("observer writer fence is stale")
    return context.grant_ref

def _query_authority(
        self, context: InvocationContext | RegistryObserverContext,
        resource_ref: ResourceVersionRef | None = None, *,
        observer_fields: Iterable[str] = ()) -> tuple[VersionRef, VersionRef]:
    if isinstance(context, InvocationContext):
        self._revalidate_invocation(context, boundary="resource-metadata-query")
        grant = context.operation_binding_ref
        if resource_ref is not None:
            self._authorize_resource(
                context, grant, resource_ref, metadata_only=True)
        return context.principal_ref, grant
    grant = self._observer_authority(
        context, required_fields=observer_fields)
    return context.observer_principal_ref, grant

def _published_ordinal(self, version_id: TypedId) -> int:
    with self._ResourceServiceKernel__core.event_store.connect() as db:
        row = db.execute(
            "SELECT e.ordinal FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id WHERE o.version_id=?",
            (str(version_id),),
        ).fetchone()
    if row is None:
        raise UnknownResourceVersion(str(version_id))
    return int(row[0])

def _canonical_published_ordinal(
        self, version_id: TypedId, *, through_ordinal: int) -> int:
    ordinal = self._ResourceServiceKernel__core.event_store.canonical_object_publication_ordinal(
        version_id, through_ordinal=through_ordinal)
    if ordinal is None:
        raise UnknownResourceVersion(
            "resource is not authoritative at the requested Registry head")
    return ordinal

def _header(self, ref: ResourceVersionRef, *, through_head: RegistryHead) -> ResourceHeader:
    prepared = self._canonical_prepared(
        ref, through_ordinal=through_head.ordinal)
    ordinal = self._canonical_published_ordinal(
        ref.resource_version_id, through_ordinal=through_head.ordinal)
    metadata = dict(prepared.metadata)
    relation_kinds = sorted({
        row["relation_type"]
        for row in self._ResourceServiceKernel__core.event_store.canonical_relation_rows(
            through_ordinal=through_head.ordinal,
            version_id=ref.resource_version_id)
    })
    descriptor_labels = tuple(
        SafeDescriptor(
            str(name), tuple(value) if isinstance(value, list)
            else value if isinstance(value, tuple) else (value,))
        for name, value in sorted(metadata.get("descriptors", {}).items())
    )
    return ResourceHeader(
        ref=ref,
        task_ref=_version_from_payload(metadata["task_ref"]),
        round_ref=(_version_from_payload(metadata["round_ref"])
                   if metadata["round_ref"] else None),
        net_ref=(_version_from_payload(metadata["net_ref"])
                 if metadata["net_ref"] else None),
        producer_ref=_version_from_payload(metadata["producer_ref"]),
        origin_kind=metadata["origin_kind"],
        media_type=prepared.media_type, byte_size=prepared.size,
        content_schema_ref=metadata.get("content_schema_ref"),
        content_schema_authority_ref=(
            _content_schema_source_from_payload(
                metadata.get("content_schema_authority_ref"))),
        display_summary=metadata.get("summary"),
        descriptor_labels=descriptor_labels,
        relation_kinds=tuple(relation_kinds),
        published_at_head=self._canonical_head(ordinal=ordinal))

def _firing_header(
        self, context: InvocationContext, ref: ResourceVersionRef, *,
        view: FiringView | None = None, prepared: Any = None,
) -> ResourceHeader:
    """Build one header through the exact canonical-plus-firing view."""

    captured = view or self._ResourceServiceKernel__core.event_store.firing_view(
        firing_version_id=context.own_transition_firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    resource = prepared or self._firing_prepared(
        context, ref, view=captured)
    row = self._ResourceServiceKernel__core.event_store.object_row_for_view(
        captured, ref.resource_version_id)
    if row is None:
        raise UnknownResourceVersion(
            "resource is outside the exact firing header authority")
    published = self._ResourceServiceKernel__core.event_store.event_by_id(TypedId.parse(
        str(row["published_event_id"]), expected="event"))
    if published is None or published.event_type != "object_version_published/v1":
        raise ResourceIntegrityFault(
            "firing-visible resource lacks its publication event")
    metadata = dict(resource.metadata)
    relation_kinds = tuple(sorted({
        str(relation["relation_type"])
        for relation in self._ResourceServiceKernel__core.event_store.relation_rows_for_view(
            captured, version_id=ref.resource_version_id)
    }))
    descriptor_labels = tuple(
        SafeDescriptor(
            str(name), tuple(value) if isinstance(value, list)
            else value if isinstance(value, tuple) else (value,))
        for name, value in sorted(metadata.get("descriptors", {}).items())
    )
    return ResourceHeader(
        ref=ref,
        task_ref=_version_from_payload(metadata["task_ref"]),
        round_ref=(_version_from_payload(metadata["round_ref"])
                   if metadata["round_ref"] else None),
        net_ref=(_version_from_payload(metadata["net_ref"])
                 if metadata["net_ref"] else None),
        producer_ref=_version_from_payload(metadata["producer_ref"]),
        origin_kind=metadata["origin_kind"],
        media_type=resource.media_type, byte_size=resource.size,
        content_schema_ref=metadata.get("content_schema_ref"),
        content_schema_authority_ref=(
            _content_schema_source_from_payload(
                metadata.get("content_schema_authority_ref"))),
        display_summary=metadata.get("summary"),
        descriptor_labels=descriptor_labels,
        relation_kinds=relation_kinds,
        published_at_head=self._head(ordinal=published.ordinal))

def get_header(
        self, context: InvocationContext | RegistryObserverContext,
        ref: ResourceVersionRef) -> ResourceHeader:
    self._query_authority(
        context, ref if isinstance(context, InvocationContext) else None,
        observer_fields=("headers",))
    return self._header(ref, through_head=self._canonical_head())

def _visible_refs(
        self, context: InvocationContext | RegistryObserverContext,
        *, through_ordinal: int) -> set[ResourceVersionRef]:
    rows = []
    for row in self._ResourceServiceKernel__core.event_store.canonical_object_rows(
            through_ordinal=through_ordinal,
            object_type="resource_version/v1"):
        version = TypedId.parse(row["version_id"], expected="resource_version")
        rows.append(ResourceVersionRef(
            TypedId.parse(row["logical_id"], expected="resource"), version))
    if isinstance(context, RegistryObserverContext):
        self._observer_authority(context)
        return set(rows)
    binding = self._exact_object(
        context.operation_binding_ref, expected_type="operation_binding/v1")
    if binding.metadata.get("task_header_query"):
        return set(rows)
    visible: set[ResourceVersionRef] = set()
    for ref in rows:
        try:
            self._authorize_resource(
                context, context.operation_binding_ref, ref, metadata_only=True)
        except UnauthorizedResourceDelivery:
            continue
        visible.add(ref)
    grants = self._ResourceServiceKernel__core.event_store.canonical_object_rows(
        through_ordinal=through_ordinal,
        object_type="capability_grant/v1")
    for row in grants:
        metadata = json.loads(row["metadata_json"])
        for value in metadata.get("resource_version_ids", []):
            candidate = next(
                (item for item in rows if str(item.resource_version_id) == value), None)
            if candidate is None:
                continue
            grant_ref = VersionRef(
                "capability_grant/v1",
                TypedId.parse(row["logical_id"], expected="grant"),
                TypedId.parse(row["version_id"]),
            )
            if self._grant_allows(context, grant_ref, candidate):
                visible.add(candidate)
    return visible

def _authority_facts(
        self, context: InvocationContext | RegistryObserverContext,
) -> Mapping[str, Any]:
    if isinstance(context, RegistryObserverContext):
        self._observer_authority(context)
        return {
            "observer_principal_ref": _ref_payload(
                context.observer_principal_ref),
            "observer_profile_ref": _ref_payload(
                context.observer_profile_ref),
            "grant_ref": _ref_payload(context.grant_ref),
            "reader_fence": context.reader_fence,
            "writer": self._ResourceServiceKernel__core.event_store.writer_epoch,
        }
    self._revalidate_invocation(context, boundary="resource-query-page")
    grant_states = [
        (event.aggregate_id, event.event_type, event.stream_sequence)
        for event in self._ResourceServiceKernel__core.event_store.canonical_events()
        if event.event_type.startswith("capability_")
        and (
            event.payload.get("target_invocation_id")
            == str(context.invocation_ref.entity_id)
            or event.aggregate_id in {
                row["logical_id"]
                for row in self._ResourceServiceKernel__core.event_store.canonical_object_rows(
                    object_type="capability_grant/v1")
                if json.loads(row["metadata_json"]).get("target_invocation_id")
                == str(context.invocation_ref.entity_id)
            }
        )
    ]
    return {
        "invocation_ref": _ref_payload(context.invocation_ref),
        "binding": _ref_payload(context.operation_binding_ref),
        "grant_states": grant_states,
        "writer": self._ResourceServiceKernel__core.event_store.writer_epoch,
    }

def _query_material(query: ResourceQuery) -> dict[str, Any]:
    return {
        "task_ref": _ref_payload(query.task_ref),
        "scope_refs": [_ref_payload(item) for item in query.scope_refs],
        "producer_refs": [_ref_payload(item) for item in query.producer_refs],
        "relation_filters": [asdict(item) for item in query.relation_filters],
        "media_types": list(query.media_types),
        "address_name": query.address_name,
        "limit": query.limit,
    }

def _validate_query_head(self, head: RegistryHead) -> None:
    current = self._canonical_head()
    if (head.ordinal < 0 or head.ordinal > current.ordinal
            or head.writer_fencing_epoch != current.writer_fencing_epoch):
        raise StaleQueryContext("query snapshot head is invalid or stale")
    if head != self._canonical_head(ordinal=head.ordinal):
        raise StaleQueryContext("query snapshot head does not name committed history")

def _cursor_for(
        self, *, query_facts: Mapping[str, Any], principal_ref: VersionRef,
        grant_ref: VersionRef, through_head: RegistryHead, offset: int,
        authority_facts: Mapping[str, Any]) -> QueryCursor:
    return QueryCursor(
        query_facts, principal_ref, grant_ref, through_head, offset,
        authority_facts)

def _validate_cursor(
        self, cursor: QueryCursor, *, query_facts: Mapping[str, Any],
        principal_ref: VersionRef, grant_ref: VersionRef,
        authority_facts: Mapping[str, Any]) -> None:
    if (cursor.query_facts != query_facts
            or cursor.principal_ref != principal_ref
            or cursor.grant_ref != grant_ref
            or cursor.authority_facts != authority_facts
            or cursor.offset < 0):
        raise StaleQueryContext("query cursor was reused across authority/query")
    self._validate_query_head(cursor.through_head)

def query(
        self, context: InvocationContext | RegistryObserverContext,
        query: ResourceQuery) -> ResourceQueryResult:
    observer_fields = {"headers", "projection_head"}
    if query.relation_filters:
        observer_fields.add("relations")
    if query.address_name is not None:
        observer_fields.add("addresses")
    principal_ref, grant_ref = self._query_authority(
        context, observer_fields=observer_fields)
    if query.limit < 1 or query.limit > 1000:
        raise ResourceSchemaViolation("resource query limit must be between 1 and 1000")
    expected_task = context.task_ref
    if query.task_ref != expected_task or query.task_ref.entity_id != self._ResourceServiceKernel__core.task_id:
        raise UnauthorizedResourceDelivery("resource query cannot cross tasks")
    authority_facts = self._authority_facts(context)
    query_facts = self._query_material(query)
    if query.cursor is not None:
        self._validate_cursor(
            query.cursor, query_facts=query_facts,
            principal_ref=principal_ref, grant_ref=grant_ref,
            authority_facts=authority_facts)
        head = query.cursor.through_head
        offset = query.cursor.offset
    else:
        head = query.through_head or self._canonical_head()
        self._validate_query_head(head)
        offset = 0
    visible = self._visible_refs(context, through_ordinal=head.ordinal)
    headers: list[ResourceHeader] = []
    allowed_scopes = set(query.scope_refs)
    allowed_producers = set(query.producer_refs)
    for ref in sorted(visible, key=lambda item: str(item.resource_version_id)):
        header = self._header(ref, through_head=head)
        if allowed_scopes and not ({header.task_ref, header.round_ref, header.net_ref}
                                   & allowed_scopes):
            continue
        if allowed_producers and header.producer_ref not in allowed_producers:
            continue
        if query.media_types and header.media_type not in query.media_types:
            continue
        if query.address_name is not None:
            matched = False
            for scope in query.scope_refs:
                address = ResourceAddress(scope, query.address_name)
                try:
                    resolved = self._resolve_address_unchecked(address, head)
                except MissingResourceAddress:
                    continue
                if resolved.resource_ref == ref:
                    matched = True
                    break
            if not matched:
                continue
        if query.relation_filters:
            relation_types = set(header.relation_kinds)
            if any(item.relation_type not in relation_types
                   for item in query.relation_filters):
                continue
        headers.append(header)
    page = headers[offset:offset + query.limit]
    next_offset = offset + len(page)
    cursor = (self._cursor_for(
        query_facts=query_facts, principal_ref=principal_ref,
        grant_ref=grant_ref, through_head=head, offset=next_offset,
        authority_facts=authority_facts)
              if next_offset < len(headers) else None)
    current = self._canonical_head()
    return ResourceQueryResult(
        tuple(page), cursor, head, current.ordinal,
        max(current.ordinal - head.ordinal, 0))

def history(
        self, context: InvocationContext | RegistryObserverContext,
        resource_id: TypedId) -> tuple[ResourceVersionRef, ...]:
    self._query_authority(context, observer_fields=("history",))
    if resource_id.kind != "resource":
        raise TypeError("resource history requires a typed resource id")
    candidates = sorted(
        (ResourceVersionRef(
            resource_id,
            TypedId.parse(row["version_id"], expected="resource_version"))
         for row in self._ResourceServiceKernel__core.event_store.canonical_object_rows(
             object_type="resource_version/v1")
         if row["object_type"] == "resource_version/v1"
         and row["logical_id"] == str(resource_id)),
        key=lambda item: self._canonical_published_ordinal(
            item.resource_version_id,
            through_ordinal=self._ResourceServiceKernel__core.event_store.max_ordinal()),
    )
    for ref in candidates:
        self._query_authority(
            context, ref if isinstance(context, InvocationContext) else None,
            observer_fields=("history",))
    return tuple(candidates)

def _relation_endpoint(value: Mapping[str, Any]) -> ResourceVersionRef | VersionRef:
    if value.get("entity_type") == "resource_version/v1":
        return ResourceVersionRef(
            TypedId.parse(str(value["entity_id"]), expected="resource"),
            TypedId.parse(str(value["version_id"]), expected="resource_version"))
    return VersionRef(
        str(value["entity_type"]), TypedId.parse(str(value["entity_id"])),
        TypedId.parse(str(value["version_id"])))

def relation_query(
        self, context: InvocationContext | RegistryObserverContext,
        query: RelationQuery) -> RelationQueryResult:
    principal, grant = self._query_authority(
        context, observer_fields=("relations", "projection_head"))
    if query.limit < 1 or query.limit > 1000:
        raise ResourceSchemaViolation("relation query limit must be between 1 and 1000")
    if query.task_ref != context.task_ref:
        raise UnauthorizedResourceDelivery("relation query cannot cross tasks")
    authority_facts = self._authority_facts(context)
    material = {
        "task_ref": _ref_payload(query.task_ref),
        "endpoint_refs": [_resource_payload(item) for item in query.endpoint_refs],
        "relation_filters": [asdict(item) for item in query.relation_filters],
        "limit": query.limit,
    }
    query_facts = material
    if query.cursor:
        self._validate_cursor(
            query.cursor, query_facts=query_facts, principal_ref=principal,
            grant_ref=grant, authority_facts=authority_facts)
        head, offset = query.cursor.through_head, query.cursor.offset
    else:
        head, offset = query.through_head or self._canonical_head(), 0
        self._validate_query_head(head)
    visible = self._visible_refs(context, through_ordinal=head.ordinal)
    endpoints = set(query.endpoint_refs)
    filters = {item.relation_type: item.direction for item in query.relation_filters}
    rows: list[ResourceRelation] = []
    relation_rows = self._ResourceServiceKernel__core.event_store.canonical_relation_rows(
        through_ordinal=head.ordinal)
    for row in relation_rows:
        source_raw, target_raw = json.loads(row["source_json"]), json.loads(row["target_json"])
        source, target = self._relation_endpoint(source_raw), self._relation_endpoint(target_raw)
        source_resource = source if isinstance(source, ResourceVersionRef) else None
        target_resource = target if isinstance(target, ResourceVersionRef) else None
        if source_resource is not None and source_resource not in visible:
            continue
        if target_resource is not None and target_resource not in visible:
            continue
        if endpoints and source_resource not in endpoints and target_resource not in endpoints:
            continue
        direction = filters.get(row["relation_type"])
        if query.relation_filters and direction is None:
            continue
        if direction == "forward" and source_resource not in endpoints:
            continue
        if direction == "reverse" and target_resource not in endpoints:
            continue
        rows.append(ResourceRelation(
            TypedId.parse(row["relation_id"], expected="relation"),
            row["relation_type"], source, target, row["strength"]))
    page = rows[offset:offset + query.limit]
    next_offset = offset + len(page)
    cursor = (self._cursor_for(
        query_facts=query_facts, principal_ref=principal, grant_ref=grant,
        through_head=head, offset=next_offset,
        authority_facts=authority_facts)
              if next_offset < len(rows) else None)
    current = self._canonical_head()
    return RelationQueryResult(
        tuple(page), cursor, head, current.ordinal,
        max(current.ordinal - head.ordinal, 0))
