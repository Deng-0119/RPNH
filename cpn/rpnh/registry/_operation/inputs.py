"""Operation input planning, delivery hydration, and validation."""

from __future__ import annotations

from collections.abc import Sequence

from .. import operations as _facade
from .declaration import (
    _hydrate_binding,
    _hydrate_spec,
    hydrate_registered_operation_declaration,
)


def _validate_inputs(
        authority_spec: _facade.OperationSpecAuthority,
        binding: _facade.OperationBindingAuthority,
        firing: _facade.TransitionFiringAuthority,
        petri_inputs: Sequence[
            _facade.PetriInputArtifact
            | _facade.SettledPetriInputArtifact
            | _facade.HistoricalPetriInputArtifact],
        resolved: Sequence[_facade.RegisteredOperationInputAuthority],
) -> tuple[_facade.RegisteredOperationInputAuthority, ...]:
    if any(not isinstance(item, _facade.RegisteredOperationInputAuthority)
           for item in resolved):
        raise TypeError("Registry input resolver returned an untyped operation input")
    if ({item.artifact.resource.header.ref for item in resolved}
            != {item.resource.header.ref for item in petri_inputs}):
        raise _facade.OperationAuthorityError(
            "resolved operation inputs differ from exact delivered petri inputs")
    ports = {port.port_id: port for port in authority_spec.input_ports}
    grouped: dict[str, list[_facade.RegisteredOperationInputAuthority]] = {
        port_id: [] for port_id in ports}
    for item in resolved:
        port = ports.get(item.port_id)
        input_is_bound = (
            item.input_binding_ref in binding.input_binding_refs
            or (item.claimed_token_ref is not None
                and item.input_binding_ref == item.claimed_token_ref
                and item.claimed_token_ref in firing.claimed_input_refs))
        producer_schema_id = item.artifact.resource.header.content_schema_ref
        declared_substitution = (
            item.substituted_content_schema_id is not None
            and item.source_resource_ref is not None and port is not None
            and producer_schema_id == item.substituted_content_schema_id
            and item.claimed_token_ref is not None
            and item.input_binding_ref == item.claimed_token_ref
            and item.projected_payload is None)
        if declared_substitution:
            projection = None
        else:
            try:
                projection = (port.input_projection_from(producer_schema_id)
                              if port is not None
                              and isinstance(producer_schema_id, str) else None)
            except _facade.OperationAuthorityError:
                projection = object()
        if (port is None or item.schema_ref != port.schema_ref
                or not input_is_bound or not isinstance(producer_schema_id, str)
                or (not declared_substitution
                    and ((projection is None) != (item.projected_payload is None)))
                or (projection is not None
                    and not isinstance(projection, _facade.OperationInputProjection))):
            raise _facade.OperationAuthorityError(
                "resolved operation input is outside binding/spec authority")
        if (item.claimed_token_ref is not None
                and item.claimed_token_ref not in firing.claimed_input_refs):
            raise _facade.OperationAuthorityError(
                "resolved operation input claims a token outside the admitted firing")
        grouped[item.port_id].append(item)
    for port_id, port in ports.items():
        count = len(grouped[port_id])
        if count < port.minimum or count > port.maximum:
            artifact_refs = tuple(sorted(
                repr(item.resource_ref) for item in grouped[port_id]))
            raise _facade.OperationAuthorityError(
                "operation input port violates declared cardinality: "
                f"port_id={port_id!r}; expected_minimum={port.minimum}; "
                f"expected_maximum={port.maximum}; actual_artifact_count={count}; "
                f"artifact_refs={artifact_refs!r}; "
                f"declared_port_mapping=(port_id={port.port_id!r}, "
                f"place={port.place!r}, schema_ref={port.schema_ref!r}); "
                f"operation_binding_ref={binding.operation_binding_ref!r}; "
                f"operation_spec_ref={authority_spec.operation_spec_ref!r}; "
                f"transition_firing_ref={firing.transition_firing_ref!r}")
    result = tuple(sorted(
        resolved,
        key=lambda item: (
            item.port_id,
            str(item.artifact.resource.header.ref.resource_version_id))))
    return result


def hydrate_operation_authority(
        repository: _facade._OperationAuthorityRepository, *,
        input_plan: _facade.RegisteredOperationInputPlanAuthority,
        petri_inputs: tuple[
            _facade.PetriInputArtifact
            | _facade.SettledPetriInputArtifact
            | _facade.HistoricalPetriInputArtifact, ...],
) -> _facade.RegisteredOperationAuthority:
    """Hydrate one exact executable operation with resolved file inputs.

    ``RegistryFacade`` owns the repository argument and exposes the remaining
    keyword-only signature publicly.  Callers cannot provide metadata maps,
    implementation names, input labels or resolver callbacks.
    """
    if not isinstance(input_plan, _facade.RegisteredOperationInputPlanAuthority):
        raise TypeError("operation hydration requires its exact input plan")
    if any(not isinstance(item, (
            _facade.PetriInputArtifact, _facade.SettledPetriInputArtifact,
            _facade.HistoricalPetriInputArtifact)) for item in petri_inputs):
        raise TypeError("operation hydration accepts only registered Petri input files")
    canonical = input_plan.canonical
    firing = input_plan.firing
    transition = input_plan.transition
    binding, spec = hydrate_registered_operation_declaration(
        repository, canonical=canonical, firing=firing, transition=transition)
    if binding != input_plan.operation_binding or spec != input_plan.spec:
        raise _facade.OperationAuthorityError(
            "operation declaration changed after input planning")
    delivered_by_ref = {artifact.resource.header.ref: artifact for artifact in petri_inputs}
    planned_by_ref = {claim.resource_ref: claim for claim in input_plan.claims}
    if (len(delivered_by_ref) != len(petri_inputs)
            or len(planned_by_ref) != len(input_plan.claims)
            or set(delivered_by_ref) != set(planned_by_ref)):
        raise _facade.OperationAuthorityError(
            "delivered operation inputs differ from the exact input plan")
    # These concrete, name-mangled seams are an existing compatibility
    # contract.  Keep both spellings and their lookup order exact.
    reverify_input = getattr(
        repository, "_RegistryOperationAuthorityRepository__reverify_input", None)
    project_input = getattr(
        repository, "_RegistryOperationAuthorityRepository__project_input_payload", None)
    if not callable(reverify_input) or not callable(project_input):
        raise TypeError("operation hydration requires the Registry input verifier")
    resolved_values: list[_facade.RegisteredOperationInputAuthority] = []
    for claim in input_plan.claims:
        artifact = delivered_by_ref[claim.resource_ref]
        if isinstance(artifact, _facade.SettledPetriInputArtifact):
            if (artifact.source_invocation_ref != canonical.context.invocation_ref
                    or artifact.source_firing_ref != firing.transition_firing_ref
                    or artifact.receipt.exact_resource_ref != artifact.resource.header.ref
                    or artifact.receipt.positive_byte_count != len(artifact.payload)):
                raise _facade.OperationAuthorityError(
                    "settled operation input differs from its firing/start closure")
        else:
            reverify_input(canonical, artifact)
        static_binding_ref = claim.resource_ref.as_version_ref()
        input_binding_ref = (static_binding_ref
                             if (static_binding_ref in binding.input_binding_refs
                                 and claim.source_resource_ref is None)
                             else claim.claimed_token_ref)
        resolved_values.append(_facade.RegisteredOperationInputAuthority(
            port_id=claim.port.port_id, input_binding_ref=input_binding_ref,
            claimed_token_ref=claim.claimed_token_ref,
            schema_ref=claim.port.schema_ref, artifact=artifact,
            projected_payload=(
                None if claim.source_resource_ref is not None
                else project_input(port=claim.port, artifact=artifact)),
            source_resource_ref=claim.source_resource_ref,
            substituted_content_schema_id=claim.substituted_content_schema_id))
    resolved = tuple(resolved_values)
    inputs = _validate_inputs(spec, binding, firing, petri_inputs, resolved)
    return _facade.RegisteredOperationAuthority(
        input_plan=input_plan, canonical=canonical, firing=firing,
        transition=transition, operation_binding=binding, spec=spec,
        inputs=inputs, verified_at_head=repository.registry_head())


def prepare_registered_operation_inputs(
        repository: _facade._OperationAuthorityRepository, *,
        transition: _facade.ExecutableTransitionAuthority,
        claimed_input_refs: tuple[_facade.VersionRef, ...],
        static_excluded_port_ids: frozenset[str] = frozenset(),
) -> _facade.PreparedRegisteredOperationInputPlanAuthority:
    """Solve the sole semantic input relation before firing publication."""
    if not isinstance(transition, _facade.ExecutableTransitionAuthority):
        raise TypeError("operation input preparation requires executable authority")
    if (not isinstance(claimed_input_refs, tuple)
            or any(not isinstance(ref, _facade.VersionRef)
                   or ref.entity_type != "petri_token/v1" for ref in claimed_input_refs)
            or len(set(claimed_input_refs)) != len(claimed_input_refs)):
        raise _facade.OperationAuthorityError(
            "operation input preparation requires unique claimed tokens")
    binding_metadata = repository.exact_metadata(
        transition.operation_binding_ref, expected_type="operation_binding/v1")
    spec_ref = _facade._parse_ref(
        binding_metadata.get("operation_spec_ref"), label="binding.operation_spec_ref")
    if spec_ref.entity_type != "operation_spec/v1":
        raise _facade.OperationAuthorityError(
            "operation binding must exact-reference operation_spec/v1")
    spec = _hydrate_spec(repository, spec_ref, repository.exact_metadata(
        spec_ref, expected_type="operation_spec/v1"))
    binding = _hydrate_binding(
        repository, transition.operation_binding_ref, binding_metadata, spec)
    transition_metadata = repository.exact_metadata(
        transition.binding_ref, expected_type="executable_transition_binding/v1")
    expected_transition = {
        "executable_transition_binding_ref": _facade._ref_payload(transition.binding_ref),
        "transition_id": transition.transition_id,
        "node_ref": _facade._ref_payload(transition.node_ref),
        "activation_ref": (_facade._ref_payload(transition.activation_ref)
                           if transition.activation_ref is not None else None),
        "agent_ref": (_facade._ref_payload(transition.agent_ref)
                      if transition.agent_ref is not None else None),
        "operation_binding_ref": _facade._ref_payload(transition.operation_binding_ref),
        "principal_ref": _facade._ref_payload(transition.principal_ref),
    }
    if (any(transition_metadata.get(name) != value
            for name, value in expected_transition.items())
            or binding.node_ref != transition.node_ref
            or binding.principal_ref != transition.principal_ref):
        raise _facade.OperationAuthorityError(
            "prepared operation transition/binding closure differs")
    if (not isinstance(static_excluded_port_ids, frozenset)
            or any(not isinstance(item, str) or not item
                   for item in static_excluded_port_ids)):
        raise _facade.OperationAuthorityError(
            "pre-admission excluded ports must be explicit lexical ids")
    claims = repository.resolve_pre_admission_input_claims(
        claimed_input_refs=claimed_input_refs, binding=binding, spec=spec,
        static_excluded_port_ids=static_excluded_port_ids)
    return _facade.PreparedRegisteredOperationInputPlanAuthority(
        transition=transition, operation_binding=binding, spec=spec,
        claims=claims, verified_at_head=repository.registry_head())


def plan_registered_operation_inputs(
        repository: _facade._OperationAuthorityRepository, *,
        canonical: _facade.CanonicalInvocationAuthority,
        firing: _facade.TransitionFiringAuthority,
        transition: _facade.ExecutableTransitionAuthority,
        input_resource_substitutions: tuple[object, ...] = (),
) -> _facade.RegisteredOperationInputPlanAuthority:
    """Select exact semantic input claims before any resource byte delivery.

    The complete firing claim remains Petri authority.  This narrower plan says
    which resource-backed claims satisfy the immutable operation input ports;
    transport, lock, control, and provenance claims are never silently promoted
    into operation bytes merely because they carry a resource reference.
    """
    binding, spec = hydrate_registered_operation_declaration(
        repository, canonical=canonical, firing=firing, transition=transition)
    claims = repository.resolve_operation_input_claims(
        firing=firing, binding=binding, spec=spec,
        input_resource_substitutions=input_resource_substitutions)
    return _facade.RegisteredOperationInputPlanAuthority(
        canonical=canonical, firing=firing, transition=transition,
        operation_binding=binding, spec=spec, claims=claims,
        verified_at_head=repository.registry_head())
