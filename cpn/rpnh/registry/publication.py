"""Registry-owned exact resource references and transactional publication.

No executor/component policy or callable is loaded here. These helpers do not
commit an operation Success, adopt a graph or create terminal authority.
"""
from __future__ import annotations
import json
import uuid
from typing import Any,Mapping,Sequence
from .identities import TypedId
from .models import VersionRef,TypedRelation
from .resources import (
    ResourceVersionRef, PublicationOrigin, PetriOutputOrigin,
    WorkspaceWriteOrigin, PrivateSystemOrigin, CheckpointRepairOrigin,
    ProviderResponseOrigin, ProviderRawResponseOrigin,
    decode_provider_backend_config_document,
)
from .schema_catalog import canonical_json
from .errors import ResourceIntegrityFault, UndeclaredPublicationOrigin
from .invocations import InvocationContext


def _ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _current_process_configuration_refs_v1(
        core: Any,
) -> tuple[ResourceVersionRef, ResourceVersionRef, VersionRef, VersionRef]:
    """Resolve the exact I/M/recovery/run refs selected for this writer entry."""

    raw = core.event_store.get_meta(
        f"execution_process_configuration:writer-{core.writer_epoch}")
    if raw is None:
        raise ResourceIntegrityFault(
            "current process entry lacks exact configuration authority")
    try:
        value = json.loads(raw)
        immutable = _resource_from_payload(value["immutable_genesis_ref"])
        mutable = _resource_from_payload(value["mutable_stage_ref"])
        recovery = _version_from_payload(value["recovery_manifest_ref"])
        run_authority = _version_from_payload(
            value["run_execution_authority_ref"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResourceIntegrityFault(
            "current process configuration pointer is malformed") from exc
    return immutable, mutable, recovery, run_authority


def _resource_from_payload(value: Mapping[str, Any]) -> ResourceVersionRef:
    return ResourceVersionRef(
        TypedId.parse(str(value["resource_id"]), expected="resource"),
        TypedId.parse(
            str(value["resource_version_id"]), expected="resource_version"),
    )


def _version_from_payload(value: Mapping[str, Any]) -> VersionRef:
    return VersionRef(
        str(value["entity_type"]),
        TypedId.parse(str(value["logical_id"])),
        TypedId.parse(str(value["version_id"])),
    )


def _stable_id(kind: str, *parts: object) -> TypedId:
    material = ":".join(str(part) for part in parts)
    return TypedId(kind, uuid.uuid5(uuid.NAMESPACE_URL, f"d1-c:{kind}:{material}").hex)  # type: ignore[arg-type]


def _registry_type_catalog_ref(core: Any) -> VersionRef:
    """Reconstruct the exact current Registry type-catalog authority."""
    logical = core.event_store.get_meta("type_catalog_logical_id")
    version = core.event_store.get_meta("type_catalog_version_id")
    if logical is None or version is None:
        raise ResourceIntegrityFault(
            "Registry lacks exact schema catalog authority")
    return VersionRef(
        "registry_type_catalog/v1",
        TypedId.parse(logical, expected="schema"),
        TypedId.parse(version, expected="resource_version"),
    )


def _direct_resource_metadata(
        core: Any, *, ref: ResourceVersionRef,
        origin_kind: str, primary: VersionRef, secondary: VersionRef,
        task_ref: VersionRef, round_ref: VersionRef, net_ref: VersionRef,
        producer_ref: VersionRef, lifetime_ref: VersionRef,
        operation_binding_ref: VersionRef, agent_loop_ref: VersionRef,
        payload_size: int, media_type: str,
        content_schema_ref: str | None,
        content_schema_authority_ref: Mapping[str, Any] | None,
        summary: str, descriptors: Mapping[str, Any],
        extensions: Mapping[str, Any],
        input_resource_refs: Sequence[ResourceVersionRef],
        intended_boundary: str) -> dict[str, Any]:
    """Build fresh provider provenance from exact refs, without a proof tree."""
    publication = {
        "origin_kind": origin_kind,
        "primary_ref": _ref_payload(primary),
        "secondary_ref": _ref_payload(secondary),
        "lifetime_ref": _ref_payload(lifetime_ref),
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
        "origin_kind": origin_kind,
        "origin": {
            "kind": origin_kind,
            "primary_ref": _ref_payload(primary),
            "secondary_ref": _ref_payload(secondary),
        },
        "task_ref": _ref_payload(task_ref),
        "branch_id": core.branch_id,
        "round_ref": _ref_payload(round_ref),
        "net_ref": _ref_payload(net_ref),
        "producer_ref": _ref_payload(producer_ref),
        "lifetime_ref": _ref_payload(lifetime_ref),
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
            "operation_binding_ref": _ref_payload(operation_binding_ref),
            "agent_loop_ref": _ref_payload(agent_loop_ref),
            "input_resource_refs": [
                _ref_payload(value.as_version_ref())
                for value in input_resource_refs],
            "intended_consumer": {
                "boundary": intended_boundary,
                "consumer_ref": _ref_payload(agent_loop_ref),
            },
            "publication": publication,
        },
    }


def _provider_request_resource_metadata(
        core: Any, *, ref: ResourceVersionRef,
        context: Any, consumer_authority_ref: VersionRef,
        payload_size: int, media_type: str, content_schema_ref: str,
        summary: str, descriptors: Mapping[str, Any],
        extensions: Mapping[str, Any],
        input_resource_refs: Sequence[ResourceVersionRef]) -> dict[str, Any]:
    """Build the canonical invocation-produced provider-request metadata."""
    ordered_inputs = tuple(sorted(
        dict.fromkeys(input_resource_refs),
        key=lambda value: canonical_json(
            _ref_payload(value.as_version_ref()))))
    return _direct_resource_metadata(
        core, ref=ref, origin_kind="provider_request",
        primary=context.operation_binding_ref,
        secondary=consumer_authority_ref,
        task_ref=context.task_ref, round_ref=context.task_round_ref,
        net_ref=context.net_instance_ref,
        producer_ref=context.invocation_ref,
        lifetime_ref=context.operation_execution_lease_ref,
        operation_binding_ref=context.operation_binding_ref,
        agent_loop_ref=consumer_authority_ref,
        payload_size=payload_size, media_type=media_type,
        content_schema_ref=content_schema_ref,
        content_schema_authority_ref=_ref_payload(
            _registry_type_catalog_ref(core)),
        summary=summary, descriptors=descriptors,
        extensions=extensions, input_resource_refs=ordered_inputs,
        intended_boundary="llm_prompt")


def _append_direct_resource_version_publication(
        tx: Any, *, ref: ResourceVersionRef, payload: bytes,
        metadata_factory: Any, media_type: str,
        producer_ref: VersionRef, producer_invocation_id: TypedId | None,
        relation_key: str, direct_owners: Sequence[VersionRef] = (),
        input_resources: Sequence[ResourceVersionRef] = (),
        tool_evidence_refs: Sequence[VersionRef] = (),
        contributors: Sequence[VersionRef] = (),
        supersedes: ResourceVersionRef | None = None) -> Any:
    """Append one fresh provider file and only its direct strong relations."""
    system_owned = (
        producer_invocation_id is None
        and producer_ref.entity_type in {
            "bootstrap_command/v1", "checkpoint_repair/v1"})
    if producer_invocation_id is None and not system_owned:
        raise TypeError(
            "direct resource relations require an invocation or system owner")
    prepared = tx.prewrite_with_metadata_factory(
        object_type="resource_version/v1", logical_id=ref.resource_id,
        version_id=ref.resource_version_id, payload=payload,
        metadata_factory=metadata_factory, media_type=media_type,
        schema_ref="registry_v1/resource_version/v1",
        producer_invocation_id=producer_invocation_id)
    tx.relate(TypedRelation(
        _stable_id(
            "relation", relation_key, ref.resource_version_id, "produced_by"),
        "produced_by", ref.as_version_ref(), producer_ref),
        producer_invocation_id=producer_invocation_id,
        system_owned=system_owned)
    for index, owner in enumerate(direct_owners):
        tx.relate(TypedRelation(
            _stable_id(
                "relation", relation_key, ref.resource_version_id,
                "derived_from_owner", index),
            "derived_from", ref.as_version_ref(), owner),
            producer_invocation_id=producer_invocation_id,
            system_owned=system_owned)
    for index, resource in enumerate(input_resources):
        tx.relate(TypedRelation(
            _stable_id(
                "relation", relation_key, ref.resource_version_id,
                "derived_from_resource", index),
            "derived_from", ref.as_version_ref(), resource.as_version_ref()),
            producer_invocation_id=producer_invocation_id,
            system_owned=system_owned)
    for index, action_ref in enumerate(tool_evidence_refs):
        tx.relate(TypedRelation(
            _stable_id(
                "relation", relation_key, ref.resource_version_id,
                "caused_by_tool_result", index),
            "caused_by_tool_result", ref.as_version_ref(), action_ref),
            producer_invocation_id=producer_invocation_id,
            system_owned=system_owned)
    for index, contributor in enumerate(contributors):
        tx.relate(TypedRelation(
            _stable_id(
                "relation", relation_key, ref.resource_version_id,
                "contributed_by", index),
            "contributed_by", ref.as_version_ref(), contributor),
            producer_invocation_id=producer_invocation_id,
            system_owned=system_owned)
    if supersedes is not None:
        tx.relate(TypedRelation(
            _stable_id(
                "relation", relation_key, ref.resource_version_id,
                "supersedes"),
            "supersedes", ref.as_version_ref(), supersedes.as_version_ref()),
            producer_invocation_id=producer_invocation_id,
            system_owned=system_owned)
    return prepared


_NO_CONTENT_SCHEMA_INSTANCE = object()

def _content_schema_instance(
        payload: bytes, *, media_type: str,
        decode_provider_backend: bool = False) -> Any:
    """Derive a Draft7 instance without changing the registered bytes."""
    if media_type == "application/json":
        return (
            decode_provider_backend_config_document(payload)
            if decode_provider_backend else json.loads(payload)
        )
    if media_type.startswith("text/"):
        return payload.decode("utf-8")
    return _NO_CONTENT_SCHEMA_INSTANCE


def _effective_operation_binding_metadata(
        core: Any, ref: VersionRef,
        registered: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the exact current registered binding metadata."""

    del core, ref
    return dict(registered)


def _content_schema_source_from_payload(
        value: Mapping[str, Any] | None,
) -> VersionRef | ResourceVersionRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("content schema authority ref is malformed")
    if "resource_id" in value:
        return _resource_from_payload(value)
    source = _version_from_payload(value)
    if source.entity_type != "registry_type_catalog/v1":
        raise TypeError("content schema catalog source has the wrong type")
    return source


def _fresh_reference_resource_ref(
        *, task_id: TypedId, task_round_ref: VersionRef,
        net_instance_ref: VersionRef, kind: str, primary: VersionRef,
        secondary: VersionRef | None, idempotency_key: str,
        invocation_ref: VersionRef, operation_binding_ref: VersionRef,
        resource_lineage_suffix: tuple[object, ...] = (),
) -> ResourceVersionRef:
    """Allocate the canonical identity for one fresh resource reference."""
    resource_id = _stable_id(
        "resource", task_id, task_round_ref.entity_id,
        net_instance_ref.entity_id, kind, primary.version_id,
        (secondary.version_id if secondary is not None else invocation_ref.version_id),
        *resource_lineage_suffix)
    version_id = _stable_id(
        "resource_version", task_id, idempotency_key,
        invocation_ref.version_id, operation_binding_ref.version_id, kind,
        primary.version_id,
        (secondary.version_id if secondary is not None else invocation_ref.version_id))
    return ResourceVersionRef(resource_id, version_id)


def _effective_firing_resource_access_events(
        events: Sequence[Any],
) -> tuple[Any, ...]:
    """Collapse the sole legal same-firing read-to-edit upgrade sequence."""

    grouped: dict[ResourceVersionRef, list[Any]] = {}
    for event in events:
        try:
            resource_ref = _resource_from_payload(
                event.payload["resource_ref"])
            ordinal = int(event.ordinal)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "agent resource extension event is malformed") from exc
        if isinstance(event.ordinal, bool) or ordinal < 0:
            raise ResourceIntegrityFault(
                "agent resource extension event ordinal is invalid")
        grouped.setdefault(resource_ref, []).append(event)
    effective: list[Any] = []
    for resource_ref in sorted(
            grouped,
            key=lambda value: (
                str(value.resource_id), str(value.resource_version_id))):
        ordered = tuple(sorted(
            grouped[resource_ref], key=lambda item: item.ordinal))
        modes = tuple(event.payload.get("access_mode") for event in ordered)
        if len(ordered) == 1 and modes in {("read",), ("edit",)}:
            effective.append(ordered[0])
            continue
        if len(ordered) == 2 and modes == ("read", "edit"):
            effective.append(ordered[-1])
            continue
        raise ResourceIntegrityFault(
            "agent resource extension authority has an invalid "
            "same-firing sequence")
    return tuple(effective)


def _origin_payload(origin: PublicationOrigin) -> tuple[str, VersionRef, VersionRef | None]:
    if isinstance(origin, PetriOutputOrigin):
        return "petri_output", origin.output_binding_ref, origin.activation_ref
    if isinstance(origin, WorkspaceWriteOrigin):
        return "workspace_write", origin.operation_binding_ref, origin.write_intent_ref
    if isinstance(origin, PrivateSystemOrigin):
        return "private_system", origin.bootstrap_command_ref, origin.bootstrap_command_ref
    if isinstance(origin, CheckpointRepairOrigin):
        return (
            "checkpoint_repair", origin.checkpoint_repair_ref,
            origin.source_resource_ref.as_version_ref())
    if isinstance(origin, ProviderResponseOrigin):
        return (
            "provider_response", origin.provider_attempt_ref, origin.llm_call_ref)
    if isinstance(origin, ProviderRawResponseOrigin):
        return (
            "provider_raw_response", origin.provider_attempt_ref,
            origin.llm_call_ref)
    raise UndeclaredPublicationOrigin("publication origin is not a registered typed variant")


def _fresh_reference_resource_metadata(
        core: Any, *, ref: ResourceVersionRef,
        origin_kind: str, primary: VersionRef, secondary: VersionRef | None,
        context: InvocationContext, producer_ref: VersionRef,
        lifetime_ref: VersionRef, payload_size: int,
        media_type: str, content_schema_ref: str | None,
        content_schema_authority_ref: Mapping[str, Any] | None,
        summary: str, descriptors: Mapping[str, Any],
        extensions: Mapping[str, Any],
        derived_from: Sequence[ResourceVersionRef],
        tool_evidence_refs: Sequence[VersionRef] = (),
        contributors: Sequence[VersionRef] = (),
        supersedes: ResourceVersionRef | None = None) -> dict[str, Any]:
    """Build fresh normal provenance from exact refs and no proof material."""

    ordered_inputs = tuple(sorted(
        dict.fromkeys(derived_from),
        key=lambda value: canonical_json(
            _ref_payload(value.as_version_ref()))))
    ordered_contributors = tuple(sorted(
        dict.fromkeys(contributors),
        key=lambda value: canonical_json(_ref_payload(value))))
    ordered_tool_evidence = tuple(sorted(
        dict.fromkeys(tool_evidence_refs),
        key=lambda value: canonical_json(_ref_payload(value))))
    expected_boundary = {
        "petri_output": "petri_input",
        "workspace_write": "tool_result",
    }.get(origin_kind, "not_applicable")
    expected_consumer = (
        primary if origin_kind == "petri_output" else
        secondary if origin_kind == "workspace_write" else None)
    publication = {
        "origin_kind": origin_kind,
        "primary_ref": _ref_payload(primary),
        "secondary_ref": _ref_payload(secondary) if secondary is not None else None,
        "lifetime_ref": _ref_payload(lifetime_ref),
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
        "origin_kind": origin_kind,
        "origin": {
            "kind": origin_kind,
            "primary_ref": _ref_payload(primary),
            "secondary_ref": _ref_payload(secondary) if secondary is not None else None,
        },
        "task_ref": _ref_payload(context.task_ref),
        "branch_id": core.branch_id,
        "round_ref": _ref_payload(context.task_round_ref),
        "net_ref": _ref_payload(context.net_instance_ref),
        "producer_ref": _ref_payload(producer_ref),
        "lifetime_ref": _ref_payload(lifetime_ref),
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
            "producer_invocation_ref": _ref_payload(context.invocation_ref),
            "operation_binding_ref": _ref_payload(
                context.operation_binding_ref),
            "derived_from_refs": [
                _ref_payload(value.as_version_ref()) for value in ordered_inputs],
            "tool_evidence_refs": [
                _ref_payload(value) for value in ordered_tool_evidence],
            "contributor_refs": [
                _ref_payload(value) for value in ordered_contributors],
            "supersedes_ref": (
                _ref_payload(supersedes.as_version_ref())
                if supersedes is not None else None),
            "intended_consumer": {
                "boundary": expected_boundary,
                "consumer_ref": (
                    _ref_payload(expected_consumer)
                    if expected_consumer is not None else None),
            },
            "publication": publication,
        },
    }


def _fresh_bootstrap_reference_resource_metadata(
        core: Any, *, ref: ResourceVersionRef,
        task_ref: VersionRef, bootstrap_ref: VersionRef,
        lifetime_ref: VersionRef, payload_size: int,
        media_type: str, content_schema_ref: str | None,
        content_schema_authority_ref: Mapping[str, Any] | None,
        summary: str, descriptors: Mapping[str, Any],
        extensions: Mapping[str, Any],
        derived_from: Sequence[ResourceVersionRef],
        supersedes: ResourceVersionRef | None = None) -> dict[str, Any]:
    """Build fresh bootstrap provenance with an explicit absent invocation owner."""

    ordered_inputs = tuple(sorted(
        dict.fromkeys(derived_from),
        key=lambda value: canonical_json(
            _ref_payload(value.as_version_ref()))))
    publication = {
        "origin_kind": "private_system",
        "primary_ref": _ref_payload(bootstrap_ref),
        "secondary_ref": _ref_payload(bootstrap_ref),
        "lifetime_ref": _ref_payload(lifetime_ref),
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
        "origin_kind": "private_system",
        "origin": {
            "kind": "private_system",
            "primary_ref": _ref_payload(bootstrap_ref),
            "secondary_ref": _ref_payload(bootstrap_ref),
        },
        "task_ref": _ref_payload(task_ref),
        "branch_id": core.branch_id,
        "round_ref": None,
        "net_ref": None,
        "producer_ref": _ref_payload(bootstrap_ref),
        "lifetime_ref": _ref_payload(lifetime_ref),
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
            "supersedes_ref": (
                _ref_payload(supersedes.as_version_ref())
                if supersedes is not None else None),
            "intended_consumer": {
                "boundary": "not_applicable",
                "consumer_ref": None,
            },
            "publication": publication,
        },
    }
