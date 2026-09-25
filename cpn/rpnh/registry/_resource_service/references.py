"""References operations for the live Registry resource kernel."""

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

def _prepared(self, ref: ResourceVersionRef):
    try:
        prepared = self._ResourceServiceKernel__core.get_version(ref.resource_version_id)
    except RegistryReadError as exc:
        raise UnknownResourceVersion(str(ref.resource_version_id)) from exc
    if (prepared.object_type != "resource_version/v1"
            or prepared.logical_id != ref.resource_id):
        raise UnknownResourceVersion("resource identity and immutable version disagree")
    return self._prepared_reference(ref, prepared=prepared)

def _canonical_prepared(
        self, ref: ResourceVersionRef, *, through_ordinal: int):
    view = self._ResourceServiceKernel__core.event_store.canonical_view(
        through_ordinal=through_ordinal)
    row = self._ResourceServiceKernel__core.event_store.object_row_for_view(
        view, ref.resource_version_id)
    if row is None:
        raise UnknownResourceVersion(
            "resource is not authoritative at the requested Registry head")
    try:
        prepared = self._ResourceServiceKernel__core.get_version(ref.resource_version_id)
    except RegistryReadError as exc:
        raise UnknownResourceVersion(str(ref.resource_version_id)) from exc
    return self._prepared_reference(
        ref, prepared=prepared, view=view)

def _firing_prepared(
        self, context: InvocationContext, ref: ResourceVersionRef, *,
        view: FiringView | None = None):
    """Read canonical state or this invocation's exact firing-local view."""

    firing_ref = context.own_transition_firing_ref
    if (firing_ref is None
            or firing_ref.entity_type != "transition_firing/v1"):
        raise UnknownResourceVersion(
            "resource read requires one exact transition firing")
    captured = view or self._ResourceServiceKernel__core.event_store.firing_view(
        firing_version_id=firing_ref.version_id,
        invocation_version_id=context.invocation_ref.version_id)
    if (captured.firing_version_id != str(firing_ref.version_id)
            or captured.invocation_version_id
            != str(context.invocation_ref.version_id)
            or self._ResourceServiceKernel__core.event_store.object_row_for_view(
                captured, ref.resource_version_id) is None):
        raise UnknownResourceVersion(
            "resource is outside the canonical or exact firing view")
    try:
        prepared = self._ResourceServiceKernel__core.get_version(ref.resource_version_id)
    except RegistryReadError as exc:
        raise UnknownResourceVersion(str(ref.resource_version_id)) from exc
    return self._prepared_reference(ref, prepared=prepared, view=captured)

def _agent_response_prepared(
        self, invocation_ref: VersionRef, response_ref: ResourceVersionRef,
        *, require_current_writer: bool):
    """Read an LLM response from its canonical or exact firing view."""

    if not require_current_writer:
        return self._prepared_reference(response_ref)
    try:
        return self._prepared(response_ref)
    except UnknownResourceVersion:
        context_ref = invocation_ref
        if invocation_ref.entity_type == "llm_invocation_spec/v1":
            invocation = self._exact_object(
                invocation_ref,
                expected_type="llm_invocation_spec/v1")
            context_ref = _version_from_payload(
                invocation.metadata["invocation_ref"])
        context = InvocationLifecycle(self._ResourceServiceKernel__core).hydrate_context(
            context_ref)
        return self._firing_prepared(context, response_ref)

def _read_firing_registered(
        self, context: InvocationContext,
        ref: ResourceVersionRef, *, view: FiringView | None = None,
        prepared: Any = None) -> bytes:
    """Read bytes only after the explicit firing-view visibility gate."""

    if prepared is None:
        prepared = self._firing_prepared(context, ref, view=view)
    try:
        return self._ResourceServiceKernel__core.object_store.read_registered(prepared)
    except ResourceServiceError:
        raise
    except Exception as exc:
        raise ResourceIntegrityFault(
            "firing-view registered resource read failed") from exc

def _prepared_reference(
        self, ref: ResourceVersionRef, *, prepared: Any = None,
        view: RegistryAuthorityView | None = None):
    """Validate one exact resource and its direct Registry ownership graph."""
    authority = view or self._ResourceServiceKernel__core.event_store.canonical_view()
    if self._ResourceServiceKernel__core.event_store.object_row_for_view(
            authority, ref.resource_version_id) is None:
        try:
            unresolved = self._ResourceServiceKernel__core.get_version(
                ref.resource_version_id)
            schema_ref = unresolved.metadata.get("content_schema_ref")
        except RegistryReadError:
            schema_ref = None
        with self._ResourceServiceKernel__core.event_store.connect() as db:
            owner_rows = db.execute(
                "SELECT p.state,p.firing_version_id "
                "FROM firing_temporary_members m "
                "JOIN firing_publications p ON "
                "p.firing_version_id=m.firing_version_id "
                "WHERE m.member_kind='object' AND m.member_identity=? "
                "ORDER BY p.firing_version_id",
                (str(ref.resource_version_id),),
            ).fetchall()
        owners = ";".join(
            f"{row['state']}:{row['firing_version_id']}"
            for row in owner_rows) or "none"
        authority_label = (
            f"firing@{authority.canonical.through_ordinal}"
            if isinstance(authority, FiringView)
            else f"canonical@{authority.through_ordinal}")
        callers = ">".join(
            frame.name for frame in traceback.extract_stack(limit=9)[:-1])
        raise UnknownResourceVersion(
            "resource is outside the supplied Registry read authority: "
            f"version={ref.resource_version_id};schema={schema_ref};"
            f"view={authority_label};owners={owners};callers={callers}")
    if prepared is None:
        try:
            prepared = self._ResourceServiceKernel__core.get_version(ref.resource_version_id)
        except RegistryReadError as exc:
            raise UnknownResourceVersion(str(ref.resource_version_id)) from exc
    metadata = prepared.metadata
    if (prepared.object_type != "resource_version/v1"
            or prepared.logical_id != ref.resource_id
            or prepared.version_id != ref.resource_version_id
            or metadata.get("resource_id") != str(ref.resource_id)
            or metadata.get("resource_version_id")
            != str(ref.resource_version_id)
            or metadata.get("size") != prepared.size
            or metadata.get("media_type") != prepared.media_type):
        raise ResourceIntegrityFault(
            "direct resource ref differs from its immutable Registry object")
    direct = metadata.get("reference_provenance")
    if direct is None:
        raise ResourceIntegrityFault(
            "fresh resource lacks exact reference provenance")
    if (not isinstance(direct, Mapping)
            or direct.get("schema_version")
            != "resource_reference_provenance/v1"):
        raise ResourceIntegrityFault("direct resource provenance is malformed")
    if "producer_invocation_ref" in direct:
        if direct.get("producer_invocation_ref") is None:
            if metadata.get("origin_kind") == "checkpoint_repair":
                return self._prepared_checkpoint_repair_reference(
                    ref, prepared=prepared, direct=direct,
                    view=authority)
            return self._prepared_fresh_bootstrap_reference(
                ref, prepared=prepared, direct=direct,
                view=authority)
        return self._prepared_fresh_reference(
            ref, prepared=prepared, direct=direct,
            view=authority)
    try:
        producer_ref = _version_from_payload(metadata["producer_ref"])
        task_ref = _version_from_payload(metadata["task_ref"])
        round_ref = _version_from_payload(metadata["round_ref"])
        net_ref = _version_from_payload(metadata["net_ref"])
        binding_ref = _version_from_payload(
            direct["operation_binding_ref"])
        consumer_authority_ref = _version_from_payload(
            direct["agent_loop_ref"])
        input_refs = tuple(
            _version_from_payload(value)
            for value in direct["input_resource_refs"])
        publication = direct["publication"]
        consumer = direct["intended_consumer"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "direct resource provenance refs are malformed") from exc
    invocation = self._exact_object_for_view(
        authority, producer_ref, expected_type="invocation/v1")
    binding = self._exact_object_for_view(
        authority, binding_ref, expected_type="operation_binding/v1")
    consumer_authority = self._exact_object_for_view(
        authority, consumer_authority_ref)
    for value in (task_ref, round_ref, net_ref):
        self._exact_object_for_view(authority, value)
    for value in input_refs:
        self._exact_object_for_view(
            authority, value, expected_type="resource_version/v1")
    schema_authority = metadata.get("content_schema_authority_ref")
    if schema_authority is not None:
        if not isinstance(schema_authority, Mapping):
            raise ResourceIntegrityFault(
                "direct resource schema authority ref is malformed")
        self._exact_object_for_view(
            authority,
            _version_from_payload(schema_authority),
            expected_type="registry_type_catalog/v1")
    if (not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or invocation.metadata.get("task_ref") != metadata.get("task_ref")
            or invocation.metadata.get("task_round_ref")
            != metadata.get("round_ref")
            or invocation.metadata.get("net_instance_ref")
            != metadata.get("net_ref")
            or invocation.metadata.get("operation_binding_ref")
            != direct.get("operation_binding_ref")
            or publication.get("origin_kind")
            != metadata.get("origin_kind")
            or publication.get("primary_ref")
            != metadata.get("origin", {}).get("primary_ref")
            or publication.get("secondary_ref")
            != metadata.get("origin", {}).get("secondary_ref")
            or publication.get("lifetime_ref")
            != metadata.get("lifetime_ref")
            or publication.get("content_schema_ref")
            != metadata.get("content_schema_ref")
            or publication.get("content_schema_authority_ref")
            != metadata.get("content_schema_authority_ref")
            or consumer.get("consumer_ref") != direct.get("agent_loop_ref")):
        raise ResourceIntegrityFault(
            "direct resource ownership or publication refs differ")
    related = self._ResourceServiceKernel__core.event_store.relation_rows_for_view(
        authority, version_id=ref.resource_version_id)

    def relation_ref(raw: Mapping[str, Any]) -> VersionRef:
        return VersionRef(
            str(raw["entity_type"]),
            TypedId.parse(str(raw.get("logical_id", raw["entity_id"]))),
            TypedId.parse(str(raw["version_id"])))

    def has_relation(
            relation_type: str, source: VersionRef,
            target: VersionRef) -> bool:
        expected_source = _ref_payload(source)
        expected_target = _ref_payload(target)

        return any(
            row["relation_type"] == relation_type
            and row["strength"] == "strong"
            and _ref_payload(relation_ref(
                json.loads(str(row["source_json"])))) == expected_source
            and _ref_payload(relation_ref(
                json.loads(str(row["target_json"])))) == expected_target
            for row in related)

    def strong_relation_targets(
            relation_type: str,
    ) -> list[dict[str, Any]]:
        targets: list[dict[str, Any]] = []
        for row in related:
            if (row["relation_type"] != relation_type
                    or row["strength"] != "strong"):
                continue
            source = relation_ref(json.loads(str(row["source_json"])))
            if _ref_payload(source) != _ref_payload(resource_version_ref):
                continue
            targets.append(_ref_payload(
                relation_ref(json.loads(str(row["target_json"])))))
        return sorted(targets, key=canonical_json)

    resource_version_ref = ref.as_version_ref()
    if not has_relation("produced_by", resource_version_ref, producer_ref):
        raise ResourceIntegrityFault(
            "direct resource lacks its strong producer relation")
    if metadata.get("origin_kind") in {"provider_request", "llm_request"}:
        if (consumer.get("boundary") != "llm_prompt"
                or not has_relation(
                    "derived_from", resource_version_ref, binding_ref)
                or not has_relation(
                    "derived_from", resource_version_ref,
                    consumer_authority_ref)
                or any(not has_relation(
                    "derived_from", resource_version_ref, value)
                       for value in input_refs)):
            raise ResourceIntegrityFault(
                "fresh provider request lacks direct strong relations")
    elif metadata.get("origin_kind") == "provider_raw_response":
        attempt_ref = _version_from_payload(
            metadata["origin"]["primary_ref"])
        call_ref = _version_from_payload(
            metadata["origin"]["secondary_ref"])
        expected_boundary = (
            "registered_host_response"
            if call_ref.entity_type == "llm_call_spec/v3"
            else "agent_turn_decode")
        if (call_ref.entity_type not in {
                "llm_call_spec/v2", "llm_call_spec/v3"}
                or consumer.get("boundary") != expected_boundary
                or not has_relation(
                    "attempt_produced_response", attempt_ref,
                    resource_version_ref)):
            raise ResourceIntegrityFault(
                "fresh provider response lacks direct strong relations")
        self._ResourceServiceKernel__core.event_store.require_canonical_provider_lineage(
            "attempt_of_call", attempt_ref, call_ref,
            view=authority)
        self._ResourceServiceKernel__core.event_store.require_canonical_provider_lineage(
            "call_of_invocation", call_ref, producer_ref,
            view=authority)
    elif metadata.get("origin_kind") == "llm_response":
        attempt_ref = _version_from_payload(
            metadata["origin"]["primary_ref"])
        llm_invocation_ref = _version_from_payload(
            metadata["origin"]["secondary_ref"])
        if llm_invocation_ref.entity_type == "llm_call_spec/v3":
            call = self._exact_object_for_view(
                authority, llm_invocation_ref,
                expected_type="llm_call_spec/v3")
            attempt = self._exact_object_for_view(
                authority, attempt_ref,
                expected_type="registered_host_llm_attempt/v1")
            self._exact_object_for_view(
                authority, consumer_authority_ref,
                expected_type="llm_call_spec/v3")
            extension = metadata.get("extensions", {}).get(
                "registry.registered_host_llm_response/v1")
            if (consumer.get("boundary") != "registered_host_response"
                    or consumer.get("consumer_ref")
                    != _ref_payload(consumer_authority_ref)
                    or consumer_authority_ref != llm_invocation_ref
                    or call.metadata.get("invocation_kind")
                    != "registered_host"
                    or call.metadata.get("invocation_ref")
                    != _ref_payload(producer_ref)
                    or call.metadata.get("operation_binding_ref")
                    != _ref_payload(binding_ref)
                    or len(input_refs) != 1
                    or call.metadata.get("request_resource_ref")
                    != _resource_payload(ResourceVersionRef(
                        input_refs[0].entity_id, input_refs[0].version_id))
                    or attempt.metadata.get(
                        "registered_host_llm_attempt_ref")
                    != _ref_payload(attempt_ref)
                    or attempt.metadata.get("llm_call_ref")
                    != _ref_payload(llm_invocation_ref)
                    or not isinstance(extension, Mapping)
                    or extension.get("registered_host_llm_attempt_ref")
                    != _ref_payload(attempt_ref)
                    or extension.get("llm_call_ref")
                    != _ref_payload(llm_invocation_ref)
                    or extension.get("model_condition")
                    != call.metadata.get("model")):
                raise ResourceIntegrityFault(
                    "registered HOST LLM response differs from its exact "
                    "firing authority")
            expected_derived = sorted(
                [_ref_payload(llm_invocation_ref),
                 _ref_payload(attempt_ref),
                 *(_ref_payload(value) for value in input_refs)],
                key=canonical_json)
            if (strong_relation_targets("produced_by")
                    != [_ref_payload(producer_ref)]
                    or strong_relation_targets("derived_from")
                    != expected_derived):
                raise ResourceIntegrityFault(
                    "registered HOST LLM response lacks exact strong "
                    "owner/input relations")
            return prepared
        llm_invocation = self._exact_object_for_view(
            authority, llm_invocation_ref,
            expected_type="llm_invocation_spec/v1")
        attempt = self._exact_object_for_view(
            authority, attempt_ref,
            expected_type="llm_invocation_attempt/v1")
        self._exact_object_for_view(
            authority, consumer_authority_ref,
            expected_type="agent_loop/v1")
        if (consumer.get("boundary") != "not_applicable"
                or consumer.get("consumer_ref")
                != _ref_payload(consumer_authority_ref)
                or llm_invocation.metadata.get("llm_invocation_ref")
                != _ref_payload(llm_invocation_ref)
                or llm_invocation.metadata.get("invocation_ref")
                != _ref_payload(producer_ref)
                or llm_invocation.metadata.get("operation_binding_ref")
                != _ref_payload(binding_ref)
                or llm_invocation.metadata.get("agent_loop_ref")
                != _ref_payload(consumer_authority_ref)
                or len(input_refs) != 1
                or llm_invocation.metadata.get("request_resource_ref")
                != _resource_payload(ResourceVersionRef(
                    input_refs[0].entity_id, input_refs[0].version_id))
                or attempt.metadata.get("llm_invocation_ref")
                != _ref_payload(llm_invocation_ref)
                or attempt.metadata.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt_ref)):
            raise ResourceIntegrityFault(
                "framework LLM response differs from its invocation authority")

        expected_derived = sorted(
            [_ref_payload(llm_invocation_ref), _ref_payload(attempt_ref),
             *(_ref_payload(value) for value in input_refs)],
            key=canonical_json)
        if (strong_relation_targets("produced_by")
                != [_ref_payload(producer_ref)]
                or strong_relation_targets("derived_from")
                != expected_derived):
            raise ResourceIntegrityFault(
                "framework LLM response lacks exact strong owner/input relations")
    elif (metadata.get("origin_kind")
          == "parent_owned_delegated_subtask_result"):
        attempt_ref = _version_from_payload(
            metadata["origin"]["primary_ref"])
        llm_invocation_ref = _version_from_payload(
            metadata["origin"]["secondary_ref"])
        llm_invocation = self._exact_object_for_view(
            authority, llm_invocation_ref,
            expected_type="llm_invocation_spec/v1")
        attempt = self._exact_object_for_view(
            authority, attempt_ref,
            expected_type="llm_invocation_attempt/v1")
        self._exact_object_for_view(
            authority, consumer_authority_ref,
            expected_type="agent_loop/v1")
        extension = metadata.get("extensions", {}).get(
            "registry.parent_owned_delegated_subtask/v1")
        try:
            parent_action_ref = _version_from_payload(
                extension["parent_action_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "delegated result lacks its exact future parent action") from exc
        response_ref = (
            ResourceVersionRef(input_refs[0].entity_id,
                               input_refs[0].version_id)
            if len(input_refs) == 1 else None)
        response = (
            self._exact_object_for_view(
                authority, input_refs[0],
                expected_type="resource_version/v1")
            if response_ref is not None else None)
        if (consumer.get("boundary")
                != "parent_agent_action_result"
                or consumer.get("consumer_ref")
                != _ref_payload(consumer_authority_ref)
                or parent_action_ref.entity_type != "agent_action/v2"
                or not isinstance(extension, Mapping)
                or extension.get("llm_invocation_ref")
                != _ref_payload(llm_invocation_ref)
                or extension.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt_ref)
                or llm_invocation.metadata.get("invocation_kind")
                != "delegated_subtask"
                or llm_invocation.metadata.get("invocation_ref")
                != _ref_payload(producer_ref)
                or llm_invocation.metadata.get("operation_binding_ref")
                != _ref_payload(binding_ref)
                or llm_invocation.metadata.get("agent_loop_ref")
                != _ref_payload(consumer_authority_ref)
                or attempt.metadata.get("llm_invocation_ref")
                != _ref_payload(llm_invocation_ref)
                or attempt.metadata.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt_ref)
                or response is None
                or response.metadata.get("origin_kind") != "llm_response"
                or response.metadata.get("origin", {}).get("primary_ref")
                != _ref_payload(attempt_ref)
                or response.metadata.get("origin", {}).get("secondary_ref")
                != _ref_payload(llm_invocation_ref)):
            raise ResourceIntegrityFault(
                "delegated result differs from its parent/child authority")
        parent_action_visible = (
            self._ResourceServiceKernel__core.event_store.object_row_for_view(
                authority, parent_action_ref.version_id) is not None)
        if parent_action_visible:
            parent_action = self._exact_object_for_view(
                authority, parent_action_ref,
                expected_type="agent_action/v2")
            if (parent_action.metadata.get("tool_name") != "delegate_leaf"
                    or parent_action.metadata.get("state")
                    != "ACTION_APPLIED"
                    or parent_action.metadata.get("result_refs")
                    != [_ref_payload(resource_version_ref)]):
                raise ResourceIntegrityFault(
                    "delegated result differs from its settled parent action")
        expected_derived = sorted(
            [_ref_payload(llm_invocation_ref), _ref_payload(attempt_ref),
             _ref_payload(input_refs[0]),
             *([_ref_payload(parent_action_ref)]
               if parent_action_visible else [])],
            key=canonical_json)
        if (strong_relation_targets("produced_by")
                != [_ref_payload(producer_ref)]
                or strong_relation_targets("derived_from")
                != expected_derived):
            raise ResourceIntegrityFault(
                "delegated result lacks exact parent/child relations")
    else:
        raise ResourceIntegrityFault(
            "direct resource provenance is outside the fresh provider path")
    return prepared

def _read_registered(self, ref: ResourceVersionRef) -> bytes:
    """Read one fresh resource after exact Registry-reference validation."""

    if not isinstance(ref, ResourceVersionRef):
        raise TypeError(
            "fresh registered read requires one exact ResourceVersionRef")
    prepared = self._prepared_reference(ref)
    try:
        return self._ResourceServiceKernel__core.object_store.read_registered(prepared)
    except ResourceServiceError:
        raise
    except Exception as exc:
        raise ResourceIntegrityFault(
            "fresh registered resource read failed") from exc

def _prepared_fresh_bootstrap_reference(
        self, ref: ResourceVersionRef, *, prepared: Any,
        direct: Mapping[str, Any],
        view: RegistryAuthorityView):
    """Validate one fresh bootstrap resource with no invocation owner."""

    metadata = prepared.metadata
    origin = metadata.get("origin")
    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    derived_values = direct.get("derived_from_refs")
    contributor_values = direct.get("contributor_refs")
    supersedes_value = direct.get("supersedes_ref")
    if (set(direct) != {
            "schema_version", "producer_invocation_ref",
            "operation_binding_ref", "derived_from_refs",
            "contributor_refs", "supersedes_ref", "intended_consumer",
            "publication"}
            or direct.get("producer_invocation_ref") is not None
            or direct.get("operation_binding_ref") is not None
            or not isinstance(origin, Mapping)
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_values, list)
            or contributor_values != []
            or derived_values != sorted(derived_values, key=canonical_json)
            or len(derived_values) != len({
                canonical_json(value) for value in derived_values})):
        raise ResourceIntegrityFault(
            "fresh bootstrap reference provenance is malformed")
    try:
        task_ref = _version_from_payload(metadata["task_ref"])
        producer_ref = _version_from_payload(metadata["producer_ref"])
        lifetime_ref = _version_from_payload(metadata["lifetime_ref"])
        primary_ref = _version_from_payload(origin["primary_ref"])
        secondary_ref = _version_from_payload(origin["secondary_ref"])
        derived_refs = tuple(
            _version_from_payload(value) for value in derived_values)
        supersedes_ref = (
            _version_from_payload(supersedes_value)
            if supersedes_value is not None else None)
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "fresh bootstrap exact refs are malformed") from exc
    self._exact_object_for_view(view, task_ref, expected_type="task/v1")
    self._exact_object_for_view(
        view, producer_ref, expected_type="bootstrap_command/v1")
    self._exact_object_for_view(view, lifetime_ref)
    for value in derived_refs:
        self._exact_object_for_view(
            view, value, expected_type="resource_version/v1")
    if supersedes_ref is not None:
        self._exact_object_for_view(
            view, supersedes_ref, expected_type="resource_version/v1")
    schema_source = metadata.get("content_schema_authority_ref")
    schema_id = metadata.get("content_schema_ref")
    if ((schema_id is None) != (schema_source is None)):
        raise ResourceIntegrityFault(
            "fresh bootstrap schema authority is incomplete")
    if schema_source is not None:
        try:
            exact_schema_source = _content_schema_source_from_payload(
                schema_source)
            assert exact_schema_source is not None
            self._exact_object_for_view(
                view,
                exact_schema_source.as_version_ref()
                if isinstance(exact_schema_source, ResourceVersionRef)
                else exact_schema_source)
        except (AssertionError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "fresh bootstrap schema authority ref is malformed") from exc
    if (metadata.get("origin_kind") != "private_system"
            or origin.get("kind") != "private_system"
            or task_ref.entity_id != self._ResourceServiceKernel__core.task_id
            or metadata.get("branch_id") != self._ResourceServiceKernel__core.branch_id
            or metadata.get("round_ref") is not None
            or metadata.get("net_ref") is not None
            or primary_ref != producer_ref
            or secondary_ref != producer_ref
            or prepared.producer_invocation_id is not None
            or publication != {
                "origin_kind": "private_system",
                "primary_ref": origin.get("primary_ref"),
                "secondary_ref": origin.get("secondary_ref"),
                "lifetime_ref": metadata.get("lifetime_ref"),
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": schema_id,
                "content_schema_authority_ref": schema_source,
            }
            or consumer != {
                "boundary": "not_applicable",
                "consumer_ref": None}):
        raise ResourceIntegrityFault(
            "fresh bootstrap ownership or publication refs differ")

    related = self._ResourceServiceKernel__core.event_store.relation_rows_for_view(
        view, version_id=ref.resource_version_id)

    def outgoing_targets(relation_type: str) -> list[bytes]:
        targets: list[bytes] = []
        expected_source = _ref_payload(ref.as_version_ref())
        for row in related:
            if (row["relation_type"] != relation_type
                    or row["strength"] != "strong"):
                continue
            raw_source = json.loads(str(row["source_json"]))
            source = {
                "entity_type": raw_source["entity_type"],
                "logical_id": raw_source.get(
                    "logical_id", raw_source.get("entity_id")),
                "version_id": raw_source["version_id"],
            }
            if source == expected_source:
                raw_target = json.loads(str(row["target_json"]))
                targets.append(canonical_json({
                    "entity_type": raw_target["entity_type"],
                    "logical_id": raw_target.get(
                        "logical_id", raw_target.get("entity_id")),
                    "version_id": raw_target["version_id"],
                }))
        return sorted(targets)

    expected_derived = sorted(
        canonical_json(value) for value in derived_values)
    expected_supersedes = (
        [canonical_json(supersedes_value)]
        if supersedes_value is not None else [])
    if (outgoing_targets("produced_by")
            != [canonical_json(_ref_payload(producer_ref))]
            or outgoing_targets("derived_from") != expected_derived
            or outgoing_targets("contributed_by")
            or outgoing_targets("supersedes") != expected_supersedes):
        raise ResourceIntegrityFault(
            "fresh bootstrap direct strong relations differ from provenance")
    return prepared

def _prepared_checkpoint_repair_reference(
        self, ref: ResourceVersionRef, *, prepared: Any,
        direct: Mapping[str, Any],
        view: RegistryAuthorityView):
    """Validate one committed repair replacement from Registry facts."""

    metadata = prepared.metadata
    origin = metadata.get("origin")
    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    derived_values = direct.get("derived_from_refs")
    if (set(direct) != {
            "schema_version", "producer_invocation_ref",
            "operation_binding_ref", "derived_from_refs",
            "contributor_refs", "supersedes_ref", "intended_consumer",
            "publication"}
            or direct.get("producer_invocation_ref") is not None
            or direct.get("operation_binding_ref") is not None
            or direct.get("contributor_refs") != []
            or direct.get("supersedes_ref") is not None
            or not isinstance(origin, Mapping)
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_values, list)
            or not derived_values
            or derived_values != sorted(derived_values, key=canonical_json)
            or len(derived_values) != len({
                canonical_json(value) for value in derived_values})):
        raise ResourceIntegrityFault(
            "checkpoint repair reference provenance is malformed")
    try:
        task_ref = _version_from_payload(metadata["task_ref"])
        round_ref = _version_from_payload(metadata["round_ref"])
        net_ref = _version_from_payload(metadata["net_ref"])
        producer_ref = _version_from_payload(metadata["producer_ref"])
        lifetime_ref = _version_from_payload(metadata["lifetime_ref"])
        primary_ref = _version_from_payload(origin["primary_ref"])
        source_ref = _resource_from_payload({
            "resource_id": origin["secondary_ref"]["logical_id"],
            "resource_version_id": origin["secondary_ref"]["version_id"],
        })
        derived_refs = tuple(
            _version_from_payload(value) for value in derived_values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "checkpoint repair exact refs are malformed") from exc
    task = self._exact_object_for_view(
        view, task_ref, expected_type="task/v1")
    del task
    self._exact_object_for_view(
        view, round_ref, expected_type="task_round/v1")
    self._exact_object_for_view(
        view, net_ref, expected_type="net_instance/v1")
    repair = self._exact_object_for_view(
        view, producer_ref, expected_type="checkpoint_repair/v1")
    self._exact_object_for_view(
        view, source_ref.as_version_ref(),
        expected_type="resource_version/v1")
    for value in derived_refs:
        self._exact_object_for_view(
            view, value, expected_type="resource_version/v1")
    schema_source = metadata.get("content_schema_authority_ref")
    schema_id = metadata.get("content_schema_ref")
    if ((schema_id is None) != (schema_source is None)):
        raise ResourceIntegrityFault(
            "checkpoint repair schema authority is incomplete")
    if schema_source is not None:
        try:
            exact_schema_source = _content_schema_source_from_payload(
                schema_source)
            assert exact_schema_source is not None
            self._exact_object_for_view(
                view,
                exact_schema_source.as_version_ref()
                if isinstance(exact_schema_source, ResourceVersionRef)
                else exact_schema_source)
        except (AssertionError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "checkpoint repair schema authority ref is malformed") from exc
    replacement_ref = _ref_payload(ref.as_version_ref())
    source_exact = _ref_payload(source_ref.as_version_ref())
    replacement_rows = repair.metadata.get("replacements")
    matching_rows = (
        [row for row in replacement_rows
         if isinstance(row, Mapping)
         and row.get("source_resource_ref") == source_exact
         and row.get("replacement_resource_ref") == replacement_ref]
        if isinstance(replacement_rows, list) else [])
    if (metadata.get("origin_kind") != "checkpoint_repair"
            or origin.get("kind") != "checkpoint_repair"
            or task_ref.entity_id != self._ResourceServiceKernel__core.task_id
            or metadata.get("branch_id") != self._ResourceServiceKernel__core.branch_id
            or primary_ref != producer_ref
            or lifetime_ref != producer_ref
            or source_exact not in derived_values
            or repair.metadata.get("checkpoint_repair_ref")
            != _ref_payload(producer_ref)
            or repair.metadata.get("net_instance_ref")
            != _ref_payload(net_ref)
            or len(matching_rows) != 1
            or prepared.producer_invocation_id is not None
            or publication != {
                "origin_kind": "checkpoint_repair",
                "primary_ref": origin.get("primary_ref"),
                "secondary_ref": origin.get("secondary_ref"),
                "lifetime_ref": metadata.get("lifetime_ref"),
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": schema_id,
                "content_schema_authority_ref": schema_source,
            }
            or consumer != {
                "boundary": "not_applicable",
                "consumer_ref": None}):
        raise ResourceIntegrityFault(
            "checkpoint repair ownership or publication refs differ")

    related = self._ResourceServiceKernel__core.event_store.relation_rows_for_view(
        view, version_id=ref.resource_version_id)

    def outgoing_targets(relation_type: str) -> list[bytes]:
        targets: list[bytes] = []
        expected_source = _ref_payload(ref.as_version_ref())
        for row in related:
            if (row["relation_type"] != relation_type
                    or row["strength"] != "strong"):
                continue
            raw_source = json.loads(str(row["source_json"]))
            source = {
                "entity_type": raw_source["entity_type"],
                "logical_id": raw_source.get(
                    "logical_id", raw_source.get("entity_id")),
                "version_id": raw_source["version_id"],
            }
            if source == expected_source:
                raw_target = json.loads(str(row["target_json"]))
                targets.append(canonical_json({
                    "entity_type": raw_target["entity_type"],
                    "logical_id": raw_target.get(
                        "logical_id", raw_target.get("entity_id")),
                    "version_id": raw_target["version_id"],
                }))
        return sorted(targets)

    expected_derived = sorted(
        canonical_json(value) for value in derived_values)
    if (outgoing_targets("produced_by")
            != [canonical_json(_ref_payload(producer_ref))]
            or outgoing_targets("derived_from") != expected_derived
            or outgoing_targets("contributed_by")
            or outgoing_targets("supersedes")):
        raise ResourceIntegrityFault(
            "checkpoint repair direct strong relations differ")
    return prepared

def _prepared_fresh_reference(
        self, ref: ResourceVersionRef, *, prepared: Any,
        direct: Mapping[str, Any],
        view: RegistryAuthorityView):
    """Validate one fresh normal resource from exact Registry references."""

    metadata = prepared.metadata
    origin = metadata.get("origin")
    publication = direct.get("publication")
    consumer = direct.get("intended_consumer")
    derived_values = direct.get("derived_from_refs")
    tool_evidence_values = direct.get("tool_evidence_refs")
    contributor_values = direct.get("contributor_refs")
    supersedes_value = direct.get("supersedes_ref")
    if (set(direct) != {
            "schema_version", "producer_invocation_ref",
            "operation_binding_ref", "derived_from_refs",
            "tool_evidence_refs",
            "contributor_refs", "supersedes_ref", "intended_consumer",
            "publication"}
            or not isinstance(origin, Mapping)
            or not isinstance(publication, Mapping)
            or not isinstance(consumer, Mapping)
            or not isinstance(derived_values, list)
            or not isinstance(tool_evidence_values, list)
            or not isinstance(contributor_values, list)
            or derived_values != sorted(derived_values, key=canonical_json)
            or contributor_values
            != sorted(contributor_values, key=canonical_json)
            or tool_evidence_values
            != sorted(tool_evidence_values, key=canonical_json)
            or len(derived_values) != len({
                canonical_json(value) for value in derived_values})
            or len(contributor_values) != len({
                canonical_json(value) for value in contributor_values})
            or len(tool_evidence_values) != len({
                canonical_json(value)
                for value in tool_evidence_values})
            or bool(tool_evidence_values)):
        raise ResourceIntegrityFault(
            "fresh resource reference provenance is malformed")
    try:
        invocation_ref = _version_from_payload(
            direct["producer_invocation_ref"])
        binding_ref = _version_from_payload(
            direct["operation_binding_ref"])
        task_ref = _version_from_payload(metadata["task_ref"])
        round_ref = _version_from_payload(metadata["round_ref"])
        net_ref = _version_from_payload(metadata["net_ref"])
        producer_ref = _version_from_payload(metadata["producer_ref"])
        primary_ref = _version_from_payload(origin["primary_ref"])
        secondary_ref = (_version_from_payload(origin["secondary_ref"])
                         if origin["secondary_ref"] is not None else None)
        derived_refs = tuple(
            _version_from_payload(value) for value in derived_values)
        contributor_refs = tuple(
            _version_from_payload(value) for value in contributor_values)
        tool_evidence_refs = tuple(
            _version_from_payload(value)
            for value in tool_evidence_values)
        supersedes_ref = (
            _version_from_payload(supersedes_value)
            if supersedes_value is not None else None)
    except (KeyError, TypeError, ValueError) as exc:
        raise ResourceIntegrityFault(
            "fresh resource exact refs are malformed") from exc
    if secondary_ref is None and metadata.get("origin_kind") != "petri_output":
        raise ResourceIntegrityFault("only an unpaired Petri output may omit secondary refs")
    invocation = self._exact_object_for_view(
        view, invocation_ref, expected_type="invocation/v1")
    self._exact_object_for_view(
        view, binding_ref, expected_type="operation_binding/v1")
    for value in (task_ref, round_ref, net_ref, primary_ref, secondary_ref):
        if value is None:
            continue
        self._exact_object_for_view(view, value)
    for value in derived_refs:
        self._exact_object_for_view(
            view, value, expected_type="resource_version/v1")
    for value in contributor_refs:
        self._exact_object_for_view(view, value)
    for value in tool_evidence_refs:
        self._exact_object_for_view(
            view, value, expected_type="agent_action/v2")
    if supersedes_ref is not None:
        self._exact_object_for_view(
            view, supersedes_ref, expected_type="resource_version/v1")
    if tool_evidence_refs:
        raise ResourceIntegrityFault(
            "LLM-authored resource carries model-declared tool provenance")
    schema_source = metadata.get("content_schema_authority_ref")
    if schema_source is not None:
        try:
            exact_schema_source = _content_schema_source_from_payload(
                schema_source)
            assert exact_schema_source is not None
            self._exact_object_for_view(
                view,
                exact_schema_source.as_version_ref()
                if isinstance(exact_schema_source, ResourceVersionRef)
                else exact_schema_source)
        except (AssertionError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "fresh resource schema authority ref is malformed") from exc
    kind = metadata.get("origin_kind")
    expected_boundary = {
        "petri_output": "petri_input",
        "workspace_write": "tool_result",
    }.get(str(kind), "not_applicable")
    expected_consumer = (
        _ref_payload(primary_ref) if kind == "petri_output" else
        _ref_payload(secondary_ref) if kind == "workspace_write" else None)
    expected_producer = (
        primary_ref if kind == "private_system" else invocation_ref)
    if (kind not in {"petri_output", "workspace_write", "private_system"}
            or origin.get("kind") != kind
            or invocation.metadata.get("invocation_ref")
            != _ref_payload(invocation_ref)
            or invocation.metadata.get("task_ref") != metadata.get("task_ref")
            or invocation.metadata.get("task_round_ref")
            != metadata.get("round_ref")
            or invocation.metadata.get("net_instance_ref")
            != metadata.get("net_ref")
            or invocation.metadata.get("operation_binding_ref")
            != direct.get("operation_binding_ref")
            or producer_ref != expected_producer
            or prepared.producer_invocation_id != invocation_ref.entity_id
            or publication != {
                "origin_kind": kind,
                "primary_ref": origin.get("primary_ref"),
                "secondary_ref": origin.get("secondary_ref"),
                "lifetime_ref": metadata.get("lifetime_ref"),
                "size": metadata.get("size"),
                "media_type": metadata.get("media_type"),
                "content_schema_ref": metadata.get("content_schema_ref"),
                "content_schema_authority_ref": metadata.get(
                    "content_schema_authority_ref"),
            }
            or consumer != {
                "boundary": expected_boundary,
                "consumer_ref": expected_consumer}):
        raise ResourceIntegrityFault(
            "fresh resource ownership or publication refs differ")
    if kind == "petri_output":
        output = self._exact_object_for_view(
            view, primary_ref, expected_type="output_binding/v1")
        if (invocation.metadata.get("own_transition_firing_ref") is None
                or invocation.metadata.get("activation_ref")
                != (_ref_payload(secondary_ref) if secondary_ref is not None else None)
                or output.metadata.get("task_round_ref")
                != metadata.get("round_ref")
                or output.metadata.get("net_ref") != metadata.get("net_ref")
                or output.metadata.get("node_ref")
                != invocation.metadata.get("own_node_ref")):
            raise ResourceIntegrityFault(
                "fresh Petri output origin differs from invocation authority")
    elif kind == "workspace_write":
        intent = self._exact_object_for_view(
            view, secondary_ref,
            expected_type="workspace_write_intent/v1")
        if (primary_ref != binding_ref
                or intent.metadata.get("operation_binding_ref")
                != _ref_payload(binding_ref)):
            raise ResourceIntegrityFault(
                "fresh workspace origin differs from invocation authority")
    elif primary_ref != secondary_ref:
        raise ResourceIntegrityFault(
            "fresh private-system origin is not one bootstrap authority")

    related = self._ResourceServiceKernel__core.event_store.relation_rows_for_view(
        view, version_id=ref.resource_version_id)

    def outgoing_targets(relation_type: str) -> list[bytes]:
        targets: list[bytes] = []
        expected_source = _ref_payload(ref.as_version_ref())
        for row in related:
            if (row["relation_type"] != relation_type
                    or row["strength"] != "strong"):
                continue
            raw_source = json.loads(str(row["source_json"]))
            source = {
                "entity_type": raw_source["entity_type"],
                "logical_id": raw_source.get(
                    "logical_id", raw_source.get("entity_id")),
                "version_id": raw_source["version_id"],
            }
            if source == expected_source:
                raw_target = json.loads(str(row["target_json"]))
                targets.append(canonical_json({
                    "entity_type": raw_target["entity_type"],
                    "logical_id": raw_target.get(
                        "logical_id", raw_target.get("entity_id")),
                    "version_id": raw_target["version_id"],
                }))
        return sorted(targets)

    expected_derived = sorted(
        canonical_json(value) for value in derived_values)
    expected_contributors = sorted(
        canonical_json(value) for value in contributor_values)
    expected_supersedes = (
        [canonical_json(supersedes_value)]
        if supersedes_value is not None else [])
    expected_tool_evidence = sorted(
        canonical_json(value) for value in tool_evidence_values)
    if (outgoing_targets("produced_by")
            != [canonical_json(_ref_payload(producer_ref))]
            or outgoing_targets("derived_from") != expected_derived
            or outgoing_targets("contributed_by") != expected_contributors
            or outgoing_targets("caused_by_tool_result")
            != expected_tool_evidence
            or outgoing_targets("supersedes") != expected_supersedes):
        raise ResourceIntegrityFault(
            "fresh resource direct strong relations differ from provenance")
    return prepared

def _exact_object(self, ref: VersionRef, *, expected_type: str | None = None):
    try:
        prepared = self._ResourceServiceKernel__core.get_version(ref.version_id)
    except RegistryReadError as exc:
        raise UnknownResourceVersion(f"unregistered exact ref: {ref.version_id}") from exc
    if prepared.logical_id != ref.entity_id or prepared.object_type != ref.entity_type:
        raise UnknownResourceVersion("exact reference does not match the registered object")
    if expected_type is not None and prepared.object_type != expected_type:
        raise UnknownResourceVersion(
            f"expected {expected_type}, received {prepared.object_type}")
    return prepared

def _exact_object_for_view(
        self, view: RegistryAuthorityView, ref: VersionRef, *,
        expected_type: str | None = None):
    """Resolve an exact object only through an authority read capability."""

    if self._ResourceServiceKernel__core.event_store.object_row_for_view(
            view, ref.version_id) is None:
        raise UnknownResourceVersion(
            f"exact ref is outside Registry read authority: {ref.version_id}")
    return self._exact_object(ref, expected_type=expected_type)

def _revalidate_invocation(
        self, context: InvocationContext, *, boundary: str,
        native_resume: bool = False) -> None:
    try:
        if self._ResourceServiceKernel__core.writer_epoch != self._ResourceServiceKernel__core.event_store.writer_epoch:
            raise StaleWriterFence("resource writer fence is stale")
        if native_resume:
            historical = InvocationLifecycle(self._ResourceServiceKernel__core).hydrate_context(
                context.invocation_ref, require_current_writer=False)
            if historical != context:
                raise StaleInvocationContext(
                    "native resume context differs from Registry")
            return
        InvocationLifecycle(self._ResourceServiceKernel__core).revalidate_io(context, boundary=boundary)
    except StaleWriterFence:
        raise
    except (InvocationAdmissionError, RegistryConflict, TypeError, ValueError) as exc:
        raise StaleInvocationContext(str(exc)) from exc

def _authorization_context(self, context: InvocationContext) -> InvocationContext:
    return context

def _ref_in(values: Iterable[Mapping[str, Any]], ref: VersionRef) -> bool:
    expected = _ref_payload(ref)
    return any(dict(value) == expected for value in values if isinstance(value, Mapping))

def _grant_allows(self, context: InvocationContext, authorization_ref: VersionRef,
                  resource_ref: ResourceVersionRef) -> bool:
    if authorization_ref.entity_type != "capability_grant/v1":
        return False
    prepared = self._exact_object(
        authorization_ref, expected_type="capability_grant/v1")
    metadata = dict(prepared.metadata)
    authority = self._authorization_context(context)
    lifetime_ref = authority.own_transition_firing_ref or authority.invocation_ref
    activation = (context.authorization_lifetime_activation_ref
                  or context.activation_ref)
    if (metadata.get("target_invocation_id")
            != str(authority.invocation_ref.entity_id)
            or metadata.get("operation_binding_ref")
            != _ref_payload(authority.operation_binding_ref)
            or metadata.get("lifetime_ref") != _ref_payload(lifetime_ref)
            or (metadata.get("activation_id") is not None and (
                activation is None
                or metadata["activation_id"] != str(activation.entity_id)))
            or str(resource_ref.resource_version_id)
            not in metadata.get("resource_version_ids", [])):
        return False
    events = self._ResourceServiceKernel__core.event_store.list_events_by_aggregate(
        str(authorization_ref.entity_id),
        event_types=(
            "capability_issued/v1",
            "capability_activated/v1",
            "capability_allowed/v1",
            "capability_denied/v1",
            "capability_revoked/v1",
        ))
    return bool(events and events[-1].event_type == "capability_allowed/v1")

def _claimed_petri_input_allows(
        self, context: InvocationContext, authorization_ref: VersionRef,
        resource_ref: ResourceVersionRef) -> bool:
    """Authorize only a file carried by this exact firing's Petri claim.

    Static operation bindings cannot enumerate resources that do not exist
    until an upstream firing publishes them.  Their authority is instead the
    append-only transition-firing claim plus the claimed token's registered
    state.  This is deliberately narrower than graph/task visibility: the
    exact token must name the exact file, or carry its exact admitted lease
    claim, and be addressed to this transition (or be a declared shared
    token) in this same net.
    """
    firing_ref = context.own_transition_firing_ref
    if (authorization_ref != context.operation_binding_ref
            or context.origin != "petri_operation"
            or not isinstance(firing_ref, VersionRef)):
        return False
    try:
        firing = self._exact_object(
            firing_ref, expected_type="transition_firing/v1")
        metadata = dict(firing.metadata)
        transition_id = metadata.get("transition_id")
        claimed = metadata.get("claimed_input_refs")
        if (metadata.get("operation_binding_ref")
                != _ref_payload(context.operation_binding_ref)
                or metadata.get("net_instance_ref")
                != _ref_payload(context.net_instance_ref)
                or not isinstance(transition_id, str)
                or not transition_id
                or not isinstance(claimed, list)):
            return False
        wanted_resource = _resource_payload(resource_ref)
        wanted_net = _ref_payload(context.net_instance_ref)
        for raw_ref in claimed:
            if not isinstance(raw_ref, Mapping):
                return False
            token_ref = _version_from_payload(raw_ref)
            if token_ref.entity_type != "petri_token/v1":
                return False
            token = self._exact_object(
                token_ref, expected_type="petri_token/v1")
            state = dict(token.metadata)
            if (state.get("petri_token_ref") != raw_ref
                    or state.get("net_instance_ref") != wanted_net
                    or state.get("consumer") not in {
                        None, transition_id}
                    or state.get("consumed_by") is not None):
                continue
            directly_carried = state.get("resource_ref") == wanted_resource
            lease_claim_matches = False
            if not directly_carried:
                raw_lease_claims = state.get("lease_claims")
                if not isinstance(raw_lease_claims, list):
                    continue
                for raw_claim in raw_lease_claims:
                    if not isinstance(raw_claim, Mapping):
                        lease_claim_matches = False
                        break
                    try:
                        claim_identity = _version_from_payload(
                            raw_claim["lease_identity_ref"])
                        claimed_resource = _resource_from_payload(
                            raw_claim["expected_resource_ref"])
                    except (KeyError, TypeError, ValueError):
                        lease_claim_matches = False
                        break
                    if (claim_identity.entity_type
                            == "resource_version/v1"
                            and claim_identity
                            != claimed_resource.as_version_ref()):
                        lease_claim_matches = False
                        break
                    if (claimed_resource == resource_ref
                            and raw_claim.get("access_mode")
                            in {"read", "edit"}
                            and raw_claim.get("staging_place")
                            in {None, state.get("place")}):
                        lease_claim_matches = True
                        break
            if not directly_carried and not lease_claim_matches:
                continue
            resource = self._prepared(resource_ref)
            origin = resource.metadata.get("origin")
            if (isinstance(origin, Mapping)
                    and origin.get("kind") == "checkpoint_repair"):
                return False
            return bool(
                resource.metadata.get("task_ref")
                == _ref_payload(context.task_ref)
                and isinstance(origin, Mapping)
                and origin.get("kind") in {
                    "petri_output", "private_system"})
    except (KeyError, TypeError, ValueError, ResourceServiceError):
        return False
    return False

def _live_firing_resource_extension_allows(
        self, context: InvocationContext,
        authorization_ref: VersionRef,
        resource_ref: ResourceVersionRef) -> bool:
    """Recognize one append-only same-firing AgentLoop extension fact."""

    firing_ref = context.own_transition_firing_ref
    if (authorization_ref != context.operation_binding_ref
            or context.origin != "petri_operation"
            or not isinstance(firing_ref, VersionRef)):
        return False
    expected = {
        "transition_firing_ref": _ref_payload(firing_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_execution_lease_ref": _ref_payload(
            context.operation_execution_lease_ref),
        "operation_binding_ref": _ref_payload(
            context.operation_binding_ref),
        "resource_ref": _resource_payload(resource_ref),
    }
    terminal_types = {
        "transition_firing_settled/v1",
        "transition_firing_superseded_by_growth_recovery/v1",
        "transition_firing_superseded_by_native_resume/v1",
    }
    events = tuple(self._ResourceServiceKernel__core.event_store.list_events())
    if any(
            event.aggregate_id == str(firing_ref.entity_id)
            and event.event_type in terminal_types
            for event in events):
        return False
    matches = tuple(
        event for event in events
        if (event.event_type == "live_firing_resource_extension/v1"
            and event.aggregate_id == str(firing_ref.entity_id)
            and event.writer_fencing_epoch == self._ResourceServiceKernel__core.writer_epoch
            and event.producer_invocation_id
            == context.invocation_ref.entity_id
            and all(event.payload.get(key) == value
                    for key, value in expected.items())))
    try:
        effective = _effective_firing_resource_access_events(matches)
    except ResourceIntegrityFault:
        return False
    if len(effective) != 1:
        return False
    payload = effective[0].payload
    try:
        from ..firing_resource_access import (
            registered_firing_resource_access_from_payload,
        )
        formal_access = registered_firing_resource_access_from_payload(
            payload)
        loop_ref = _version_from_payload(payload["agent_loop_ref"])
        action_ref = _version_from_payload(payload["agent_action_ref"])
        loop = self._exact_object(
            loop_ref, expected_type="agent_loop/v1")
        action = self._exact_object(
            action_ref, expected_type="agent_action/v2")
        resource = self._firing_prepared(context, resource_ref)
        result = action.metadata.get("result_metadata")
    except (KeyError, TypeError, ValueError, ResourceServiceError):
        return False
    return bool(
        loop.metadata.get("invocation_ref")
        == _ref_payload(context.invocation_ref)
        and loop.metadata.get("operation_binding_ref")
        == _ref_payload(context.operation_binding_ref)
        and action.metadata.get("agent_loop_ref") == payload["agent_loop_ref"]
        and action.metadata.get("agent_turn_ref") == payload["agent_turn_ref"]
        and action.metadata.get("tool_name") == "request_resource"
        and action.metadata.get("state") in {
            "ACTION_APPLIED", "WAITING_RESOURCE"}
        and isinstance(result, Mapping)
        and result.get("resource_ref") == expected["resource_ref"]
        and result.get("resource_use_occurrence_ref")
        == payload.get("resource_use_occurrence_ref")
        and result.get("logical_resource_id")
        == str(resource_ref.resource_id)
        and result.get("lock_resource_ref")
        == _ref_payload(resource_ref.as_version_ref())
        and payload.get("logical_resource_id")
        == str(resource_ref.resource_id)
        and payload.get("lock_resource_ref")
        == _ref_payload(resource_ref.as_version_ref())
        and payload.get("resource_access_grant_ref") is not None
        and payload.get("resource_access_lease_ref") is not None
        and payload.get("access_mode") in {"read", "edit"}
        and formal_access.access.firing_ref == firing_ref
        and formal_access.access.resource_ref == resource_ref
        and payload.get("petri_arc_kind")
        == formal_access.access.input_arc_mode
        and payload.get("return_arc_required")
        is formal_access.access.return_arc_required
        and resource.metadata.get("task_ref")
        == _ref_payload(context.task_ref)
        and payload.get("writer_fencing_epoch")
        == self._ResourceServiceKernel__core.writer_epoch)

def _binding_allows(self, context: InvocationContext, authorization_ref: VersionRef,
                    resource_ref: ResourceVersionRef, *, metadata_only: bool) -> bool:
    if authorization_ref != context.operation_binding_ref:
        return False
    binding = self._exact_object(
        context.operation_binding_ref, expected_type="operation_binding/v1")
    binding_metadata = _effective_operation_binding_metadata(
        self._ResourceServiceKernel__core, context.operation_binding_ref, binding.metadata)
    spec = self._exact_object(
        _version_from_payload(binding_metadata["operation_spec_ref"]),
        expected_type="operation_spec/v1")
    declared_read_contracts = binding_metadata["resource_read_contracts"]
    if declared_read_contracts != spec.metadata["implementation_contracts"].get(
            "resource_read_contracts", []):
        raise ResourceIntegrityFault("binding read contracts differ from exact registered spec")
    read_contracts = tuple(ResourceReadContract.from_dict(value) for value in declared_read_contracts)
    key = "discoverable_resource_refs" if metadata_only else "readable_resource_refs"
    values = binding_metadata.get(key, [])
    if self._ref_in(values, resource_ref.as_version_ref()):
        return True
    if self._claimed_petri_input_allows(
            context, authorization_ref, resource_ref):
        return True
    if self._live_firing_resource_extension_allows(
            context, authorization_ref, resource_ref):
        return True
    resource = (
        self._firing_prepared(context, resource_ref)
        if context.own_transition_firing_ref is not None
        else self._prepared(resource_ref))
    if metadata_only and any(contract.matches(
            resource.metadata, context, metadata_only=True) for contract in read_contracts):
        return True
    origin = resource.metadata.get("origin")
    workspace_raw = binding_metadata.get("workspace_binding_ref")
    if isinstance(workspace_raw, Mapping):
        workspace = self._exact_object(
            _version_from_payload(workspace_raw),
            expected_type="workspace_binding/v1")
        workspace_write_allowed = False
        if (isinstance(origin, Mapping)
                and origin.get("kind") == "workspace_write"
                and resource.metadata.get("task_ref")
                == _ref_payload(context.task_ref)):
            producer_binding_raw = origin.get("primary_ref")
            if isinstance(producer_binding_raw, Mapping):
                producer_binding = self._exact_object(
                    _version_from_payload(producer_binding_raw),
                    expected_type="operation_binding/v1")
                producer_workspace_raw = producer_binding.metadata.get(
                    "workspace_binding_ref")
                if isinstance(producer_workspace_raw, Mapping):
                    producer_workspace = self._exact_object(
                        _version_from_payload(producer_workspace_raw),
                        expected_type="workspace_binding/v1")
                    workspace_write_allowed = (
                        producer_workspace.metadata.get(
                            "workspace_lineage_ref")
                        == workspace.metadata.get(
                            "workspace_lineage_ref"))
        if workspace_write_allowed:
            producer_raw = resource.metadata.get("producer_ref")
            if producer_raw == _ref_payload(context.invocation_ref):
                return True
            if isinstance(producer_raw, Mapping):
                producer = self._exact_object(
                    _version_from_payload(producer_raw),
                    expected_type="invocation/v1")
                firing_raw = producer.metadata.get(
                    "own_transition_firing_ref")
                if isinstance(firing_raw, Mapping):
                    firing_ref = _version_from_payload(firing_raw)
                    if any(
                            event.event_type
                            == "transition_firing_settled/v1"
                            and event.aggregate_id
                            == str(firing_ref.entity_id)
                            for event in self._ResourceServiceKernel__core.event_store.list_events()):
                        return True
        manifest_raw = workspace.metadata.get(
            "workspace_seed_manifest_ref")
        seed_contract = spec.metadata["implementation_contracts"].get("workspace_seed_contract")
        if isinstance(manifest_raw, Mapping) and seed_contract is not None:
            manifest_ref = _resource_from_payload(manifest_raw)
            manifest = self._prepared(manifest_ref)
            try:
                manifest_value = json.loads(
                    self._read_registered(manifest_ref))
                members = manifest_value[seed_contract["members_field"]]
            except Exception as exc:
                raise ResourceIntegrityFault(
                    "workspace seed manifest is not readable Registry state"
                ) from exc
            if (any(manifest.metadata.get("descriptors", {}).get(key) != value
                    for key, value in seed_contract["descriptor_equals"].items())
                    or manifest_value.get("protocol")
                    != seed_contract["protocol"]
                    or not isinstance(members, list)):
                raise ResourceIntegrityFault(
                    "workspace seed manifest differs from its authority")
            if any(isinstance(item, Mapping)
                   and item.get(seed_contract["resource_ref_field"])
                   == _resource_payload(resource_ref)
                   for item in members):
                return True
    if not metadata_only and any(contract.matches(
            resource.metadata, context, metadata_only=False) for contract in read_contracts):
        return True
    if (resource.metadata.get("producer_ref")
            == _ref_payload(context.invocation_ref)
            and isinstance(origin, Mapping)
            and origin.get("kind") == "petri_output"
            and self._ref_in(
                binding_metadata.get("output_binding_refs", []),
                _version_from_payload(origin["primary_ref"]))):
        return True
    if metadata_only and bool(binding_metadata.get("task_header_query")):
        return True
    return False

def _authorize_resource(self, context: InvocationContext,
                        authorization_ref: VersionRef,
                        resource_ref: ResourceVersionRef, *, metadata_only: bool,
                        native_resume: bool = False) -> None:
    self._revalidate_invocation(context, boundary=(
        "resource-metadata-query" if metadata_only else "resource-delivery"),
        native_resume=native_resume)
    if self._binding_allows(
            context, authorization_ref, resource_ref, metadata_only=metadata_only):
        return
    if self._grant_allows(context, authorization_ref, resource_ref):
        return
    raise UnauthorizedResourceDelivery(
        "exact resource is outside the registered binding/capability scope")

def _origin_authority(
        self, context: InvocationContext,
        origin: PublicationOrigin, *, native_resume: bool = False,
) -> tuple[str, VersionRef, VersionRef | None, VersionRef]:
    self._revalidate_invocation(
        context, boundary="resource-publish", native_resume=native_resume)
    kind, primary, secondary = _origin_payload(origin)
    binding = self._exact_object(
        context.operation_binding_ref, expected_type="operation_binding/v1")
    binding_metadata = _effective_operation_binding_metadata(
        self._ResourceServiceKernel__core, context.operation_binding_ref, binding.metadata)
    allowed = set(binding_metadata.get("allowed_publication_origins", []))
    if kind not in allowed:
        raise UndeclaredPublicationOrigin(
            f"operation binding does not declare {kind!r} publication")
    if kind == "petri_output":
        if (context.origin != "petri_operation"
                or context.own_transition_firing_ref is None
                or context.activation_ref != secondary):
            raise WrongNetOutputBinding("Petri output activation does not match invocation")
        output = self._exact_object(primary, expected_type="output_binding/v1")
        if not self._ref_in(
                binding_metadata.get("output_binding_refs", []), primary):
            raise WrongNetOutputBinding("output binding is not declared by this operation")
        expected = {
            "task_round_ref": _ref_payload(context.task_round_ref),
            "net_ref": _ref_payload(context.net_instance_ref),
            "node_ref": _ref_payload(context.own_node_ref) if context.own_node_ref else None,
        }
        if any(output.metadata.get(key) != value for key, value in expected.items()):
            raise WrongNetOutputBinding("output binding belongs to another round/net/node")
    elif kind == "workspace_write":
        if primary != context.operation_binding_ref:
            raise UndeclaredPublicationOrigin(
                "workspace write uses another operation binding")
        intent = self._exact_object(
            secondary, expected_type="workspace_write_intent/v1")
        if intent.metadata.get("operation_binding_ref") != _ref_payload(primary):
            raise UndeclaredPublicationOrigin("write intent belongs to another operation")
        factories = binding.metadata.get("permitted_write_intent_factory_refs", [])
        source = intent.metadata.get("source_binding_ref")
        if not any(source == item for item in factories):
            raise UndeclaredPublicationOrigin("write intent factory is not permitted")
    else:
        raise UndeclaredPublicationOrigin(
            "private-system publication is unavailable to application invocations")
    return kind, primary, secondary, context.invocation_ref
