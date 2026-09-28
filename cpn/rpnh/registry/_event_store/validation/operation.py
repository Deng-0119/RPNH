"""Authoritative transaction-domain validation."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ... import event_store as facade
from ...models import PreparedObject

def validate_operation_contract_objects(context) -> None:
    events = context.events
    objects = context.objects
    transaction_id = context.transaction_id
    exact_ref_exists = context.exact_ref_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    canonical_json = facade.canonical_json
    canonical_text = facade.canonical_text
    _FAULT_MECHANICAL_ADMISSION_FIELDS = facade._HISTORICAL_FAULT_MECHANICAL_ADMISSION_FIELDS
    _FAULT_MECHANICAL_COMPLETION_FIELDS = facade._HISTORICAL_FAULT_MECHANICAL_COMPLETION_FIELDS
    """Reject low-level bypasses of current authority semantics."""
    historical_fault_object_types = frozenset({
        "fault_subnet_template/v1",
        "fault_petri_place/v1",
        "fault_petri_transition/v1",
        "fault_disposition_policy/v1",
        "fault_route_binding/v1",
        "fault_mechanical_firing/v1",
        "fault_mechanical_settlement/v1",
        "fault_sidecar_action/v1",
        "operation_fault/v1",
        "fault_retry_admission/v1",
        "fault_terminal_witness/v1",
    })
    historical_fault_classes = (
        "provider", "tool", "process", "delegated_leaf", "petri_input",
        "publication", "delivery", "cancellation")

    def self_ref(item: PreparedObject, name: str) -> Mapping[str, Any]:
        expected = {
            "entity_type": item.object_type,
            "logical_id": str(item.logical_id),
            "version_id": str(item.version_id),
        }
        value = item.metadata.get(name)
        if value != expected:
            raise RegistryConflict(
                f"{item.object_type} self reference differs from its object identity")
        return expected

    def exact_refs(values: object, *, nonempty: bool = False,
                   expected_type: str | None = None,
                   canonical: bool = True,
                   unique: bool = True) -> list[Mapping[str, Any]]:
        if not isinstance(values, list) or (nonempty and not values):
            raise RegistryConflict("strict exact reference list is absent")
        if (unique and len({canonical_json(value) for value in values})
                != len(values)):
            raise RegistryConflict("strict exact reference list has duplicates")
        if any(not exact_ref_exists(value, expected_type) for value in values):
            raise RegistryConflict("strict exact reference list is unregistered")
        if canonical and values != sorted(values, key=canonical_json):
            raise RegistryConflict("strict exact reference list is not canonical")
        return values  # type: ignore[return-value]

    for item in objects:
        metadata = dict(item.metadata)
        if item.object_type in historical_fault_object_types:
            # HISTORICAL_INERT: current schemas remove these objects;
            # retained branches below are unreachable from this path.
            continue
        if item.object_type == "user_authority_decision/v1":
            self_ref(item, "decision_ref")
            principal_ref = metadata.get("user_principal_ref")
            governed = exact_refs(
                metadata.get("governed_artifact_refs"), nonempty=True)
            supersedes_ref = metadata.get("supersedes_ref")
            choices = metadata.get("selected_choices")
            if (metadata.get("decision_id") != str(item.logical_id)
                    or metadata.get("decision_version_id")
                    != str(item.version_id)
                    or metadata.get("status") != "effective"
                    or not exact_ref_exists(principal_ref, "principal/v1")
                    or (supersedes_ref is not None and not exact_ref_exists(
                        supersedes_ref, "user_authority_decision/v1"))
                or not isinstance(choices, list) or not choices
                or choices != sorted(
                    choices, key=lambda value: str(value.get("choice_id", "")))):
                raise RegistryConflict(
                    "user authority decision has unclosed identity/content authority")

        elif item.object_type == "fault_subnet_template/v1":
            self_ref(item, "template_ref")
            authority_ref = metadata.get("authority_decision_ref")
            authority = (version_metadata(
                str(authority_ref.get("version_id", "")),
                "user_authority_decision/v1")
                if isinstance(authority_ref, Mapping) else None)
            place_roles = metadata.get("place_roles")
            transition_roles = metadata.get("transition_roles")
            place_refs = exact_refs(
                metadata.get("place_refs"), nonempty=True,
                expected_type="fault_petri_place/v1", canonical=False)
            transition_refs = exact_refs(
                metadata.get("transition_refs"), nonempty=True,
                expected_type="fault_petri_transition/v1",
                canonical=False)
            arcs = metadata.get("arcs")
            template_ref = metadata.get("template_ref")
            expected_place_roles = [
                "capacity_return", "fault_pending",
                "original_work_hold", "reconciliation_pending",
                "structural_terminal_intake",
            ]
            expected_transition_roles = [
                "exhaust", "reconcile", "retry"]
            place_by_role: dict[str, Mapping[str, Any]] = {}
            for role, ref in zip(
                    place_roles if isinstance(place_roles, list) else [],
                    place_refs, strict=False):
                endpoint = version_metadata(
                    str(ref.get("version_id", "")),
                    "fault_petri_place/v1")
                if (endpoint is None
                        or endpoint.get("subnet_template_ref")
                        != template_ref
                        or endpoint.get("role") != role
                        or endpoint.get("place_ref") != ref):
                    raise RegistryConflict(
                        "fault subnet place differs from its template role")
                place_by_role[str(role)] = endpoint
            expected_arcs = {
                "retry": {
                    "input_place_refs": [
                        place_by_role.get("fault_pending", {}).get(
                            "place_ref"),
                        place_by_role.get("original_work_hold", {}).get(
                            "place_ref"),
                    ],
                    "output_place_refs": [
                        place_by_role.get("original_work_hold", {}).get(
                            "place_ref")],
                    "accepted_verdicts": ["retry"],
                },
                "exhaust": {
                    "input_place_refs": [
                        place_by_role.get("fault_pending", {}).get(
                            "place_ref")],
                    "output_place_refs": [
                        place_by_role.get(
                            "structural_terminal_intake", {}).get(
                                "place_ref")],
                    "accepted_verdicts": ["exhaust"],
                },
                "reconcile": {
                    "input_place_refs": [
                        place_by_role.get("fault_pending", {}).get(
                            "place_ref")],
                    "output_place_refs": [
                        place_by_role.get(
                            "reconciliation_pending", {}).get(
                                "place_ref")],
                    "accepted_verdicts": ["reconcile"],
                },
            }
            if (not isinstance(arcs, list)
                    or [arc.get("transition_role")
                        for arc in arcs
                        if isinstance(arc, Mapping)]
                    != expected_transition_roles):
                raise RegistryConflict(
                    "fault subnet transition arcs are not canonical")
            for role, ref, arc in zip(
                    transition_roles
                    if isinstance(transition_roles, list) else [],
                    transition_refs, arcs, strict=False):
                endpoint = version_metadata(
                    str(ref.get("version_id", "")),
                    "fault_petri_transition/v1")
                expected = expected_arcs.get(str(role))
                if (endpoint is None or expected is None
                        or endpoint.get("subnet_template_ref")
                        != template_ref
                        or endpoint.get("role") != role
                        or endpoint.get("transition_ref") != ref
                        or not isinstance(arc, Mapping)
                        or arc.get("transition_role") != role
                        or any(endpoint.get(name) != value
                               or arc.get(name) != value
                               for name, value in expected.items())):
                    raise RegistryConflict(
                        "fault subnet transition differs from its exact arcs")
            if (metadata.get("template_id") != str(item.logical_id)
                    or metadata.get("template_version_id")
                    != str(item.version_id)
                    or authority is None or authority.get("status") != "effective"
                    or place_roles != expected_place_roles
                    or transition_roles != expected_transition_roles
                    or len(place_refs) != 5
                    or len(transition_refs) != 3
                    or len({value.get("place")
                            for value in place_by_role.values()}) != 5):
                raise RegistryConflict(
                    "fault subnet template differs from effective authority/topology")

        elif item.object_type == "fault_petri_place/v1":
            self_ref(item, "place_ref")
            template_ref = metadata.get("subnet_template_ref")
            template = (version_metadata(
                str(template_ref.get("version_id", "")),
                "fault_subnet_template/v1")
                if isinstance(template_ref, Mapping) else None)
            place_roles = (
                template.get("place_roles") if template else None)
            role = metadata.get("role")
            if (metadata.get("place_id") != str(item.logical_id)
                    or metadata.get("place_version_id")
                    != str(item.version_id)
                    or template is None
                    or not isinstance(place_roles, list)
                    or role not in place_roles
                    or not isinstance(metadata.get("place"), str)
                    or not metadata.get("place")):
                raise RegistryConflict(
                    "fault Petri place is outside its exact subnet template")

        elif item.object_type == "fault_petri_transition/v1":
            self_ref(item, "transition_ref")
            template_ref = metadata.get("subnet_template_ref")
            template = (version_metadata(
                str(template_ref.get("version_id", "")),
                "fault_subnet_template/v1")
                if isinstance(template_ref, Mapping) else None)
            transition_roles = (
                template.get("transition_roles") if template else None)
            transition_refs = (
                template.get("transition_refs") if template else None)
            arcs = template.get("arcs") if template else None
            role = metadata.get("role")
            if (metadata.get("transition_id") != str(item.logical_id)
                    or metadata.get("transition_version_id")
                    != str(item.version_id)
                    or template is None
                    or not isinstance(transition_roles, list)
                    or not isinstance(transition_refs, list)
                    or not isinstance(arcs, list)
                    or role not in transition_roles
                    or transition_refs[transition_roles.index(role)]
                    != metadata.get("transition_ref")):
                raise RegistryConflict(
                    "fault Petri transition is outside its exact subnet template")
            arc = arcs[transition_roles.index(role)]
            if (not isinstance(arc, Mapping)
                    or arc.get("transition_role") != role
                    or any(metadata.get(name) != arc.get(name)
                           for name in (
                               "input_place_refs", "output_place_refs",
                               "accepted_verdicts"))
                    or not all(exact_ref_exists(
                        ref, "fault_petri_place/v1")
                        for name in (
                            "input_place_refs", "output_place_refs")
                        for ref in metadata.get(name, []))):
                raise RegistryConflict(
                    "fault Petri transition arcs differ from its template")

        elif item.object_type == "fault_disposition_policy/v1":
            self_ref(item, "policy_ref")
            authority_ref = metadata.get("decision_authority_ref")
            root_ref = metadata.get("root_fault_chain_ref")
            if (not exact_ref_exists(authority_ref)
                    or not isinstance(root_ref, Mapping)
                    or root_ref.get("entity_type") != "fault_chain/v1"
                    or metadata.get("decision") not in {
                        "retry", "exhaust", "reconcile"}
                    or isinstance(metadata.get("retry_ordinal"), bool)
                    or not isinstance(metadata.get("retry_ordinal"), int)
                    or metadata["retry_ordinal"] < 0
                    or isinstance(
                        metadata.get("registered_retry_limit"), bool)
                    or not isinstance(
                        metadata.get("registered_retry_limit"), int)
                    or metadata["registered_retry_limit"] < 0):
                raise RegistryConflict(
                    "fault disposition policy is not exact post-fault authority")
            continue
            self_ref(item, "policy_ref")
            authority_ref = metadata.get("authority_decision_ref")
            template_ref = metadata.get("subnet_template_ref")
            repair_ref = metadata.get("repair_authority_ref")
            limits = metadata.get("retry_limits")
            if (metadata.get("policy_id") != str(item.logical_id)
                    or metadata.get("policy_version_id")
                    != str(item.version_id)
                    or not exact_ref_exists(
                        authority_ref, "user_authority_decision/v1")
                    or not exact_ref_exists(
                        template_ref, "fault_subnet_template/v1")
                    or not exact_ref_exists(repair_ref)
                    or not isinstance(limits, list)
                    or [value.get("fault_class") for value in limits]
                    != list(historical_fault_classes)
                    or metadata.get("routing_rule")
                    != "petri_selects_mechanical_role"
                    or metadata.get("terminal_evidence_rule")
                    != "generic_evidence_before_mechanical_role"):
                raise RegistryConflict(
                    "fault policy is not a complete exact Option-A policy")
            material = {
                "schema_version": "fault_disposition_policy_dependency/v1",
                "authority_decision_ref": authority_ref,
                "subnet_template_ref": template_ref,
                "retry_limits": limits,
                "repair_authority_ref": repair_ref,
                "routing_rule": metadata.get("routing_rule"),
                "terminal_evidence_rule": metadata.get(
                    "terminal_evidence_rule"),
            }
            if metadata.get("dependency_fingerprint") != canonical_text(material):
                raise RegistryConflict("fault policy dependency fingerprint differs")

        elif item.object_type == "fault_route_binding/v1":
            self_ref(item, "fault_route_binding_ref")
            template_ref = metadata.get("subnet_template_ref")
            endpoints = (
                ("fault_pending_place_ref", "fault_petri_place/v1",
                 "fault_pending"),
                ("retry_transition_ref", "fault_petri_transition/v1",
                 "retry"),
                ("exhaust_transition_ref", "fault_petri_transition/v1",
                 "exhaust"),
                ("reconcile_transition_ref",
                 "fault_petri_transition/v1", "reconcile"),
            )
            if (not exact_ref_exists(
                    template_ref, "fault_subnet_template/v1")
                    or metadata.get(
                        "blocked_waiting_retry_decision_control")
                    != "blocked_waiting_retry_decision"):
                raise RegistryConflict(
                    "static fault route lacks its exact subnet/control")
            for name, expected_type, expected_role in endpoints:
                endpoint_ref = metadata.get(name)
                endpoint = (version_metadata(
                    str(endpoint_ref.get("version_id", "")),
                    expected_type)
                    if isinstance(endpoint_ref, Mapping) else None)
                if (endpoint is None
                        or endpoint.get("subnet_template_ref")
                        != template_ref
                        or endpoint.get("role") != expected_role):
                    raise RegistryConflict(
                        "static fault route endpoint differs from subnet")
            continue
            self_ref(item, "binding_ref")
            scalar_ref_fields = (
                ("operation_binding_ref", "operation_binding/v1"),
                ("policy_ref", "fault_disposition_policy/v1"),
                ("subnet_template_ref", "fault_subnet_template/v1"),
                ("fault_pending_place_ref", "fault_petri_place/v1"),
                ("capacity_return_place_ref", "fault_petri_place/v1"),
                ("reconcile_ref", "fault_petri_transition/v1"),
                ("reconcile_place_ref", "fault_petri_place/v1"),
                ("terminal_place_ref", "fault_petri_place/v1"),
                ("retry_transition_ref", "fault_petri_transition/v1"),
                ("exhaust_transition_ref",
                 "fault_petri_transition/v1"),
            )
            if any(not exact_ref_exists(metadata.get(name), expected)
                   for name, expected in scalar_ref_fields):
                raise RegistryConflict("fault route has an unregistered endpoint")
            work_ports = exact_refs(
                metadata.get("held_work_port_refs"), nonempty=True,
                unique=False)
            held_place_refs = exact_refs(
                metadata.get("held_work_place_refs"), nonempty=True,
                expected_type="fault_petri_place/v1",
                canonical=False)
            held_places = metadata.get("held_work_places")
            work_places = metadata.get("original_work_places")
            capacity_ports = exact_refs(
                metadata.get("capacity_return_port_refs"), nonempty=True)
            capacity_places = metadata.get("capacity_return_places")
            policy = version_metadata(
                str(metadata.get("policy_ref", {}).get("version_id", "")),
                "fault_disposition_policy/v1")
            binding = version_metadata(
                str(metadata.get("operation_binding_ref", {}).get(
                    "version_id", "")), "operation_binding/v1")
            subnet_payload = metadata.get("subnet_template_ref")
            endpoint_roles = (
                ("fault_pending_place_ref", "fault_pending"),
                ("capacity_return_place_ref", "capacity_return"),
                ("reconcile_place_ref", "reconciliation_pending"),
                ("terminal_place_ref", "structural_terminal_intake"),
                ("retry_transition_ref", "retry"),
                ("exhaust_transition_ref", "exhaust"),
                ("reconcile_ref", "reconcile"),
            )
            endpoints = {
                name: version_metadata(
                    str(metadata.get(name, {}).get(
                        "version_id", "")),
                    ("fault_petri_transition/v1"
                     if _role in {"retry", "exhaust", "reconcile"}
                     else "fault_petri_place/v1"))
                for name, _role in endpoint_roles
            }
            material = {
                "schema_version": "fault_route_binding_dependency/v1",
                "operation_binding_ref": metadata.get(
                    "operation_binding_ref"),
                "policy_ref": metadata.get("policy_ref"),
                "subnet_template_ref": metadata.get("subnet_template_ref"),
                "fault_pending_place_ref": metadata.get(
                    "fault_pending_place_ref"),
                "fault_pending_place": metadata.get(
                    "fault_pending_place"),
                "held_work_port_refs": work_ports,
                "held_work_place_refs": held_place_refs,
                "held_work_places": held_places,
                "original_work_places": work_places,
                "capacity_return_port_refs": capacity_ports,
                "capacity_return_places": capacity_places,
                "capacity_return_place_ref": metadata.get(
                    "capacity_return_place_ref"),
                "reconcile_ref": metadata.get("reconcile_ref"),
                "reconcile_place_ref": metadata.get(
                    "reconcile_place_ref"),
                "reconcile_place": metadata.get("reconcile_place"),
                "terminal_place_ref": metadata.get(
                    "terminal_place_ref"),
                "terminal_place": metadata.get("terminal_place"),
                "retry_transition_ref": metadata.get(
                    "retry_transition_ref"),
                "exhaust_transition_ref": metadata.get(
                    "exhaust_transition_ref"),
                "mechanical_transitions": metadata.get(
                    "mechanical_transitions"),
            }
            route_material = {
                key: material[key] for key in (
                    "operation_binding_ref", "subnet_template_ref",
                    "fault_pending_place_ref", "fault_pending_place",
                    "held_work_port_refs", "held_work_place_refs",
                    "held_work_places",
                    "original_work_places",
                    "capacity_return_port_refs",
                    "capacity_return_places", "capacity_return_place_ref",
                    "reconcile_ref",
                    "reconcile_place_ref", "reconcile_place",
                    "terminal_place_ref", "terminal_place",
                    "retry_transition_ref", "exhaust_transition_ref",
                    "mechanical_transitions")
            }
            mechanical = metadata.get("mechanical_transitions")
            common_inputs = [
                metadata.get("fault_pending_place"),
                *(held_places if isinstance(held_places, list) else []),
            ]
            expected_mechanical = [
                {
                    "role": "exhaust",
                    "transition_ref": metadata.get(
                        "exhaust_transition_ref"),
                    "input_places": common_inputs,
                    "output_places": [metadata.get("terminal_place")],
                    "accepted_verdicts": ["exhaust"],
                    "emit": "forward",
                },
                {
                    "role": "reconcile",
                    "transition_ref": metadata.get("reconcile_ref"),
                    "input_places": common_inputs,
                    "output_places": [metadata.get("reconcile_place")],
                    "accepted_verdicts": ["reconcile"],
                    "emit": "forward",
                },
                {
                    "role": "retry",
                    "transition_ref": metadata.get(
                        "retry_transition_ref"),
                    "input_places": common_inputs,
                    "output_places": work_places,
                    "accepted_verdicts": ["retry"],
                    "emit": "forward",
                },
            ]
            if (metadata.get("binding_id") != str(item.logical_id)
                    or metadata.get("binding_version_id")
                    != str(item.version_id)
                    or policy is None or binding is None
                    or policy.get("subnet_template_ref")
                    != metadata.get("subnet_template_ref")
                    or binding.get("fault_route_binding_ref")
                    != metadata.get("binding_ref")
                    or binding.get("fault_policy_ref")
                    != metadata.get("policy_ref")
                    or any(endpoint is None
                           or endpoint.get("subnet_template_ref")
                           != subnet_payload
                           or endpoint.get("role") != role
                           for (name, role), endpoint in zip(
                               endpoint_roles,
                               (endpoints[name]
                                for name, _ in endpoint_roles),
                               strict=True))
                    or endpoints["fault_pending_place_ref"].get(
                        "place") != metadata.get("fault_pending_place")
                    or endpoints["reconcile_place_ref"].get("place")
                    != metadata.get("reconcile_place")
                    or endpoints["terminal_place_ref"].get("place")
                    != metadata.get("terminal_place")
                    or not isinstance(held_places, list)
                    or not isinstance(work_places, list)
                    or not isinstance(capacity_places, list)
                    or len(held_places) != len(work_ports)
                    or len(held_place_refs) != len(work_ports)
                    or len(work_places) != len(work_ports)
                    or len(capacity_places) != len(capacity_ports)
                    or any(not isinstance(value, str) or not value
                           for value in (
                               metadata.get("fault_pending_place"),
                               metadata.get("reconcile_place"),
                               metadata.get("terminal_place"),
                               *held_places, *work_places,
                               *capacity_places))
                or len({
                    (canonical_json(ref), place)
                    for ref, place in zip(
                        work_ports, work_places, strict=True)})
                != len(work_ports)
                or len({
                    (canonical_json(ref), place)
                    for ref, place in zip(
                        capacity_ports, capacity_places, strict=True)})
                != len(capacity_ports)
                    or len(set((
                        metadata.get("fault_pending_place"),
                        *held_places,
                        metadata.get("reconcile_place"),
                        metadata.get("terminal_place"),
                        *work_places,
                        *capacity_places))) != (
                            3 + len(held_places) + len(work_places)
                            + len(capacity_places))
                    or len({canonical_json(value) for value in (
                        *(metadata.get(name)
                          for name, _role in endpoint_roles),
                        *held_place_refs)}) != (
                            len(endpoint_roles) + len(held_place_refs))
                    or any((endpoint := version_metadata(
                        str(ref.get("version_id", "")),
                        "fault_petri_place/v1")) is None
                        or endpoint.get("subnet_template_ref")
                        != subnet_payload
                        or endpoint.get("role")
                        != "original_work_hold"
                        or endpoint.get("place") != place
                        for ref, place in zip(
                            held_place_refs, held_places, strict=True))
                    or mechanical != expected_mechanical
                    or metadata.get("route_digest")
                    != canonical_text(route_material)
                    or metadata.get("dependency_fingerprint")
                    != canonical_text(material)):
                raise RegistryConflict(
                    "fault route is not the operation/policy reverse closure")

        elif item.object_type == "fault_mechanical_firing/v1":
            self_ref(item, "fault_mechanical_firing_ref")
            route_ref = metadata.get("fault_route_binding_ref")
            control_ref = metadata.get("control_ref")
            evidence_ref = metadata.get(
                "fault_terminal_evidence_ref")
            retry_admission_ref = metadata.get(
                "retry_admission_ref")
            net_ref = metadata.get("net_instance_ref")
            checkpoint_ref = metadata.get("admission_checkpoint_ref")
            template_ref = metadata.get("subnet_template_ref")
            template_transition_ref = metadata.get(
                "template_transition_ref")
            delta_ref = metadata.get("claim_marking_delta_ref")
            claimed_refs = exact_refs(
                metadata.get("claimed_token_refs"), nonempty=True,
                expected_type="petri_token/v1")
            route = (version_metadata(
                str(route_ref.get("version_id", "")),
                "fault_route_binding/v1")
                if isinstance(route_ref, Mapping) else None)
            fault = (version_metadata(
                str(control_ref.get("version_id", "")),
                "operation_fault/v1")
                if isinstance(control_ref, Mapping) else None)
            evidence = (version_metadata(
                str(evidence_ref.get("version_id", "")),
                "fault_terminal_witness/v1")
                if isinstance(evidence_ref, Mapping) else None)
            retry_admission = (version_metadata(
                str(retry_admission_ref.get("version_id", "")),
                "fault_retry_admission/v1")
                if isinstance(retry_admission_ref, Mapping) else None)
            checkpoint = (version_metadata(
                str(checkpoint_ref.get("version_id", "")),
                "marking_checkpoint/v1")
                if isinstance(checkpoint_ref, Mapping) else None)
            delta = (version_metadata(
                str(delta_ref.get("version_id", "")),
                "marking_delta/v1")
                if isinstance(delta_ref, Mapping) else None)
            role = metadata.get("transition_role")
            verdict = metadata.get("verdict")
            mechanical = next((
                value for value in (
                    route.get("mechanical_transitions", [])
                    if isinstance(route, Mapping) else [])
                if isinstance(value, Mapping)
                and value.get("role") == role
            ), None)
            expected_route_transition_id = (
                f"{route_ref.get('logical_id')}::{role}"
                if isinstance(route_ref, Mapping) else None)
            claimed_tokens = [
                version_metadata(
                    str(value.get("version_id", "")),
                    "petri_token/v1")
                for value in claimed_refs
            ]
            pending_tokens = [
                value for value in claimed_tokens
                if (isinstance(value, Mapping)
                    and isinstance(route, Mapping)
                    and value.get("place")
                    == route.get("fault_pending_place"))
            ]
            claimed_places = sorted(
                str(value.get("place", ""))
                for value in claimed_tokens
                if isinstance(value, Mapping))
            expected_places = sorted(
                str(value) for value in (
                    mechanical.get("input_places", [])
                    if isinstance(mechanical, Mapping) else []))
            checkpoint_tokens = (
                checkpoint.get("token_refs", [])
                if isinstance(checkpoint, Mapping) else [])
            admission_material = {
                name: metadata.get(name)
                for name in _FAULT_MECHANICAL_ADMISSION_FIELDS
            }
            admission_events = [
                value for value in events
                if (value.event_type
                    == "fault_mechanical_firing_admitted/v1"
                    and value.payload.get(
                        "fault_mechanical_firing_ref")
                    == metadata.get(
                        "fault_mechanical_firing_ref"))
            ]
            if (metadata.get("fault_mechanical_firing_id")
                    != str(item.logical_id)
                    or metadata.get(
                        "fault_mechanical_firing_version_id")
                    != str(item.version_id)
                    or item.producer_invocation_id is not None
                    or len(admission_events) != 1
                    or route is None or fault is None
                    or evidence is None
                    or checkpoint is None or delta is None
                    or not exact_ref_exists(net_ref, "net_instance/v1")
                    or not exact_ref_exists(
                        template_ref, "fault_subnet_template/v1")
                    or not exact_ref_exists(
                        template_transition_ref,
                        "fault_petri_transition/v1")
                    or route.get("binding_ref") != route_ref
                    or route.get("subnet_template_ref") != template_ref
                    or mechanical is None
                    or mechanical.get("transition_ref")
                    != template_transition_ref
                    or verdict != role
                    or mechanical.get("accepted_verdicts") != [role]
                    or metadata.get("route_transition_id")
                    != expected_route_transition_id
                    or fault.get("fault_route_binding_ref") != route_ref
                    or evidence.get("operation_fault_ref") != control_ref
                    or evidence.get("fault_route_binding_ref") != route_ref
                    or evidence.get("evidence_kind")
                    != "generic_fault_terminal_evidence"
                    or ((role == "retry") != (
                        retry_admission is not None))
                    or (retry_admission is not None and (
                        retry_admission.get("operation_fault_ref")
                        != control_ref
                        or retry_admission.get(
                            "fault_terminal_evidence_ref")
                        != evidence_ref
                        or retry_admission.get(
                            "fault_mechanical_firing_ref")
                        != metadata.get(
                            "fault_mechanical_firing_ref")))
                    or checkpoint.get("net_instance_ref") != net_ref
                    or any(value is None for value in claimed_tokens)
                    or claimed_places != expected_places
                    or len(claimed_places) != len(set(claimed_places))
                    or any(value not in checkpoint_tokens
                           for value in claimed_refs)
                    or any(token.get("net_instance_ref") != net_ref
                           or token.get("control_ref") != control_ref
                           or token.get("consumer") is not None
                           or token.get("consumed_by") is not None
                           or token.get("faulted") is not False
                           for token in claimed_tokens
                           if isinstance(token, Mapping))
                    or len(pending_tokens) != 1
                    or pending_tokens[0].get("verdict") is not None
                    or pending_tokens[0].get("resource_ref") is not None
                    or pending_tokens[0].get(
                        "work_resource_ref") is not None
                    or pending_tokens[0].get("kind") is not None
                    or pending_tokens[0].get("continuation") is not None
                    or delta.get("marking_delta_ref") != delta_ref
                    or delta.get("net_instance_ref") != net_ref
                    or delta.get("transition_firing_refs") != []
                    or delta.get("mechanical_firing_refs")
                    != [metadata.get(
                        "fault_mechanical_firing_ref")]
                    or delta.get("operation_binding_refs") != []
                    or delta.get("consumed_refs") != claimed_refs
                    or delta.get("deposited_refs") != []
                    or delta.get(
                        "provider_submission_unknown_refs") != []
                    or delta.get("phase") != "claim"
                    or delta.get("operation_fault_refs")
                    != [control_ref]
                    or metadata.get("admission_closure_digest")
                    != canonical_text(admission_material)):
                raise RegistryConflict(
                    "fault mechanical firing is not an exact route-scoped "
                    "claim closure")

        elif item.object_type == "fault_mechanical_settlement/v1":
            self_ref(item, "fault_mechanical_settlement_ref")
            exact_fields = (
                ("transition_firing_ref", "transition_firing/v2"),
                ("fault_sidecar_action_ref", "fault_sidecar_action/v1"),
                ("operation_fault_ref", "operation_fault/v1"),
                ("fault_route_binding_ref", "fault_route_binding/v1"),
                ("marking_delta_ref", "marking_delta/v1"),
                ("successor_checkpoint_ref", "marking_checkpoint/v1"),
            )
            root_ref = metadata.get("root_fault_chain_ref")
            transaction_ref = metadata.get("transaction_ref")
            emitted = exact_refs(
                metadata.get("emitted_token_refs"), nonempty=True,
                expected_type="petri_token/v1")
            action_ref = metadata.get("fault_sidecar_action_ref")
            action = (version_metadata(
                str(action_ref.get("version_id", "")),
                "fault_sidecar_action/v1")
                if isinstance(action_ref, Mapping) else None)
            if (any(not exact_ref_exists(metadata.get(name), expected)
                    for name, expected in exact_fields)
                    or not isinstance(root_ref, Mapping)
                    or root_ref.get("entity_type") != "fault_chain/v1"
                    or not isinstance(transaction_ref, Mapping)
                    or transaction_ref.get("entity_type")
                    != "transaction/v1"
                    or transaction_ref.get("logical_id")
                    != str(transaction_id)
                    or not emitted
                    or action is None
                    or action.get("operation_fault_ref")
                    != metadata.get("operation_fault_ref")
                    or action.get("root_fault_chain_ref") != root_ref
                    or isinstance(metadata.get("writer_fencing_epoch"), bool)
                    or not isinstance(
                        metadata.get("writer_fencing_epoch"), int)
                    or metadata["writer_fencing_epoch"] < 1):
                raise RegistryConflict(
                    "fault mechanical settlement is not one exact closure")
            continue
            self_ref(item, "fault_mechanical_settlement_ref")
            firing_ref = metadata.get("fault_mechanical_firing_ref")
            route_ref = metadata.get("fault_route_binding_ref")
            control_ref = metadata.get("control_ref")
            net_ref = metadata.get("net_instance_ref")
            admission_checkpoint_ref = metadata.get(
                "admission_checkpoint_ref")
            settlement_checkpoint_ref = metadata.get(
                "settlement_checkpoint_ref")
            delta_ref = metadata.get("settlement_marking_delta_ref")
            firing = (version_metadata(
                str(firing_ref.get("version_id", "")),
                "fault_mechanical_firing/v1")
                if isinstance(firing_ref, Mapping) else None)
            route = (version_metadata(
                str(route_ref.get("version_id", "")),
                "fault_route_binding/v1")
                if isinstance(route_ref, Mapping) else None)
            fault = (version_metadata(
                str(control_ref.get("version_id", "")),
                "operation_fault/v1")
                if isinstance(control_ref, Mapping) else None)
            checkpoint = (version_metadata(
                str(settlement_checkpoint_ref.get("version_id", "")),
                "marking_checkpoint/v1")
                if isinstance(settlement_checkpoint_ref, Mapping)
                else None)
            delta = (version_metadata(
                str(delta_ref.get("version_id", "")),
                "marking_delta/v1")
                if isinstance(delta_ref, Mapping) else None)
            emitted_refs = exact_refs(
                metadata.get("emitted_token_refs"), nonempty=True,
                expected_type="petri_token/v1")
            emitted_tokens = [
                version_metadata(
                    str(value.get("version_id", "")),
                    "petri_token/v1")
                for value in emitted_refs
            ]
            role = metadata.get("transition_role")
            verdict = metadata.get("verdict")
            mechanical = next((
                value for value in (
                    route.get("mechanical_transitions", [])
                    if isinstance(route, Mapping) else [])
                if isinstance(value, Mapping)
                and value.get("role") == role
            ), None)
            expected_route_transition_id = (
                f"{route_ref.get('logical_id')}::{role}"
                if isinstance(route_ref, Mapping) else None)
            emitted_places = sorted(
                str(value.get("place", ""))
                for value in emitted_tokens
                if isinstance(value, Mapping))
            expected_places = sorted(
                str(value) for value in (
                    mechanical.get("output_places", [])
                    if isinstance(mechanical, Mapping) else []))
            checkpoint_tokens = (
                checkpoint.get("token_refs", [])
                if isinstance(checkpoint, Mapping) else [])
            completion_material = {
                name: metadata.get(name)
                for name in _FAULT_MECHANICAL_COMPLETION_FIELDS
            }
            inherited_fields = (
                "net_instance_ref", "admission_checkpoint_ref",
                "fault_route_binding_ref", "control_ref",
                "subnet_template_ref", "template_transition_ref",
                "transition_role", "route_transition_id",
                "fault_terminal_evidence_ref",
                "retry_admission_ref", "verdict",
            )
            completion_events = [
                value for value in events
                if (value.event_type
                    == "fault_mechanical_firing_completed/v1"
                    and value.payload.get(
                        "fault_mechanical_settlement_ref")
                    == metadata.get(
                        "fault_mechanical_settlement_ref"))
            ]
            settled_events = [
                value for value in events
                if (value.event_type
                    == "fault_mechanical_firing_settled/v1"
                    and value.payload.get(
                        "fault_mechanical_settlement_ref")
                    == metadata.get(
                        "fault_mechanical_settlement_ref"))
            ]
            if (metadata.get("fault_mechanical_settlement_id")
                    != str(item.logical_id)
                    or metadata.get(
                        "fault_mechanical_settlement_version_id")
                    != str(item.version_id)
                    or item.producer_invocation_id is not None
                    or len(completion_events) != 1
                    or len(settled_events) != 1
                    or firing is None or route is None
                    or checkpoint is None or delta is None
                    or any(metadata.get(name) != firing.get(name)
                           for name in inherited_fields)
                    or not exact_ref_exists(net_ref, "net_instance/v1")
                    or not exact_ref_exists(
                        admission_checkpoint_ref,
                        "marking_checkpoint/v1")
                    or route.get("binding_ref") != route_ref
                    or route.get("subnet_template_ref")
                    != metadata.get("subnet_template_ref")
                    or mechanical is None
                    or mechanical.get("transition_ref")
                    != metadata.get("template_transition_ref")
                    or verdict != role
                    or mechanical.get("accepted_verdicts") != [role]
                    or metadata.get("route_transition_id")
                    != expected_route_transition_id
                    or any(value is None for value in emitted_tokens)
                    or emitted_places != expected_places
                    or len(emitted_places) != len(set(emitted_places))
                    or any(token.get("net_instance_ref") != net_ref
                           or token.get("producer")
                           != expected_route_transition_id
                           or token.get("consumed_by") is not None
                           or ((token.get("control_ref") is not None)
                               if role == "retry"
                               else (token.get("control_ref")
                                     != control_ref))
                           for token in emitted_tokens
                           if isinstance(token, Mapping))
                    or (role != "retry" and any(
                        token.get("resource_ref") is not None
                        or token.get("work_resource_ref") is not None
                        or token.get("kind") is not None
                        or token.get("faulted") is not False
                        or token.get("override_warning") is not None
                        or token.get("verdict") != verdict
                        or token.get("continuation") is not None
                        for token in emitted_tokens
                        if isinstance(token, Mapping)))
                    or delta.get("marking_delta_ref") != delta_ref
                    or delta.get("net_instance_ref") != net_ref
                    or firing_ref not in delta.get(
                        "mechanical_firing_refs", [])
                    or (not delta.get("transition_firing_refs")
                        and delta.get("operation_binding_refs") != [])
                    or any(ref not in delta.get("consumed_refs", [])
                           for ref in firing.get(
                               "claimed_token_refs", []))
                    or any(ref not in delta.get("deposited_refs", [])
                           for ref in emitted_refs)
                    or delta.get("phase") != "settlement"
                    or control_ref not in delta.get(
                        "operation_fault_refs", [])
                    or checkpoint.get("net_instance_ref") != net_ref
                    or checkpoint.get("previous_checkpoint_ref")
                    != admission_checkpoint_ref
                    or checkpoint.get("settlement_delta_ref")
                    != delta_ref
                    or any(ref not in checkpoint_tokens
                           for ref in emitted_refs)
                    or any(ref in checkpoint_tokens for ref in
                           firing.get("claimed_token_refs", []))
                    or firing_ref not in checkpoint.get(
                        "mechanical_firing_refs", [])
                    or metadata.get("completion_closure_digest")
                    != canonical_text(completion_material)):
                raise RegistryConflict(
                    "fault mechanical settlement is not an exact "
                    "route-scoped token closure")

        elif item.object_type == "operation_binding/v1":
            self_ref(item, "operation_binding_ref")
            exact_scalar_refs = (
                ("principal_ref", None),
                ("team_design_root_ref", None),
                ("authority_decision_ref", None),
            )
            exact_list_fields = (
                "input_binding_refs", "input_schema_refs",
                "output_schema_refs", "discoverable_resource_refs",
                "readable_resource_refs", "output_binding_refs",
                "permitted_write_intent_factory_refs",
            )
            if (any(not exact_ref_exists(metadata.get(name), expected)
                    for name, expected in exact_scalar_refs)
                    or any(not isinstance(metadata.get(name), list)
                           or not exact_refs(metadata.get(name))
                           and metadata.get(name) != []
                           for name in exact_list_fields)):
                raise RegistryConflict(
                    "operation binding lacks exact static current authority")
            origin = metadata.get("origin")
            if origin != "petri_operation":
                raise RegistryConflict(
                    "operation binding origin is not the registered Petri runtime")
            if not exact_ref_exists(
                    metadata.get("operation_spec_ref"),
                    "operation_spec/v1"):
                raise RegistryConflict(
                    "Petri binding lacks its exact operation spec")
            workspace_ref = metadata.get("workspace_binding_ref")
            if (workspace_ref is not None
                    and not exact_ref_exists(
                        workspace_ref, "workspace_binding/v1")):
                raise RegistryConflict(
                    "operation binding workspace ref is not exact")

        elif item.object_type == "output_binding/v1":
            content_schema_ref = metadata.get("content_schema_ref")
            content_schema_id = metadata.get("content_schema_id")
            cardinality = metadata.get("normal_output_cardinality")
            place_ref = metadata.get("place_ref")
            output_port_id = metadata.get("output_port_id")
            operation_spec_ref = metadata.get("opaque_action_ref")
            operation_spec = (version_metadata(
                str(operation_spec_ref.get("version_id", "")),
                "operation_spec/v1")
                if isinstance(operation_spec_ref, Mapping) else None)
            declared_ports = tuple(
                port for port in operation_spec.get("output_ports", ())
                if isinstance(port, Mapping)
                and port.get("port_id") == output_port_id
            ) if operation_spec is not None else ()
            schema_resource = (
                version_metadata(
                    str(place_ref.get("version_id", "")),
                    "resource_version/v1")
                if isinstance(place_ref, Mapping) else None)
            schema_document_exists = bool(
                exact_ref_exists(place_ref, "resource_version/v1")
                and schema_resource is not None
                and schema_resource.get("media_type")
                == "application/schema+json")
            catalog_schema = bool(
                isinstance(content_schema_id, str)
                and exact_ref_exists(
                    content_schema_ref,
                    "registry_type_catalog/v1"))
            resource_schema = bool(
                isinstance(content_schema_id, str)
                and isinstance(content_schema_ref, Mapping)
                and isinstance(place_ref, Mapping)
                and content_schema_ref == {
                    "resource_id": place_ref.get("logical_id"),
                    "resource_version_id": place_ref.get(
                        "version_id"),
                })
            schema_exists = bool(
                schema_document_exists
                and (catalog_schema or resource_schema))
            exact_port = declared_ports[0] if len(declared_ports) == 1 else None
            if (not schema_exists
                    or exact_port is None
                    or exact_port.get("content_schema_ref")
                    != content_schema_ref
                    or exact_port.get("cardinality") != cardinality
                    or not isinstance(cardinality, Mapping)
                    or not isinstance(cardinality.get("minimum"), int)
                    or not isinstance(cardinality.get("maximum"), int)
                    or cardinality["minimum"] < 0
                    or cardinality["maximum"] < cardinality["minimum"]):
                raise RegistryConflict(
                    "output binding lacks a registered schema/cardinality")

        elif item.object_type == "operation_result/v1":
            result_ref = self_ref(item, "operation_result_ref")
            invocation_ref = metadata.get("invocation_ref")
            firing_ref = metadata.get("transition_firing_ref")
            workspace_ref = metadata.get("workspace_access_set_ref")
            evidence_refs = exact_refs(
                metadata.get("provider_attempt_evidence_refs"),
                expected_type="provider_attempt_evidence/v1",
                canonical=False)
            output_refs = exact_refs(
                metadata.get("output_resource_refs"), canonical=False)
            ready = tuple(
                value for value in events
                if value.event_type == "operation_terminal_ready/v1"
                and value.payload.get("operation_result_ref") == result_ref)
            if (not exact_ref_exists(invocation_ref, "invocation/v1")
                    or not exact_ref_exists(
                        firing_ref, "transition_firing/v1")
                    or metadata.get("business_outcome") != "completed"
                    or (workspace_ref is not None
                        and not exact_ref_exists(
                            workspace_ref, "workspace_access_set/v1"))
                    or (metadata.get("provider_attempt_evidence_refs")
                        and not evidence_refs)
                    or (metadata.get("output_resource_refs")
                        and not output_refs)
                    or (
                        len(ready) != 1
                        or ready[0].payload.get("invocation_ref")
                        != invocation_ref
                        or ready[0].payload.get("business_outcome")
                        != metadata.get("business_outcome")
                        or ready[0].payload.get("output_resource_refs")
                        != metadata.get("output_resource_refs"))):
                raise RegistryConflict(
                    "operation result lacks its exact current terminal closure")
            # HISTORICAL_INERT: the retired digest/fault-result validator
            # below is unreachable after the current exact-ref closure.
            continue
            result_ref = self_ref(item, "operation_result_ref")
            invocation_ref = metadata.get("invocation_ref")
            fault_ref = metadata.get("fault_ref")
            unknown_ref = metadata.get(
                "provider_submission_unknown_ref")
            outcome = metadata.get("terminal_outcome")
            invocation = (version_metadata(
                str(invocation_ref.get("version_id", "")),
                "invocation/v1")
                if isinstance(invocation_ref, Mapping) else None)
            fault = (version_metadata(
                str(fault_ref.get("version_id", "")),
                "operation_fault/v1")
                if isinstance(fault_ref, Mapping) else None)
            unknown = (version_metadata(
                str(unknown_ref.get("version_id", "")),
                "provider_submission_unknown/v1")
                if isinstance(unknown_ref, Mapping) else None)
            terminal_ready = [
                value for value in events
                if (value.event_type == "operation_terminal_ready/v1"
                    and value.payload.get("operation_result_ref")
                    == result_ref)
            ]
            if (invocation is None
                    or len(terminal_ready) != 1
                    or terminal_ready[0].payload.get(
                        "terminal_outcome") != outcome
                    or terminal_ready[0].payload.get(
                        "provider_submission_unknown_ref")
                    != unknown_ref):
                raise RegistryConflict(
                    "operation result lacks one exact terminal-ready closure")
            if outcome == "uncertain":
                uncertainty_fields = (
                    "provider_attempt_ref", "llm_call_ref",
                    "invocation_ref", "operation_binding_ref",
                    "llm_execution_target_ref", "request_resource_ref",
                    "terminal_delivery_ref", "dispatch_event_id",
                    "diagnostic_resource_ref", "uncertainty_kind",
                )
                uncertainty_material = {
                    name: unknown.get(name)
                    for name in uncertainty_fields
                } if isinstance(unknown, Mapping) else {}
                attempt_ref = (unknown.get("provider_attempt_ref")
                               if isinstance(unknown, Mapping)
                               else None)
                attempt = (version_metadata(
                    str(attempt_ref.get("version_id", "")),
                    "provider_attempt_spec/v1")
                    if isinstance(attempt_ref, Mapping) else None)
                if (unknown is None or fault is None or attempt is None
                        or not exact_ref_exists(
                            unknown_ref,
                            "provider_submission_unknown/v1")
                        or not exact_ref_exists(
                            fault_ref, "operation_fault/v1")
                        or metadata.get("output_resource_refs") != []
                        or metadata.get("retry_admission_ref") is not None
                        or not exact_ref_exists(metadata.get(
                            "fault_terminal_witness_ref"),
                            "fault_terminal_witness/v1")
                        or unknown.get(
                            "provider_submission_unknown_ref")
                        != unknown_ref
                        or unknown.get("invocation_ref")
                        != invocation_ref
                        or unknown.get("operation_binding_ref")
                        != invocation.get("operation_binding_ref")
                        or unknown.get("uncertainty_kind")
                        != "possibly_submitted_response_unknown"
                        or unknown.get("uncertainty_digest")
                        != canonical_text(uncertainty_material)
                        or attempt.get("provider_attempt_ref")
                        != attempt_ref
                        or attempt.get("invocation_ref")
                        != invocation_ref
                        or attempt.get("operation_binding_ref")
                        != invocation.get("operation_binding_ref")
                        or fault.get("invocation_ref")
                        != invocation_ref
                        or fault.get("operation_binding_ref")
                        != invocation.get("operation_binding_ref")
                        or fault.get("boundary") != "provider"
                        or fault.get("cause_class")
                        != "submitted_outcome_unknown"
                        or fault.get("certainty")
                        != "unknown_requires_reconciliation"
                        or fault.get(
                            "provider_submission_unknown_ref")
                        != unknown_ref):
                    raise RegistryConflict(
                        "uncertain operation result differs from its exact "
                        "provider witness/reconcile fault")
            elif unknown_ref is not None:
                raise RegistryConflict(
                    "ordinary operation result cites provider uncertainty")

        elif item.object_type == "petri_token/v1":
            self_ref(item, "petri_token_ref")

        elif item.object_type == "operation_fault/v1":
            self_ref(item, "operation_fault_ref")
            invocation_ref = metadata.get("invocation_ref")
            firing_ref = metadata.get("transition_firing_ref")
            root_ref = metadata.get("root_fault_chain_ref")
            optional_refs = (
                ("provider_attempt_evidence_ref",
                 "provider_attempt_evidence/v1"),
                ("provider_submission_unknown_ref",
                 "provider_submission_unknown/v1"),
                ("fault_terminal_evidence_ref",
                 "fault_terminal_witness/v1"),
                ("business_firing_settlement_ref",
                 "transition_firing_settled/v1"),
                ("fault_sidecar_action_ref", "fault_sidecar_action/v1"),
            )
            business_ref = metadata.get(
                "business_firing_settlement_ref")
            action_ref = metadata.get("fault_sidecar_action_ref")
            if (not exact_ref_exists(invocation_ref, "invocation/v1")
                    or not exact_ref_exists(
                        firing_ref, "transition_firing/v1")
                    or not isinstance(root_ref, Mapping)
                    or root_ref.get("entity_type") != "fault_chain/v1"
                    or any(metadata.get(name) is not None
                           and not exact_ref_exists(
                               metadata.get(name), expected)
                           for name, expected in optional_refs)
                    or ((business_ref is None) != (action_ref is None))):
                raise RegistryConflict(
                    "operation fault is outside its exact current evidence")
            continue
            self_ref(item, "fault_ref")
            scalar_refs = (
                ("invocation_ref", "invocation/v1"),
                ("operation_binding_ref", "operation_binding/v1"),
                ("fault_route_binding_ref", "fault_route_binding/v1"),
                ("policy_ref", "fault_disposition_policy/v1"),
                ("authority_decision_ref", "user_authority_decision/v1"),
            )
            if any(not exact_ref_exists(metadata.get(name), expected)
                   for name, expected in scalar_refs):
                raise RegistryConflict("operation fault has unregistered authority")
            firing_ref = metadata.get("transition_firing_ref")
            if firing_ref is not None and not exact_ref_exists(
                    firing_ref, "transition_firing/v1"):
                raise RegistryConflict("operation fault firing ref is unregistered")
            held = exact_refs(metadata.get("held_work_refs"))
            returned = exact_refs(metadata.get("returned_capacity_refs"))
            if {canonical_json(value) for value in held} & {
                    canonical_json(value) for value in returned}:
                raise RegistryConflict("fault held work overlaps returned capacity")
            invocation = version_metadata(
                str(metadata.get("invocation_ref", {}).get("version_id", "")),
                "invocation/v1")
            policy = version_metadata(
                str(metadata.get("policy_ref", {}).get("version_id", "")),
                "fault_disposition_policy/v1")
            expected = {
                "operation_binding_ref": metadata.get(
                    "operation_binding_ref"),
                "fault_route_binding_ref": metadata.get(
                    "fault_route_binding_ref"),
                "fault_policy_ref": metadata.get("policy_ref"),
                "authority_decision_ref": metadata.get(
                    "authority_decision_ref"),
                "root_fault_chain_id": metadata.get("root_fault_chain_id"),
                "retry_ordinal": metadata.get("retry_ordinal"),
            }
            material = {
                "schema_version": "operation_fault_dependency/v1",
                **{name: metadata.get(name) for name in (
                    "invocation_ref", "transition_firing_ref",
                    "operation_binding_ref", "fault_route_binding_ref",
                    "policy_ref", "authority_decision_ref", "boundary",
                    "cause_class", "certainty",
                    "provider_submission_unknown_ref",
                    "root_fault_chain_id", "retry_ordinal",
                    "held_work_refs", "returned_capacity_refs",
                    "normal_output_resource_refs")},
            }
            is_unknown = (
                metadata.get("boundary") == "provider"
                and metadata.get("cause_class")
                == "submitted_outcome_unknown"
                and metadata.get("certainty")
                == "unknown_requires_reconciliation")
            unknown_ref = metadata.get(
                "provider_submission_unknown_ref")
            if (invocation is None
                    or any(invocation.get(name) != value
                           for name, value in expected.items())
                    or invocation.get("own_transition_firing_ref") != firing_ref
                    or (is_unknown and not exact_ref_exists(
                        unknown_ref,
                        "provider_submission_unknown/v1"))
                    or (not is_unknown and unknown_ref is not None)
                    or policy is None
                    or policy.get("routing_rule")
                    != "petri_selects_mechanical_role"
                    or metadata.get("dependency_fingerprint")
                    != canonical_text(material)):
                raise RegistryConflict(
                    "operation fault differs from invocation/policy authority")

        elif item.object_type == "fault_retry_admission/v1":
            self_ref(item, "admission_ref")
            fault_ref = metadata.get("operation_fault_ref")
            evidence_ref = metadata.get(
                "fault_terminal_evidence_ref")
            mechanical_firing_ref = metadata.get(
                "fault_mechanical_firing_ref")
            policy_ref = metadata.get("policy_ref")
            route_ref = metadata.get("fault_route_binding_ref")
            previous_ref = metadata.get("previous_invocation_ref")
            work = exact_refs(
                metadata.get("readmitted_work_refs"), nonempty=True)
            fault = (version_metadata(
                str(fault_ref.get("version_id", "")), "operation_fault/v1")
                if isinstance(fault_ref, Mapping) else None)
            evidence = (version_metadata(
                str(evidence_ref.get("version_id", "")),
                "fault_terminal_witness/v1")
                if isinstance(evidence_ref, Mapping) else None)
            mechanical_firing = (version_metadata(
                str(mechanical_firing_ref.get("version_id", "")),
                "fault_mechanical_firing/v1")
                if isinstance(mechanical_firing_ref, Mapping) else None)
            policy = (version_metadata(
                str(policy_ref.get("version_id", "")),
                "fault_disposition_policy/v1")
                if isinstance(policy_ref, Mapping) else None)
            limits = {
                value.get("fault_class"): value.get(
                    "per_root_chain_limit")
                for value in (policy or {}).get("retry_limits", [])
            }
            material = {
                "schema_version": "fault_retry_admission_dependency/v1",
                **{name: metadata.get(name) for name in (
                    "operation_fault_ref", "root_fault_chain_id",
                    "fault_terminal_evidence_ref",
                    "fault_mechanical_firing_ref",
                    "next_retry_ordinal", "policy_ref",
                    "fault_route_binding_ref", "pinned_policy_witness",
                    "readmitted_work_refs", "previous_invocation_ref")},
            }
            if (fault is None or policy is None or evidence is None
                    or mechanical_firing is None
                    or not exact_ref_exists(previous_ref, "invocation/v1")
                    or not exact_ref_exists(
                        evidence_ref, "fault_terminal_witness/v1")
                    or not exact_ref_exists(
                        mechanical_firing_ref,
                        "fault_mechanical_firing/v1")
                    or fault.get("policy_ref") != policy_ref
                    or fault.get("fault_route_binding_ref") != route_ref
                    or evidence.get("operation_fault_ref") != fault_ref
                    or mechanical_firing.get("control_ref") != fault_ref
                    or mechanical_firing.get("transition_role") != "retry"
                    or mechanical_firing.get(
                        "fault_terminal_evidence_ref") != evidence_ref
                    or mechanical_firing.get("retry_admission_ref")
                    != metadata.get("admission_ref")
                    or fault.get("root_fault_chain_id")
                    != metadata.get("root_fault_chain_id")
                    or fault.get("retry_ordinal", -1) + 1
                    != metadata.get("next_retry_ordinal")
                    or metadata.get("next_retry_ordinal", 0)
                    > int(limits.get(fault.get("boundary"), -1))
                    or fault.get("held_work_refs") != work
                    or metadata.get("pinned_policy_witness") is None):
                raise RegistryConflict(
                    "fault retry admission exceeds or changes pinned authority")

        elif item.object_type == "fault_terminal_witness/v1":
            self_ref(item, "witness_ref")
            fault_ref = metadata.get("operation_fault_ref")
            route_ref = metadata.get("fault_route_binding_ref")
            pending_ref = metadata.get("fault_pending_place_ref")
            terminal_ref = metadata.get("route_terminal_place_ref")
            fault = (version_metadata(
                str(fault_ref.get("version_id", "")), "operation_fault/v1")
                if isinstance(fault_ref, Mapping) else None)
            route = (version_metadata(
                str(route_ref.get("version_id", "")),
                "fault_route_binding/v1")
                if isinstance(route_ref, Mapping) else None)
            invocation_ref = (
                fault.get("invocation_ref")
                if isinstance(fault, Mapping) else None)
            invocation = (version_metadata(
                str(invocation_ref.get("version_id", "")),
                "invocation/v1")
                if isinstance(invocation_ref, Mapping) else None)
            template_ref = (
                route.get("subnet_template_ref")
                if isinstance(route, Mapping) else None)
            template = (version_metadata(
                str(template_ref.get("version_id", "")),
                "fault_subnet_template/v1")
                if isinstance(template_ref, Mapping) else None)
            pending = (version_metadata(
                str(pending_ref.get("version_id", "")),
                "fault_petri_place/v1")
                if isinstance(pending_ref, Mapping) else None)
            terminal = (version_metadata(
                str(terminal_ref.get("version_id", "")),
                "fault_petri_place/v1")
                if isinstance(terminal_ref, Mapping) else None)
            held = exact_refs(
                metadata.get("held_work_refs"),
                expected_type="petri_token/v1")
            returned = exact_refs(
                metadata.get("returned_capacity_refs"),
                expected_type="petri_token/v1")
            firing_ref = (
                fault.get("transition_firing_ref")
                if isinstance(fault, Mapping) else None)
            firing = (version_metadata(
                str(firing_ref.get("version_id", "")),
                "transition_firing/v1")
                if isinstance(firing_ref, Mapping) else None)
            claimed = (exact_refs(
                firing.get("claimed_input_refs"),
                expected_type="petri_token/v1")
                if isinstance(firing, Mapping) else [])
            witness_ref = {
                "entity_type": "fault_terminal_witness/v1",
                "logical_id": str(item.logical_id),
                "version_id": str(item.version_id),
            }
            if (fault is None or route is None or invocation is None
                    or template is None or pending is None
                    or terminal is None or firing is None
                    or fault.get("fault_terminal_evidence_ref")
                    != witness_ref
                    or invocation.get("fault_route_binding_ref")
                    != route_ref
                    or route.get("fault_pending_place_ref")
                    != pending_ref
                    or pending.get("subnet_template_ref")
                    != template_ref
                    or pending.get("role") != "fault_pending"
                    or terminal.get("subnet_template_ref")
                    != template_ref
                    or terminal.get("role")
                    != "structural_terminal_intake"
                    or terminal_ref not in template.get("place_refs", [])
                    or metadata.get("evidence_kind")
                    != "generic_fault_terminal_evidence"
                    or metadata.get("application_terminal_contract")
                    != "schema_bound_resource_after_mechanical_settlement"
                    or {canonical_json(value) for value in held}
                    & {canonical_json(value) for value in returned}
                    or {canonical_json(value)
                        for value in (*held, *returned)}
                    != {canonical_json(value) for value in claimed}):
                raise RegistryConflict(
                    "generic fault terminal evidence is outside its route")

def validate_operation_event(context, pending):
    db = context.db
    event_store = context.event_store
    payload = pending.payload
    event_type = pending.event_type
    task_id = context.task_id
    exact_ref_exists = context.exact_ref_exists
    exact_resource_ref_exists = context.exact_resource_ref_exists
    version_metadata = context.version_metadata
    RegistryConflict = facade.RegistryConflict
    RegistryCorruptError = facade.RegistryCorruptError
    _exact_object_metadata = facade._exact_object_metadata
    _version_ref_from_payload = facade._version_ref_from_payload
    verified_adoption_head = facade.verified_adoption_head
    if event_type == "registered_operation_completion_recorded/v1":
        invocation_ref = payload.get("invocation_ref")
        firing_ref = payload.get("transition_firing_ref")
        lease_ref = payload.get("operation_execution_lease_ref")
        admission_ref = payload.get("firing_admission_ref")
        net_ref = payload.get("net_instance_ref")
        checkpoint_ref = payload.get("admission_marking_checkpoint_ref")
        spec_ref = payload.get("operation_spec_ref")
        binding_ref = payload.get("operation_binding_ref")
        typed_refs = (
            (invocation_ref, "invocation/v1"),
            (firing_ref, "transition_firing/v1"),
            (lease_ref, "operation_execution_lease/v1"),
            (admission_ref, "firing_admission/v1"),
            (net_ref, "net_instance/v1"),
            (checkpoint_ref, "marking_checkpoint/v1"),
            (spec_ref, "operation_spec/v1"),
            (binding_ref, "operation_binding/v1"),
            (payload.get("run_ref"), "native_run_identity/v1"),
        )
        if any(not exact_ref_exists(ref, object_type)
               for ref, object_type in typed_refs):
            raise RegistryConflict(
                "registered-operation completion has an unregistered exact ref")
        invocation = version_metadata(
            str(invocation_ref["version_id"]), "invocation/v1")
        firing = version_metadata(
            str(firing_ref["version_id"]), "transition_firing/v1")
        lease = version_metadata(
            str(lease_ref["version_id"]), "operation_execution_lease/v1")
        admission = version_metadata(
            str(admission_ref["version_id"]), "firing_admission/v1")
        binding = version_metadata(
            str(binding_ref["version_id"]), "operation_binding/v1")
        checkpoint = version_metadata(
            str(checkpoint_ref["version_id"]), "marking_checkpoint/v1")
        start_rows = db.execute(
            "SELECT event_id,payload_json,writer_fencing_epoch FROM events "
            "WHERE aggregate_id=? AND event_type="
            "'operation_execution_started/v1'",
            (str(lease_ref["logical_id"]),),
        ).fetchall()
        prior_completion_rows = db.execute(
            "SELECT event_id,payload_json FROM events WHERE event_type=?",
            (event_type,),
        ).fetchall()
        crossed = [row for row in prior_completion_rows
                   if (json.loads(row["payload_json"]).get(
                           "transition_firing_ref") == firing_ref
                       or json.loads(row["payload_json"]).get(
                           "operation_execution_lease_ref") == lease_ref)]
        outputs = payload.get("ordered_outputs")
        if (invocation is None or firing is None or lease is None
                or admission is None or binding is None or checkpoint is None
                or len(start_rows) != 1 or crossed
                or pending.aggregate_id != str(lease_ref["logical_id"])
                or pending.aggregate_type != "operation_execution_lease"
                or pending.producer_invocation_id is None
                or str(pending.producer_invocation_id)
                != str(invocation_ref["logical_id"])
                or pending.payload_schema_ref
                != "registry_v1/registered_operation_completion_recorded/v1"
                or invocation.get("invocation_ref") != invocation_ref
                or invocation.get("own_transition_firing_ref") != firing_ref
                or invocation.get("operation_execution_lease_ref") != lease_ref
                or invocation.get("operation_binding_ref") != binding_ref
                or invocation.get("net_instance_ref") != net_ref
                or invocation.get("admission_marking_checkpoint_ref")
                != checkpoint_ref
                or firing.get("transition_firing_ref") != firing_ref
                or firing.get("firing_admission_ref") != admission_ref
                or firing.get("operation_binding_ref") != binding_ref
                or firing.get("net_instance_ref") != net_ref
                or firing.get("admission_marking_checkpoint_ref")
                != checkpoint_ref
                or lease.get("operation_execution_lease_ref") != lease_ref
                or lease.get("invocation_ref") != invocation_ref
                or admission.get("firing_admission_ref") != admission_ref
                or admission.get("transition_firing_ref") != firing_ref
                or admission.get("invocation_ref") != invocation_ref
                or admission.get("operation_execution_lease_ref") != lease_ref
                or admission.get("admission_marking_checkpoint_ref")
                != checkpoint_ref
                or admission.get("writer_fencing_epoch")
                != payload.get("admission_writer_fencing_epoch")
                or lease.get("writer_fencing_epoch")
                != payload.get("admission_writer_fencing_epoch")
                or binding.get("operation_binding_ref") != binding_ref
                or binding.get("operation_spec_ref") != spec_ref
                or checkpoint.get("marking_checkpoint_ref") != checkpoint_ref
                or checkpoint.get("net_instance_ref") != net_ref
                or not isinstance(outputs, list)
                or [item.get("ordinal") for item in outputs
                    if isinstance(item, Mapping)] != list(range(len(outputs)))):
            raise RegistryConflict(
                "registered-operation completion crosses execution authority")
        start = json.loads(start_rows[0]["payload_json"])
        if (str(start_rows[0]["event_id"])
                != payload.get("operation_start_event_id")
                or start.get("invocation_ref") != invocation_ref
                or start.get("transition_firing_ref") != firing_ref
                or start.get("operation_execution_lease_ref") != lease_ref
                or start.get("operation_spec_ref") != spec_ref
                or start.get("operation_binding_ref") != binding_ref
                or start.get("admission_writer_fencing_epoch")
                != payload.get("admission_writer_fencing_epoch")):
            raise RegistryConflict(
                "registered-operation completion differs from exact Start")
        if any(not isinstance(item, Mapping) for item in outputs):
            raise RegistryConflict(
                "registered-operation completion output order is malformed")
        binding_outputs = binding.get("output_binding_refs", [])
        resource_ids = set()
        for item in outputs:
            output_binding_ref = item.get("output_binding_ref")
            resource_ref = item.get("resource_ref")
            if (not exact_ref_exists(output_binding_ref, "output_binding/v1")
                    or not exact_resource_ref_exists(resource_ref)
                    or output_binding_ref not in binding_outputs):
                raise RegistryConflict(
                    "registered-operation completion output is outside binding")
            output_binding = version_metadata(
                str(output_binding_ref["version_id"]), "output_binding/v1")
            resource = version_metadata(
                str(resource_ref["resource_version_id"]),
                "resource_version/v1")
            if output_binding is None or resource is None:
                raise RegistryConflict(
                    "registered-operation completion output object is absent")
            origin = resource.get("origin")
            descriptors = resource.get("descriptors", {})
            resource_key = str(resource_ref["resource_version_id"])
            if (resource_key in resource_ids
                    or output_binding.get("output_port_id")
                    != item.get("port_id")
                    or resource.get("producer_ref") != invocation_ref
                    or not isinstance(origin, Mapping)
                    or origin.get("kind") != "petri_output"
                    or origin.get("primary_ref") != output_binding_ref
                    or origin.get("secondary_ref")
                    != invocation.get("activation_ref")
                    or not isinstance(descriptors, Mapping)
                    or descriptors.get(
                        "output_port_id", item.get("port_id"))
                    != item.get("port_id")
                    or descriptors.get(
                        "output_outcome_id",
                        payload.get("selected_outcome_id"))
                    != payload.get("selected_outcome_id")):
                raise RegistryConflict(
                    "registered-operation completion output origin differs")
            resource_ids.add(resource_key)

    if event_type == "operation_execution_started/v1":
        invocation_ref = payload.get("invocation_ref")
        lease_ref = payload.get("operation_execution_lease_ref")
        spec_ref = payload.get("operation_spec_ref")
        binding_ref = payload.get("operation_binding_ref")
        firing_ref = payload.get("transition_firing_ref")
        executable_ref = payload.get(
            "executable_transition_binding_ref")
        declaration_terminal_ref = payload.get(
            "declaration_terminal_delivery_ref")
        invocation = (version_metadata(
            str(invocation_ref.get("version_id", "")),
            "invocation/v1")
            if isinstance(invocation_ref, Mapping) else None)
        lease = (version_metadata(
            str(lease_ref.get("version_id", "")),
            "operation_execution_lease/v1")
            if isinstance(lease_ref, Mapping) else None)
        spec = (version_metadata(
            str(spec_ref.get("version_id", "")),
            "operation_spec/v1")
            if isinstance(spec_ref, Mapping) else None)
        binding = (version_metadata(
            str(binding_ref.get("version_id", "")),
            "operation_binding/v1")
            if isinstance(binding_ref, Mapping) else None)
        firing = (version_metadata(
            str(firing_ref.get("version_id", "")),
            "transition_firing/v1")
            if isinstance(firing_ref, Mapping) else None)
        executable = (version_metadata(
            str(executable_ref.get("version_id", "")),
            "executable_transition_binding/v1")
            if isinstance(executable_ref, Mapping) else None)
        input_binding_refs = payload.get("input_binding_refs")
        input_resource_refs = payload.get("input_resource_refs")
        claimed_input_refs = payload.get("claimed_input_refs")
        if (invocation is None or lease is None or spec is None
                or binding is None or firing is None
                or executable is None
                or pending.aggregate_id
                != str(lease_ref.get("logical_id", ""))
                or pending.producer_invocation_id is None
                or str(pending.producer_invocation_id)
                != str(invocation_ref.get("logical_id", ""))
                or invocation.get("operation_execution_lease_ref")
                != lease_ref
                or invocation.get("operation_binding_ref") != binding_ref
                or invocation.get("own_transition_firing_ref") != firing_ref
                or invocation.get("agent_ref")
                != payload.get("agent_ref")
                or invocation.get("principal_ref")
                != payload.get("principal_ref")
                or invocation.get("authority_decision_ref")
                != payload.get("authority_decision_ref")
                or lease.get("invocation_ref") != invocation_ref
                or binding.get("operation_spec_ref") != spec_ref
                or firing.get("operation_binding_ref") != binding_ref
                or firing.get("agent_ref") != payload.get("agent_ref")
                or firing.get("claimed_input_refs") != claimed_input_refs
                or executable.get("operation_binding_ref") != binding_ref
                or executable.get("agent_ref")
                != payload.get("agent_ref")
                or executable.get("net_instance_ref")
                != invocation.get("net_instance_ref")
                or firing.get("net_instance_ref")
                != invocation.get("net_instance_ref")
                or executable.get("node_ref")
                != invocation.get("own_node_ref")
                or executable.get("transition_id")
                != firing.get("transition_id")
                or not isinstance(input_binding_refs, list)
                or not isinstance(input_resource_refs, list)
                or not isinstance(claimed_input_refs, list)):
            raise RegistryConflict(
                "operation start differs from exact execution closure")

        declaration_resource_ref = executable.get(
            "declaration_resource_ref")
        execution_net_ref = invocation.get("net_instance_ref")
        if not exact_resource_ref_exists(declaration_resource_ref):
            raise RegistryConflict(
                "operation start declaration is not one exact "
                "Registry resource identity")
        if declaration_terminal_ref is None:
            structural_rows = db.execute(
                "SELECT aggregate_id,payload_json FROM events "
                "WHERE event_type='structural_growth_adopted/v1'",
            ).fetchall()
            structural_matches = [
                row for row in structural_rows
                if (row["aggregate_id"]
                    == str(execution_net_ref.get("logical_id", ""))
                    and json.loads(row["payload_json"]).get(
                        "candidate_net_ref") == execution_net_ref
                    and json.loads(row["payload_json"]).get(
                        "lowered_declaration_ref")
                    == declaration_resource_ref)
            ]
            native_match = False
            if not structural_matches:
                try:
                    native_net_ref = _version_ref_from_payload(
                        execution_net_ref)
                    current_net_ref = verified_adoption_head(
                        event_store, event_store.catalog, task_id, _db=db)
                    current_net = _exact_object_metadata(
                        event_store, current_net_ref,
                        expected_type="net_instance/v1", db=db)
                except (KeyError, TypeError, ValueError,
                        RegistryCorruptError) as exc:
                    raise RegistryConflict(
                        "native-launch operation start has an "
                        "invalid current adopted net closure") from exc
                native_match = (
                    current_net_ref == native_net_ref
                    and current_net.get(
                        "team_net_declaration_resource_ref")
                    == declaration_resource_ref)
            if sum((
                    native_match,
                    len(structural_matches) == 1,
            )) != 1:
                raise RegistryConflict(
                    "operation start lacks its exact declaration "
                    "authority")
        else:
            terminal = (version_metadata(
                str(declaration_terminal_ref.get("version_id", "")),
                "resource_delivery/v1")
                if isinstance(declaration_terminal_ref, Mapping)
                else None)
            owner_ref = (terminal.get("context_ref")
                         if terminal is not None else None)
            owner = (version_metadata(
                str(owner_ref.get("version_id", "")),
                "invocation/v1")
                if isinstance(owner_ref, Mapping) else None)
            authorized_ref = (terminal.get("previous_delivery_ref")
                              if terminal is not None else None)
            authorized = (version_metadata(
                str(authorized_ref.get("version_id", "")),
                "resource_delivery/v1")
                if isinstance(authorized_ref, Mapping) else None)
            prepared_ref = (authorized.get("previous_delivery_ref")
                            if authorized is not None else None)
            boundary_ref = (terminal.get("boundary_receipt_ref")
                            if terminal is not None else None)
            boundary = (version_metadata(
                str(boundary_ref.get("version_id", "")),
                "delivery_boundary_receipt/v1")
                if isinstance(boundary_ref, Mapping) else None)
            witness_ref = (terminal.get("witness_ref")
                           if terminal is not None else None)
            witness = (version_metadata(
                str(witness_ref.get("version_id", "")),
                "resource_release_witness/v1")
                if isinstance(witness_ref, Mapping) else None)
            declaration_row = db.execute(
                "SELECT size FROM objects WHERE version_id=? "
                "AND object_type='resource_version/v1'",
                (str(declaration_resource_ref.get(
                    "resource_version_id", "")),),
            ).fetchone() if isinstance(
                declaration_resource_ref, Mapping) else None
            if (not exact_ref_exists(
                        declaration_terminal_ref,
                        "resource_delivery/v1")
                    or terminal is None
                    or owner is None
                    or authorized is None
                    or boundary is None
                    or witness is None
                    or not exact_ref_exists(owner_ref, "invocation/v1")
                    or not exact_ref_exists(
                        authorized_ref, "resource_delivery/v1")
                    or not exact_ref_exists(
                        prepared_ref, "resource_delivery/v1")
                    or not exact_ref_exists(
                        boundary_ref,
                        "delivery_boundary_receipt/v1")
                    or not exact_ref_exists(
                        witness_ref, "resource_release_witness/v1")
                    or declaration_row is None
                    or owner.get("invocation_ref") != owner_ref
                    or owner.get("net_instance_ref")
                    != execution_net_ref
                    or terminal.get("state") != "acknowledged"
                    or terminal.get("boundary") != "petri_input"
                    or terminal.get("resource_ref")
                    != declaration_resource_ref
                    or terminal.get("context_ref") != owner_ref
                    or terminal.get("previous_delivery_ref")
                    != authorized_ref
                    or authorized.get("state")
                    != "release_authorized"
                    or authorized.get("boundary") != "petri_input"
                    or authorized.get("resource_ref")
                    != declaration_resource_ref
                    or authorized.get("context_ref") != owner_ref
                    or authorized.get("witness_ref") != witness_ref
                    or boundary.get("delivery_ref") != authorized_ref
                    or boundary.get("witness_ref") != witness_ref
                    or boundary.get("boundary") != "petri_input"
                    or boundary.get("outcome") != "acknowledged"
                    or isinstance(
                        boundary.get("positive_byte_count"), bool)
                    or not isinstance(
                        boundary.get("positive_byte_count"), int)
                    or int(boundary["positive_byte_count"]) < 0
                    or int(boundary["positive_byte_count"])
                    != int(declaration_row["size"])
                    or witness.get("delivery_ref") != prepared_ref
                    or witness.get("resource_ref")
                    != declaration_resource_ref
                    or witness.get("boundary") != "petri_input"
                    or witness.get("context_ref") != owner_ref
                    or witness.get("authorization_ref")
                    != terminal.get("authorization_ref")):
                raise RegistryConflict(
                    "operation start declaration delivery closure is invalid")

            acknowledgement_rows = db.execute(
                "SELECT aggregate_id,producer_invocation_id,payload_json "
                "FROM events WHERE event_type="
                "'resource_delivery_acknowledged/v1'",
            ).fetchall()
            acknowledgements = [
                (row, json.loads(row["payload_json"]))
                for row in acknowledgement_rows
                if json.loads(row["payload_json"]).get(
                    "terminal_delivery_ref")
                == declaration_terminal_ref]
            release_rows = db.execute(
                "SELECT aggregate_id,producer_invocation_id,payload_json "
                "FROM events WHERE event_type="
                "'resource_release_authorized/v1'",
            ).fetchall()
            releases = [
                (row, json.loads(row["payload_json"]))
                for row in release_rows
                if (json.loads(row["payload_json"]).get(
                        "delivery_ref") == prepared_ref
                    and json.loads(row["payload_json"]).get(
                        "witness_ref") == witness_ref)]
            observed_rows = db.execute(
                "SELECT aggregate_id,producer_invocation_id,payload_json "
                "FROM events WHERE event_type='observed_read/v1'",
            ).fetchall()
            observed_reads = [
                (row, json.loads(row["payload_json"]))
                for row in observed_rows
                if (row["aggregate_id"]
                    == str(declaration_terminal_ref.get(
                        "logical_id", ""))
                    and json.loads(row["payload_json"]).get(
                        "boundary_receipt_ref") == boundary_ref
                    and json.loads(row["payload_json"]).get(
                        "witness_ref") == witness_ref
                    and json.loads(row["payload_json"]).get(
                        "resource_ref")
                    == declaration_resource_ref)]
            if (len(acknowledgements) != 1
                    or len(releases) != 1
                    or len(observed_reads) != 1):
                raise RegistryConflict(
                    "operation start declaration delivery facts are not unique")
            acknowledgement_row, acknowledgement = acknowledgements[0]
            release_row, release = releases[0]
            observed_row, observed = observed_reads[0]
            owner_id = str(owner_ref.get("logical_id", ""))
            terminal_id = str(declaration_terminal_ref.get(
                "logical_id", ""))
            if (acknowledgement_row["aggregate_id"] != terminal_id
                    or str(acknowledgement_row[
                        "producer_invocation_id"]) != owner_id
                    or acknowledgement.get("delivery_ref")
                    != authorized_ref
                    or acknowledgement.get("witness_ref")
                    != witness_ref
                    or acknowledgement.get("boundary_receipt_ref")
                    != boundary_ref
                    or acknowledgement.get("outcome")
                    != "acknowledged"
                    or release_row["aggregate_id"] != terminal_id
                    or str(release_row[
                        "producer_invocation_id"]) != owner_id
                    or release.get("resource_ref")
                    != declaration_resource_ref
                    or release.get("boundary") != "petri_input"
                    or observed_row["aggregate_id"] != terminal_id
                    or str(observed_row[
                        "producer_invocation_id"]) != owner_id
                    or observed.get("actor_invocation_ref") != owner_ref
                    or observed.get("net_ref") != execution_net_ref
                    or observed.get("delivery_id") != terminal_id
                    or observed.get("boundary") != "petri_input"
                    or observed.get("authorization_ref")
                    != terminal.get("authorization_ref")):
                raise RegistryConflict(
                    "operation start declaration delivery facts differ")
        binding_inputs = binding.get("input_binding_refs", [])
        expected_resource_bindings = [{
            "entity_type": "resource_version/v1",
            "logical_id": resource_ref.get("resource_id"),
            "version_id": resource_ref.get("resource_version_id"),
        } for resource_ref in input_resource_refs
            if isinstance(resource_ref, Mapping)]

        def exact_input_binding(
                input_binding_ref: object,
                expected_resource_ref: object) -> bool:
            if (input_binding_ref == expected_resource_ref
                    and input_binding_ref in binding_inputs):
                return True
            if (not isinstance(input_binding_ref, Mapping)
                    or input_binding_ref not in claimed_input_refs
                    or input_binding_ref.get("entity_type")
                    != "petri_token/v1"
                    or not isinstance(expected_resource_ref, Mapping)):
                return False
            token = version_metadata(
                str(input_binding_ref.get("version_id", "")),
                "petri_token/v1")
            return bool(
                token is not None
                and token.get("petri_token_ref") == input_binding_ref
                and token.get("net_instance_ref")
                == firing.get("net_instance_ref")
                and token.get("consumer") in {
                    None, firing.get("transition_id")}
                and token.get("consumed_by") is None)

        if (len(expected_resource_bindings) != len(input_resource_refs)
                or len(input_binding_refs)
                != len(expected_resource_bindings)
                or any(not exact_ref_exists(
                    ref, "resource_version/v1")
                    for ref in expected_resource_bindings)
                or any(not exact_input_binding(binding_input, resource)
                       for binding_input, resource in zip(
                           input_binding_refs,
                           expected_resource_bindings,
                           strict=True))):
            raise RegistryConflict(
                "operation start input files lack exact binding authority")
        workspace_ref = binding.get("workspace_binding_ref")
        current_ordinal_row = db.execute(
            "SELECT MAX(ordinal) FROM events").fetchone()
        current_task_row = db.execute(
            "SELECT sequence FROM task_control_heads WHERE task_id=?",
            (str(task_id),)).fetchone()
        writer_epoch_row = db.execute(
            "SELECT value FROM registry_meta WHERE key='writer_epoch'",
        ).fetchone()
        if (payload.get("admission_registry_ordinal")
                != int(current_ordinal_row[0] or 0)
                or payload.get("admission_task_control_sequence")
                != int(current_task_row["sequence"]
                       if current_task_row is not None else 0)
                or writer_epoch_row is None
                or payload.get("admission_writer_fencing_epoch")
                != int(writer_epoch_row["value"])):
            raise RegistryConflict(
                "operation start admission fence is stale")

__all__ = ('validate_operation_contract_objects', 'validate_operation_event')
