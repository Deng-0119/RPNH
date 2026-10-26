"""Explicit recovery of one fully registered, unadopted owner edit.

This is a narrow owner entry, not general running-run or effect recovery. An
already adopted edit resumes only at its untouched adoption checkpoint. The
retained declaration is loaded, never lowered by HOST code or republished.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from .module import ModuleDeclaration
from .owner_commands import OwnerControl
from .owner_edits import PendingEdit
from .registration import Registration
from .registry._registry import _RegistryCore
from .registry._event_store.adoption_reads import AdoptionPrefixReads
from .registry.event_store import (
    RegistryConflict, RegistryCorruptError,
    verified_adoption_head, verified_checkpoint_head,
)
from .registry._event_store.net_lineage import validate_registered_net_closure
from .registry.identities import TypedId, new_id
from .registry.models import VersionRef
from .registry.module_nets import ModuleNetPublication
from .registry.module_runtime import hydrate_module_resource_plan, hydrate_module_runtime
from .registry.owner_adoption import OwnerAdoptionRequest, owner_command_document
from .registry.publication import _resource_from_payload, _version_from_payload
from .registry.resource_service import _ResourceServiceKernel
from .registry.resources import ResourceVersionRef
from .registry.run_authority import current_run_execution_authority, RunReadCut, read_run_execution
from .registry.schema_catalog import canonical_json, PROTECTED_SCHEMA_REFS
from .registry.strict_contracts import ref_payload


@dataclass(frozen=True)
class PendingEditResumeResult:
    """RESUMED alone supplies an owner; other outcomes carry read-only evidence."""
    recovery_status: str
    receipt: dict
    owner: object | None = None
    reason: str | None = None



def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _require(condition, message):
    if not condition:
        raise RegistryCorruptError(message)


def _receipt_owner(core):
    """The same original owner filter without a writer, gateway or HOST calls."""
    executable, _structure, _marking = hydrate_module_runtime(core)
    root = _ResourceServiceKernel(core)._exact_object(
        executable.team_design_root_ref, expected_type="team_design_root/v1").metadata
    owner = SimpleNamespace(_core=core,
        identity=SimpleNamespace(task_ref=_version_from_payload(root["task_ref"])),
        principal_ref=_version_from_payload(root["owner_principal_ref"]))
    owner.control = OwnerControl(owner)
    return owner


def _resource_ref(value):
    if type(value) is not dict or set(value) != {"entity_type", "logical_id", "version_id"}:
        raise ValueError("pending edit selector requires exact Resource version refs")
    ref = _version_from_payload(value)
    if ref.entity_type != "resource_version/v1" or not _same(value, ref_payload(ref)):
        raise ValueError("pending edit selector requires exact Resource version refs")
    return ResourceVersionRef(ref.entity_id, ref.version_id)


def _control_record(reads, ref, body):
    metadata = reads.metadata(ref.as_version_ref(), "resource_version/v1")
    _require(metadata["origin_kind"] == "private_system"
        and metadata["descriptors"].get("owner_control_kind") == body["kind"]
        and metadata["descriptors"].get("command_id") == body["command_id"]
        and _same(metadata["reference_provenance"]["derived_from_refs"],
                  sorted(body["lineage"], key=canonical_json)),
        "retained control record differs from ordinary registered provenance")


def _registered_inventory(core, reads, registration):
    durable = {}
    for row in reads.db.execute("SELECT * FROM objects WHERE object_type='resource_version/v1'"):
        metadata = json.loads(row["metadata_json"])
        descriptor = metadata.get("descriptors", {})
        kind, key = descriptor.get("host_registration_kind"), descriptor.get("registered_key")
        if not isinstance(kind, str) or not isinstance(key, str):
            continue
        ref = VersionRef("resource_version/v1", TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]))
        prepared = reads.prepared(ref, "resource_version/v1")
        body = json.loads(core.object_store.read_registered(prepared))
        durable.setdefault((kind, key), []).append(
            {"kind": "schema", "key": key, "schema": body} if kind == "schema" else body)
    for supplied in registration.declarations():
        stored = durable.get((supplied["kind"], supplied["key"]), [])
        _require(len(stored) == 1 and _same(stored[0], supplied),
            "resume HOST registration differs from exact durable inventory")


def _publication(core, reads, net_ref, net, compiled):
    """Hydrate the original address inventory; it confers no new authority."""
    root_ref = _version_from_payload(net["team_design_root_ref"])
    root = reads.metadata(root_ref, "team_design_root/v1")
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    nodes, bindings, executable_refs, operations, outputs = {}, {}, {}, {}, {}
    transitions = {t.name: t for t in compiled.symbolic.transitions}
    for value in net["executable_transition_binding_refs"]:
        ref = _version_from_payload(value)
        body = reads.metadata(ref, "executable_transition_binding/v1")
        name = body["transition_id"]
        nodes[name] = _version_from_payload(body["node_ref"])
        bindings[name] = _version_from_payload(body["operation_binding_ref"])
        executable_refs[name] = ref
        binding = reads.metadata(bindings[name], "operation_binding/v1")
        operations[transitions[name].operation] = _version_from_payload(binding["operation_spec_ref"])
        for output in binding["output_binding_refs"]:
            output_ref = _version_from_payload(output)
            output_body = reads.metadata(output_ref, "output_binding/v1")
            outputs[name, output_body["output_port_id"]] = output_ref
    plan = hydrate_module_resource_plan(core, compiled, net_ref, net, root_ref, root, declaration)
    transaction = reads.db.execute("SELECT transaction_id FROM objects WHERE version_id=?",
        (str(net_ref.version_id),)).fetchone()[0]
    return ModuleNetPublication(_version_from_payload(net["plan_ref"]), root_ref, net_ref,
        declaration, MappingProxyType(nodes), MappingProxyType(operations),
        MappingProxyType(bindings), MappingProxyType(outputs), MappingProxyType(executable_refs),
        MappingProxyType(dict(compiled.place_aliases)), TypedId.parse(transaction), plan)


def _inputs(core, reads, command_id, arguments, root, compiled):
    """Resolve only inputs already published by this exact edit submission."""
    declared = arguments["owner_inputs"]
    _require(type(declared) is dict, "pending owner inputs must be a JSON object")
    resources = {}
    for value in root["resource_refs"]:
        ref = _version_from_payload(value)
        if ref.entity_type != "resource_version/v1":
            continue
        prepared = reads.prepared(ref, "resource_version/v1")
        key = prepared.metadata.get("descriptors", {}).get("owner_input_command_id")
        if isinstance(key, str) and key.startswith(command_id + ":owner-input:"):
            _require(key not in resources, "pending owner input has conflicting publications")
            resources[key] = ResourceVersionRef(ref.entity_id, ref.version_id), prepared
    selected, expected = {}, set()
    for place, values in declared.items():
        _require(type(values) is list, "pending owner inputs must be explicit arrays")
        bundle = []
        for index, value in enumerate(values):
            _require(type(value) is dict and set(value) == {"schema_id", "value", "summary"},
                "pending owner input declaration is incomplete")
            key = f"{command_id}:owner-input:{place}:{index}"
            expected.add(key)
            _require(key in resources, "pending owner input is not fully registered")
            ref, prepared = resources[key]
            _require(prepared.metadata["task_ref"] == root["task_ref"]
                and prepared.metadata["origin_kind"] == "private_system"
                and prepared.metadata["content_schema_ref"] == value["schema_id"]
                and prepared.metadata["summary"] == value["summary"]
                and core.object_store.read_registered(prepared) == canonical_json(value["value"]),
                "pending owner input differs from immutable candidate data")
            bundle.append(ref)
        selected[place] = tuple(bundle)
    _require(set(resources) == expected, "pending owner input inventory differs from its command")
    places = {p.name for p in compiled.symbolic.places}
    resource_symbols = {item.name for item in compiled.symbolic.lease_identities if item.kind == "resource"}
    _require(not (set(selected) - places - resource_symbols), "pending owner input key is undeclared")
    return {place: bundle for place, bundle in selected.items() if place in places}


@dataclass(frozen=True)
class _Pending:
    cut: RunReadCut
    material: object
    candidate_record: ResourceVersionRef
    publication: ModuleNetPublication
    command_ref: ResourceVersionRef
    data: dict
    arguments: dict
    compiled: object
    marking: object
    owner_inputs: dict
    receipt: dict


def _blocked(receipt, reason, *, conflict=False):
    status = ("UNSUPPORTED_CONTINUATION" if receipt.get("status") == "ADOPTED"
              else "CONFLICT" if conflict else "BLOCKED")
    return PendingEditResumeResult(status, receipt, reason=reason)


def _preflight(core, registration, *, model_condition, edit_command_id,
               owner_command_ref, candidate_ref):
    """Read-only classification before writer acquisition, repeated after it.

    Control/candidate closure uses one SQLite read transaction. Existing current
    runtime and registration readers keep their original policies; their reads
    are bracketed by an unchanged physical fact cut. Legal append or promotion,
    metadata change, or a competing writer during those reads rejects the pass.
    No generic snapshot adapter or alternate authority reader is introduced.
    """
    cut = RunReadCut.capture(core)
    owner = _receipt_owner(core)
    receipt = owner.control.edits.read_receipt(edit_command_id)
    with core.event_store.connect() as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        cut.assert_unchanged(core)
        reads = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, terminal=True)
        commands = [(ref, body) for ref, body in owner.control.records("command", _reads=reads)
                    if body["command_id"] == edit_command_id]
        if len(commands) != 1:
            return _blocked(receipt, "one fully published owner edit command is required")
        command_ref, command = commands[0]
        if (command_ref != owner_command_ref
                or not _same(command["data"].get("candidate_ref"), ref_payload(candidate_ref.as_version_ref()))):
            return _blocked(receipt, "retained command/candidate selector differs", conflict=True)
        if receipt["status"] not in {"ADOPTED", "REGISTERED", "DRAINING"}:
            result = _blocked(receipt, "recorded edit state is not recoverable pending work")
        else:
            result = _pending(core, registration, reads, owner, cut, command_ref, command,
                candidate_ref, receipt, model_condition)
    cut.assert_unchanged(core)
    return result


def _pending(core, registration, reads, owner, cut, command_ref, command,
             candidate_ref, receipt, model_condition):
    from .run import _load_resume_material, _validate_resume_registration
    from .budgets import validate_budget_binding
    command_id, data = command["command_id"], command["data"]
    _require(set(data) == {"command", "base_net_ref", "submission_checkpoint_ref",
        "candidate_net_ref", "candidate_ref", "owner_principal_ref", "control_authority_ref"},
        "retained edit command is incomplete")
    adopted = receipt["status"] == "ADOPTED"
    candidates = owner.control.records("candidate", _reads=reads)
    results = owner.control.records("result", _reads=reads)
    unresolved = []
    for ref, candidate in candidates:
        statuses = [body["data"].get("status") for _, body in results
                    if body["command_id"] == candidate["command_id"]]
        if not statuses or statuses[-1] not in {"ADOPTED", "CONFLICT", "RETRACTED"}:
            unresolved.append((ref, candidate))
    if ((adopted and unresolved) or (not adopted and
            (len(unresolved) != 1 or unresolved[0][1]["command_id"] != command_id))):
        return _blocked(receipt, "pending edit order is not uniquely established")
    matching = [(ref, body) for ref, body in candidates if body["command_id"] == command_id]
    _require(len(matching) == 1, "pending edit lacks one immutable candidate record")
    candidate_record, candidate = matching[0]
    candidate_ids = {body["command_id"] for _, body in candidates}
    if any(body["command_id"] not in candidate_ids and "edit_command_id" not in body["data"]
           and body["data"].get("status") in {"REGISTERED", "DRAINING", "NEEDS_MARKING_DECISION", "ADOPTED"}
           for _, body in results):
        return _blocked(receipt, "another edit result lacks its immutable candidate")
    if any(body["data"].get("command") == "edit" and body["command_id"] not in candidate_ids
           for _, body in owner.control.records("command", _reads=reads)):
        return _blocked(receipt, "another edit command lacks its immutable candidate")
    arguments = candidate["data"]
    required = {"candidate", "base_net_ref", "marking_mapping", "retire_token_refs", "owner_inputs"}
    _require(set(arguments) == required and type(arguments["marking_mapping"]) is dict
        and type(arguments["retire_token_refs"]) is list, "pending candidate arguments are incomplete")
    if reads.db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' LIMIT 1").fetchone():
        return _blocked(receipt, "active or provisional firing outcome is unresolved")
    kernel = _ResourceServiceKernel(core)
    execution = read_run_execution(core, kernel, cut=cut)
    _authority_ref, authority = current_run_execution_authority(core, kernel,
        expected_model_condition=model_condition, view=cut.view)
    if authority["status"] != "running" or authority["terminal_evidence_ref"] is not None:
        return _blocked(receipt, "pending recovery requires the original running authority")
    material = _load_resume_material(core, registration)
    _registered_inventory(core, reads, registration)
    _control_record(reads, candidate_record, candidate)
    _control_record(reads, command_ref, command)
    _require(candidate["target"] is None and _same(candidate["lineage"],
        [ref_payload(material.original_input_ref.as_version_ref())]),
        "retained candidate differs from original input lineage")
    command_ordinal = reads.db.execute("SELECT e.ordinal FROM objects o JOIN events e "
        "ON e.event_id=o.published_event_id WHERE o.version_id=?",
        (str(command_ref.resource_version_id),)).fetchone()[0]
    for ref, _body in owner.control.records("input_revision", _reads=reads):
        ordinal = reads.db.execute("SELECT e.ordinal FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id WHERE o.version_id=?",
            (str(ref.resource_version_id),)).fetchone()[0]
        if ordinal > command_ordinal:
            return _blocked(receipt, "owner input authority changed after submission", conflict=True)
    from .pn_validation.runtime_gate import restore_validation
    restore_validation(owner)
    executable, structure, marking = hydrate_module_runtime(core)
    base = _version_from_payload(data["base_net_ref"])
    net_ref = _version_from_payload(data["candidate_net_ref"])
    expected_net = net_ref if adopted else base
    if executable.net_ref != expected_net or not _same(arguments["base_net_ref"], ref_payload(base)):
        return _blocked(receipt, "base NET changed; recovery cannot rebase", conflict=True)
    checkpoint = reads.metadata(marking.checkpoint_ref, "marking_checkpoint/v1")
    if (not checkpoint["settled"] or authority["latest_checkpoint_ref"] != ref_payload(marking.checkpoint_ref)
            or authority["declaration_ref"] != ref_payload(executable.declaration_resource_ref.as_version_ref())
            or verified_adoption_head(core.event_store, core.catalog, core.task_id, _db=reads.db) != expected_net
            or verified_checkpoint_head(core.event_store, core.catalog, core.task_id, expected_net, _db=reads.db) != marking.checkpoint_ref):
        return _blocked(receipt, "current run and settled checkpoint disagree")
    if execution.checkpoint_ref != marking.checkpoint_ref:
        return _blocked(receipt, "current execution read differs from settled marking")
    if adopted and (not _same(receipt["checkpoint_ref"], ref_payload(marking.checkpoint_ref))
            or reads.db.execute("SELECT 1 FROM firing_publications WHERE net_version_id=? LIMIT 1",
                (str(net_ref.version_id),)).fetchone()):
        return _blocked(receipt, "adopted candidate has already advanced or admitted a firing")
    # Allocation is durable before invocation publication; zero provisional
    # firings alone cannot prove that this checkpoint has no outstanding claim.
    if reads.db.execute("SELECT 1 FROM objects WHERE object_type='firing_allocation/v1' "
            "AND json_extract(metadata_json,'$.checkpoint_ref.version_id')=? LIMIT 1",
            (str(marking.checkpoint_ref.version_id),)).fetchone():
        return _blocked(receipt, "current checkpoint has a registered firing allocation")
    net = validate_registered_net_closure(core.event_store, core.catalog, net_ref,
        _db=reads.db, _memo={}, _prefix_reads=reads)
    compiled = reads.compiled(candidate_ref.as_version_ref())
    root = reads.metadata(_version_from_payload(net["team_design_root_ref"]), "team_design_root/v1")
    old_net = validate_registered_net_closure(core.event_store, core.catalog, base,
        _db=reads.db, _memo={})
    old_root = reads.metadata(_version_from_payload(old_net["team_design_root_ref"]), "team_design_root/v1")
    _require(_resource_from_payload(net["team_net_declaration_resource_ref"]) == candidate_ref
        and root["run_ref"] == old_root["run_ref"] == authority["run_ref"]
        and root["task_ref"] == old_root["task_ref"] == authority["task_ref"]
        and root["owner_principal_ref"] == old_root["owner_principal_ref"] == ref_payload(material.principal_ref)
        and root["task_round_ref"] == old_root["task_round_ref"] == ref_payload(material.task_round_ref),
        "pending candidate crosses original run/task/principal authority")
    _require(_same(ModuleDeclaration.from_dict(arguments["candidate"]).to_dict(), compiled.source.to_dict()),
        "pending compiled source differs from canonical submitted candidate")
    _validate_resume_registration(core, registration, compiled)
    for kind, declarations in compiled.registrations.items():
        for key, expected in declarations.items():
            _require(_same(registration.declaration(kind, key), expected),
                "pending candidate HOST registration differs canonically")
    control = reads.metadata(_version_from_payload(data["control_authority_ref"]), "user_authority_decision/v1")
    _require(control["status"] == "effective" and control["authority_kind"] in {"scope", "architecture"}
        and control["user_principal_ref"] == root["owner_principal_ref"]
        and root["task_ref"] in control["governed_artifact_refs"]
        and data["control_authority_ref"] in root["artifact_refs"]
        and data["control_authority_ref"] in old_root["artifact_refs"],
        "pending command lacks effective owner control authority")
    # An immutable effective document can itself have a later superseding decision.
    for row in reads.db.execute("SELECT metadata_json FROM objects WHERE object_type='user_authority_decision/v1'"):
        if _same(json.loads(row[0]).get("supersedes_ref"), data["control_authority_ref"]):
            return _blocked(receipt, "owner control authority was superseded", conflict=True)
    submitted = reads.metadata(_version_from_payload(data["submission_checkpoint_ref"]), "marking_checkpoint/v1")
    _require(submitted["net_instance_ref"] == ref_payload(base), "pending submission checkpoint differs from base")
    manifest = reads.metadata(material.recovery_manifest_ref, "task_recovery_manifest/v1")
    _require(_same(compiled.source.to_dict()["budget_buckets"], manifest["budget_buckets"]),
        "pending candidate budget inventory differs from original manifest")
    for value in net["operation_binding_refs"]:
        binding = reads.metadata(_version_from_payload(value), "operation_binding/v1")
        _require(binding["authority_decision_ref"] == data["control_authority_ref"],
            "pending operation binding differs from control authority")
        validate_budget_binding(manifest, binding)
    publication = _publication(core, reads, net_ref, net, compiled)
    request = OwnerAdoptionRequest(command_id, command_ref, candidate_ref, material.principal_ref,
        _version_from_payload(data["control_authority_ref"]), base,
        _version_from_payload(data["submission_checkpoint_ref"]), net_ref, marking.checkpoint_ref,
        marking.checkpoint_ref, marking.epoch, marking.next_token_id, (), (), (), (), ())
    _require(_same(command, owner_command_document(request)), "pending command closure differs from immutable request")
    for ref, body in results:
        if body["command_id"] != command_id:
            continue
        _control_record(reads, ref, body)
        _require(body["data"].get("status") in ({"REGISTERED", "DRAINING", "ADOPTED"} if adopted else {"REGISTERED", "DRAINING"})
            and body["target"] == str(net_ref.entity_id)
            and _same(body["lineage"], [ref_payload(command_ref.as_version_ref()), ref_payload(candidate_ref.as_version_ref())])
            and _same(body["data"].get("owner_command_ref"), ref_payload(command_ref.as_version_ref()))
            and _same(body["data"].get("candidate_ref"), ref_payload(candidate_ref.as_version_ref())),
            "pending status does not close the original command/candidate")
    source_ref = _resource_ref(receipt["result_ref"]) if adopted else command_ref
    row = reads.db.execute("SELECT t.writer_epoch FROM objects o JOIN transactions t "
        "ON t.transaction_id=o.transaction_id WHERE o.version_id=?", (str(source_ref.resource_version_id),)).fetchone()
    source_epoch = row[0]
    if (source_epoch > cut.writer_epoch or reads.db.execute(
            "SELECT 1 FROM transactions WHERE writer_epoch>? LIMIT 1", (source_epoch,)).fetchone()
            or reads.db.execute("SELECT 1 FROM events WHERE writer_fencing_epoch>? LIMIT 1", (source_epoch,)).fetchone()):
        return _blocked(receipt, "later writer facts prevent pending recovery", conflict=True)
    inputs = _inputs(core, reads, command_id, arguments, root, compiled)
    from .registry.owner_mapping import plan_owner_mapping
    retirement = tuple(_version_from_payload(value) for value in arguments["retire_token_refs"])
    if not adopted:
        decision = plan_owner_mapping(core, structure.compiled, compiled, marking,
            arguments["marking_mapping"], retirement, inputs)
        if decision.status != "READY":
            return _blocked(receipt, "retained mapping needs an explicit owner decision")
        from .registry.owner_mapping import preview_owner_mapping
        from .pn_validation.runtime_gate import analyze_owner_preview
        analyze_owner_preview(owner, preview_owner_mapping(core, publication, marking, decision))
    return _Pending(cut, material, candidate_record, publication, command_ref, data,
        arguments, compiled, marking, inputs, receipt)


def resume_pending_edit(registration: Registration, *, run_dir, model_condition,
                        edit_command_id, owner_command_ref, candidate_ref,
                        catalog=None, host_execution_bindings=None):
    """Explicitly recover one retained pending command, without running a suffix.

    An adopted command resumes only at its exact current adoption checkpoint,
    before any suffix allocation or firing. Other adopted cuts return the
    original receipt with UNSUPPORTED_CONTINUATION and owner=None. A receipt
    alone is not a resumed session or a completed report. No suffix is executed.
    Ordinary resume_run eligibility is intact.
    """
    if (not isinstance(registration, Registration) or type(model_condition) is not str
            or not model_condition or type(edit_command_id) is not str or not edit_command_id):
        raise TypeError("pending edit recovery requires registration, exact model and command id")
    selector = dict(model_condition=model_condition, edit_command_id=edit_command_id,
        owner_command_ref=_resource_ref(owner_command_ref), candidate_ref=_resource_ref(candidate_ref))
    preflight = _RegistryCore(Path(run_dir), create=False, read_only=True, catalog=catalog)
    registration.bind_schema_catalog(preflight.catalog)
    selected = _preflight(preflight, registration, **selector)
    if isinstance(selected, PendingEditResumeResult):
        return selected
    core = _RegistryCore(Path(run_dir), create=False, catalog=catalog)
    # No retry: acquiring a fence does not permit using changed preflight facts.
    checked = _preflight(core, registration, **selector)
    if (not isinstance(checked, _Pending) or checked.cut.physical_head != selected.cut.physical_head
            or checked.cut.task_id != selected.cut.task_id
            or checked.cut.native_run_ref != selected.cut.native_run_ref
            or checked.cut.writer_epoch != selected.cut.writer_epoch + 1
            or core.writer_epoch != checked.cut.writer_epoch
            or checked.material.identity != selected.material.identity
            or checked.material.recovery_manifest_ref != selected.material.recovery_manifest_ref
            or checked.material.principal_ref != selected.material.principal_ref
            or checked.material.original_input_ref != selected.material.original_input_ref
            or not _same(checked.data, selected.data)
            or not _same(checked.arguments, selected.arguments)
            or checked.marking.checkpoint_ref != selected.marking.checkpoint_ref):
        return _blocked(selected.receipt, "Registry changed after writer acquisition", conflict=True)
    from .run import RunOwner
    from .registry.registration_gateway import RegistryRegistrationGateway
    from .registry.operations import bind_operation_registration
    material = checked.material
    gateway = RegistryRegistrationGateway(core, material.identity.task_ref, material.bootstrap_ref)
    registration.bind_schema_catalog(core.catalog)
    registration.bind_gateway(gateway)
    bind_operation_registration(registration)
    for schema_id in set(material.structure.compiled.source.required_schemas) | set(checked.compiled.source.required_schemas):
        if schema_id in PROTECTED_SCHEMA_REFS and schema_id not in gateway.schema_refs:
            gateway.bind_builtin_schema(schema_id)
    owner = RunOwner(core, registration, material.identity, None, material.original_input_ref,
        material.principal_ref, material.task_round_ref, material.bootstrap_ref, gateway, material.budgets)
    owner.host_execution_bindings = host_execution_bindings
    owner.current_input_ref = owner.control.current_input()
    from .pn_validation.runtime_gate import restore_validation
    restore_validation(owner)
    checked.cut.assert_unchanged(core)
    if checked.receipt["status"] == "ADOPTED":
        owner.publication = checked.publication
        receipt = checked.receipt
    else:
        marking, data = checked.marking, checked.data
        checkpoint = VersionRef("marking_checkpoint/v1", new_id("marking_checkpoint"), new_id("marking_checkpoint_version"))
        request = OwnerAdoptionRequest(edit_command_id, checked.command_ref, checked.publication.declaration_resource_ref,
            material.principal_ref, _version_from_payload(data["control_authority_ref"]),
            _version_from_payload(data["base_net_ref"]), _version_from_payload(data["submission_checkpoint_ref"]),
            checked.publication.net_ref, marking.checkpoint_ref, checkpoint, marking.epoch,
            marking.next_token_id, (), (), (), (), ())
        owner.control.edits.queue.append(PendingEdit(edit_command_id, checked.candidate_record,
            checked.publication, request, material.structure.compiled, checked.compiled,
            checked.arguments["marking_mapping"], tuple(_version_from_payload(value)
                for value in checked.arguments["retire_token_refs"]), checked.owner_inputs,
            status=checked.receipt["status"]))
        owner.admission_paused = True
        receipt = owner.control.edits.advance()
        if receipt["status"] != "ADOPTED":
            raise RegistryConflict("pending recovery did not adopt the preflighted command")
    from .registry.run_authority import record_recovered_run_entry
    entry_cut = RunReadCut.capture(core)
    if entry_cut.writer_epoch != core.writer_epoch:
        raise RegistryConflict("pending recovery writer was superseded before entry")
    record_recovered_run_entry(core, _ResourceServiceKernel(core),
        immutable_input_ref=material.original_input_ref,
        mutable_stage_ref=checked.publication.declaration_resource_ref,
        recovery_manifest_ref=material.recovery_manifest_ref)
    entry_cut.assert_unchanged(core)
    return PendingEditResumeResult("RESUMED", receipt, owner=owner)


__all__ = ("PendingEditResumeResult", "resume_pending_edit")
