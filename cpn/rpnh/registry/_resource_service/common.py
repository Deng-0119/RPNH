"""Shared stateless helpers for the Registry resource-service components."""

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

_REGISTRY_WORKSPACE_ROOT_PARTS = ("workspace", "views")

def _resolve_registry_workspace_root(
        core: _RegistryCore, raw_locator: object,
) -> Path:
    """Resolve one strict run-relative Registry workspace locator."""

    if not isinstance(raw_locator, str) or not raw_locator:
        raise PathPublicationFault("workspace locator is missing")
    locator = PurePath(raw_locator)
    parts = locator.parts
    if (locator.is_absolute()
            or len(parts) < 2
            or tuple(parts[:2]) != _REGISTRY_WORKSPACE_ROOT_PARTS
            or any(part in {"", os.curdir, os.pardir} for part in parts)):
        raise PathPublicationFault(
            "workspace locator must be run-relative under workspace/views")
    return Path(os.path.abspath(core.run_dir)).joinpath(*parts)

_OBSERVER_SAFE_FIELDS = frozenset({
    "addresses", "headers", "history", "projection_head", "relations",
})

def _resource_payload(ref: ResourceVersionRef) -> dict[str, str]:
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
    }

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")

def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def _fresh_checkpoint_repair_resource_metadata(
        core: _RegistryCore, *, ref: ResourceVersionRef,
        task_ref: VersionRef, round_ref: VersionRef, net_ref: VersionRef,
        repair_ref: VersionRef, source_ref: ResourceVersionRef,
        payload_size: int, media_type: str,
        content_schema_ref: str | None,
        content_schema_authority_ref: Mapping[str, Any] | None,
        summary: str, descriptors: Mapping[str, Any],
        extensions: Mapping[str, Any],
        derived_from: Sequence[ResourceVersionRef]) -> dict[str, Any]:
    """Build one repair-owned replacement in the repaired net's authority."""

    ordered_inputs = tuple(sorted(
        dict.fromkeys(derived_from),
        key=lambda value: canonical_json(
            _ref_payload(value.as_version_ref()))))
    source_exact = source_ref.as_version_ref()
    publication = {
        "origin_kind": "checkpoint_repair",
        "primary_ref": _ref_payload(repair_ref),
        "secondary_ref": _ref_payload(source_exact),
        "lifetime_ref": _ref_payload(repair_ref),
        "size": payload_size,
        "media_type": media_type,
        "content_schema_ref": content_schema_ref,
        "content_schema_authority_ref": (
            dict(content_schema_authority_ref)
            if content_schema_authority_ref is not None else None),
    }
    return {
        "resource_id": str(ref.resource_id),
        "resource_version_id": str(ref.resource_version_id),
        "origin_kind": "checkpoint_repair",
        "origin": {
            "kind": "checkpoint_repair",
            "primary_ref": _ref_payload(repair_ref),
            "secondary_ref": _ref_payload(source_exact),
        },
        "task_ref": _ref_payload(task_ref),
        "branch_id": core.branch_id,
        "round_ref": _ref_payload(round_ref),
        "net_ref": _ref_payload(net_ref),
        "producer_ref": _ref_payload(repair_ref),
        "lifetime_ref": _ref_payload(repair_ref),
        "size": payload_size,
        "media_type": media_type,
        "content_schema_ref": content_schema_ref,
        "content_schema_authority_ref": (
            dict(content_schema_authority_ref)
            if content_schema_authority_ref is not None else None),
        "summary": summary,
        "descriptors": dict(descriptors),
        "extensions": dict(extensions),
        "reference_provenance": {
            "schema_version": "resource_reference_provenance/v1",
            "producer_invocation_ref": None,
            "operation_binding_ref": None,
            "derived_from_refs": [
                _ref_payload(value.as_version_ref()) for value in ordered_inputs],
            "contributor_refs": [],
            "supersedes_ref": None,
            "intended_consumer": {
                "boundary": "not_applicable",
                "consumer_ref": None,
            },
            "publication": publication,
        },
    }
