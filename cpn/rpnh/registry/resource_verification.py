"""Exact live resource and acknowledged Petri input verification over sole Core.

The caller supplies the existing execution-owner Core and resource Kernel.
Live canonical authority is rehydrated by the caller before verification.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from jsonschema import Draft7Validator

from ._registry import _RegistryCore
from .content_schemas import hydrate_registered_content_schema
from .errors import (
    DeliveryAcknowledgementConflict, ResourceIntegrityFault, ResourceServiceError,
)
from .invocations import InvocationLifecycle
from .publication import (
    _NO_CONTENT_SCHEMA_INSTANCE, _content_schema_instance, _ref_payload,
    _resource_from_payload, _version_from_payload,
)
from .resource_service import _ResourceServiceKernel, _resource_payload
from .resources import (
    CanonicalInvocationAuthority, PetriInputReceiptAuthority,
    ResourceDeliveryReceipt, ResourceHeader, ResourceVersionRef,
    VerifiedResourceArtifact,
)

def _verify_registered_resource_content_schema(
        core: _RegistryCore, header: ResourceHeader, prepared: Any, *,
        fresh_reader: Callable[[ResourceVersionRef], bytes] | None = None,
) -> None:
    schema_id = header.content_schema_ref
    source = header.content_schema_authority_ref
    if schema_id is None:
        if source is not None:
            raise ResourceIntegrityFault(
                "untyped resource carries a schema authority")
        return
    if source is None:
        raise ResourceIntegrityFault(
            "typed resource lacks an exact schema authority")
    if (not isinstance(
            prepared.metadata.get("reference_provenance"), Mapping)
            or fresh_reader is None):
        raise ResourceIntegrityFault(
            "current typed resource lacks registered-reference authority")
    try:
        authority = hydrate_registered_content_schema(
            core, source, schema_id=schema_id,
            fresh_reader=(fresh_reader
                          if isinstance(source, ResourceVersionRef) else None))
        instance = _NO_CONTENT_SCHEMA_INSTANCE
        if (header.media_type == "application/json"
                or header.media_type.startswith("text/")):
            resource_payload = (
                fresh_reader(header.ref)
                if fresh_reader is not None
                else core.object_store.read_verified(prepared))
            instance = _content_schema_instance(
                resource_payload, media_type=header.media_type)
        if instance is not _NO_CONTENT_SCHEMA_INSTANCE:
            if authority.catalog_ref is not None:
                core.catalog.validate_schema_ref(schema_id, instance)
            else:
                assert authority.resource_ref is not None
                schema_payload = (
                    fresh_reader(authority.resource_ref)
                    if fresh_reader is not None
                    else core.object_store.read_verified(core.get_version(
                        authority.resource_ref.resource_version_id)))
                schema_document = json.loads(schema_payload)
                if not isinstance(schema_document, Mapping):
                    raise TypeError("schema root is not an object")
                Draft7Validator(schema_document).validate(instance)
    except ResourceIntegrityFault:
        raise
    except Exception as exc:
        raise ResourceIntegrityFault(
            "resource content schema failed exact hydration/validation: "
            f"resource_ref={header.ref!r}; "
            f"content_schema_ref={schema_id!r}; "
            f"content_schema_authority_ref={source!r}; "
            f"inner_exception={type(exc).__name__}: {exc}") from exc
    return


def verify_resource(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        canonical: CanonicalInvocationAuthority, ref: ResourceVersionRef, *,
        native_resume: bool = False,
) -> VerifiedResourceArtifact:
    if not isinstance(ref, ResourceVersionRef):
        raise TypeError("resource verification requires an exact resource ref")
    try:
        kernel._authorize_resource(
            canonical.context, canonical.context.operation_binding_ref,
            ref, metadata_only=False, native_resume=native_resume)
        firing_ref = canonical.context.own_transition_firing_ref
        if firing_ref is None:
            raise ResourceIntegrityFault(
                "current resource verification lacks firing authority")
        view = core.event_store.firing_view(
            firing_version_id=firing_ref.version_id,
            invocation_version_id=(
                canonical.context.invocation_ref.version_id))
        prepared = kernel._firing_prepared(
            canonical.context, ref, view=view)
        header = kernel._firing_header(
            canonical.context, ref, view=view, prepared=prepared)
        _verify_registered_resource_content_schema(
            core, header, prepared,
            fresh_reader=lambda candidate: (
                kernel._read_firing_registered(
                    canonical.context, candidate, view=view)))
        if prepared.size != header.byte_size:
            raise ResourceIntegrityFault(
                "resource bytes/schema/reference envelope differs")
    except ResourceServiceError:
        raise
    except Exception as exc:
        raise ResourceIntegrityFault(
            "resource exact reference closure failed verification") from exc
    return VerifiedResourceArtifact(
        header=header,
        verified_at_head=kernel._head(),
    )

def verify_petri_input_receipt(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        receipt: ResourceDeliveryReceipt,
        expected_resource_ref: ResourceVersionRef,
) -> PetriInputReceiptAuthority:
    if (not isinstance(receipt, ResourceDeliveryReceipt)
            or not isinstance(expected_resource_ref, ResourceVersionRef)):
        raise TypeError(
            "petri input receipt verification requires typed exact refs")
    if receipt.outcome != "acknowledged" or receipt.observed_read_event_id is None:
        raise DeliveryAcknowledgementConflict(
            "petri input requires acknowledged delivery and observed read")
    try:
        terminal = kernel._exact_object(
            receipt.delivery_ref, expected_type="resource_delivery/v1")
        terminal_meta = dict(terminal.metadata)
        authorized_ref = _version_from_payload(
            terminal_meta["previous_delivery_ref"])
        authorized = kernel._exact_object(
            authorized_ref, expected_type="resource_delivery/v1")
        boundary_receipt_ref = _version_from_payload(
            terminal_meta["boundary_receipt_ref"])
        boundary_receipt = kernel._exact_object(
            boundary_receipt_ref,
            expected_type="delivery_boundary_receipt/v1")
        boundary_meta = dict(boundary_receipt.metadata)
        witness_ref = _version_from_payload(terminal_meta["witness_ref"])
        exact_resource_ref = _resource_from_payload(
            terminal_meta["resource_ref"])
        receipt_context_ref = _version_from_payload(
            terminal_meta["context_ref"])
        receipt_context = InvocationLifecycle(
            core).hydrate_context(
                receipt_context_ref, require_current_writer=False)
        receipt_firing_ref = receipt_context.own_transition_firing_ref
        if receipt_firing_ref is None:
            raise LookupError(
                "petri input receipt has no exact transition firing")
        publication = core.event_store.firing_publication_row(
            receipt_firing_ref.version_id)
        if (publication is None
                or str(publication["invocation_version_id"])
                != str(receipt_context.invocation_ref.version_id)):
            raise LookupError(
                "petri input receipt has no exact firing publication")
        if publication["state"] == "PROVISIONAL":
            receipt_view = core.event_store.firing_view(
                firing_version_id=receipt_firing_ref.version_id,
                invocation_version_id=(
                    receipt_context.invocation_ref.version_id))
            resource = kernel._firing_prepared(
                receipt_context, exact_resource_ref, view=receipt_view)
        elif publication["state"] == "PUBLISHED":
            resource = kernel._prepared(exact_resource_ref)
        else:
            raise LookupError(
                "petri input receipt firing is not readable")
        terminal_event = core.event_store.event_by_id(
            receipt.terminal_event_id)
        observed_event = core.event_store.event_by_id(
            receipt.observed_read_event_id)
        if terminal_event is None or observed_event is None:
            raise LookupError("petri input receipt event is missing")
    except Exception as exc:
        raise ResourceIntegrityFault(
            "petri input receipt exact objects or events are incomplete") from exc
    expected_boundary_ref = _ref_payload(boundary_receipt_ref)
    expected_terminal_ref = _ref_payload(receipt.delivery_ref)
    expected_authorized_ref = _ref_payload(authorized_ref)
    expected_resource = _resource_payload(expected_resource_ref)
    positive_count = boundary_meta.get("positive_byte_count")
    evidence = boundary_meta.get("consumer_evidence")
    if (exact_resource_ref != expected_resource_ref
            or terminal_meta.get("state") != "acknowledged"
            or terminal_meta.get("boundary") != "petri_input"
            or terminal_meta.get("previous_delivery_ref")
            != expected_authorized_ref
            or authorized.metadata.get("state") != "release_authorized"
            or authorized.metadata.get("boundary") != "petri_input"
            or authorized.metadata.get("resource_ref") != expected_resource
            or authorized.metadata.get("witness_ref") != _ref_payload(witness_ref)
            or authorized.metadata.get("resource_byte_count")
            != resource.size
            or boundary_meta.get("boundary") != "petri_input"
            or boundary_meta.get("outcome") != "acknowledged"
            or boundary_meta.get("delivery_ref") != expected_authorized_ref
            or boundary_meta.get("witness_ref") != _ref_payload(witness_ref)
            or not isinstance(positive_count, int)
            or isinstance(positive_count, bool)
            or positive_count < 0 or positive_count != resource.size
            or not isinstance(evidence, str)
            or terminal_event.event_type
            != "resource_delivery_acknowledged/v1"
            or terminal_event.payload.get("terminal_delivery_ref")
            != expected_terminal_ref
            or terminal_event.payload.get("delivery_ref")
            != expected_authorized_ref
            or terminal_event.payload.get("boundary_receipt_ref")
            != expected_boundary_ref
            or terminal_event.payload.get("witness_ref")
            != _ref_payload(witness_ref)
            or terminal_event.payload.get("outcome") != "acknowledged"
            or observed_event.event_type != "observed_read/v1"
            or observed_event.aggregate_id != str(authorized_ref.entity_id)
            or observed_event.payload.get("boundary") != "petri_input"
            or observed_event.payload.get("boundary_receipt_ref")
            != expected_boundary_ref
            or observed_event.payload.get("witness_ref")
            != _ref_payload(witness_ref)
            or observed_event.payload.get("resource_ref") != expected_resource
            or observed_event.payload.get("resource_byte_count")
            != terminal_meta.get("resource_byte_count")
            or boundary_meta.get("resource_byte_count")
            != terminal_meta.get("resource_byte_count")):
        raise ResourceIntegrityFault(
            "petri input receipt does not prove one exact acknowledged read")
    return PetriInputReceiptAuthority(
        authorized_delivery_ref=authorized_ref,
        terminal_delivery_ref=receipt.delivery_ref,
        boundary_receipt_ref=boundary_receipt_ref,
        witness_ref=witness_ref,
        exact_resource_ref=exact_resource_ref,
        positive_byte_count=positive_count,
        terminal_event_id=receipt.terminal_event_id,
        observed_read_event_id=receipt.observed_read_event_id,
        consumer_evidence=evidence,
        verified_at_head=kernel._head(),
    )
