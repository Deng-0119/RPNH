"""Addresses operations for the live Registry resource kernel."""

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

def _address_key(address: ResourceAddress) -> str:
    if not address.opaque_name or "\x00" in address.opaque_name:
        raise ResourceSchemaViolation("resource address name is invalid")
    return f"{address.scope_ref.version_id}:{address.opaque_name}"

def _address_scope_allowed(self, context: InvocationContext,
                           address: ResourceAddress,
                           authorization_ref: VersionRef, *,
                           native_resume: bool = False) -> None:
    self._revalidate_invocation(
        context, boundary="resource-address-mutation",
        native_resume=native_resume)
    if authorization_ref != context.operation_binding_ref:
        raise ResourceAddressConflict(
            "address mutation requires the exact operation binding")
    allowed = {
        context.task_ref, context.task_round_ref, context.net_instance_ref,
        context.invocation_ref,
    }
    if context.activation_ref is not None:
        allowed.add(context.activation_ref)
    if address.scope_ref not in allowed:
        raise ResourceAddressConflict("address scope is outside this invocation")
    self._exact_object(address.scope_ref)

def _binding_events(
        self, address: ResourceAddress, *, through_ordinal: int,
        provisional_firing_ref: VersionRef | None = None) -> list[Any]:
    stream = f"resource-address:{self._address_key(address)}"
    canonical = [
        event for event in self._ResourceServiceKernel__core.event_store.canonical_events(
            through_ordinal=through_ordinal, stream_id=stream)
        if event.event_type in {
            "resource_address_bound/v1", "resource_address_unbound/v1"}
    ]
    if provisional_firing_ref is None:
        return canonical
    if provisional_firing_ref.entity_type != "transition_firing/v1":
        raise ResourceAddressConflict(
            "provisional address history requires an exact firing ref")
    provisional = [
        event for event in
        self._ResourceServiceKernel__core.event_store.provisional_firing_events(
            provisional_firing_ref.version_id)
        if (event.stream_id == stream
            and event.ordinal <= through_ordinal
            and event.event_type in {
                "resource_address_bound/v1",
                "resource_address_unbound/v1"})
    ]
    return sorted(
        {event.event_id: event for event in (*canonical, *provisional)}
        .values(), key=lambda event: event.ordinal)

def _current_binding(self, address: ResourceAddress, *, through_ordinal: int,
                     include_tombstone: bool,
                     provisional_firing_ref: VersionRef | None = None,
                     ) -> ResourceAddressBindingRef | None:
    events = self._binding_events(
        address, through_ordinal=through_ordinal,
        provisional_firing_ref=provisional_firing_ref)
    if not events:
        return None
    binding_ref = _version_from_payload(events[-1].payload["binding_ref"])
    prepared = self._exact_object(
        binding_ref, expected_type="resource_address_binding/v1")
    tombstone = prepared.metadata["lifecycle_state"] == "tombstoned"
    if tombstone and not include_tombstone:
        return None
    return ResourceAddressBindingRef(
        binding_ref.entity_id, binding_ref.version_id, tombstone)

def _append_binding(
        self, context: InvocationContext, tx: Any, address: ResourceAddress,
        resource_ref: ResourceVersionRef, authorization_ref: VersionRef,
        expected_binding_ref: ResourceAddressBindingRef | None, *,
        idempotency_key: str, tombstone: bool = False,
        resource_already_validated: bool = False,
        native_resume: bool = False) -> ResourceAddressBindingRef:
    self._address_scope_allowed(
        context, address, authorization_ref,
        native_resume=native_resume)
    if not tombstone and not resource_already_validated:
        self._prepared(resource_ref)
    address_key = self._address_key(address)
    binding_id = _stable_id("resource_address_binding", self._ResourceServiceKernel__core.task_id, address_key)
    command_material = {
        "address": {"scope_ref": _ref_payload(address.scope_ref),
                    "opaque_name": address.opaque_name},
        "resource_ref": None if tombstone else _resource_payload(resource_ref),
        "authorization_ref": _ref_payload(authorization_ref),
        "expected_binding_ref": (
            _ref_payload(expected_binding_ref.as_version_ref())
            if expected_binding_ref else None),
        "tombstone": tombstone,
    }
    binding_version_id = _stable_id(
        "resource_address_binding_version", idempotency_key, address_key)
    result = ResourceAddressBindingRef(
        binding_id, binding_version_id, tombstone)
    current = self._current_binding(
        address, through_ordinal=self._ResourceServiceKernel__core.event_store.max_ordinal(),
        include_tombstone=True,
        provisional_firing_ref=context.own_transition_firing_ref)
    if current != expected_binding_ref:
        raise ResourceAddressConflict(
            "address expected binding does not match the append-only chain head")
    stream = f"resource-address:{address_key}"
    sequence = tx.next_stream_sequence(stream)
    metadata = {
        "binding_id": str(binding_id),
        "binding_version_id": str(binding_version_id),
        "scope_ref": _ref_payload(address.scope_ref),
        "opaque_name": address.opaque_name,
        "resource_ref": None if tombstone else _resource_payload(resource_ref),
        "previous_binding_ref": (
            _ref_payload(expected_binding_ref.as_version_ref())
            if expected_binding_ref else None),
        "lifecycle_state": "tombstoned" if tombstone else "bound",
        "authorization_ref": _ref_payload(authorization_ref),
        "commit_transaction_id": str(tx.transaction_id),
        "resulting_stream_sequence": sequence,
        "command_material": command_material,
    }
    tx.prewrite(
        object_type="resource_address_binding/v1", logical_id=binding_id,
        version_id=binding_version_id, payload=canonical_json(metadata),
        metadata=metadata, media_type="application/json",
        schema_ref="registry_v1/resource_address_binding/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    payload = {
        "binding_ref": _ref_payload(result.as_version_ref()),
        "address": {"scope_ref": _ref_payload(address.scope_ref),
                    "opaque_name": address.opaque_name},
        "previous_binding_ref": (
            _ref_payload(expected_binding_ref.as_version_ref())
            if expected_binding_ref else None),
        "resulting_stream_sequence": sequence,
    }
    if not tombstone:
        payload["resource_ref"] = _resource_payload(resource_ref)
    event_name = "resource_address_unbound/v1" if tombstone else "resource_address_bound/v1"
    tx.append(PendingEvent(
        event_type=event_name, criticality="authoritative",
        stream_id=stream, aggregate_id=str(binding_id),
        aggregate_type="resource_address_binding/v1",
        idempotency_key=idempotency_key, command_id=idempotency_key,
        payload=payload, payload_schema_ref=f"registry_v1/{event_name}",
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    return result

def _binding_replay(
        self, address: ResourceAddress, resource_ref: ResourceVersionRef,
        authorization_ref: VersionRef,
        expected_binding_ref: ResourceAddressBindingRef | None, *,
        idempotency_key: str,
        tombstone: bool) -> ResourceAddressBindingRef | None:
    command_material = {
        "address": {"scope_ref": _ref_payload(address.scope_ref),
                    "opaque_name": address.opaque_name},
        "resource_ref": None if tombstone else _resource_payload(resource_ref),
        "authorization_ref": _ref_payload(authorization_ref),
        "expected_binding_ref": (
            _ref_payload(expected_binding_ref.as_version_ref())
            if expected_binding_ref else None),
        "tombstone": tombstone,
    }
    binding_id = _stable_id(
        "resource_address_binding", self._ResourceServiceKernel__core.task_id,
        self._address_key(address))
    version_id = _stable_id(
        "resource_address_binding_version", idempotency_key,
        self._address_key(address))
    existing = self._ResourceServiceKernel__core.event_store.object_row(version_id)
    if existing is None:
        return None
    metadata = json.loads(existing["metadata_json"])
    if metadata.get("command_material") != command_material:
        raise ResourceAddressConflict(
            "address idempotency command differs from committed binding")
    return ResourceAddressBindingRef(binding_id, version_id, tombstone)

def bind_address(
        self, context: InvocationContext,
        command: BindResourceAddress, *,
        native_resume: bool = False,
        reference_only: bool = False) -> ResourceAddressBindingRef:
    self._revalidate_invocation(
        context, boundary="resource-address-mutation",
        native_resume=native_resume)
    if reference_only:
        if context.own_transition_firing_ref is not None:
            self._firing_prepared(context, command.resource_ref)
        else:
            self._prepared_reference(command.resource_ref)
    else:
        self._prepared(command.resource_ref)
    replay = self._binding_replay(
        command.address, command.resource_ref, command.authorization_ref,
        command.expected_binding_ref, idempotency_key=command.idempotency_key,
        tombstone=False)
    if replay is not None:
        return replay
    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=command.idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    result = self._append_binding(
        context, tx, command.address, command.resource_ref,
        command.authorization_ref, command.expected_binding_ref,
        idempotency_key=command.idempotency_key,
        native_resume=native_resume, resource_already_validated=True)
    try:
        tx.commit()
    except RegistryConflict as exc:
        raise ResourceAddressConflict(str(exc)) from exc
    return result

def bind_addresses(
        self, context: InvocationContext,
        commands: tuple[BindResourceAddress, ...], *,
        idempotency_key: str,
        native_resume: bool = False,
        reference_only: bool = False,
) -> tuple[ResourceAddressBindingRef, ...]:
    """Bind one located-input set in one Registry transaction."""

    items = tuple(commands)
    if (not items
            or any(not isinstance(item, BindResourceAddress)
                   for item in items)
            or not isinstance(idempotency_key, str)
            or not idempotency_key):
        raise TypeError(
            "address batch requires commands and one idempotency key")
    address_keys = tuple(self._address_key(item.address) for item in items)
    command_keys = tuple(item.idempotency_key for item in items)
    if (len(set(address_keys)) != len(address_keys)
            or len(set(command_keys)) != len(command_keys)):
        raise ResourceAddressConflict(
            "address batch contains a duplicate address or command identity")
    self._revalidate_invocation(
        context, boundary="resource-address-mutation",
        native_resume=native_resume)
    for item in items:
        if reference_only:
            if context.own_transition_firing_ref is not None:
                self._firing_prepared(context, item.resource_ref)
            else:
                self._prepared_reference(item.resource_ref)
        else:
            self._prepared(item.resource_ref)

    replayed = tuple(self._binding_replay(
        item.address, item.resource_ref, item.authorization_ref,
        item.expected_binding_ref,
        idempotency_key=item.idempotency_key, tombstone=False)
        for item in items)
    if all(item is not None for item in replayed):
        return tuple(item for item in replayed if item is not None)

    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    results: list[ResourceAddressBindingRef | None] = list(replayed)
    for index, item in enumerate(items):
        if results[index] is not None:
            continue
        results[index] = self._append_binding(
            context, tx, item.address, item.resource_ref,
            item.authorization_ref, item.expected_binding_ref,
            idempotency_key=item.idempotency_key,
            native_resume=native_resume,
            resource_already_validated=True)
    try:
        tx.commit()
    except RegistryConflict as exc:
        raise ResourceAddressConflict(str(exc)) from exc
    return tuple(item for item in results if item is not None)

def unbind_address(
        self, context: InvocationContext,
        command: UnbindResourceAddress) -> ResourceAddressBindingRef:
    self._revalidate_invocation(
        context, boundary="resource-address-mutation")
    if command.expected_binding_ref.tombstone:
        raise ResourceAddressConflict("an address tombstone cannot be tombstoned twice")
    prior = self._exact_object(
        command.expected_binding_ref.as_version_ref(),
        expected_type="resource_address_binding/v1")
    prior_resource = _resource_from_payload(prior.metadata["resource_ref"])
    replay = self._binding_replay(
        command.address, prior_resource, command.authorization_ref,
        command.expected_binding_ref, idempotency_key=command.idempotency_key,
        tombstone=True)
    if replay is not None:
        return replay
    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=command.idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    result = self._append_binding(
        context, tx, command.address, prior_resource,
        command.authorization_ref, command.expected_binding_ref,
        idempotency_key=command.idempotency_key, tombstone=True)
    try:
        tx.commit()
    except RegistryConflict as exc:
        raise ResourceAddressConflict(str(exc)) from exc
    return result

def _resolve_address_unchecked(
        self, address: ResourceAddress,
        head: RegistryHead) -> ResourceAddressResult:
    bindings = self._binding_events(address, through_ordinal=head.ordinal)
    if not bindings:
        raise MissingResourceAddress(address.opaque_name)
    latest = bindings[-1]
    ref = _version_from_payload(latest.payload["binding_ref"])
    if self._ResourceServiceKernel__core.event_store.canonical_object_row(
            ref.version_id, through_ordinal=head.ordinal) is None:
        raise MissingResourceAddress(
            "address binding is not authoritative at the requested head")
    prepared = self._exact_object(
        ref, expected_type="resource_address_binding/v1")
    tombstone = prepared.metadata["lifecycle_state"] == "tombstoned"
    if tombstone:
        raise MissingResourceAddress(address.opaque_name)
    resource_ref = _resource_from_payload(prepared.metadata["resource_ref"])
    return ResourceAddressResult(
        address,
        ResourceAddressBindingRef(ref.entity_id, ref.version_id, False),
        resource_ref, head)

def resolve_address(
        self, context: InvocationContext | RegistryObserverContext,
        address: ResourceAddress, at_head: RegistryHead) -> ResourceAddressResult:
    self._query_authority(
        context, observer_fields=("addresses", "projection_head"))
    self._validate_query_head(at_head)
    result = self._resolve_address_unchecked(address, at_head)
    if isinstance(context, InvocationContext):
        self._authorize_resource(
            context, context.operation_binding_ref,
            result.resource_ref, metadata_only=True)
    return result
