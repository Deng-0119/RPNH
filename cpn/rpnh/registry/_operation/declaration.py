"""Exact declaration hydration for registered Operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import operations as _facade


def _parse_content_schema_ref(
        value: object, *, label: str,
) -> _facade.VersionRef | _facade.ResourceVersionRef:
    if not isinstance(value, Mapping):
        raise _facade.OperationAuthorityError(f"{label} is not an exact schema ref")
    if set(value) == {"entity_type", "logical_id", "version_id"}:
        ref = _facade._parse_ref(value, label=label)
        if ref.entity_type != "registry_type_catalog/v1":
            raise _facade.OperationAuthorityError(
                f"{label} catalog authority has the wrong exact type")
        return ref
    if set(value) == {"resource_id", "resource_version_id"}:
        return _facade._parse_resource_ref(value, label=label)
    raise _facade.OperationAuthorityError(f"{label} is not one exact schema source ref")


def _parse_field_projection(
        value: object, *, label: str,
) -> _facade.OperationFieldProjection:
    if not isinstance(value, Mapping) or set(value) != {
            "producer_path", "consumer_path"}:
        raise _facade.OperationAuthorityError(
            f"{label} is not one closed field projection")
    return _facade.OperationFieldProjection(
        producer_path=value["producer_path"],  # type: ignore[arg-type]
        consumer_path=value["consumer_path"],  # type: ignore[arg-type]
    )


def _parse_input_projection(
        value: object, *, label: str,
) -> _facade.OperationInputProjection:
    if not isinstance(value, Mapping) or set(value) != {
            "producer_content_schema_ref", "field_projection"}:
        raise _facade.OperationAuthorityError(
            f"{label} is not one closed input projection")
    raw_fields = value["field_projection"]
    if not isinstance(raw_fields, list) or not raw_fields:
        raise _facade.OperationAuthorityError(
            f"{label}.field_projection must be a nonempty array")
    return _facade.OperationInputProjection(
        producer_content_schema_ref=value[  # type: ignore[arg-type]
            "producer_content_schema_ref"],
        field_projection=tuple(
            _parse_field_projection(item, label=f"{label}.field_projection[{index}]")
            for index, item in enumerate(raw_fields)),
    )


def _parse_port(
        repository: _facade._OperationAuthorityRepository,
        value: object, *, label: str,
        allow_input_projections: bool,
) -> _facade.OperationPortAuthority:
    required = {
        "port_id", "place", "schema_ref", "content_schema_ref",
        "cardinality", "lease_identity_ref"}
    allowed = required | ({"input_projections"}
                          if allow_input_projections else set())
    if (not isinstance(value, Mapping) or not required.issubset(value)
            or not set(value).issubset(allowed)):
        raise _facade.OperationAuthorityError(f"{label} is not one closed operation port")
    cardinality = value["cardinality"]
    if not isinstance(cardinality, Mapping) or set(cardinality) != {
            "minimum", "maximum"}:
        raise _facade.OperationAuthorityError(f"{label}.cardinality is not closed")
    schema_ref = _facade._parse_ref(
        value["schema_ref"], label=f"{label}.schema_ref")
    if "input_projections" in value:
        raw_projections = value["input_projections"]
        if not isinstance(raw_projections, list) or not raw_projections:
            raise _facade.OperationAuthorityError(
                f"{label}.input_projections must be a nonempty array")
        input_projections = tuple(
            _parse_input_projection(
                item, label=f"{label}.input_projections[{index}]")
            for index, item in enumerate(raw_projections))
    else:
        input_projections = ()
    raw_lease_identity_ref = value["lease_identity_ref"]
    lease_identity_ref = (
        _facade._parse_ref(raw_lease_identity_ref,
                           label=f"{label}.lease_identity_ref")
        if raw_lease_identity_ref is not None else None)
    return _facade.OperationPortAuthority(
        port_id=_facade._require_lexical_id(f"{label}.port_id", value["port_id"]),
        place=value["place"],  # type: ignore[arg-type]
        schema_ref=schema_ref,
        content_schema=repository.registered_content_schema_authority(
            _parse_content_schema_ref(
                value["content_schema_ref"],
                label=f"{label}.content_schema_ref"),
            schema_document_ref=schema_ref),
        minimum=cardinality["minimum"],  # type: ignore[arg-type]
        maximum=cardinality["maximum"],  # type: ignore[arg-type]
        input_projections=input_projections,
        lease_identity_ref=lease_identity_ref,
    )


def _hydrate_spec(
        repository: _facade._OperationAuthorityRepository,
        spec_ref: _facade.VersionRef,
        metadata: Mapping[str, Any],
) -> _facade.OperationSpecAuthority:
    required = {
        "operation_spec_id", "operation_spec_version_id", "operation_spec_ref",
        "operation_id", "executor_key", "transport",
        "implementation_identity", "implementation_contracts",
        "llm_prompt_port_id", "input_ports", "output_ports",
        "allowed_tool_ids",
    }
    if set(metadata) != required:
        raise _facade.OperationAuthorityError("operation_spec/v1 metadata is not closed")
    declaration = _facade.registered_operation_contract(metadata["executor_key"])
    if (metadata["implementation_identity"] != declaration["identity"]
            or metadata["implementation_contracts"] != declaration["contracts"]):
        raise _facade.OperationAuthorityError(
            "operation spec differs from the exact registered HOST implementation contract")
    if (metadata["operation_spec_id"] != str(spec_ref.entity_id)
            or metadata["operation_spec_version_id"] != str(spec_ref.version_id)
            or _facade._parse_ref(metadata["operation_spec_ref"],
                                  label="operation_spec_ref") != spec_ref):
        raise _facade.OperationAuthorityError("operation spec self-reference differs from exact ref")
    raw_inputs = metadata["input_ports"]
    raw_outputs = metadata["output_ports"]
    raw_tools = metadata["allowed_tool_ids"]
    if (not isinstance(raw_inputs, list) or not isinstance(raw_outputs, list)
            or not isinstance(raw_tools, list)):
        raise _facade.OperationAuthorityError("operation spec port/tool collections are not arrays")
    inputs = tuple(_parse_port(
        repository, value, label=f"input_ports[{index}]",
        allow_input_projections=True)
                   for index, value in enumerate(raw_inputs))
    outputs = tuple(_parse_port(
        repository, value, label=f"output_ports[{index}]",
        allow_input_projections=False)
                    for index, value in enumerate(raw_outputs))
    tools = tuple(raw_tools)
    spec = _facade.OperationSpecAuthority(
        operation_spec_ref=spec_ref,
        operation_id=str(metadata["operation_id"]),
        executor_key=metadata["executor_key"],
        transport=metadata["transport"],  # type: ignore[arg-type]
        llm_prompt_port_id=(
            str(metadata["llm_prompt_port_id"])
            if metadata["llm_prompt_port_id"] is not None else None),
        input_ports=inputs,
        output_ports=outputs,
        allowed_tool_ids=tools,
    )
    for ref in (
            *(port.schema_ref for port in inputs),
            *(port.schema_ref for port in outputs)):
        repository.require_registered_ref(ref)
    for port in inputs:
        if port.lease_identity_ref is not None:
            repository.require_registered_ref(port.lease_identity_ref)
    return spec


def _hydrate_binding(
        repository: _facade._OperationAuthorityRepository,
        binding_ref: _facade.VersionRef, metadata: Mapping[str, Any],
        spec: _facade.OperationSpecAuthority,
) -> _facade.OperationBindingAuthority:
    if (metadata.get("operation_binding_ref") != _facade._ref_payload(binding_ref)
            or metadata.get("operation_binding_id") != str(binding_ref.entity_id)
            or metadata.get("operation_binding_version_id")
            != str(binding_ref.version_id)
            or metadata.get("origin") != "petri_operation"):
        raise _facade.OperationAuthorityError("operation binding self/origin identity differs")
    operation_spec_ref = _facade._parse_ref(
        metadata.get("operation_spec_ref"), label="binding.operation_spec_ref")
    node_ref = _facade._parse_ref(metadata.get("node_ref"), label="binding.node_ref")
    principal_ref = _facade._parse_ref(
        metadata.get("principal_ref"), label="binding.principal_ref")
    authority_decision_ref = _facade._parse_ref(
        metadata.get("authority_decision_ref"),
        label="binding.authority_decision_ref")
    raw_code_ref = metadata.get("code_artifact_ref")
    code_ref = (_facade._parse_ref(raw_code_ref, label="binding.code_artifact_ref")
                if raw_code_ref is not None else None)
    raw_backend_ref = metadata.get("llm_input_target_ref")
    target_ref = (_facade._parse_resource_ref(
        raw_backend_ref, label="binding.llm_input_target_ref")
        if raw_backend_ref is not None else None)
    raw_workspace_ref = metadata.get("workspace_binding_ref")
    workspace_ref = (_facade._parse_ref(
        raw_workspace_ref, label="binding.workspace_binding_ref")
        if raw_workspace_ref is not None else None)
    input_binding_refs = _facade._parse_refs(
        metadata.get("input_binding_refs"), label="binding.input_binding_refs")
    discoverable_resource_refs = _facade._parse_refs(
        metadata.get("discoverable_resource_refs"), label="binding.discoverable_resource_refs")
    readable_resource_refs = _facade._parse_refs(
        metadata.get("readable_resource_refs"), label="binding.readable_resource_refs")
    declared_lease_identity_refs = tuple(
        port.lease_identity_ref for port in spec.input_ports
        if port.lease_identity_ref is not None)
    if any(ref not in input_binding_refs or ref not in readable_resource_refs
           for ref in declared_lease_identity_refs):
        raise _facade.OperationAuthorityError(
            "operation binding does not authorize every declared static lease identity")
    input_schema_refs = _facade._parse_refs(
        metadata.get("input_schema_refs"), label="binding.input_schema_refs")
    output_schema_refs = _facade._parse_refs(
        metadata.get("output_schema_refs"), label="binding.output_schema_refs")
    output_binding_refs = _facade._parse_refs(
        metadata.get("output_binding_refs"), label="binding.output_binding_refs")
    if "fault_route_binding_ref" in metadata:
        raise _facade.OperationAuthorityError(
            "current operation binding cannot contain a fault route")
    if operation_spec_ref != spec.operation_spec_ref:
        raise _facade.OperationAuthorityError("operation binding selects another operation spec")
    if set(input_schema_refs) != {port.schema_ref for port in spec.input_ports}:
        raise _facade.OperationAuthorityError("operation binding/spec input schemas differ")
    if set(output_schema_refs) != {port.schema_ref for port in spec.output_ports}:
        raise _facade.OperationAuthorityError("operation binding/spec output schemas differ")
    if len(output_binding_refs) != len(spec.output_ports):
        raise _facade.OperationAuthorityError(
            "operation binding output binding cardinality differs from spec ports")
    output_port_bindings: list[_facade.OperationOutputPortBindingAuthority] = []
    unmatched = {port.port_id: port for port in spec.output_ports}
    for output_ref in output_binding_refs:
        if output_ref.entity_type != "output_binding/v1":
            raise _facade.OperationAuthorityError("operation binding contains non-output binding ref")
        output = repository.exact_metadata(output_ref, expected_type="output_binding/v1")
        cardinality = output.get("normal_output_cardinality")
        output_port_id = output.get("output_port_id")
        if (_facade._LEXICAL_ID.fullmatch(output_port_id)
                if isinstance(output_port_id, str) else None) is None:
            raise _facade.OperationAuthorityError(
                "output binding lacks an exact lexical output_port_id")
        content_schema_ref = _parse_content_schema_ref(
            output.get("content_schema_ref"), label="output_binding.content_schema_ref")
        port = unmatched.get(output_port_id)
        if (port is None or content_schema_ref != port.content_schema_ref
                or output.get("content_schema_id") != port.content_schema_id
                or not isinstance(cardinality, Mapping)
                or cardinality.get("minimum") != port.minimum
                or cardinality.get("maximum") != port.maximum
                or output.get("node_ref") != _facade._ref_payload(node_ref)):
            raise _facade.OperationAuthorityError(
                "output binding differs from its exact declared spec port")
        place = output.get("place")
        place_ref = _facade._parse_ref(
            output.get("place_ref"), label="output_binding.place_ref")
        if not isinstance(place, str) or not place:
            raise _facade.OperationAuthorityError(
                "output binding lacks its explicit declared Petri place")
        del unmatched[port.port_id]
        output_port_bindings.append(_facade.OperationOutputPortBindingAuthority(
            port_id=port.port_id, place=place, place_ref=place_ref,
            output_binding_ref=output_ref, content_schema=port.content_schema))
    if unmatched:
        raise _facade.OperationAuthorityError("operation binding leaves spec output ports unbound")
    raw_origins = metadata.get("allowed_publication_origins")
    if not isinstance(raw_origins, list) or any(
            not isinstance(value, str) for value in raw_origins):
        raise _facade.OperationAuthorityError("operation publication origins are not closed")
    limits_raw = repository.exact_metadata(
        node_ref, expected_type="node_declaration/v1").get("resource_bounds")
    if not isinstance(limits_raw, Mapping):
        raise _facade.OperationAuthorityError("operation node lacks resource bounds")
    limits = _facade.OperationExecutionLimits(
        max_llm_attempts=limits_raw.get("max_llm_attempts"),
        max_tool_turns=limits_raw.get("max_tool_turns"))
    has_llm_capacity = (
        limits.max_llm_attempts is None or limits.max_llm_attempts > 0)
    if ((spec.transport == "llm") != has_llm_capacity):
        raise _facade.OperationAuthorityError(
            "operation LLM-attempt limit differs from its transport")
    binding = _facade.OperationBindingAuthority(
        operation_binding_ref=binding_ref, operation_spec_ref=operation_spec_ref,
        node_ref=node_ref, principal_ref=principal_ref,
        authority_decision_ref=authority_decision_ref, code_artifact_ref=code_ref,
        llm_input_target_ref=target_ref, workspace_binding_ref=workspace_ref,
        input_binding_refs=input_binding_refs,
        discoverable_resource_refs=discoverable_resource_refs,
        readable_resource_refs=readable_resource_refs,
        input_schema_refs=input_schema_refs, output_schema_refs=output_schema_refs,
        output_port_bindings=tuple(sorted(
            output_port_bindings, key=lambda item: item.port_id)),
        allowed_publication_origins=tuple(raw_origins), limits=limits)
    for ref in (
            operation_spec_ref, node_ref, principal_ref, authority_decision_ref,
            *input_binding_refs, *discoverable_resource_refs,
            *readable_resource_refs, *input_schema_refs, *output_schema_refs,
            *(item.output_binding_ref for item in output_port_bindings),
            *(item.place_ref for item in output_port_bindings),
            *((workspace_ref,) if workspace_ref is not None else ()),
            *((code_ref,) if code_ref is not None else ())):
        repository.require_registered_ref(ref)
    if spec.transport == "llm":
        if (target_ref is None
                or target_ref.as_version_ref() not in input_binding_refs
                or target_ref.as_version_ref() not in readable_resource_refs):
            raise _facade.OperationAuthorityError(
                "LLM target is not an exact readable operation input")
        repository.require_registered_ref(target_ref.as_version_ref())
    elif target_ref is not None:
        raise _facade.OperationAuthorityError(
            "deterministic operation cannot bind an LLM target")
    return binding


def hydrate_registered_operation_declaration(
        repository: _facade._OperationAuthorityRepository, *,
        canonical: _facade.CanonicalInvocationAuthority,
        firing: _facade.TransitionFiringAuthority,
        transition: _facade.ExecutableTransitionAuthority,
) -> tuple[_facade.OperationBindingAuthority, _facade.OperationSpecAuthority]:
    """Rebuild the immutable invocation/firing/binding/spec closure."""
    if not isinstance(canonical, _facade.CanonicalInvocationAuthority):
        raise TypeError("operation declaration requires canonical authority")
    if not isinstance(firing, _facade.TransitionFiringAuthority):
        raise TypeError("operation declaration requires firing authority")
    if not isinstance(transition, _facade.ExecutableTransitionAuthority):
        raise TypeError("operation declaration requires transition authority")
    context = canonical.context
    binding_ref = firing.operation_binding_ref
    if (context.origin != "petri_operation"
            or context.invocation_ref != canonical.handle.invocation_ref
            or context.own_transition_firing_ref != firing.transition_firing_ref
            or context.operation_binding_ref != binding_ref
            or context.agent_ref != firing.agent_ref
            or context.agent_ref != transition.agent_ref
            or transition.operation_binding_ref != binding_ref
            or transition.transition_id != firing.transition_id
            or transition.node_ref != firing.node_ref
            or transition.principal_ref != firing.principal_ref):
        raise _facade.OperationAuthorityError(
            "invocation/firing/executable transition operation closure differs")
    if ((transition.execution_kind == "agent" and transition.agent_ref is None)
            or (transition.execution_kind != "agent" and transition.agent_ref is not None)):
        raise _facade.OperationAuthorityError(
            "executable transition agent identity differs from its kind")
    transition_metadata = repository.exact_metadata(
        transition.binding_ref, expected_type="executable_transition_binding/v1")
    expected_transition = {
        "executable_transition_binding_ref": _facade._ref_payload(transition.binding_ref),
        "net_instance_ref": _facade._ref_payload(firing.net_ref),
        "transition_id": transition.transition_id,
        "node_ref": _facade._ref_payload(transition.node_ref),
        "activation_ref": (_facade._ref_payload(transition.activation_ref)
                           if transition.activation_ref is not None else None),
        "agent_ref": (_facade._ref_payload(transition.agent_ref)
                      if transition.agent_ref is not None else None),
        "operation_binding_ref": _facade._ref_payload(
            transition.operation_binding_ref),
        "principal_ref": _facade._ref_payload(transition.principal_ref),
    }
    if any(transition_metadata.get(name) != value
           for name, value in expected_transition.items()):
        raise _facade.OperationAuthorityError(
            "executable transition DTO differs from exact Registry binding")
    binding_metadata = repository.exact_metadata(
        binding_ref, expected_type="operation_binding/v1")
    spec_ref = _facade._parse_ref(
        binding_metadata.get("operation_spec_ref"),
        label="binding.operation_spec_ref")
    if spec_ref.entity_type != "operation_spec/v1":
        raise _facade.OperationAuthorityError(
            "operation binding must exact-reference operation_spec/v1")
    spec_metadata = repository.exact_metadata(
        spec_ref, expected_type="operation_spec/v1")
    spec = _hydrate_spec(repository, spec_ref, spec_metadata)
    binding = _hydrate_binding(repository, binding_ref, binding_metadata, spec)
    if (binding.node_ref != firing.node_ref
            or binding.principal_ref != firing.principal_ref
            or binding.authority_decision_ref != context.authority_decision_ref):
        raise _facade.OperationAuthorityError(
            "operation binding is outside canonical invocation authority")
    return binding, spec


# Keep the established private façade lookup available without retaining a
# second implementation body in ``operations.py``.
_hydrate_registered_operation_declaration = hydrate_registered_operation_declaration
