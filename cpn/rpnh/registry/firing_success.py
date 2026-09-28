"""Ordinary registered operation result and single-transaction Petri Success.

Executor products are reclosed against their exact registered bundle before
publication. A registered effect HOST callback may supply a pure instruction;
Core mechanically rechecks it at commit, without interpreting a role.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .invocations import InvocationLifecycle
from .models import VersionRef
from .operations import RegisteredOperationOutputsAuthority
from .publication import _ref_payload, _stable_id
from .resource_service import _ResourceServiceKernel
from .resources import FiringSettlementAuthority


@dataclass(frozen=True, slots=True)
class PreparedFiringSuccess:
    settlement: FiringSettlementAuthority
    operation_outputs: RegisteredOperationOutputsAuthority
    result_metadata: Mapping[str, Any]
    terminal_ready_payload: Mapping[str, Any]


def _verify_closed_operation_outputs(
        kernel: _ResourceServiceKernel, repository, execution,
        supplied: RegisteredOperationOutputsAuthority,
) -> RegisteredOperationOutputsAuthority:
    """Recheck mechanical closure without reopening module output semantics."""
    if supplied.execution != execution:
        raise ResourceIntegrityFault(
            "Success outputs differ from the exact operation execution")
    authority = execution.operation
    outputs = supplied.outputs
    if len({item.resource_ref for item in outputs}) != len(outputs):
        raise ResourceIntegrityFault("Success outputs repeat a resource")
    ports = {item.port_id: item for item in authority.spec.output_ports}
    bindings = {
        item.port_id: item
        for item in authority.operation_binding.output_port_bindings}
    grouped = {port_id: [] for port_id in ports}
    for item in outputs:
        port = ports.get(item.port_id)
        binding = bindings.get(item.port_id)
        if (port is None or binding is None
                or item.output_binding_ref != binding.output_binding_ref
                or item.place != binding.place
                or item.place_ref != binding.place_ref
                or item.schema_ref != port.schema_ref
                or item.artifact.header.ref != item.resource_ref
                or item.artifact.header.content_schema_ref != port.content_schema_id
                or item.artifact.header.task_ref
                != authority.canonical.context.task_ref):
            raise ResourceIntegrityFault(
                "Success output differs from its exact registered binding")
        prepared = kernel._firing_prepared(
            authority.canonical.context, item.resource_ref)
        descriptors = prepared.metadata.get("descriptors")
        if descriptors is not None and not isinstance(descriptors, Mapping):
            raise ResourceIntegrityFault("Success output descriptors are malformed")
        descriptors = descriptors or {}
        if (descriptors.get("output_port_id", item.port_id) != item.port_id
                or descriptors.get("place", item.place) != item.place
                or descriptors.get("output_outcome_id", supplied.selected_outcome_id)
                != supplied.selected_outcome_id):
            raise ResourceIntegrityFault(
                "Success output descriptors differ from registered authority")
        grouped[item.port_id].append(item)
    _compiled, declared = repository.registered_compiled_operation(authority)
    selected = next((item for item in declared.declaration.outcomes
                     if item.name == supplied.selected_outcome_id), None)
    if selected is None:
        raise ResourceIntegrityFault("Success lacks one declared outcome")
    symbolic_ports = {
        item.name: item.port_id for item in _compiled.ports}
    quantities = {symbolic_ports[item.port]: item for item in selected.products}
    for port_id, items in grouped.items():
        quantity = quantities.get(port_id)
        count = len(items)
        if ((quantity is None and count)
                or (quantity is not None
                    and not quantity.minimum <= count <= quantity.maximum)):
            raise ResourceIntegrityFault(
                "Success output quantity differs from its declared outcome")
    ordered = tuple(sorted(outputs, key=lambda item: (
        item.port_id, str(item.resource_ref.resource_version_id))))
    if outputs != ordered:
        raise ResourceIntegrityFault("Success outputs are not canonically ordered")
    return supplied


def prepare_firing_success(core: _RegistryCore,
        outputs: RegisteredOperationOutputsAuthority, *,
        idempotency_key: str,
        provider_submission_unknown_ref: VersionRef | None = None,
        allow_failed_invocations: bool = False,
) -> PreparedFiringSuccess:
    """Stage result material only; terminal-ready is committed with Success.

Module-specific review/workspace policy is not part of harness settlement.
"""
    canonical = outputs.execution.operation.canonical
    context = canonical.context
    firing = outputs.execution.operation.firing
    exact_outputs = tuple(sorted((item.resource_ref.as_version_ref()
        for item in outputs.outputs), key=lambda ref:
        (ref.entity_type, str(ref.entity_id), str(ref.version_id))))
    if len(set(exact_outputs)) != len(exact_outputs) or not idempotency_key:
        raise ResourceIntegrityFault("success repeats products or lacks command identity")
    result_ref = VersionRef(
        "operation_result/v1", _stable_id("operation_result", idempotency_key),
        _stable_id("operation_result_version", idempotency_key))
    result = {
        "operation_result_ref": _ref_payload(result_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(firing.transition_firing_ref),
        "business_outcome": "completed",
        "output_resource_refs": [_ref_payload(ref) for ref in exact_outputs],
        "provider_attempt_evidence_refs": [],
        "workspace_access_set_ref": None,
    }
    core.catalog.validate_instance("operation_result/v1", category="object", instance=result)
    ready = {key: value for key, value in result.items() if key != "transition_firing_ref"}
    ready.update({
        "operation_execution_lease_ref": _ref_payload(context.operation_execution_lease_ref),
        "sealed_terminal_event_ids": list(
            InvocationLifecycle(core)._terminal_descendant_event_ids(
                context, provider_submission_unknown_ref,
                allow_failed_invocations=allow_failed_invocations)),
    })
    settlement = FiringSettlementAuthority(
        canonical=canonical, operation_result_ref=result_ref)
    return PreparedFiringSuccess(settlement, outputs, result, ready)


def _effect_witnesses_with_route_tokens(effects, new_tokens):
    """Attach Core-minted route occurrences to the prepared pure witnesses."""
    available = {}
    for ref, state in new_tokens:
        if state.resource_ref is None:
            continue
        available.setdefault((state.place, state.resource_ref), []).append(ref)
    for refs in available.values():
        refs.sort(key=lambda ref: (
            ref.entity_type, str(ref.entity_id), str(ref.version_id)))

    prepared_routes = iter(effects.routes)
    prepared_outputs = iter(effects.selected_outputs)
    used_control_refs = set()
    witnesses = []
    for witness in effects.witnesses:
        copied = dict(witness)
        routes = []
        for route in witness["routes"]:
            prepared = next(prepared_routes, None)
            if prepared is None:
                raise ResourceIntegrityFault(
                    "effect witness has more routes than its PN projection")
            refs = available.get((prepared.place, prepared.source.resource_ref), [])
            if not refs:
                raise ResourceIntegrityFault(
                    "effect route lacks its exact Core-minted successor occurrence")
            routes.append(dict(route, token_ref=_ref_payload(refs.pop(0))))
        copied["routes"] = routes
        outputs = []
        for output in witness.get("outputs", ()):
            prepared = next(prepared_outputs, None)
            if prepared is None:
                raise ResourceIntegrityFault(
                    "effect witness has more selected outputs than its PN projection")
            refs = tuple(sorted((
                ref for ref, state in new_tokens
                if (ref not in used_control_refs
                    and state.place == prepared.place
                    and state.producer == prepared.transition_id
                    and state.resource_ref is None
                    and state.verdict == prepared.colour)
            ), key=lambda ref: (
                ref.entity_type, str(ref.entity_id), str(ref.version_id))))
            if len(refs) != prepared.weight:
                raise ResourceIntegrityFault(
                    "selected output lacks its exact Core-minted token multiset")
            used_control_refs.update(refs)
            outputs.append(dict(
                output,
                token_refs=[_ref_payload(ref) for ref in refs],
            ))
        copied["outputs"] = outputs
        witnesses.append(copied)
    if next(prepared_routes, None) is not None:
        raise ResourceIntegrityFault(
            "effect PN projection has an unrecorded selected route")
    if next(prepared_outputs, None) is not None:
        raise ResourceIntegrityFault(
            "effect PN projection has an unrecorded selected output")
    return tuple(witnesses)


def succeed_module_operation(core: _RegistryCore, kernel: _ResourceServiceKernel,
        repository, supplied: RegisteredOperationOutputsAuthority, *,
        idempotency_key: str, registration=None,
        candidate_publisher: Callable | None = None,
        workspace_plans: tuple[Mapping[str, Any], ...] = ()):
    """Close one ordinary operation and publish exactly one real successor.

The latest marking is read from the execution-owning Registry. No caller
checkpoint, executor body text, or diagnostic can grant settlement authority.
"""
    from .operation_execution import verify_operation_execution
    execution = verify_operation_execution(core, kernel, repository, supplied.execution)
    outputs = _verify_closed_operation_outputs(
        kernel, repository, execution, supplied)
    return _succeed_verified_module_operation(
        core, kernel, repository, outputs,
        idempotency_key=idempotency_key, registration=registration,
        candidate_publisher=candidate_publisher,
        workspace_plans=workspace_plans,
        resource_access_writer_epoch=core.writer_epoch)


def _succeed_verified_module_operation(
        core: _RegistryCore, kernel: _ResourceServiceKernel,
        repository, outputs: RegisteredOperationOutputsAuthority, *,
        idempotency_key: str, registration=None,
        candidate_publisher: Callable | None = None,
        workspace_plans: tuple[Mapping[str, Any], ...] = (),
        resource_access_writer_epoch: int,
        provider_submission_unknown_ref: VersionRef | None = None,
        allow_failed_invocations: bool = False,
        authorize_stale_lease_settlement: bool = False,
):
    """Project and stage Success after an ordinary or recovery-specific gate."""

    if (isinstance(resource_access_writer_epoch, bool)
            or not isinstance(resource_access_writer_epoch, int)
            or resource_access_writer_epoch < 1):
        raise TypeError("Success requires one exact resource-access writer epoch")
    from ..marking import TeamNetMarking
    from .module_execution import install_active_module_claims
    from .module_effects import prepare_module_effects
    from .module_revision import prepare_module_revision, stage_operation_revision
    from .module_runtime import hydrate_module_runtime
    from .success_projection import project_module_firing_success
    from .success_publication import stage_success_material, stage_success_publication, stage_success_completion
    from ..marking import derive_petri_marking_delta
    from ..firing_resource_access import (
        registered_firing_resource_access_from_payload,
    )
    from .publication import _effective_firing_resource_access_events
    execution = outputs.execution
    executable, structure, prior = hydrate_module_runtime(core)
    if executable.net_ref != execution.operation.firing.net_ref:
        raise ResourceIntegrityFault("Success firing differs from the current adopted net")
    _compiled, declared = repository.registered_compiled_operation(execution.operation)
    selected = next(item for item in declared.declaration.outcomes if item.name == outputs.selected_outcome_id)
    effects = None
    if selected.effects:
        if registration is None:
            raise ResourceIntegrityFault(
                "declared effects require the execution owner's explicit Registration")
        local = TeamNetMarking.from_authority(structure, prior)
        active = install_active_module_claims(core, local, executable.net_ref)
        context = execution.operation.canonical.context

        def prepared_resource(ref):
            return kernel._firing_prepared(context, ref)

        effects = prepare_module_effects(
            executable, structure, prior, outputs, registration,
            active_firings=active,
            read_resource=lambda ref: kernel._read_firing_registered(
                context, ref, prepared=prepared_resource(ref)),
            read_resource_media_type=lambda ref: prepared_resource(ref).media_type,
        )
    firing = execution.operation.firing
    resource_access_events = tuple(
        event for event in core.event_store.list_events()
        if (event.event_type == "petri_firing_resource_accessed/v1"
            and event.aggregate_id == str(firing.transition_firing_ref.entity_id)
            and event.writer_fencing_epoch == resource_access_writer_epoch
            and event.payload.get("transition_firing_ref")
            == _ref_payload(firing.transition_firing_ref)))
    effective_resource_access_events = (
        _effective_firing_resource_access_events(
            resource_access_events))
    resource_accesses = tuple(
        registered_firing_resource_access_from_payload(event.payload)
        for event in effective_resource_access_events)
    projected, new_tokens = project_module_firing_success(
        executable, structure, prior, outputs, effects=effects,
        resource_accesses=resource_accesses)
    formal_delta = derive_petri_marking_delta(prior, projected, new_tokens)
    revision = None
    declared_effects = None
    if effects is not None:
        witnesses = _effect_witnesses_with_route_tokens(effects, new_tokens)
        declared_effects = {
            "selected_outcome_id": effects.outcome_id,
            "effects": list(witnesses),
        }
        if effects.revisions:
            revision = prepare_module_revision(
                core, kernel, registration, executable, structure, prior,
                projected, new_tokens, effects,
                candidate_publisher=candidate_publisher)
    prepared = prepare_firing_success(
        core, outputs, idempotency_key=idempotency_key,
        provider_submission_unknown_ref=provider_submission_unknown_ref,
        allow_failed_invocations=allow_failed_invocations)
    context = prepared.settlement.canonical.context
    tx = core.begin(idempotency_key=idempotency_key, task_round_id=context.task_round_ref.entity_id,
        net_instance_id=executable.net_ref.entity_id)
    material = stage_success_material(core, kernel, tx, executable=executable, prior_marking=prior,
        projected=projected, new_tokens=new_tokens, settlement=prepared.settlement, operation_outputs=outputs,
        formal_delta=formal_delta,
        result_metadata=prepared.result_metadata, terminal_ready_payload=prepared.terminal_ready_payload,
        workspace_plans=workspace_plans, disposition_relations=(), publish_checkpoint=True, idempotency_key=idempotency_key,
        declared_effects=declared_effects, revision=revision,
        historical_lease_writer_epoch=(
            resource_access_writer_epoch
            if authorize_stale_lease_settlement else None))
    if revision is not None:
        stage_operation_revision(core, tx, revision,
            old_executable=executable, checkpoint_ref=material.checkpoint_ref,
            settlement=prepared.settlement)
    publication = stage_success_publication(core, tx, executable=executable, prior_marking=prior,
        settlement=prepared.settlement, operation_outputs=outputs, result_metadata=prepared.result_metadata,
        material=material, checkpoint_ref=material.checkpoint_ref, checkpoint=material.checkpoint,
        workspace_plans=workspace_plans, idempotency_key=idempotency_key)
    from .agent_resource_broker import stage_firing_resource_lifecycle_seals
    stage_firing_resource_lifecycle_seals(
        core, tx,
        firing_ref=firing.transition_firing_ref,
        settlement_ref=publication.completion_ref,
        disposition="success",
        idempotency_key=idempotency_key,
        producer_invocation_id=context.invocation_ref.entity_id)
    stage_success_completion(core, kernel, tx, executable=executable, settlement=prepared.settlement,
        operation_outputs=outputs, result_metadata=prepared.result_metadata, material=material,
        checkpoint_ref=material.checkpoint_ref, publication=publication, idempotency_key=idempotency_key,
        revision=revision)
    tx.commit()
    _executable, _structure, successor = hydrate_module_runtime(core)
    if successor.checkpoint_ref != material.checkpoint_ref:
        raise ResourceIntegrityFault("Success publication lacks its exact adopted successor")
    return successor


__all__ = ("PreparedFiringSuccess", "prepare_firing_success", "succeed_module_operation")
