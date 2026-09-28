"""Delivery operations for the live Registry resource kernel."""

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

def prepare_delivery(
        self, context: InvocationContext,
        command: PrepareResourceDelivery, *,
        native_resume: bool = False) -> PreparedResourceDelivery:
    self._revalidate_invocation(
        context, boundary="resource-delivery", native_resume=native_resume)
    if command.delivery_id.kind != "resource_delivery":
        raise TypeError("delivery_id must be a typed resource-delivery identity")
    if command.consumer_boundary not in {
            "llm_prompt", "tool_result", "subprocess_read",
            "parent_receipt", "petri_input"}:
        raise UnmonitorableDeliveryBoundary("delivery boundary is not monitored")
    firing_ref = context.own_transition_firing_ref
    if (firing_ref is None
            or firing_ref.entity_type != "transition_firing/v1"):
        raise UnauthorizedResourceDelivery(
            "resource delivery requires one exact firing authority")
    view = self._ResourceServiceKernel__core.event_store.firing_view(
        firing_version_id=firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    prepared = self._firing_prepared(
        context, command.resource_ref, view=view)
    self._authorize_resource(
        context, command.authorization_ref, command.resource_ref,
        metadata_only=False, native_resume=native_resume)
    try:
        self._read_firing_registered(
            context, command.resource_ref, view=view, prepared=prepared)
    except Exception as exc:
        raise ResourceIntegrityFault("resource failed exact registered read") from exc
    material = {
        "context_ref": _ref_payload(context.invocation_ref),
        "resource_ref": _resource_payload(command.resource_ref),
        "authorization_ref": _ref_payload(command.authorization_ref),
        "delivery_id": str(command.delivery_id),
        "boundary": command.consumer_boundary, "purpose": command.purpose,
    }
    command_identity = canonical_text(material)
    version_id = _stable_id(
        "resource_delivery_version", command.idempotency_key, command_identity,
        "prepared")
    delivery_ref = VersionRef(
        "resource_delivery/v1", command.delivery_id, version_id)
    existing = self._ResourceServiceKernel__core.event_store.object_row_for_view(view, version_id)
    if existing is not None:
        existing_metadata = json.loads(existing["metadata_json"])
        if existing_metadata.get("command_identity") != command_identity:
            raise ResourceIdempotencyConflict(
                "delivery prepare idempotency command changed")
        canonical_ordinal = (
            self._ResourceServiceKernel__core.event_store.canonical_object_publication_ordinal(
                version_id,
                through_ordinal=self._ResourceServiceKernel__core.event_store.max_ordinal()))
        return PreparedResourceDelivery(
            delivery_ref, command.resource_ref, prepared.size,
            command.consumer_boundary, command.authorization_ref,
            (self._canonical_head(ordinal=canonical_ordinal)
             if canonical_ordinal is not None
             else self._canonical_head()))
    metadata = {
        "delivery_id": str(command.delivery_id),
        "delivery_version_id": str(version_id), "state": "prepared",
        "previous_delivery_ref": None,
        "resource_ref": _resource_payload(command.resource_ref),
        "resource_byte_count": prepared.size,
        "authorization_ref": _ref_payload(command.authorization_ref),
        "context_ref": _ref_payload(context.invocation_ref),
        "accounting_parent_ref": _ref_payload(
            context.accounting_parent_invocation_ref),
        "boundary": command.consumer_boundary, "purpose": command.purpose,
        "expires_at": _timestamp(_utc_now() + timedelta(minutes=5)),
        "witness_ref": None, "boundary_receipt_ref": None,
        "command_identity": command_identity,
    }
    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=command.idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.prewrite(
        object_type="resource_delivery/v1", logical_id=command.delivery_id,
        version_id=version_id, payload=canonical_json(metadata), metadata=metadata,
        media_type="application/json", schema_ref="registry_v1/resource_delivery/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    tx.append(PendingEvent(
        event_type="resource_delivery_prepared/v1", criticality="authoritative",
        stream_id=f"resource-delivery:{command.delivery_id}",
        aggregate_id=str(command.delivery_id), aggregate_type="resource_delivery/v1",
        idempotency_key=command.idempotency_key, command_id=command.idempotency_key,
        payload={
            "delivery_ref": _ref_payload(delivery_ref),
            "resource_ref": _resource_payload(command.resource_ref),
            "resource_byte_count": prepared.size,
            "authorization_ref": _ref_payload(command.authorization_ref),
            "context_ref": _ref_payload(context.invocation_ref),
            "boundary": command.consumer_boundary,
        }, payload_schema_ref="registry_v1/resource_delivery_prepared/v1",
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    try:
        tx.commit()
    except RegistryConflict as exc:
        raise ResourceIdempotencyConflict(str(exc)) from exc
    return PreparedResourceDelivery(
        delivery_ref, command.resource_ref, prepared.size,
        command.consumer_boundary, command.authorization_ref,
        self._canonical_head())

def _delivery_events(self, delivery_id: TypedId) -> list[Any]:
    return list(self._ResourceServiceKernel__core.event_store.list_events_by_aggregate(
        str(delivery_id),
        event_types=(
            "resource_delivery_prepared/v1", "resource_release_authorized/v1",
            "resource_delivery_acknowledged/v1", "resource_delivery_failed/v1",
            "resource_delivery_unknown/v1", "observed_read/v1",
        )))

def _delivery_object(self, ref: VersionRef, *, state: str | None = None):
    prepared = self._exact_object(ref, expected_type="resource_delivery/v1")
    if state is not None and prepared.metadata.get("state") != state:
        raise ReleaseAuthorizationDenied(
            f"delivery must be {state!r}, received {prepared.metadata.get('state')!r}")
    return prepared

def authorize_release(
        self, context: InvocationContext,
        command: AuthorizeResourceRelease, *,
        native_resume: bool = False) -> AuthorizedResourceRelease:
    self._revalidate_invocation(
        context, boundary="resource-release", native_resume=native_resume)
    if command.release_nonce.kind != "release_nonce":
        raise TypeError("release nonce must be a typed release-nonce identity")
    firing_ref = context.own_transition_firing_ref
    if (firing_ref is None
            or firing_ref.entity_type != "transition_firing/v1"):
        raise ReleaseAuthorizationDenied(
            "resource release requires one exact firing authority")
    read_view = self._ResourceServiceKernel__core.event_store.firing_view(
        firing_version_id=firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    prepared_delivery = self._exact_object_for_view(
        read_view, command.delivery_ref,
        expected_type="resource_delivery/v1")
    if prepared_delivery.metadata.get("state") != "prepared":
        raise ReleaseAuthorizationDenied(
            "delivery must be 'prepared', received "
            f"{prepared_delivery.metadata.get('state')!r}")
    metadata = dict(prepared_delivery.metadata)
    if (metadata["context_ref"] != _ref_payload(context.invocation_ref)
            or metadata["boundary"] != command.expected_boundary):
        raise ReleaseAuthorizationDenied(
            "delivery context or boundary differs from release request")
    resource_ref = _resource_from_payload(metadata["resource_ref"])
    authorization_ref = _version_from_payload(metadata["authorization_ref"])
    resource = self._firing_prepared(
        context, resource_ref, view=read_view)
    if resource.size != metadata["resource_byte_count"]:
        raise ResourceIntegrityFault(
            "prepared delivery byte count no longer matches")
    material = {
        "delivery_ref": _ref_payload(command.delivery_ref),
        "boundary": command.expected_boundary,
        "release_nonce": str(command.release_nonce),
        "context_ref": _ref_payload(context.invocation_ref),
    }
    command_identity = canonical_text(material)
    witness_id = _stable_id(
        "release_witness", command.delivery_ref.entity_id, command.release_nonce)
    witness_version = _stable_id(
        "release_witness_version", command.idempotency_key, command_identity)
    witness_ref = VersionRef(
        "resource_release_witness/v1", witness_id, witness_version)
    authorized_version = _stable_id(
        "resource_delivery_version", command.idempotency_key,
        command_identity, "release_authorized")
    authorized_ref = VersionRef(
        "resource_delivery/v1", command.delivery_ref.entity_id,
        authorized_version)
    existing = self._ResourceServiceKernel__core.event_store.object_row(witness_version)
    if existing is not None:
        witness_meta = json.loads(existing["metadata_json"])
        expires_at = str(witness_meta["expires_at"])
        if witness_meta.get("command_identity") != command_identity:
            raise ResourceIdempotencyConflict("release witness command changed")
        events = self._delivery_events(command.delivery_ref.entity_id)
        if any(event.event_type in {
                "resource_delivery_acknowledged/v1",
                "resource_delivery_failed/v1",
                "resource_delivery_unknown/v1",
        } for event in events):
            raise ReleaseWitnessConsumed("release witness already has an outcome")
        with self._ResourceServiceKernel__release_lock:
            matches = [
                (channel_id, channel)
                for channel_id, channel in self._ResourceServiceKernel__release_channels.items()
                if channel["witness_ref"] == witness_ref
            ]
        if len(matches) != 1:
            raise DeliveryOutcomeUnknown(
                "durable release witness has no live broker channel")
        channel_id, channel = matches[0]
        if channel["released"]:
            raise ReleaseWitnessConsumed("release witness is one-use")
        return AuthorizedResourceRelease(
            authorized_ref, witness_ref, resource_ref,
            resource.size, command.expected_boundary, expires_at,
            OpaqueBrokerChannelRef(channel_id))
    else:
        # X-A linearization: authority is re-read immediately before the
        # witness is committed. No byte or locator has crossed the boundary.
        self._authorize_resource(
            context, authorization_ref, resource_ref, metadata_only=False,
            native_resume=native_resume)
        expires_at = _timestamp(_utc_now() + timedelta(seconds=60))
        head = self._head()
        witness_meta = {
            "witness_id": str(witness_id),
            "witness_version_id": str(witness_version),
            "delivery_ref": _ref_payload(command.delivery_ref),
            "resource_ref": _resource_payload(resource_ref),
            "resource_byte_count": resource.size,
            "boundary": command.expected_boundary,
            "context_ref": _ref_payload(context.invocation_ref),
            "accounting_parent_ref": _ref_payload(
                context.accounting_parent_invocation_ref),
            "authorization_ref": _ref_payload(authorization_ref),
            "release_nonce": str(command.release_nonce),
            "authority_head": asdict(head), "expires_at": expires_at,
            "consumed": False, "outcome": None,
            "command_identity": command_identity,
        }
        delivery_meta = {
            **metadata,
            "delivery_version_id": str(authorized_version),
            "state": "release_authorized",
            "previous_delivery_ref": _ref_payload(command.delivery_ref),
            "witness_ref": _ref_payload(witness_ref),
            "expires_at": expires_at,
            "command_identity": command_identity,
        }
        tx = self._ResourceServiceKernel__core.begin(
            idempotency_key=command.idempotency_key,
            task_round_id=context.task_round_ref.entity_id,
            net_instance_id=context.net_instance_ref.entity_id)
        tx.prewrite(
            object_type="resource_release_witness/v1", logical_id=witness_id,
            version_id=witness_version, payload=canonical_json(witness_meta),
            metadata=witness_meta, media_type="application/json",
            schema_ref="registry_v1/resource_release_witness/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.prewrite(
            object_type="resource_delivery/v1",
            logical_id=command.delivery_ref.entity_id,
            version_id=authorized_version, payload=canonical_json(delivery_meta),
            metadata=delivery_meta, media_type="application/json",
            schema_ref="registry_v1/resource_delivery/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.append(PendingEvent(
            event_type="resource_release_authorized/v1",
            criticality="authoritative",
            stream_id=f"resource-delivery:{command.delivery_ref.entity_id}",
            aggregate_id=str(command.delivery_ref.entity_id),
            aggregate_type="resource_delivery/v1",
            idempotency_key=command.idempotency_key,
            command_id=command.idempotency_key,
            payload={
                "delivery_ref": _ref_payload(command.delivery_ref),
                "witness_ref": _ref_payload(witness_ref),
                "resource_ref": _resource_payload(resource_ref),
                "resource_byte_count": resource.size,
                "boundary": command.expected_boundary,
                "expires_at": expires_at,
            }, payload_schema_ref="registry_v1/resource_release_authorized/v1",
            producer_principal=str(context.principal_ref.entity_id),
            producer_invocation_id=context.invocation_ref.entity_id))
        try:
            tx.commit()
        except RegistryConflict as exc:
            raise ReleaseAuthorizationDenied(str(exc)) from exc
    channel_id = uuid.uuid4().hex + uuid.uuid4().hex
    with self._ResourceServiceKernel__release_lock:
        self._ResourceServiceKernel__release_channels[channel_id] = {
            "witness_ref": witness_ref,
            "delivery_ref": authorized_ref,
            "resource_ref": resource_ref,
            "context_ref": context.invocation_ref,
            "byte_count": resource.size,
            "boundary": command.expected_boundary,
            "expires_at": expires_at,
            "native_resume": native_resume,
            "read_view": read_view,
            "released": False,
        }
    return AuthorizedResourceRelease(
        authorized_ref, witness_ref, resource_ref,
        resource.size, command.expected_boundary, expires_at,
        OpaqueBrokerChannelRef(channel_id))

def _consume_authorized_release(
        self, context: InvocationContext,
        release: AuthorizedResourceRelease) -> bytes:
    with self._ResourceServiceKernel__release_lock:
        channel = self._ResourceServiceKernel__release_channels.get(
            release.broker_channel_ref.channel_id)
        if channel is None:
            raise ReleaseAuthorizationDenied("broker channel is unknown")
        if channel["released"]:
            raise ReleaseWitnessConsumed("release witness is one-use")
        if (channel["witness_ref"] != release.witness_ref
                or channel["delivery_ref"] != release.delivery_ref
                or channel["resource_ref"] != release.exact_resource_ref
                or channel["context_ref"] != context.invocation_ref
                or channel["boundary"] != release.boundary
                or channel["byte_count"] != release.byte_count):
            raise ReleaseAuthorizationDenied("broker channel binding mismatch")
        if _parse_timestamp(channel["expires_at"]) <= _utc_now():
            raise ReleaseAuthorizationDenied("release witness expired")
        # Do not re-evaluate authority here: the committed witness is the
        # approved single-I/O linearization point.
        try:
            payload = self._read_firing_registered(
                context, release.exact_resource_ref,
                view=channel["read_view"])
        except Exception as exc:
            raise ResourceIntegrityFault(
                "released bytes failed exact registered read") from exc
        channel["released"] = True
    return payload

def _record_boundary_receipt(
        self, context: InvocationContext,
        release: AuthorizedResourceRelease, *, outcome: str,
        positive_byte_count: int | None, consumer_evidence: str,
        idempotency_key: str) -> VersionRef:
    if outcome not in {"acknowledged", "failed", "unknown"}:
        raise ValueError("invalid boundary outcome")
    with self._ResourceServiceKernel__release_lock:
        channel = self._ResourceServiceKernel__release_channels.get(
            release.broker_channel_ref.channel_id)
        if channel is None:
            raise ReleaseAuthorizationDenied("broker channel is unknown")
        if (channel["witness_ref"] != release.witness_ref
                or channel["delivery_ref"] != release.delivery_ref
                or channel["resource_ref"] != release.exact_resource_ref
                or channel["context_ref"] != context.invocation_ref
                or channel["boundary"] != release.boundary
                or channel["byte_count"] != release.byte_count
                or channel["expires_at"] != release.expires_at):
            raise ReleaseAuthorizationDenied(
                "boundary receipt does not match its released channel")
        released = bool(channel.get("released"))
    if outcome == "acknowledged" and (
            not released
            or isinstance(positive_byte_count, bool)
            or not isinstance(positive_byte_count, int)
            or positive_byte_count < 0
            or positive_byte_count != release.byte_count):
        raise DeliveryAcknowledgementConflict(
            "successful boundary acknowledgement requires the exact consumed "
            "resource byte count")
    if outcome == "failed" and released:
        raise DeliveryAcknowledgementConflict(
            "released bytes with uncertain consumption must be recorded unknown")
    receipt_id = _stable_id(
        "boundary_receipt", release.witness_ref.version_id, idempotency_key)
    receipt_version = _stable_id(
        "boundary_receipt_version", receipt_id, outcome,
        positive_byte_count, consumer_evidence)
    receipt_ref = VersionRef(
        "delivery_boundary_receipt/v1", receipt_id, receipt_version)
    metadata = {
        "receipt_id": str(receipt_id),
        "receipt_version_id": str(receipt_version),
        "delivery_ref": _ref_payload(release.delivery_ref),
        "witness_ref": _ref_payload(release.witness_ref),
        "boundary": release.boundary, "outcome": outcome,
        "positive_byte_count": positive_byte_count,
        "resource_byte_count": release.byte_count,
        "consumer_evidence": consumer_evidence,
    }
    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=f"{idempotency_key}:boundary-receipt",
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.prewrite(
        object_type="delivery_boundary_receipt/v1", logical_id=receipt_id,
        version_id=receipt_version, payload=canonical_json(metadata),
        metadata=metadata, media_type="application/json",
        schema_ref="registry_v1/delivery_boundary_receipt/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    tx.commit()
    return receipt_ref

def acknowledge_delivery(
        self, context: InvocationContext,
        command: AcknowledgeResourceDelivery) -> ResourceDeliveryReceipt:
    delivery = self._delivery_object(command.delivery_ref)
    metadata = dict(delivery.metadata)
    if metadata["context_ref"] != _ref_payload(context.invocation_ref):
        raise DeliveryAcknowledgementConflict("delivery belongs to another invocation")
    receipt = self._exact_object(
        command.boundary_receipt_ref,
        expected_type="delivery_boundary_receipt/v1")
    receipt_meta = dict(receipt.metadata)
    if (receipt_meta.get("receipt_id") != str(receipt.logical_id)
            or receipt_meta.get("receipt_version_id")
            != str(receipt.version_id)
            or receipt_meta["delivery_ref"] != _ref_payload(command.delivery_ref)
            or receipt_meta["outcome"] != command.outcome
            or receipt_meta["boundary"] != metadata["boundary"]
            or receipt_meta["resource_byte_count"]
            != metadata["resource_byte_count"]):
        raise DeliveryAcknowledgementConflict(
            "boundary receipt does not close this delivery")
    witness_payload = receipt_meta.get("witness_ref")
    witness_ref = (_version_from_payload(witness_payload)
                   if witness_payload is not None else None)
    expected_witness = metadata.get("witness_ref")
    if witness_payload != expected_witness:
        raise DeliveryAcknowledgementConflict(
            "boundary receipt witness does not match the authorized release")
    if witness_ref is not None:
        witness = self._exact_object(
            witness_ref, expected_type="resource_release_witness/v1")
        expected_witness_metadata = {
            "delivery_ref": metadata.get("previous_delivery_ref"),
            "resource_ref": metadata.get("resource_ref"),
            "resource_byte_count": metadata.get("resource_byte_count"),
            "boundary": metadata.get("boundary"),
            "context_ref": metadata.get("context_ref"),
            "authorization_ref": metadata.get("authorization_ref"),
        }
        if (witness.metadata.get("witness_id") != str(witness_ref.entity_id)
                or witness.metadata.get("witness_version_id")
                != str(witness_ref.version_id)
                or any(witness.metadata.get(name) != value
                       for name, value in expected_witness_metadata.items())):
            raise DeliveryAcknowledgementConflict(
                "boundary receipt witness does not close this delivery")
    if command.outcome == "acknowledged":
        if (witness_ref is None
                or isinstance(
                    receipt_meta.get("positive_byte_count"), bool)
                or not isinstance(
                    receipt_meta.get("positive_byte_count"), int)
                or int(receipt_meta["positive_byte_count"]) < 0
                or int(receipt_meta["positive_byte_count"])
                != int(receipt_meta["resource_byte_count"])):
            raise DeliveryAcknowledgementConflict(
                "acknowledged delivery lacks exact boundary byte evidence")
    events = self._delivery_events(command.delivery_ref.entity_id)
    terminal_events = [event for event in events if event.event_type in {
        "resource_delivery_acknowledged/v1", "resource_delivery_failed/v1",
        "resource_delivery_unknown/v1"}]
    if terminal_events:
        prior = terminal_events[-1]
        if (prior.payload.get("boundary_receipt_ref")
                == _ref_payload(command.boundary_receipt_ref)
                and prior.payload.get("outcome") == command.outcome):
            observed = next((event.event_id for event in events
                             if event.event_type == "observed_read/v1"), None)
            return ResourceDeliveryReceipt(
                _version_from_payload(prior.payload["terminal_delivery_ref"]),
                prior.event_id, command.outcome, observed)
        raise DeliveryAcknowledgementConflict("delivery already has a terminal outcome")
    if command.outcome in {"acknowledged", "unknown"} and witness_ref is None:
        raise DeliveryAcknowledgementConflict(
            "released delivery outcome requires its exact witness")
    command_material = {
        "delivery_ref": _ref_payload(command.delivery_ref),
        "receipt_ref": _ref_payload(command.boundary_receipt_ref),
        "outcome": command.outcome,
    }
    command_identity = canonical_text(command_material)
    terminal_version = _stable_id(
        "resource_delivery_version", command.idempotency_key,
        command_identity, command.outcome)
    terminal_ref = VersionRef(
        "resource_delivery/v1", command.delivery_ref.entity_id,
        terminal_version)
    terminal_meta = {
        **metadata,
        "delivery_version_id": str(terminal_version),
        "state": command.outcome,
        "previous_delivery_ref": _ref_payload(command.delivery_ref),
        "witness_ref": (_ref_payload(witness_ref) if witness_ref else None),
        "boundary_receipt_ref": _ref_payload(command.boundary_receipt_ref),
        "command_identity": command_identity,
    }
    tx = self._ResourceServiceKernel__core.begin(
        idempotency_key=command.idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.prewrite(
        object_type="resource_delivery/v1",
        logical_id=command.delivery_ref.entity_id,
        version_id=terminal_version, payload=canonical_json(terminal_meta),
        metadata=terminal_meta, media_type="application/json",
        schema_ref="registry_v1/resource_delivery/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    event_type = {
        "acknowledged": "resource_delivery_acknowledged/v1",
        "failed": "resource_delivery_failed/v1",
        "unknown": "resource_delivery_unknown/v1",
    }[command.outcome]
    terminal_payload = {
        "delivery_ref": _ref_payload(command.delivery_ref),
        "terminal_delivery_ref": _ref_payload(terminal_ref),
        "witness_ref": _ref_payload(witness_ref) if witness_ref else None,
        "boundary_receipt_ref": _ref_payload(command.boundary_receipt_ref),
        "outcome": command.outcome,
    }
    tx.append(PendingEvent(
        event_type=event_type, criticality="authoritative",
        stream_id=f"resource-delivery:{command.delivery_ref.entity_id}",
        aggregate_id=str(command.delivery_ref.entity_id),
        aggregate_type="resource_delivery/v1",
        idempotency_key=command.idempotency_key,
        command_id=command.idempotency_key, payload=terminal_payload,
        payload_schema_ref=f"registry_v1/{event_type}",
        producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id))
    if command.outcome == "acknowledged":
        assert witness_ref is not None
        resource_ref = _resource_from_payload(metadata["resource_ref"])
        firing_ref = (context.own_transition_firing_ref
                      or context.sponsoring_transition_firing_ref)
        activation_ref = (context.activation_ref
                          or context.authorization_lifetime_activation_ref)
        authorization_ref = _version_from_payload(metadata["authorization_ref"])
        if firing_ref is None:
            raise DeliveryAcknowledgementConflict(
                "observed read lacks firing attribution")
        observed = {
            "origin": context.origin,
            "task_ref": _ref_payload(context.task_ref),
            "task_branch_ref": _ref_payload(context.task_branch_ref),
            "task_round_ref": _ref_payload(context.task_round_ref),
            "net_ref": _ref_payload(context.net_instance_ref),
            "actor_invocation_ref": _ref_payload(context.invocation_ref),
            "accounting_parent_ref": _ref_payload(
                context.accounting_parent_invocation_ref),
            "node_ref": (_ref_payload(context.own_node_ref)
                         if context.own_node_ref else None),
            "firing_ref": _ref_payload(firing_ref),
            "activation_ref": (_ref_payload(activation_ref) if activation_ref else None),
            "resource_ref": _resource_payload(resource_ref),
            "resource_byte_count": metadata["resource_byte_count"],
            "authorization_ref": _ref_payload(authorization_ref),
            "witness_ref": _ref_payload(witness_ref),
            "boundary_receipt_ref": _ref_payload(command.boundary_receipt_ref),
            "delivery_id": str(command.delivery_ref.entity_id),
            "transaction_id": str(tx.transaction_id),
            "boundary": metadata["boundary"],
        }
        tx.append(PendingEvent(
            event_type="observed_read/v1", criticality="authoritative",
            stream_id=f"observed-read:{context.invocation_ref.entity_id}",
            aggregate_id=str(command.delivery_ref.entity_id),
            aggregate_type="resource_delivery/v1",
            idempotency_key=f"{command.idempotency_key}:observed-read",
            command_id=command.idempotency_key, payload=observed,
            payload_schema_ref="registry_v1/observed_read/v1",
            producer_principal=str(context.principal_ref.entity_id),
            producer_invocation_id=context.invocation_ref.entity_id))
        tx.relate(TypedRelation(
            _stable_id("relation", command.idempotency_key, "read_by"),
            "read_by", resource_ref.as_version_ref(), context.invocation_ref),
            producer_invocation_id=context.invocation_ref.entity_id)
    try:
        committed = tx.commit()
    except RegistryConflict as exc:
        raise DeliveryAcknowledgementConflict(str(exc)) from exc
    terminal_event = next(event for event in committed if event.event_type == event_type)
    observed_event = next(
        (event.event_id for event in committed
         if event.event_type == "observed_read/v1"), None)
    return ResourceDeliveryReceipt(
        terminal_ref, terminal_event.event_id, command.outcome, observed_event)

def reconcile_incomplete_deliveries_on_resume(self) -> tuple[TypedId, ...]:
    """Close durable delivery crash windows without fabricating a read."""
    latest: dict[str, Any] = {}
    for event in self._ResourceServiceKernel__core.event_store.list_events():
        if event.event_type in {
                "resource_delivery_prepared/v1",
                "resource_release_authorized/v1",
                "resource_delivery_acknowledged/v1",
                "resource_delivery_failed/v1",
                "resource_delivery_unknown/v1",
        }:
            latest[event.aggregate_id] = event
    reconciled: list[TypedId] = []
    for delivery_id_text, state in sorted(latest.items()):
        if state.event_type not in {
                "resource_delivery_prepared/v1",
                "resource_release_authorized/v1",
        }:
            continue
        delivery_id = TypedId.parse(
            delivery_id_text, expected="resource_delivery")
        object_rows = [
            row for row in self._ResourceServiceKernel__core.event_store.object_rows()
            if row["logical_id"] == delivery_id_text
            and row["object_type"] == "resource_delivery/v1"
        ]
        wanted_state = (
            "prepared" if state.event_type == "resource_delivery_prepared/v1"
            else "release_authorized")
        candidates = [
            row for row in object_rows
            if json.loads(row["metadata_json"]).get("state") == wanted_state
        ]
        if len(candidates) != 1:
            raise DeliveryOutcomeUnknown(
                f"incomplete delivery has no unique {wanted_state} object")
        source_row = candidates[0]
        metadata = json.loads(source_row["metadata_json"])
        if (wanted_state == "prepared"
                and _parse_timestamp(str(metadata["expires_at"])) > _utc_now()):
            continue
        source_ref = VersionRef(
            "resource_delivery/v1", delivery_id,
            TypedId.parse(
                source_row["version_id"], expected="resource_delivery_version"))
        context_ref = _version_from_payload(metadata["context_ref"])
        invocation = self._exact_object(
            context_ref, expected_type="invocation/v1")
        invocation_metadata = dict(invocation.metadata)
        task_round_ref = _version_from_payload(
            invocation_metadata["task_round_ref"])
        net_ref = _version_from_payload(
            invocation_metadata["net_instance_ref"])
        principal_ref = _version_from_payload(
            invocation_metadata["principal_ref"])
        witness_ref = (
            _version_from_payload(metadata["witness_ref"])
            if metadata.get("witness_ref") else None)
        if witness_ref is not None:
            receipt_rows = []
            for row in self._ResourceServiceKernel__core.event_store.object_rows():
                if row["object_type"] != "delivery_boundary_receipt/v1":
                    continue
                receipt_metadata = json.loads(row["metadata_json"])
                if (receipt_metadata.get("delivery_ref") != _ref_payload(source_ref)
                        or receipt_metadata.get("witness_ref")
                        != _ref_payload(witness_ref)
                        or receipt_metadata.get("boundary") != metadata["boundary"]
                        or receipt_metadata.get("resource_byte_count")
                        != metadata["resource_byte_count"]):
                    continue
                receipt_rows.append((row, receipt_metadata))
            acknowledged = [
                item for item in receipt_rows
                if item[1].get("outcome") == "acknowledged"
                and isinstance(item[1].get("positive_byte_count"), int)
                and not isinstance(item[1].get("positive_byte_count"), bool)
                and int(item[1]["positive_byte_count"]) >= 0
                and int(item[1]["positive_byte_count"])
                == int(item[1]["resource_byte_count"])
            ]
            unknown = [
                item for item in receipt_rows
                if item[1].get("outcome") == "unknown"
            ]
            failed = [
                item for item in receipt_rows
                if item[1].get("outcome") == "failed"
            ]
            durable = acknowledged or unknown or failed
            if durable:
                row, receipt_metadata = sorted(
                    durable, key=lambda item: item[0]["version_id"])[0]
                receipt_ref = VersionRef(
                    "delivery_boundary_receipt/v1",
                    TypedId.parse(row["logical_id"], expected="boundary_receipt"),
                    TypedId.parse(
                        row["version_id"], expected="boundary_receipt_version"),
                )
                # This is immutable historical attribution, not renewed I/O
                # authority. Native resume advances the writer fence, so a
                # lifecycle hydrate would incorrectly reject the exact
                # invocation that owns the durable boundary receipt.
                context = InvocationContext.from_serialized(
                    invocation_metadata)
                self.acknowledge_delivery(
                    context,
                    AcknowledgeResourceDelivery(
                        source_ref, receipt_ref,
                        receipt_metadata["outcome"],
                        f"native-resume-boundary-receipt:"
                        f"{delivery_id}:{receipt_ref.version_id}"),
                )
                reconciled.append(delivery_id)
                continue
        outcome = "unknown" if witness_ref is not None else "failed"
        key = f"native-resume-delivery:{delivery_id}:{state.event_id}"
        receipt_id = _stable_id("boundary_receipt", key)
        receipt_version = _stable_id(
            "boundary_receipt_version", key, outcome)
        receipt_ref = VersionRef(
            "delivery_boundary_receipt/v1", receipt_id, receipt_version)
        receipt_metadata = {
            "receipt_id": str(receipt_id),
            "receipt_version_id": str(receipt_version),
            "delivery_ref": _ref_payload(source_ref),
            "witness_ref": _ref_payload(witness_ref) if witness_ref else None,
            "boundary": metadata["boundary"], "outcome": outcome,
            "positive_byte_count": None,
            "resource_byte_count": metadata["resource_byte_count"],
            "consumer_evidence": (
                "native resume found a released witness without durable boundary outcome"
                if witness_ref else
                "native resume expired a prepared delivery before release"),
        }
        terminal_version = _stable_id(
            "resource_delivery_version", key, outcome)
        terminal_ref = VersionRef(
            "resource_delivery/v1", delivery_id, terminal_version)
        terminal_metadata = {
            **metadata,
            "delivery_version_id": str(terminal_version),
            "state": outcome,
            "previous_delivery_ref": _ref_payload(source_ref),
            "witness_ref": _ref_payload(witness_ref) if witness_ref else None,
            "boundary_receipt_ref": _ref_payload(receipt_ref),
            "command_identity": canonical_text({
                "source_ref": _ref_payload(source_ref),
                "state_event_id": str(state.event_id),
                "outcome": outcome,
            }),
        }
        tx = self._ResourceServiceKernel__core.begin(
            idempotency_key=key,
            task_round_id=task_round_ref.entity_id,
            net_instance_id=net_ref.entity_id)
        tx.prewrite(
            object_type="delivery_boundary_receipt/v1",
            logical_id=receipt_id, version_id=receipt_version,
            payload=canonical_json(receipt_metadata),
            metadata=receipt_metadata, media_type="application/json",
            schema_ref="registry_v1/delivery_boundary_receipt/v1",
            producer_invocation_id=context_ref.entity_id)
        tx.prewrite(
            object_type="resource_delivery/v1", logical_id=delivery_id,
            version_id=terminal_version,
            payload=canonical_json(terminal_metadata),
            metadata=terminal_metadata, media_type="application/json",
            schema_ref="registry_v1/resource_delivery/v1",
            producer_invocation_id=context_ref.entity_id)
        event_type = (
            "resource_delivery_unknown/v1" if outcome == "unknown"
            else "resource_delivery_failed/v1")
        tx.append(PendingEvent(
            event_type=event_type, criticality="authoritative",
            stream_id=f"resource-delivery:{delivery_id}",
            aggregate_id=str(delivery_id),
            aggregate_type="resource_delivery/v1",
            idempotency_key=key, command_id=key,
            payload={
                "delivery_ref": _ref_payload(source_ref),
                "terminal_delivery_ref": _ref_payload(terminal_ref),
                "witness_ref": _ref_payload(witness_ref) if witness_ref else None,
                "boundary_receipt_ref": _ref_payload(receipt_ref),
                "outcome": outcome,
            }, payload_schema_ref=f"registry_v1/{event_type}",
            producer_principal=str(principal_ref.entity_id),
            producer_invocation_id=context_ref.entity_id))
        tx.commit()
        reconciled.append(delivery_id)
    return tuple(reconciled)
