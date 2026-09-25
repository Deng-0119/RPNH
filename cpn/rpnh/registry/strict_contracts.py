"""Strict producers for current user and operation authority objects.

These functions are the construction boundary for the new Registry objects.
Callers provide exact, already-registered version references; no caller may
substitute names, free-form error payloads, or process-local authority.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from .identities import TypedId
from .models import TypedRelation, VersionRef
from .operations import (
    FaultPetriPlaceAuthority,
    FaultPetriTransitionAuthority,
    FaultSubnetAuthority,
)
from .resources import ResourceVersionRef
from .schema_catalog import canonical_json


_HistoricalFaultClass = Literal[
    "provider", "tool", "process", "petri_input",
    "publication", "delivery", "cancellation",
]
_HistoricalFaultMechanicalRole = Literal["reconcile", "retry", "exhaust"]
_HistoricalFaultCause = Literal[
    "not_submitted", "submitted_outcome_unknown", "execution_failed",
    "schema_mismatch", "digest_mismatch", "stale_authority", "cancelled",
    "capacity_unavailable",
]

_FAULT_CLASSES: tuple[_HistoricalFaultClass, ...] = (
    "provider", "tool", "process", "petri_input",
    "publication", "delivery", "cancellation",
)


class StrictContractError(RuntimeError):
    """A strict authority or fault object cannot be proven before commit."""


@dataclass(frozen=True, slots=True)
class _HistoricalFaultFoundation:
    subnet_template_ref: VersionRef
    subnet: FaultSubnetAuthority

    def __post_init__(self) -> None:
        if (not isinstance(self.subnet, FaultSubnetAuthority)
                or self.subnet.subnet_template_ref
                != self.subnet_template_ref):
            raise StrictContractError(
                "fault foundation lacks its registered Petri subnet")


def ref_payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def content_schema_ref_payload(
        ref: VersionRef | ResourceVersionRef) -> dict[str, str]:
    if isinstance(ref, VersionRef):
        return ref_payload(ref)
    if isinstance(ref, ResourceVersionRef):
        return {
            "resource_id": str(ref.resource_id),
            "resource_version_id": str(ref.resource_version_id),
        }
    raise StrictContractError("content schema source is not an exact ref")


def _ref_from_payload(value: Mapping[str, Any]) -> VersionRef:
    return VersionRef(
        str(value["entity_type"]), TypedId.parse(str(value["logical_id"])),
        TypedId.parse(str(value["version_id"])))


def _stable_id(kind: str, key: str) -> TypedId:
    return TypedId(
        kind, uuid.uuid5(uuid.NAMESPACE_URL, f"d1-c:{kind}:{key}").hex)  # type: ignore[arg-type]


def _registered(core: Any, ref: VersionRef, expected_type: str | None = None,
                ) -> tuple[Any, dict[str, Any]]:
    if not isinstance(ref, VersionRef):
        raise StrictContractError("authority requires an exact VersionRef")
    if expected_type is not None and ref.entity_type != expected_type:
        raise StrictContractError(
            f"expected {expected_type}, received {ref.entity_type}")
    row = core.event_store.object_row(ref.version_id)
    if (row is None or row["object_type"] != ref.entity_type
            or row["logical_id"] != str(ref.entity_id)):
        raise StrictContractError(
            f"exact reference is not registered: {ref.version_id}")
    try:
        metadata = json.loads(row["metadata_json"])
    except Exception as exc:
        raise StrictContractError(
            f"registered reference is malformed: {ref.version_id}") from exc
    return row, metadata


def _unique_sorted_refs(core: Any, refs: Sequence[VersionRef], *,
                        require_nonempty: bool = False,
                        ) -> tuple[VersionRef, ...]:
    values = tuple(refs)
    if require_nonempty and not values:
        raise StrictContractError("at least one exact reference is required")
    for ref in values:
        _registered(core, ref)
    if len(set(values)) != len(values):
        raise StrictContractError("exact reference list contains duplicates")
    return tuple(sorted(values, key=lambda ref: canonical_json(ref_payload(ref))))


def _publish_json(core: Any, object_type: str, ref: VersionRef,
                  metadata: Mapping[str, Any], *, idempotency_key: str,
                  producer_invocation_id: TypedId | None = None,
                  derived_from: Sequence[VersionRef] = (),
                  transaction: Any | None = None) -> VersionRef:
    core.catalog.validate_instance(
        object_type, category="object", instance=metadata)
    relations = tuple(
        TypedRelation(
            _stable_id(
                "relation",
                f"{idempotency_key}:derived:{index}:{target.version_id}"),
            "derived_from", ref, target,
            metadata={"strict_contract": object_type},
            producer_invocation_id=producer_invocation_id,
            system_owned=producer_invocation_id is None)
        for index, target in enumerate(derived_from)
    )
    if transaction is None:
        return core.publish_bytes(
            object_type=object_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(metadata),
            metadata=metadata, media_type="application/json",
            schema_ref=f"registry_v1/{object_type}",
            idempotency_key=idempotency_key,
            producer_invocation_id=producer_invocation_id,
            relations=relations)
    transaction.prewrite(
        object_type=object_type, logical_id=ref.entity_id,
        version_id=ref.version_id, payload=canonical_json(metadata),
        metadata=metadata, media_type="application/json",
        schema_ref=f"registry_v1/{object_type}",
        producer_invocation_id=producer_invocation_id)
    for relation in relations:
        transaction.relate(
            relation, producer_invocation_id=producer_invocation_id)
    return ref


def publish_operation_spec(
        core: Any, *, operation_id: str, executor_key: str,
        input_ports: Sequence[Any], output_ports: Sequence[Any],
        llm_prompt_port_id: str | None, allowed_tool_ids: Sequence[str],
        idempotency_key: str,
        producer_invocation_id: TypedId | None = None,
        transaction: Any | None = None) -> VersionRef:
    """Publish the execution owner's HOST-registered operation contract.

    Registry stores explicit implementation identity and contracts, never the
    callable. JSON callers select a registered key, not executable locators.
    """
    from .operations import (
        OperationPortAuthority,
        OperationSpecAuthority,
        registered_operation_contract,
        registered_operation_transport,
    )

    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise StrictContractError("operation spec idempotency key is empty")
    inputs = tuple(input_ports)
    outputs = tuple(output_ports)
    if (any(not isinstance(port, OperationPortAuthority) for port in inputs)
            or any(not isinstance(port, OperationPortAuthority)
                   for port in outputs)):
        raise StrictContractError("operation spec ports must be typed authorities")
    tools = tuple(sorted(allowed_tool_ids))
    if (any(not isinstance(value, str) for value in tools)
            or len(set(tools)) != len(tools)):
        raise StrictContractError("operation tool ids must be unique strings")
    for port in (*inputs, *outputs):
        _registered(core, port.schema_ref)
        verifier = getattr(core, "verify_registered_content_schema_ref", None)
        if not callable(verifier):
            raise StrictContractError(
                "Registry content schema verifier is unavailable")
        verified_schema = verifier(
            port.content_schema_ref,
            schema_document_ref=port.schema_ref)
        if verified_schema != port.content_schema:
            raise StrictContractError(
                "operation port content schema differs from exact Registry authority")
        if port.lease_identity_ref is not None:
            _registered(core, port.lease_identity_ref)
    spec_ref = VersionRef(
        "operation_spec/v1",
        _stable_id("operation_spec", idempotency_key),
        _stable_id("operation_spec_version", idempotency_key),
    )

    def port_payload(port: Any, *, input_port: bool) -> dict[str, Any]:
        payload = {
            "port_id": port.port_id,
            "place": port.place,
            "schema_ref": ref_payload(port.schema_ref),
            "content_schema_ref": content_schema_ref_payload(
                port.content_schema_ref),
            "cardinality": {
                "minimum": port.minimum,
                "maximum": port.maximum,
            },
        }
        if port.lease_identity_ref is not None and not input_port:
            raise StrictContractError(
                "operation output port cannot declare a lease identity")
        payload["lease_identity_ref"] = (
            ref_payload(port.lease_identity_ref)
            if port.lease_identity_ref is not None else None)
        if port.input_projections:
            if not input_port:
                raise StrictContractError(
                    "operation output port cannot publish input projections")
            payload["input_projections"] = [
                {
                    "producer_content_schema_ref": (
                        projection.producer_content_schema_ref),
                    "field_projection": [
                        {
                            "producer_path": field.producer_path,
                            "consumer_path": field.consumer_path,
                        }
                        for field in projection.field_projection
                    ],
                }
                for projection in port.input_projections
            ]
        return payload

    input_payloads = [port_payload(port, input_port=True) for port in inputs]
    output_payloads = [port_payload(port, input_port=False) for port in outputs]
    transport = registered_operation_transport(executor_key)
    declaration = registered_operation_contract(executor_key)
    metadata = {
        "operation_spec_id": str(spec_ref.entity_id),
        "operation_spec_version_id": str(spec_ref.version_id),
        "operation_spec_ref": ref_payload(spec_ref),
        "operation_id": operation_id,
        "executor_key": executor_key,
        "implementation_identity": declaration["identity"],
        "implementation_contracts": declaration["contracts"],
        "transport": transport,
        "llm_prompt_port_id": llm_prompt_port_id,
        "input_ports": input_payloads,
        "output_ports": output_payloads,
        "allowed_tool_ids": list(tools),
    }
    OperationSpecAuthority(
        operation_spec_ref=spec_ref,
        operation_id=operation_id,
        executor_key=executor_key,
        transport=transport,
        llm_prompt_port_id=llm_prompt_port_id,
        input_ports=inputs,
        output_ports=outputs,
        allowed_tool_ids=tools,
    )
    return _publish_json(
        core, "operation_spec/v1", spec_ref, metadata,
        idempotency_key=idempotency_key,
        producer_invocation_id=producer_invocation_id,
        transaction=transaction,
        derived_from=(
            *(port.schema_ref for port in inputs),
            *(port.schema_ref for port in outputs),
            *(port.content_schema_ref.as_version_ref()
              if isinstance(port.content_schema_ref, ResourceVersionRef)
              else port.content_schema_ref
              for port in (*inputs, *outputs)),
            *(port.lease_identity_ref
              for port in inputs
              if port.lease_identity_ref is not None),
        ),
    )


def publish_user_authority_decision(
        core: Any, *, authority_kind: Literal["architecture", "scope", "experiment"],
        canonical_statement: str, user_principal_ref: VersionRef,
        governed_artifact_refs: Sequence[VersionRef],
        selected_choices: Mapping[str, str], effective_sequence: int,
        supersedes_ref: VersionRef | None, idempotency_key: str) -> VersionRef:
    """Publish one effective, immutable user decision as exact authority."""
    if not canonical_statement:
        raise StrictContractError("canonical authority statement is empty")
    if effective_sequence < 1 or not selected_choices:
        raise StrictContractError("effective authority sequence/choices are absent")
    _registered(core, user_principal_ref, "principal/v1")
    governed = _unique_sorted_refs(
        core, governed_artifact_refs, require_nonempty=True)
    if supersedes_ref is not None:
        _registered(core, supersedes_ref, "user_authority_decision/v1")
        _row, prior = _registered(core, supersedes_ref)
        if (prior.get("status") != "effective"
                or int(prior.get("effective_sequence", 0)) >= effective_sequence):
            raise StrictContractError(
                "superseded authority is not an earlier effective decision")
    choices = [
        {"choice_id": key, "selected_option": selected_choices[key]}
        for key in sorted(selected_choices)
    ]
    if any(not key or not value for key, value in selected_choices.items()):
        raise StrictContractError("authority choice names/options must be nonempty")
    decision_ref = VersionRef(
        "user_authority_decision/v1",
        _stable_id("user_authority_decision", idempotency_key),
        _stable_id("user_authority_decision_version", idempotency_key))
    metadata = {
        "decision_id": str(decision_ref.entity_id),
        "decision_version_id": str(decision_ref.version_id),
        "decision_ref": ref_payload(decision_ref),
        "authority_kind": authority_kind,
        "canonical_statement": canonical_statement,
        "user_principal_ref": ref_payload(user_principal_ref),
        "governed_artifact_refs": [ref_payload(ref) for ref in governed],
        "selected_choices": choices,
        "effective_sequence": effective_sequence,
        "supersedes_ref": (
            ref_payload(supersedes_ref) if supersedes_ref else None),
        "status": "effective",
    }
    return _publish_json(
        core, "user_authority_decision/v1", decision_ref, metadata,
        idempotency_key=idempotency_key,
        derived_from=(
            user_principal_ref, *governed,
            *((supersedes_ref,) if supersedes_ref else ())))


def _historical_publish_fault_foundation(
        core: Any, *, authority_decision_ref: VersionRef,
        repair_authority_ref: VersionRef,
        idempotency_key: str) -> _HistoricalFaultFoundation:
    """Retained unreachable historical fault-sidecar template producer."""
    _row, authority = _registered(
        core, authority_decision_ref, "user_authority_decision/v1")
    if authority.get("status") != "effective":
        raise StrictContractError("fault foundation authority is not effective")
    _registered(core, repair_authority_ref)
    template_ref = VersionRef(
        "fault_subnet_template/v1",
        _stable_id("fault_subnet_template", idempotency_key),
        _stable_id("fault_subnet_template_version", idempotency_key))
    place_roles = (
        "capacity_return", "fault_pending", "original_work_hold",
        "reconciliation_pending", "structural_terminal_intake",
    )
    transition_roles = ("exhaust", "reconcile", "retry")
    place_refs = {
        role: VersionRef(
            "fault_petri_place/v1",
            _stable_id("fault_petri_place", f"{idempotency_key}:{role}"),
            _stable_id(
                "fault_petri_place_version", f"{idempotency_key}:{role}"),
        )
        for role in place_roles
    }
    transition_refs = {
        role: VersionRef(
            "fault_petri_transition/v1",
            _stable_id(
                "fault_petri_transition", f"{idempotency_key}:{role}"),
            _stable_id(
                "fault_petri_transition_version",
                f"{idempotency_key}:{role}"),
        )
        for role in transition_roles
    }
    transition_topology = {
        "retry": {
            "input_place_refs": (
                place_refs["fault_pending"],
                place_refs["original_work_hold"],
            ),
            "output_place_refs": (place_refs["original_work_hold"],),
            "accepted_verdicts": ("retry",),
        },
        "exhaust": {
            "input_place_refs": (place_refs["fault_pending"],),
            "output_place_refs": (
                place_refs["structural_terminal_intake"],),
            "accepted_verdicts": ("exhaust",),
        },
        "reconcile": {
            "input_place_refs": (place_refs["fault_pending"],),
            "output_place_refs": (place_refs["reconciliation_pending"],),
            "accepted_verdicts": ("reconcile",),
        },
    }
    topology = {
        "topology_kind": "flat_operation_local_option_a",
        "place_roles": list(place_roles),
        "transition_roles": list(transition_roles),
        "place_refs": [ref_payload(place_refs[role]) for role in place_roles],
        "transition_refs": [
            ref_payload(transition_refs[role]) for role in transition_roles],
        "arcs": [
            {
                "transition_role": role,
                "input_place_refs": [
                    ref_payload(ref)
                    for ref in transition_topology[role]["input_place_refs"]],
                "output_place_refs": [
                    ref_payload(ref)
                    for ref in transition_topology[role]["output_place_refs"]],
                "accepted_verdicts": list(
                    transition_topology[role]["accepted_verdicts"]),
            }
            for role in transition_roles
        ],
        "guard_contract": "content_blind_retry_exhaust_reconcile_partition",
        "counter_scope": "per_root_fault_chain",
    }
    template = {
        "template_id": str(template_ref.entity_id),
        "template_version_id": str(template_ref.version_id),
        "template_ref": ref_payload(template_ref),
        "authority_decision_ref": ref_payload(authority_decision_ref),
        **topology,
    }
    place_metadata = {
        role: {
            "place_id": str(place_refs[role].entity_id),
            "place_version_id": str(place_refs[role].version_id),
            "place_ref": ref_payload(place_refs[role]),
            "subnet_template_ref": ref_payload(template_ref),
            "role": role,
            "place": role,
        }
        for role in place_roles
    }
    transition_metadata = {
        role: {
            "transition_id": str(transition_refs[role].entity_id),
            "transition_version_id": str(transition_refs[role].version_id),
            "transition_ref": ref_payload(transition_refs[role]),
            "subnet_template_ref": ref_payload(template_ref),
            "role": role,
            "input_place_refs": [
                ref_payload(ref)
                for ref in transition_topology[role]["input_place_refs"]],
            "output_place_refs": [
                ref_payload(ref)
                for ref in transition_topology[role]["output_place_refs"]],
            "accepted_verdicts": list(
                transition_topology[role]["accepted_verdicts"]),
        }
        for role in transition_roles
    }
    subnet = FaultSubnetAuthority(
        subnet_template_ref=template_ref,
        places=tuple(FaultPetriPlaceAuthority(
            place_ref=place_refs[role], subnet_template_ref=template_ref,
            role=role, place=role,  # type: ignore[arg-type]
        ) for role in place_roles),
        transitions=tuple(FaultPetriTransitionAuthority(
            transition_ref=transition_refs[role],
            subnet_template_ref=template_ref,
            role=role,  # type: ignore[arg-type]
            input_place_refs=transition_topology[role]["input_place_refs"],
            output_place_refs=transition_topology[role]["output_place_refs"],
            accepted_verdicts=transition_topology[role]["accepted_verdicts"],
        ) for role in transition_roles),
    )
    core.catalog.validate_instance(
        "fault_subnet_template/v1", category="object", instance=template)
    for role in place_roles:
        core.catalog.validate_instance(
            "fault_petri_place/v1", category="object",
            instance=place_metadata[role])
    for role in transition_roles:
        core.catalog.validate_instance(
            "fault_petri_transition/v1", category="object",
            instance=transition_metadata[role])
    tx = core.begin(idempotency_key=idempotency_key)
    objects = (
        *(("fault_petri_place/v1", place_refs[role], place_metadata[role])
          for role in place_roles),
        *(("fault_petri_transition/v1", transition_refs[role],
           transition_metadata[role]) for role in transition_roles),
        ("fault_subnet_template/v1", template_ref, template),
    )
    for object_type, ref, metadata in objects:
        tx.prewrite(
            object_type=object_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(metadata),
            metadata=metadata, media_type="application/json",
            schema_ref=f"registry_v1/{object_type}")
    for index, target in enumerate((
            authority_decision_ref, repair_authority_ref)):
        tx.relate(TypedRelation(
            _stable_id("relation", f"{idempotency_key}:foundation:{index}"),
            "derived_from", template_ref, target,
            metadata={"strict_contract": "fault_subnet_template/v1"},
            system_owned=True))
    tx.commit()
    return _HistoricalFaultFoundation(template_ref, subnet)


def _historical_build_fault_route_binding(
        core: Any, *, operation_binding_ref: VersionRef,
        policy_ref: VersionRef, subnet_template_ref: VersionRef,
        fault_pending_place_ref: VersionRef, fault_pending_place: str,
        original_work_hold_place_ref: VersionRef,
        original_work_port_refs: Sequence[VersionRef],
        original_work_places: Sequence[str],
        capacity_return_port_refs: Sequence[VersionRef],
        capacity_return_places: Sequence[str],
        capacity_return_place_ref: VersionRef,
        reconcile_ref: VersionRef, reconcile_place_ref: VersionRef,
        reconcile_place: str,
        structural_terminal_intake_ref: VersionRef,
        retry_transition_ref: VersionRef, exhaust_transition_ref: VersionRef,
        identity_key: str,
        proposed_route_refs: Sequence[VersionRef] = (),
        ) -> tuple[VersionRef, dict[str, Any]]:
    """Build a route for atomic prewrite beside its operation binding.

    ``operation_binding_ref`` may be a proposed ref in the caller's same graph
    transaction; all other route endpoints must already be registered.
    """
    _registered(core, policy_ref, "fault_disposition_policy/v1")
    _registered(core, subnet_template_ref, "fault_subnet_template/v1")
    route_ref = VersionRef(
        "fault_route_binding/v1",
        _stable_id("fault_route_binding", identity_key),
        _stable_id("fault_route_binding_version", identity_key))
    try:
        work_pairs = tuple(zip(
            tuple(original_work_port_refs), tuple(original_work_places),
            strict=True))
        capacity_pairs = tuple(zip(
            tuple(capacity_return_port_refs), tuple(capacity_return_places),
            strict=True))
    except ValueError as exc:
        raise StrictContractError(
            "fault route port/place cardinalities differ") from exc
    if not work_pairs or not capacity_pairs:
        raise StrictContractError(
            "fault route requires work and capacity port/place pairs")
    if (any(not isinstance(place, str) or not place
            for _ref, place in (*work_pairs, *capacity_pairs))
            or not isinstance(fault_pending_place, str)
            or not fault_pending_place
            or not isinstance(reconcile_place, str)
            or not reconcile_place):
        raise StrictContractError("fault route place names must be nonempty strings")
    all_refs = tuple(ref for ref, _place in (*work_pairs, *capacity_pairs))
    all_places = tuple(place for _ref, place in (*work_pairs, *capacity_pairs))
    # A content schema is not a port identity.  Two distinct work subsets may
    # intentionally share one schema (for example team agent documents and the
    # Reviewer-A document).  The closed route identity is the exact
    # (schema-authority ref, Petri place) pair; places, and therefore pairs,
    # remain disjoint from capacity.
    if (len(set(work_pairs)) != len(work_pairs)
            or len(set(capacity_pairs)) != len(capacity_pairs)
            or len(set(all_places)) != len(all_places)):
        raise StrictContractError(
            "fault route work/capacity port and place partitions must be disjoint")
    proposed = set(proposed_route_refs)
    if (len(proposed) != len(tuple(proposed_route_refs))
            or any(not isinstance(ref, VersionRef) for ref in proposed)):
        raise StrictContractError(
            "proposed fault-route refs must be unique exact refs")
    for ref in all_refs:
        if ref not in proposed:
            _registered(core, ref)
    work_pairs = tuple(sorted(
        work_pairs, key=lambda item: (
            canonical_json(ref_payload(item[0])), item[1])))
    capacity_pairs = tuple(sorted(
        capacity_pairs, key=lambda item: canonical_json(ref_payload(item[0]))))
    work_ports = tuple(ref for ref, _place in work_pairs)
    work_places = tuple(place for _ref, place in work_pairs)
    capacity_ports = tuple(ref for ref, _place in capacity_pairs)
    capacity_places = tuple(place for _ref, place in capacity_pairs)
    for ref in (
            fault_pending_place_ref, original_work_hold_place_ref,
            capacity_return_place_ref, reconcile_ref, reconcile_place_ref,
            structural_terminal_intake_ref,
            retry_transition_ref, exhaust_transition_ref):
        _registered(core, ref)
    subnet_payload = ref_payload(subnet_template_ref)
    endpoint_roles = (
        (fault_pending_place_ref, "fault_petri_place/v1", "fault_pending"),
        (original_work_hold_place_ref, "fault_petri_place/v1",
         "original_work_hold"),
        (capacity_return_place_ref, "fault_petri_place/v1",
         "capacity_return"),
        (reconcile_place_ref, "fault_petri_place/v1",
         "reconciliation_pending"),
        (structural_terminal_intake_ref, "fault_petri_place/v1",
         "structural_terminal_intake"),
        (retry_transition_ref, "fault_petri_transition/v1", "retry"),
        (exhaust_transition_ref, "fault_petri_transition/v1", "exhaust"),
        (reconcile_ref, "fault_petri_transition/v1", "reconcile"),
    )
    for ref, expected_type, role in endpoint_roles:
        _row, endpoint = _registered(core, ref, expected_type)
        if (endpoint.get("subnet_template_ref") != subnet_payload
                or endpoint.get("role") != role):
            raise StrictContractError(
                "fault route endpoint belongs to another subnet role")
    _row, pending_endpoint = _registered(
        core, fault_pending_place_ref, "fault_petri_place/v1")
    _row, hold_endpoint = _registered(
        core, original_work_hold_place_ref, "fault_petri_place/v1")
    _row, reconcile_endpoint = _registered(
        core, reconcile_place_ref, "fault_petri_place/v1")
    _row, terminal_endpoint = _registered(
        core, structural_terminal_intake_ref, "fault_petri_place/v1")
    if (pending_endpoint.get("place") != fault_pending_place
            or reconcile_endpoint.get("place") != reconcile_place):
        raise StrictContractError(
            "fault route place name differs from registered subnet endpoint")
    hold_base = hold_endpoint.get("place")
    terminal_place = terminal_endpoint.get("place")
    if (not isinstance(hold_base, str) or not hold_base
            or not isinstance(terminal_place, str) or not terminal_place):
        raise StrictContractError(
            "fault route hold/terminal endpoint place is malformed")
    local_place_specs: tuple[tuple[str, str, str], ...] = (
        ("pending", "fault_pending", str(pending_endpoint["place"])),
        *(
            (f"held:{index}:{ref.version_id}", "original_work_hold", hold_base)
            for index, ref in enumerate(work_ports)
        ),
        ("reconcile", "reconciliation_pending",
         str(reconcile_endpoint["place"])),
        ("terminal", "structural_terminal_intake", terminal_place),
    )
    local_places: dict[str, tuple[VersionRef, str]] = {}
    for key, role, base_place in local_place_specs:
        local_ref = VersionRef(
            "fault_petri_place/v1",
            _stable_id("fault_petri_place", f"{identity_key}:{key}"),
            _stable_id(
                "fault_petri_place_version", f"{identity_key}:{key}"))
        local_name = (
            f"{base_place}::{route_ref.entity_id}::{key.replace(':', '_')}")
        local_metadata = {
            "place_id": str(local_ref.entity_id),
            "place_version_id": str(local_ref.version_id),
            "place_ref": ref_payload(local_ref),
            "subnet_template_ref": ref_payload(subnet_template_ref),
            "role": role,
            "place": local_name,
        }
        _publish_json(
            core, "fault_petri_place/v1", local_ref, local_metadata,
            idempotency_key=f"{identity_key}:route-place:{key}",
            derived_from=(subnet_template_ref,))
        local_places[key] = (local_ref, local_name)
    fault_pending_place_ref, fault_pending_place = local_places["pending"]
    held_place_records = tuple(
        local_places[f"held:{index}:{ref.version_id}"]
        for index, ref in enumerate(work_ports))
    held_work_place_refs = tuple(ref for ref, _place in held_place_records)
    held_places = tuple(place for _ref, place in held_place_records)
    reconcile_place_ref, reconcile_place = local_places["reconcile"]
    terminal_place_ref, terminal_place = local_places["terminal"]
    if (len(set(held_places)) != len(held_places)
            or set(held_places) & set(work_places)
            or set(held_places) & set(capacity_places)):
        raise StrictContractError(
            "fault route held work slots are not pairwise disjoint")
    common_inputs = (fault_pending_place, *held_places)
    mechanical_transitions = (
        {
            "role": "exhaust",
            "transition_ref": ref_payload(exhaust_transition_ref),
            "input_places": list(common_inputs),
            "output_places": [terminal_place],
            "accepted_verdicts": ["exhaust"],
            "emit": "forward",
        },
        {
            "role": "reconcile",
            "transition_ref": ref_payload(reconcile_ref),
            "input_places": list(common_inputs),
            "output_places": [reconcile_place],
            "accepted_verdicts": ["reconcile"],
            "emit": "forward",
        },
        {
            "role": "retry",
            "transition_ref": ref_payload(retry_transition_ref),
            "input_places": list(common_inputs),
            "output_places": list(work_places),
            "accepted_verdicts": ["retry"],
            "emit": "forward",
        },
    )
    route_material = {
        "schema_version": "fault_route_binding_dependency/v1",
        "operation_binding_ref": ref_payload(operation_binding_ref),
        "policy_ref": ref_payload(policy_ref),
        "subnet_template_ref": ref_payload(subnet_template_ref),
        "fault_pending_place_ref": ref_payload(fault_pending_place_ref),
        "fault_pending_place": fault_pending_place,
        "held_work_port_refs": [ref_payload(ref) for ref in work_ports],
        "held_work_place_refs": [
            ref_payload(ref) for ref in held_work_place_refs],
        "held_work_places": list(held_places),
        "original_work_places": list(work_places),
        "capacity_return_port_refs": [
            ref_payload(ref) for ref in capacity_ports],
        "capacity_return_places": list(capacity_places),
        "capacity_return_place_ref": ref_payload(
            capacity_return_place_ref),
        "reconcile_ref": ref_payload(reconcile_ref),
        "reconcile_place_ref": ref_payload(reconcile_place_ref),
        "reconcile_place": reconcile_place,
        "terminal_place_ref": ref_payload(terminal_place_ref),
        "terminal_place": terminal_place,
        "retry_transition_ref": ref_payload(retry_transition_ref),
        "exhaust_transition_ref": ref_payload(exhaust_transition_ref),
        "mechanical_transitions": list(mechanical_transitions),
    }
    metadata = {
        "binding_id": str(route_ref.entity_id),
        "binding_version_id": str(route_ref.version_id),
        "binding_ref": ref_payload(route_ref),
        **{key: value for key, value in route_material.items()
           if key != "schema_version"},
    }
    core.catalog.validate_instance(
        "fault_route_binding/v1", category="object", instance=metadata)
    return route_ref, metadata


def _historical_build_static_fault_route_binding(
        core: Any, *, subnet_template_ref: VersionRef,
        fault_pending_place_ref: VersionRef,
        retry_transition_ref: VersionRef,
        exhaust_transition_ref: VersionRef,
        reconcile_transition_ref: VersionRef,
        identity_key: str,
) -> tuple[VersionRef, dict[str, Any]]:
    """Retained unreachable historical static sidecar route builder."""
    _registered(core, subnet_template_ref, "fault_subnet_template/v1")
    endpoints = (
        (fault_pending_place_ref, "fault_petri_place/v1", "fault_pending"),
        (retry_transition_ref, "fault_petri_transition/v1", "retry"),
        (exhaust_transition_ref, "fault_petri_transition/v1", "exhaust"),
        (reconcile_transition_ref, "fault_petri_transition/v1", "reconcile"),
    )
    template_payload = ref_payload(subnet_template_ref)
    for ref, expected_type, role in endpoints:
        _row, endpoint = _registered(core, ref, expected_type)
        if (endpoint.get("subnet_template_ref") != template_payload
                or endpoint.get("role") != role):
            raise StrictContractError(
                "static fault route endpoint differs from its subnet role")
    route_ref = VersionRef(
        "fault_route_binding/v1",
        _stable_id("fault_route_binding", identity_key),
        _stable_id("fault_route_binding_version", identity_key))
    metadata = {
        "fault_route_binding_ref": ref_payload(route_ref),
        "subnet_template_ref": template_payload,
        "fault_pending_place_ref": ref_payload(fault_pending_place_ref),
        "blocked_waiting_retry_decision_control": (
            "blocked_waiting_retry_decision"),
        "retry_transition_ref": ref_payload(retry_transition_ref),
        "exhaust_transition_ref": ref_payload(exhaust_transition_ref),
        "reconcile_transition_ref": ref_payload(reconcile_transition_ref),
    }
    core.catalog.validate_instance(
        "fault_route_binding/v1", category="object", instance=metadata)
    return route_ref, metadata


def _historical_build_operation_fault(
        core: Any, context: Any, *, boundary: FaultClass,
        cause_class: FaultCause,
        certainty: Literal["proven", "unknown_requires_reconciliation"],
        held_work_refs: Sequence[VersionRef],
        returned_capacity_refs: Sequence[VersionRef],
        provider_submission_unknown_ref: VersionRef | None,
        idempotency_key: str,
) -> tuple[VersionRef, dict[str, Any], tuple[VersionRef, ...]]:
    """Build the reference-only fault for an enclosing atomic transaction."""
    _registered(core, context.invocation_ref, "invocation/v1")
    _registered(core, context.operation_binding_ref, "operation_binding/v1")
    _registered(core, context.authority_decision_ref,
                "user_authority_decision/v1")
    _route_row, route = _registered(
        core, context.fault_route_binding_ref, "fault_route_binding/v1")
    _policy_row, policy = _registered(
        core, context.fault_policy_ref, "fault_disposition_policy/v1")
    expected_route = {
        "operation_binding_ref": ref_payload(context.operation_binding_ref),
        "policy_ref": ref_payload(context.fault_policy_ref),
    }
    if any(route.get(name) != value for name, value in expected_route.items()):
        raise StrictContractError("fault route is outside invocation authority")
    if (policy.get("routing_rule") != "petri_selects_mechanical_role"
            or policy.get("terminal_evidence_rule")
            != "generic_evidence_before_mechanical_role"):
        raise StrictContractError(
            "fault policy does not delegate mechanical role selection to Petri")
    is_unknown = (
        boundary == "provider"
        and cause_class == "submitted_outcome_unknown"
        and certainty == "unknown_requires_reconciliation"
    )
    if is_unknown:
        if (not isinstance(provider_submission_unknown_ref, VersionRef)
                or provider_submission_unknown_ref.entity_type
                != "provider_submission_unknown/v1"):
            raise StrictContractError(
                "unknown external outcome requires its persistent witness")
        _registered(
            core, provider_submission_unknown_ref,
            "provider_submission_unknown/v1")
    elif provider_submission_unknown_ref is not None:
        raise StrictContractError(
            "ordinary operation fault cannot cite submission uncertainty")
    from .operations import (
        OperationAuthorityError,
        _canonical_operation_fault_refs,
    )
    try:
        held = _canonical_operation_fault_refs(
            held_work_refs, label="held work")
        returned = _canonical_operation_fault_refs(
            returned_capacity_refs, label="returned capacity")
    except OperationAuthorityError as exc:
        raise StrictContractError(str(exc)) from exc
    for ref in (*held, *returned):
        _registered(core, ref, "petri_token/v1")
    if set(held) & set(returned):
        raise StrictContractError("held work and returned capacity overlap")
    if (context.origin != "petri_operation"
            or context.own_transition_firing_ref is None):
        raise StrictContractError(
            "operation fault requires its own Petri firing authority")
    _row, firing = _registered(
        core, context.own_transition_firing_ref, "transition_firing/v1")
    claimed = {
        _ref_from_payload(value)
        for value in firing.get("claimed_input_refs", [])
    }
    if set(held) | set(returned) != claimed:
        raise StrictContractError(
            "fault hold/capacity partition differs from firing claim")
    fault_ref = VersionRef(
        "operation_fault/v1", _stable_id("operation_fault", idempotency_key),
        _stable_id("operation_fault_version", idempotency_key))
    material = {
        "schema_version": "operation_fault_dependency/v1",
        "invocation_ref": ref_payload(context.invocation_ref),
        "transition_firing_ref": (
            ref_payload(context.own_transition_firing_ref)
            if context.own_transition_firing_ref else None),
        "operation_binding_ref": ref_payload(context.operation_binding_ref),
        "fault_route_binding_ref": ref_payload(
            context.fault_route_binding_ref),
        "policy_ref": ref_payload(context.fault_policy_ref),
        "authority_decision_ref": ref_payload(context.authority_decision_ref),
        "boundary": boundary, "cause_class": cause_class,
        "certainty": certainty,
        "provider_submission_unknown_ref": (
            ref_payload(provider_submission_unknown_ref)
            if provider_submission_unknown_ref is not None else None),
        "root_fault_chain_id": str(context.root_fault_chain_id),
        "retry_ordinal": context.retry_ordinal,
        "held_work_refs": [ref_payload(ref) for ref in held],
        "returned_capacity_refs": [ref_payload(ref) for ref in returned],
        "normal_output_resource_refs": [],
    }
    metadata = {
        "fault_id": str(fault_ref.entity_id),
        "fault_version_id": str(fault_ref.version_id),
        "fault_ref": ref_payload(fault_ref),
        **{key: value for key, value in material.items()
           if key != "schema_version"},
    }
    derived = (
        context.invocation_ref, context.operation_binding_ref,
        context.fault_route_binding_ref, context.fault_policy_ref,
        context.authority_decision_ref, *held, *returned,
        *((provider_submission_unknown_ref,)
          if provider_submission_unknown_ref is not None else ()),
    )
    core.catalog.validate_instance(
        "operation_fault/v1", category="object", instance=metadata)
    return fault_ref, metadata, derived


def _historical_build_fault_terminal_witness(
        core: Any, context: Any, *, operation_fault_ref: VersionRef,
        held_work_refs: tuple[VersionRef, ...],
        returned_capacity_refs: tuple[VersionRef, ...],
        idempotency_key: str,
) -> tuple[VersionRef, dict[str, Any], tuple[VersionRef, ...]]:
    """Build generic terminal evidence for one same-transaction fault."""
    if (not isinstance(operation_fault_ref, VersionRef)
            or operation_fault_ref.entity_type != "operation_fault/v1"):
        raise StrictContractError(
            "generic evidence proposed fault identity differs")
    _route_row, route = _registered(
        core, context.fault_route_binding_ref, "fault_route_binding/v1")
    pending_ref = _ref_from_payload(route["fault_pending_place_ref"])
    template_ref = _ref_from_payload(route["subnet_template_ref"])
    _template_row, template = _registered(
        core, template_ref, "fault_subnet_template/v1")
    terminal_refs = []
    for raw_ref in template["place_refs"]:
        place_ref = _ref_from_payload(raw_ref)
        _place_row, place = _registered(
            core, place_ref, "fault_petri_place/v1")
        if place.get("role") == "structural_terminal_intake":
            terminal_refs.append(place_ref)
    if len(terminal_refs) != 1:
        raise StrictContractError(
            "fault subnet requires one structural terminal intake")
    terminal_ref = terminal_refs[0]
    _registered(core, pending_ref, "fault_petri_place/v1")
    held = tuple(held_work_refs)
    returned = tuple(returned_capacity_refs)
    if any(not isinstance(ref, VersionRef) for ref in (*held, *returned)):
        raise StrictContractError(
            "generic terminal evidence requires exact partition refs")
    evidence_material = {
        "operation_fault_ref": ref_payload(operation_fault_ref),
        "evidence_kind": "generic_fault_terminal_evidence",
        "held_work_refs": [ref_payload(ref) for ref in held],
        "returned_capacity_refs": [ref_payload(ref) for ref in returned],
        "fault_pending_place_ref": ref_payload(pending_ref),
        "route_terminal_place_ref": ref_payload(terminal_ref),
        "application_terminal_contract": (
            "schema_bound_resource_after_mechanical_settlement"),
    }
    witness_ref = VersionRef(
        "fault_terminal_witness/v1",
        _stable_id("fault_terminal_witness", idempotency_key),
        _stable_id("fault_terminal_witness_version", idempotency_key))
    material = {
        "schema_version": "fault_terminal_witness_dependency/v1",
        **evidence_material,
        "fault_route_binding_ref": ref_payload(
            context.fault_route_binding_ref),
    }
    metadata = {
        "witness_id": str(witness_ref.entity_id),
        "witness_version_id": str(witness_ref.version_id),
        "witness_ref": ref_payload(witness_ref),
        **{key: value for key, value in material.items()
           if key != "schema_version"},
    }
    derived = (
        operation_fault_ref, context.fault_route_binding_ref,
        template_ref, pending_ref, terminal_ref, *held, *returned)
    core.catalog.validate_instance(
        "fault_terminal_witness/v1", category="object", instance=metadata)
    return witness_ref, metadata, derived


def _historical_build_operation_fault_closure(
        core: Any, context: Any, *, observation_kind: str,
        certainty: Literal["proven", "unknown_requires_reconciliation"],
        provider_attempt_evidence_ref: VersionRef | None,
        provider_submission_unknown_ref: VersionRef | None,
        fault_terminal_evidence_ref: VersionRef,
        idempotency_key: str,
) -> tuple[VersionRef, dict[str, Any], tuple[VersionRef, ...]]:
    """Build exactly one selected ``operation_fault/v1`` object."""
    if observation_kind not in {
            "retryable_provider_failure", "terminal_provider_failure",
            "provider_cancellation_after_submission", "protocol_invalidity",
            "resource_limit_exhaustion", "submission_unknown",
            "outcome_unknown", "tool_error", "framework_fault"}:
        raise StrictContractError("operation fault observation kind is not closed")
    if certainty not in {"proven", "unknown_requires_reconciliation"}:
        raise StrictContractError("operation fault certainty is not closed")
    _registered(core, context.invocation_ref, "invocation/v1")
    firing_ref = context.own_transition_firing_ref
    if (not isinstance(firing_ref, VersionRef)
            or firing_ref.entity_type != "transition_firing/v1"):
        raise StrictContractError(
            "operation fault requires its exact transition_firing/v1")
    _registered(core, firing_ref, "transition_firing/v1")
    root_fault_chain_ref = context.root_fault_chain_ref
    if (not isinstance(root_fault_chain_ref, VersionRef)
            or root_fault_chain_ref.entity_type != "fault_chain/v1"):
        raise StrictContractError(
            "operation fault requires its versioned root fault chain")
    optional_refs = (
        (provider_attempt_evidence_ref, "provider_attempt_evidence/v1"),
        (provider_submission_unknown_ref, "provider_submission_unknown/v1"),
    )
    for ref, expected_type in optional_refs:
        if ref is not None:
            _registered(core, ref, expected_type)
    if (not isinstance(fault_terminal_evidence_ref, VersionRef)
            or fault_terminal_evidence_ref.entity_type
            != "fault_terminal_witness/v1"):
        raise StrictContractError(
            "operation fault terminal evidence ref is not exact")
    if observation_kind in {"submission_unknown", "outcome_unknown"}:
        if (certainty != "unknown_requires_reconciliation"
                or provider_submission_unknown_ref is None):
            raise StrictContractError(
                "unknown provider outcome requires its exact persistent ref")
    elif provider_submission_unknown_ref is not None:
        raise StrictContractError(
            "proven operation fault cannot cite submission uncertainty")
    fault_ref = VersionRef(
        "operation_fault/v1", _stable_id("operation_fault", idempotency_key),
        _stable_id("operation_fault_version", idempotency_key))
    metadata = {
        "operation_fault_ref": ref_payload(fault_ref),
        "invocation_ref": ref_payload(context.invocation_ref),
        "transition_firing_ref": ref_payload(firing_ref),
        "observation_kind": observation_kind,
        "certainty": certainty,
        "root_fault_chain_ref": ref_payload(root_fault_chain_ref),
        "provider_attempt_evidence_ref": (
            ref_payload(provider_attempt_evidence_ref)
            if provider_attempt_evidence_ref is not None else None),
        "provider_submission_unknown_ref": (
            ref_payload(provider_submission_unknown_ref)
            if provider_submission_unknown_ref is not None else None),
        "fault_terminal_evidence_ref": ref_payload(
            fault_terminal_evidence_ref),
    }
    derived = tuple(ref for ref in (
        context.invocation_ref, firing_ref, provider_attempt_evidence_ref,
        provider_submission_unknown_ref, fault_terminal_evidence_ref)
        if ref is not None)
    core.catalog.validate_instance(
        "operation_fault/v1", category="object", instance=metadata)
    return fault_ref, metadata, derived


def _historical_build_fault_disposition_policy(
        core: Any, *, operation_fault_ref: VersionRef,
        decision_authority_ref: VersionRef, root_fault_chain_ref: VersionRef,
        decision: Literal["retry", "exhaust", "reconcile"],
        retry_ordinal: int, registered_retry_limit: int,
        idempotency_key: str,
) -> tuple[VersionRef, dict[str, Any], tuple[VersionRef, ...]]:
    """Build the exact external disposition registered after a fault."""
    _fault_row, fault = _registered(
        core, operation_fault_ref, "operation_fault/v1")
    _registered(core, decision_authority_ref)
    if (not isinstance(root_fault_chain_ref, VersionRef)
            or root_fault_chain_ref.entity_type != "fault_chain/v1"
            or fault.get("root_fault_chain_ref")
            != ref_payload(root_fault_chain_ref)):
        raise StrictContractError("fault disposition has another root chain")
    if decision not in {"retry", "exhaust", "reconcile"}:
        raise StrictContractError("fault disposition decision is not closed")
    if (isinstance(retry_ordinal, bool) or not isinstance(retry_ordinal, int)
            or retry_ordinal < 0
            or isinstance(registered_retry_limit, bool)
            or not isinstance(registered_retry_limit, int)
            or registered_retry_limit < 0):
        raise StrictContractError("fault disposition retry bounds are invalid")
    policy_ref = VersionRef(
        "fault_disposition_policy/v1",
        _stable_id("fault_disposition_policy", idempotency_key),
        _stable_id("fault_disposition_policy_version", idempotency_key))
    metadata = {
        "policy_ref": ref_payload(policy_ref),
        "decision_authority_ref": ref_payload(decision_authority_ref),
        "root_fault_chain_ref": ref_payload(root_fault_chain_ref),
        "decision": decision,
        "retry_ordinal": retry_ordinal,
        "registered_retry_limit": registered_retry_limit,
    }
    core.catalog.validate_instance(
        "fault_disposition_policy/v1", category="object", instance=metadata)
    return policy_ref, metadata, (operation_fault_ref, decision_authority_ref)


def _historical_build_fault_sidecar_action(
        core: Any, *, business_firing_settlement_ref: VersionRef,
        operation_fault_ref: VersionRef, role: _HistoricalFaultMechanicalRole,
        disposition_policy_ref: VersionRef,
        disposition_policy_document: Mapping[str, Any] | None = None,
        idempotency_key: str,
) -> tuple[VersionRef, dict[str, Any], tuple[VersionRef, ...]]:
    """Build one mechanical role from an exact post-fault disposition."""
    if (not isinstance(business_firing_settlement_ref, VersionRef)
            or business_firing_settlement_ref.entity_type != "fact_event/v1"
            or len(tuple(
                event for event in core.event_store.list_events()
                if event.event_type == "transition_firing_settled/v1"
                and event.payload.get("event_ref")
                == ref_payload(business_firing_settlement_ref))) != 1):
        raise StrictContractError(
            "fault sidecar action requires one exact business settlement event")
    _fault_row, fault = _registered(
        core, operation_fault_ref, "operation_fault/v1")
    if disposition_policy_document is None:
        _policy_row, policy = _registered(
            core, disposition_policy_ref, "fault_disposition_policy/v1")
    else:
        policy = dict(disposition_policy_document)
        core.catalog.validate_instance(
            "fault_disposition_policy/v1", category="object", instance=policy)
        if policy.get("policy_ref") != ref_payload(disposition_policy_ref):
            raise StrictContractError(
                "fault sidecar action policy document names another ref")
    decision = policy.get("decision")
    retry_ordinal = policy.get("retry_ordinal")
    retry_limit = policy.get("registered_retry_limit")
    expected_role = (
        "reconcile" if decision == "reconcile" else
        "exhaust" if decision == "exhaust" or retry_ordinal >= retry_limit else
        "retry")
    if role != expected_role:
        raise StrictContractError(
            "fault sidecar role differs from the exact disposition guard")
    if (fault.get("root_fault_chain_ref")
            != policy.get("root_fault_chain_ref")):
        raise StrictContractError(
            "fault disposition belongs to another root fault chain")
    action_ref = VersionRef(
        "fault_sidecar_action/v1",
        _stable_id("fault_sidecar_action", idempotency_key),
        _stable_id("fault_sidecar_action_version", idempotency_key))
    metadata = {
        "fault_sidecar_action_ref": ref_payload(action_ref),
        "business_firing_settlement_ref": ref_payload(
            business_firing_settlement_ref),
        "operation_fault_ref": ref_payload(operation_fault_ref),
        "role": role,
        "disposition_policy_ref": ref_payload(disposition_policy_ref),
        "decision_authority_ref": policy["decision_authority_ref"],
        "root_fault_chain_ref": policy["root_fault_chain_ref"],
        "retry_ordinal": retry_ordinal,
        "registered_retry_limit": retry_limit,
    }
    core.catalog.validate_instance(
        "fault_sidecar_action/v1", category="object", instance=metadata)
    return action_ref, metadata, (
        business_firing_settlement_ref, operation_fault_ref,
        disposition_policy_ref)


__all__ = [
    "StrictContractError",
    "ref_payload",
    "content_schema_ref_payload",
    "publish_operation_spec",
    "publish_user_authority_decision",
]
