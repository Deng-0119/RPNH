"""Register a composed Module's actual drained, settled terminal product.

The declared tool key is publication authority, never a callable to execute.
Business outcome colours remain distinct from explicit framework run outcomes.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Mapping

from ._registry import _RegistryCore
from .errors import ResourceIntegrityFault
from .firing_authority import verify_transition_firing
from .identities import TypedId, new_id
from .invocations import InvocationLifecycle
from .models import TypedRelation, VersionRef
from .module_runtime import hydrate_module_runtime
from .publication import _ref_payload, _resource_from_payload, _version_from_payload
from .resource_service import _ResourceServiceKernel, _resource_payload
from .resources import CanonicalInvocationAuthority, InvocationContextHandle
from .run_authority import current_run_execution_authority
from .schema_catalog import canonical_json


def _object(core, kernel, ref, object_type):
    prepared = kernel._exact_object(ref, expected_type=object_type)
    document = dict(prepared.metadata)
    core.catalog.validate_instance(object_type, category="object", instance=document)
    if json.loads(core.object_store.read_registered(prepared)) != document:
        raise ResourceIntegrityFault("terminal object bytes differ from registered material")
    return document


def _existing(core, kernel, object_type, self_field, material):
    rows = core.event_store.canonical_object_rows(object_type=object_type)
    if not rows:
        return None
    if len(rows) != 1:
        raise ResourceIntegrityFault("run has conflicting Module terminal objects")
    row = rows[0]
    ref = VersionRef(object_type, TypedId.parse(str(row["logical_id"])),
                     TypedId.parse(str(row["version_id"])))
    if _object(core, kernel, ref, object_type) != {self_field: _ref_payload(ref), **material}:
        raise ResourceIntegrityFault("registered Module terminal material conflicts")
    return ref


def _registered_terminal_key(core, kernel, compiled, task_ref, terminal):
    declaration = compiled.registrations["tool"].get(terminal.key)
    if declaration is None:
        return False
    matches = []
    for row in core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        metadata = json.loads(str(row["metadata_json"]))
        descriptors = metadata.get("descriptors", {})
        if (isinstance(descriptors, Mapping)
                and descriptors.get("host_registration_kind") == "tool"
                and descriptors.get("registered_key") == terminal.key):
            matches.append(row)
    if not matches:
        return False
    if len(matches) != 1:
        raise ResourceIntegrityFault("Module terminal key has conflicting stored declarations")
    row = matches[0]
    resource = _resource_from_payload({"resource_id": str(row["logical_id"]),
                                      "resource_version_id": str(row["version_id"])})
    prepared = kernel._prepared(resource)
    if (prepared.metadata.get("task_ref") != task_ref
            or prepared.metadata.get("origin_kind") != "private_system"
            or json.loads(core.object_store.read_registered(prepared)) != declaration):
        raise ResourceIntegrityFault("terminal key differs from exact stored HOST declaration")
    return True


def _producing_token(core, kernel, executable, structure, carrier, product, port, firing_ref):
    """Reclose exact immutable consumed-forward deposits back to the producer.

    A token's creation transaction selects its actual settled firing, not its
    producer label alone. Each hop must consume the declared source occurrence;
    equal file bytes or a router's own product cannot establish this lineage.
    """
    ref = carrier.token_ref
    seen = set()
    places = {item.name: item for item in structure.compiled.symbolic.places}
    while ref not in seen:
        seen.add(ref)
        token = _object(core, kernel, ref, "petri_token/v1")
        resources = {canonical_json(value) for value in
            (token["resource_ref"], token["work_resource_ref"]) if value is not None}
        if (token["petri_token_ref"] != _ref_payload(ref)
                or token["net_instance_ref"] != _ref_payload(executable.net_ref)
                or resources != {canonical_json(_resource_payload(product))}
                or token["verdict"] != carrier.state.verdict
                or token["kind"] != carrier.state.kind
                or port.schema not in places[token["place"]].admitted_schemas):
            raise ResourceIntegrityFault("terminal forward lineage changes exact resource/schema/colour")
        transaction = core.event_store.object_row(ref.version_id)["transaction_id"]
        with core.event_store.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM firing_publications WHERE state='PUBLISHED' "
                "AND published_transaction_id=?", (str(transaction),)).fetchall()]
        if len(rows) != 1:
            raise ResourceIntegrityFault("terminal carrier lacks one actual published deposit firing")
        publication = rows[0]
        record = core.event_store.ordered_firing_record(publication["firing_version_id"])
        actual_ref = _version_from_payload(record["firing"]["transition_firing_ref"])
        for name, field, object_type in (
                ("firing", "transition_firing_ref", "transition_firing/v1"),
                ("marking_delta", "marking_delta_ref", "marking_delta/v1"),
                ("successor_checkpoint", "marking_checkpoint_ref", "marking_checkpoint/v1"),
                ("firing_completion", "firing_completion_ref", "firing_completion/v2")):
            data = record[name]
            if _object(core, kernel, _version_from_payload(data[field]), object_type) != data:
                raise ResourceIntegrityFault("terminal carrier settlement differs from immutable objects")
        settled = tuple(event for event in record["events"]
            if event.event_type == "transition_firing_settled/v1"
            and event.payload.get("transition_firing_ref") == _ref_payload(actual_ref))
        delta = record["marking_delta"]
        successor = record["successor_checkpoint"]
        result_ref = _version_from_payload(record["firing_completion"]["operation_result_ref"])
        result = _object(core, kernel, result_ref, "operation_result/v1")
        if (len(settled) != 1 or str(settled[0].transaction_id) != str(transaction)
                or record["firing"]["net_instance_ref"] != _ref_payload(executable.net_ref)
                or record["firing"]["transition_id"] != token["producer"]
                or successor.get("settled") is not True
                or _ref_payload(ref) not in successor["token_refs"]
                or _ref_payload(ref) not in delta["deposited_refs"]
                or result["operation_result_ref"] != _ref_payload(result_ref)
                or result["transition_firing_ref"] != _ref_payload(actual_ref)
                or result["business_outcome"] != "completed"
                or result["invocation_ref"] != record["firing_completion"]["invocation_ref"]
                or result["invocation_ref"]["version_id"] != str(publication["invocation_version_id"])
                or settled[0].payload["operation_result_ref"] != _ref_payload(result_ref)
                or str(core.event_store.object_row(result_ref.version_id)["transaction_id"])
                    != str(transaction)):
            raise ResourceIntegrityFault("terminal carrier lacks exact atomic settled deposit")
        if actual_ref == firing_ref:
            if token["place"] != port.place:
                raise ResourceIntegrityFault("terminal origin token differs from actual producing port")
            return ref
        transition_id = token["producer"]
        arcs = tuple(arc for arc in structure.compiled.symbolic.arcs
            if arc.direction == "output" and arc.transition == transition_id
            and arc.place == token["place"] and arc.emit == "forward")
        if len(arcs) != 1:
            raise ResourceIntegrityFault("terminal carrier is not a declared true-forward deposit")
        arc = arcs[0]
        inputs = tuple(item for item in structure.compiled.symbolic.arcs
            if item.direction == "input" and item.transition == transition_id
            and item.place == arc.forward_source and item.mode in {"consume", "borrow"})
        if len(inputs) != 1:
            raise ResourceIntegrityFault("terminal forward source is not a consumed input")
        candidates = []
        for value in delta["consumed_refs"]:
            if value not in record["firing"]["claimed_input_refs"]:
                continue
            previous_ref = _version_from_payload(value)
            previous = _object(core, kernel, previous_ref, "petri_token/v1")
            if (previous["place"] == arc.forward_source
                    and previous["epoch"] == token["epoch"]
                    and previous["consumed_by"] is None
                    and previous["resource_ref"] == _resource_payload(product)
                    and previous["work_resource_ref"] in (None, _resource_payload(product))
                    and previous["verdict"] == token["verdict"]
                    and previous["kind"] == token["kind"]):
                candidates.append(previous_ref)
        if len(candidates) != 1:
            raise ResourceIntegrityFault("terminal forward lacks one exact consumed origin occurrence")
        ref = candidates[0]
    raise ResourceIntegrityFault("terminal forward lineage does not reach its actual producing firing")


def _unwrap_reentry_carrier(core, kernel, carrier):
    """Resolve fresh reentry occurrences back to a firing-produced token."""

    by_replacement: dict[str, list[tuple[VersionRef, dict, VersionRef]]] = {}
    for row in core.event_store.canonical_object_rows(
            object_type="marking_checkpoint/v1"):
        checkpoint_ref = VersionRef(
            "marking_checkpoint/v1",
            TypedId.parse(str(row["logical_id"]),
                          expected="marking_checkpoint"),
            TypedId.parse(str(row["version_id"]),
                          expected="marking_checkpoint_version"))
        checkpoint = _object(
            core, kernel, checkpoint_ref, "marking_checkpoint/v1")
        for mapping in checkpoint.get("reentry_token_mappings", []):
            replacement_ref = _version_from_payload(
                mapping["replacement_token_ref"])
            by_replacement.setdefault(
                str(replacement_ref.version_id), []).append((
                    checkpoint_ref, checkpoint,
                    _version_from_payload(mapping["source_token_ref"])))

    current = carrier
    seen: set[VersionRef] = set()
    while current.token_ref not in seen:
        seen.add(current.token_ref)
        current_document = _object(
            core, kernel, current.token_ref, "petri_token/v1")
        replacement_payload = _ref_payload(current.token_ref)
        matches = by_replacement.get(
            str(current.token_ref.version_id), [])
        if not matches:
            return current
        if len(matches) != 1:
            raise ResourceIntegrityFault(
                "terminal reentry carrier has ambiguous token ancestry")
        checkpoint_ref, checkpoint, source_ref = matches[0]
        if source_ref in seen:
            raise ResourceIntegrityFault(
                "terminal reentry carrier token ancestry is cyclic")
        source_document = _object(
            core, kernel, source_ref, "petri_token/v1")
        expected = {
            **source_document,
            "petri_token_ref": replacement_payload,
            "token_id": current_document["token_id"],
            "epoch": current_document["epoch"],
            "consumed_by": None,
        }
        if (current_document != expected
                or replacement_payload not in checkpoint["token_refs"]
                or core.event_store.object_row(
                    current.token_ref.version_id)["transaction_id"]
                != core.event_store.object_row(
                    checkpoint_ref.version_id)["transaction_id"]):
            raise ResourceIntegrityFault(
                "terminal reentry carrier is not one atomic exact clone")
        current = replace(
            current, token_ref=source_ref,
            state=replace(
                current.state, token_ref=source_ref,
                token_id=source_document["token_id"],
                epoch=source_document["epoch"],
                consumed_by=source_document["consumed_by"]))
    raise ResourceIntegrityFault(
        "terminal reentry carrier token ancestry is cyclic")


def _terminal_material_for_binding(core, kernel, terminal):
    # An absent or non-Module execution source is not a terminal prestate.
    if not core.event_store.canonical_object_rows(object_type="run_execution_authority/v1"):
        return None
    authority_ref, authority = current_run_execution_authority(core, kernel)
    if authority["declaration_schema_ref"] != "rpnh/executable_net/v1":
        return None
    executable, structure, marking = hydrate_module_runtime(core)
    compiled = structure.compiled
    outcome = terminal.config.get("run_outcome")
    if not isinstance(outcome, str) or outcome not in {"complete", "failed"}:
        return None
    if (authority["declaration_ref"] != _ref_payload(executable.declaration_resource_ref.as_version_ref())
            or authority["latest_checkpoint_ref"] != _ref_payload(marking.checkpoint_ref)):
        raise ResourceIntegrityFault("terminal run source/checkpoint differs from current Registry heads")
    if not _registered_terminal_key(core, kernel, compiled, authority["task_ref"], terminal):
        return None
    checkpoint = _object(core, kernel, marking.checkpoint_ref, "marking_checkpoint/v1")
    if checkpoint.get("settled") is not True:
        return None
    # Normal drain requires no unresolved invocation, including an older cut.
    with core.event_store.connect() as db:
        if db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' LIMIT 1").fetchone():
            return None
    if InvocationLifecycle(core)._active_claim_inputs(executable.net_ref):
        return None
    port_name = f"{terminal.source.component}.{terminal.source.port}"
    operation_name = f"{terminal.source.component}.{terminal.operation}"
    port = next(item for item in compiled.ports if item.name == port_name)
    operation = next(item for item in compiled.operations if item.declaration.name == operation_name)
    tokens = tuple(token for token in marking.tokens if token.state.epoch == marking.epoch
        and token.state.consumed_by is None and token.state.place == port.place
        and token.state.verdict == terminal.outcome)
    if not tokens:
        return None
    if len(tokens) != 1:
        # This API selects one exact product, not a semantic multi-product index.
        return None
    token = tokens[0]
    resources = {ref for ref in (token.state.resource_ref, token.state.work_resource_ref) if ref is not None}
    if len(resources) != 1:
        return None
    product = next(iter(resources))
    prepared = kernel._prepared(product)
    metadata = dict(prepared.metadata)
    invocation_ref = _version_from_payload(metadata["producer_ref"])
    publication = core.event_store.firing_publication_for_invocation(invocation_ref.version_id)
    if publication is None or publication["state"] != "PUBLISHED":
        return None
    context = InvocationLifecycle(core).hydrate_context(invocation_ref, require_current_writer=False)
    canonical = CanonicalInvocationAuthority(context, InvocationContextHandle(invocation_ref), kernel._head())
    firing = verify_transition_firing(core, kernel, canonical)
    firing_ref = firing.transition_firing_ref
    if (str(publication["invocation_version_id"]) != str(invocation_ref.version_id)
            or str(publication["firing_version_id"]) != str(firing_ref.version_id)
            or str(publication["net_version_id"]) != str(executable.net_ref.version_id)
            or context.task_ref != _version_from_payload(authority["task_ref"])
            or context.net_instance_ref != executable.net_ref
            or context.team_design_root_ref != executable.team_design_root_ref):
        raise ResourceIntegrityFault("terminal product differs from exact published producing firing")
    transition = next(item for item in executable.transitions if item.transition_id == firing.transition_id)
    binding = _object(core, kernel, transition.operation_binding_ref, "operation_binding/v1")
    spec_ref = _version_from_payload(binding["operation_spec_ref"])
    spec = _object(core, kernel, spec_ref, "operation_spec/v1")
    if (transition.operation_binding_ref != context.operation_binding_ref
            or transition.node_ref != firing.node_ref
            or transition.principal_ref != context.principal_ref
            or spec["operation_id"] != operation.operation_id
            or spec["executor_key"] != operation.executor_key
            or spec["implementation_identity"] != operation.executor_declaration["identity"]
            or spec["implementation_contracts"] != operation.executor_declaration["contracts"]):
        raise ResourceIntegrityFault("terminal firing does not execute the declared composed operation")
    if not any(item.name == firing.transition_id and item.operation == operation_name
               for item in compiled.symbolic.transitions):
        raise ResourceIntegrityFault("terminal origin is not a declared producing operation transition")
    origin = metadata.get("origin", {})
    output_ref = _version_from_payload(origin["primary_ref"])
    output = _object(core, kernel, output_ref, "output_binding/v1")
    producing_ports = tuple(item for item in compiled.ports if item.port_id == output["output_port_id"])
    if len(producing_ports) != 1:
        raise ResourceIntegrityFault("terminal origin lacks its actual compiled producing port")
    carrier_port = port
    port = producing_ports[0]
    if port.schema != carrier_port.schema or port.name not in operation.declaration.outputs:
        raise ResourceIntegrityFault("terminal origin differs from declared producer/carrier schema")
    spec_ports = tuple(item for item in spec["output_ports"] if item["port_id"] == port.port_id)
    if len(spec_ports) != 1:
        raise ResourceIntegrityFault("terminal port lacks its exact operation spec output")
    spec_port = spec_ports[0]
    descriptors = metadata.get("descriptors", {})
    if (origin.get("kind") != "petri_output"
            or origin.get("secondary_ref") != (None if context.activation_ref is None
                else _ref_payload(context.activation_ref))
            or _ref_payload(output_ref) not in binding["output_binding_refs"]
            or output["output_binding_id"] != str(output_ref.entity_id)
            or output["output_binding_version_id"] != str(output_ref.version_id)
            or output["output_port_id"] != port.port_id or output["place"] != port.place
            or output["net_ref"] != _ref_payload(executable.net_ref)
            or output["node_ref"] != _ref_payload(transition.node_ref)
            or output["task_round_ref"] != _ref_payload(context.task_round_ref)
            or output["opaque_action_ref"] != _ref_payload(spec_ref)
            or spec_port["place"] != port.place
            or output["place_ref"] != spec_port["schema_ref"]
            or output["content_schema_id"] != port.schema
            or output["content_schema_ref"] != spec_port["content_schema_ref"]
            or metadata.get("content_schema_ref") != port.schema
            or metadata.get("content_schema_authority_ref") != output["content_schema_ref"]
            or descriptors.get("output_port_id") != port.port_id
            or descriptors.get("place") != port.place
            or descriptors.get("output_outcome_id") != terminal.outcome
            or output.get("declared_outcome_id", terminal.outcome) != terminal.outcome):
        raise ResourceIntegrityFault("terminal product lacks exact output-binding/origin/outcome authority")
    record = core.event_store.ordered_firing_record(firing_ref.version_id)
    settlements = tuple(event for event in record["events"]
        if event.event_type == "transition_firing_settled/v1"
        and event.payload.get("transition_firing_ref") == _ref_payload(firing_ref))
    if len(settlements) != 1:
        raise ResourceIntegrityFault("terminal firing lacks one exact settlement")
    settlement = settlements[0]
    result_ref = _version_from_payload(settlement.payload["operation_result_ref"])
    result = _object(core, kernel, result_ref, "operation_result/v1")
    completion = record["firing_completion"]
    successor = record["successor_checkpoint"]
    producing_carrier = _unwrap_reentry_carrier(
        core, kernel, token)
    producing_token_ref = _producing_token(
        core, kernel, executable, structure, producing_carrier,
        product, port, firing_ref)
    if (record["state"] != "PUBLISHED"
            or result["operation_result_ref"] != _ref_payload(result_ref)
            or result["invocation_ref"] != _ref_payload(invocation_ref)
            or result["transition_firing_ref"] != _ref_payload(firing_ref)
            or _ref_payload(product.as_version_ref()) not in result["output_resource_refs"]
            or result["business_outcome"] != "completed"
            or completion["operation_result_ref"] != _ref_payload(result_ref)
            or completion["invocation_ref"] != _ref_payload(invocation_ref)
            or settlement.payload["successor_checkpoint_ref"] != successor["marking_checkpoint_ref"]
            or successor.get("settled") is not True
            or _ref_payload(producing_token_ref) not in successor["token_refs"]
            or _ref_payload(producing_token_ref) not in record["marking_delta"]["deposited_refs"]
            or str(settlement.transaction_id) != str(publication["published_transaction_id"])
            or str(core.event_store.object_row(result_ref.version_id)["transaction_id"])
                != str(settlement.transaction_id)):
        raise ResourceIntegrityFault("terminal result/token lacks its actual atomic settlement")
    # Reclose the entire selected product bundle, not just the indexed product.
    counts = {name: 0 for name in operation.declaration.outputs}
    by_port = {item.port_id: item.name for item in compiled.ports}
    for value in result["output_resource_refs"]:
        ref = _version_from_payload(value)
        if ref.entity_type != "resource_version/v1":
            raise ResourceIntegrityFault("terminal result contains a non-resource output reference")
        resource = _resource_from_payload({"resource_id": str(ref.entity_id), "resource_version_id": str(ref.version_id)})
        item = kernel._prepared(resource)
        data = item.metadata
        out_ref = _version_from_payload(data["origin"]["primary_ref"])
        out = _object(core, kernel, out_ref, "output_binding/v1")
        name = by_port[out["output_port_id"]]
        if (name not in counts or _ref_payload(out_ref) not in binding["output_binding_refs"]
                or data["producer_ref"] != _ref_payload(invocation_ref)
                or data["origin"]["kind"] != "petri_output"
                or data["descriptors"].get("output_outcome_id") != terminal.outcome):
            raise ResourceIntegrityFault("terminal result bundle differs from declared producer/outcome")
        counts[name] += 1
    from .operation_output_contract import validate_compiled_output_bundle
    validate_compiled_output_bundle(operation.declaration, counts,
        declared_outcomes=(terminal.outcome,), selected_outcome_id=terminal.outcome)
    index_material = {"authority_kind": terminal.key, "terminal_outcome": outcome,
        "entries": [_resource_payload(product)], "upstream_outcome": None,
        "upstream_disposition_refs": [], "terminal_result_ref": _ref_payload(product.as_version_ref()),
        "terminal_occurrence_ref": _ref_payload(firing_ref)}
    evidence_material = {"run_ref": authority["run_ref"], "terminal_occurrence_ref": _ref_payload(firing_ref),
        "producer": terminal.key, "run_outcome": outcome, "terminal_result_ref": _ref_payload(product.as_version_ref()),
        "final_checkpoint_ref": _ref_payload(marking.checkpoint_ref)}
    return authority_ref, authority, index_material, evidence_material


def _terminal_material(core, kernel):
    if not core.event_store.canonical_object_rows(object_type="run_execution_authority/v1"):
        return None
    _ref, authority = current_run_execution_authority(core, kernel)
    if authority["declaration_schema_ref"] != "rpnh/executable_net/v1":
        return None
    _executable, structure, _marking = hydrate_module_runtime(core)
    source = structure.compiled.source
    matches = [material for terminal in (source.terminal, *source.terminal_alternatives)
               if (material := _terminal_material_for_binding(core, kernel, terminal)) is not None]
    if len(matches) > 1:
        raise ResourceIntegrityFault("multiple registered terminal bindings match exact settled products")
    return matches[0] if matches else None


def register_module_terminal(core: _RegistryCore, kernel: _ResourceServiceKernel) -> VersionRef | None:
    """Publish index/evidence/same-lineage authority together, or leave open.

Unsupported or unavailable exact prestates return None. Conflicting immutable
material is an integrity fault, not permission to repair or fabricate closure.
The caller must be the sole execution owner using this Registry's writer epoch.
"""
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(kernel, _ResourceServiceKernel)
            or kernel._ResourceServiceKernel__core is not core):
        raise TypeError("Module terminal requires execution-owner Core and Kernel")
    if core.writer_epoch != core.event_store.writer_epoch:
        raise ResourceIntegrityFault("Module terminal writer epoch is stale")
    material = _terminal_material(core, kernel)
    if material is None:
        # Earlier execution generations retain immutable terminal objects.
        # Current authority, not global object count, selects whether this
        # generation is open or closed.
        if core.event_store.canonical_object_rows(
                object_type="run_execution_authority/v1"):
            _authority_ref, authority = current_run_execution_authority(
                core, kernel)
            if ((authority["status"] == "terminal")
                    != (authority["terminal_evidence_ref"] is not None)):
                raise ResourceIntegrityFault(
                    "current terminal authority is internally inconsistent")
        return None
    authority_ref, authority, index_material, evidence_material = material
    if authority["status"] == "terminal":
        if authority["terminal_evidence_ref"] is None:
            raise ResourceIntegrityFault(
                "terminal authority lacks current terminal evidence")
        evidence_ref = _version_from_payload(
            authority["terminal_evidence_ref"])
        evidence = _object(
            core, kernel, evidence_ref, "run_terminal_evidence/v1")
        index_ref = _version_from_payload(
            evidence["final_result_index_ref"])
        index = _object(core, kernel, index_ref, "final_result_index/v1")
        expected_index = {
            "final_result_index_ref": _ref_payload(index_ref),
            **index_material,
        }
        expected_evidence = {
            "terminal_evidence_ref": _ref_payload(evidence_ref),
            "final_result_index_ref": _ref_payload(index_ref),
            **evidence_material,
        }
        if (index != expected_index or evidence != expected_evidence
                or len({
                    core.event_store.object_row(ref.version_id)[
                        "transaction_id"]
                    for ref in (authority_ref, index_ref, evidence_ref)
                }) != 1):
            raise ResourceIntegrityFault(
                "current terminal closure conflicts with its generation")
        return evidence_ref
    if (authority["terminal_evidence_ref"] is not None
            or authority["status"] == "stopped_by_owner"):
        raise ResourceIntegrityFault(
            "open terminal publication has conflicting run authority")
    index_ref = VersionRef("final_result_index/v1", new_id("terminal_evidence"), new_id("terminal_evidence_version"))
    evidence_ref = VersionRef("run_terminal_evidence/v1", new_id("terminal_evidence"), new_id("terminal_evidence_version"))
    successor_ref = VersionRef("run_execution_authority/v1", authority_ref.entity_id, new_id("run_execution_authority_version"))
    index = {"final_result_index_ref": _ref_payload(index_ref), **index_material}
    evidence = {"terminal_evidence_ref": _ref_payload(evidence_ref), "final_result_index_ref": _ref_payload(index_ref), **evidence_material}
    successor = {**authority, "run_execution_authority_ref": _ref_payload(successor_ref),
                 "status": "terminal", "terminal_evidence_ref": _ref_payload(evidence_ref)}
    documents = ((index_ref, index), (evidence_ref, evidence), (successor_ref, successor))
    for ref, document in documents:
        core.catalog.validate_instance(ref.entity_type, category="object", instance=document)
    # A producing firing may legitimately close more than one execution
    # generation when the owner reopens an already-terminal checkpoint.  The
    # selected final checkpoint is the immutable generation-specific cut, so
    # include it in the command identity instead of conflating those closures.
    tx = core.begin(idempotency_key=(
        "run-terminal-evidence:"
        f"{evidence_material['terminal_occurrence_ref']['version_id']}:"
        f"{evidence_material['final_checkpoint_ref']['version_id']}"))
    for ref, document in documents:
        tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(document), metadata=document, media_type="application/json",
            schema_ref=f"registry_v1/{ref.entity_type}")
    occurrence = _version_from_payload(evidence_material["terminal_occurrence_ref"])
    product = _version_from_payload(evidence_material["terminal_result_ref"])
    checkpoint = _version_from_payload(evidence_material["final_checkpoint_ref"])
    for source, targets in ((index_ref, (occurrence, product)),
            (evidence_ref, (occurrence, product, checkpoint, index_ref)),
            (successor_ref, (authority_ref, evidence_ref, checkpoint))):
        for target in targets:
            tx.relate(TypedRelation(new_id("relation"), "derived_from", source, target), system_owned=True)
    tx.commit()
    return evidence_ref


__all__ = ("register_module_terminal",)
