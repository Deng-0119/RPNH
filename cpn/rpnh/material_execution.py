"""Deterministic literal material preparation on the original Owner and PN.

The HOST explicitly registers this executor and its atomic-write tool, and
provides an ordinary workspace lineage binding. A started operation consumes
only its admitted Owner input, stages one semantic output and one workspace
file, and finalizes the original workspace candidate. It never performs
business Success, changes a Viewer draft, or creates an AgentLoop.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from types import MappingProxyType

from .executable_net import load_compiled_net
from .file_execution_net import (
    FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET, execute_idempotent_materialization,
    execution_parent, execution_states_for_parent,
)
from .material_types import (
    AGENT_TEXT_SCHEMA, agent_text_publication, agent_text_workspace_bytes,
    decode_agent_text,
)
from .module import ModuleDeclaration
from .registry.errors import ResourceIntegrityFault
from .registry.event_store import RegistryConflict
from .registry.identities import TypedId, new_id
from .registry.models import VersionRef
from .registry.module_host_bindings import (
    HostExecutionBinding, ModuleHostBindingPlan, preserved_workspace_authority,
    validate_host_execution_bindings,
)
from .registry.operation_execution import verify_operation_execution
from .registry.operations import (
    OperationAuthorityError, OperationExecutionResult,
    register_operation_outputs,
)
from .registry.publication import _resource_from_payload, _version_from_payload
from .registry.resource_service import _resolve_registry_workspace_root
from .registry.resource_verification import verify_resource
from .registry.resources import (
    AddressBindingIntent, PublishResource, ResourceAddress,
    ResourceAddressBindingRef, ResourceVersionRef, WorkspaceWriteOrigin,
)
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import _registered, ref_payload
from .workspace_settlement import (
    _archive_files, _firing_workspace_view, _full_archive, _restore_tree,
    _workspace_resource_state, finalize_firing_workspace_candidate,
    workspace_finalization_candidate,
)


MATERIAL_EXECUTOR = "rpnh/material-literal-executor/v1"
MATERIAL_TOOL = "rpnh/material-literal-write/v1"
MATERIAL_CONFIG_SCHEMA = "application/rpnh_material_execution_config/v1"
EXECUTOR_IDENTITY = {
    "implementation_id": "rpnh.material_literal_executor", "revision": "v1",
}
EXECUTOR_CONTRACTS = {
    "transport": "deterministic", "input_ports": None, "output_ports": None,
    "config_schema": MATERIAL_CONFIG_SCHEMA,
    "owner_material_commands": "rpnh/material_command/v1",
}
TOOL_IDENTITY = {
    "implementation_id": "rpnh.material_literal_write", "revision": "v1",
}
TOOL_CONTRACTS = {
    "binding_protocol": "rpnh/material_literal_write/v1",
    "input_document_type": AGENT_TEXT_SCHEMA,
    "workspace_encoding": "utf-8",
}
CONFIG_SCHEMA = {
    "$id": MATERIAL_CONFIG_SCHEMA,
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object", "additionalProperties": False,
    "properties": {
        "workspace_relative_path": {"type": "string", "minLength": 1},
        "output_port_id": {"type": "string", "minLength": 1},
        "outcome_id": {"type": "string", "minLength": 1},
    },
    "required": ["workspace_relative_path", "output_port_id", "outcome_id"],
}


def is_literal_producer_spec(spec):
    """Recognize the exact original literal producer variants, not a label.

    Both variants run the same registered FILE/workspace operation. The
    terminal grant belongs only to its separately adopted identity/contract;
    carrier inspection and dedicated-input drain share this exact predicate.
    """
    from .terminal_management import (
        TERMINAL_LITERAL_EXECUTOR, TERMINAL_LITERAL_IDENTITY, TERMINAL_LITERAL_CONTRACTS,
    )
    variants = ((MATERIAL_EXECUTOR, EXECUTOR_IDENTITY, EXECUTOR_CONTRACTS),
        (TERMINAL_LITERAL_EXECUTOR, TERMINAL_LITERAL_IDENTITY, TERMINAL_LITERAL_CONTRACTS))
    return (spec.get("transport") == "deterministic"
        and spec.get("allowed_tool_ids") == [MATERIAL_TOOL]
        and any(spec.get("executor_key") == key and spec.get("implementation_identity") == identity
                and spec.get("implementation_contracts") == contracts
                for key, identity, contracts in variants))


def write_literal_material(*, root, relative_path, value, before_replace):
    """Real registered tool: exact UTF-8 bytes, original atomic file writer."""
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    return WorkspaceExecutionMixin._write_workspace_bytes(
        root, relative_path, agent_text_workspace_bytes(value),
        before_replace=before_replace)


def material_operation_executor(*, execution, gateway, resources, host_context):
    """Standard worker ABI; all mutation stays on the original Owner gateway."""
    del resources, host_context
    return gateway.prepare_material_operation(execution)


def register_material_execution(registration):
    """Explicit HOST opt-in; this does not enable any Viewer capability."""
    registration.register_schema(MATERIAL_CONFIG_SCHEMA, CONFIG_SCHEMA)
    registration.register_tool(MATERIAL_TOOL, write_literal_material,
        identity=TOOL_IDENTITY, contracts=TOOL_CONTRACTS)
    registration.register_executor(MATERIAL_EXECUTOR, material_operation_executor,
        identity=EXECUTOR_IDENTITY, contracts=EXECUTOR_CONTRACTS)


def _material_host_publication(core, key, kind):
    """Read the original one-object static transaction, including its outbox."""
    from .registry._event_store.collaboration_descriptors import exact_prepared
    store = core.event_store
    with store.connect() as db:
        db.execute("BEGIN")
        transaction = db.execute(
            "SELECT * FROM transactions WHERE task_id=? AND idempotency_key=?",
            (str(core.task_id), key)).fetchone()
        if transaction is None:
            if db.execute("SELECT 1 FROM events WHERE task_id=? AND idempotency_key=?",
                          (str(core.task_id), key)).fetchone() is not None:
                raise RegistryConflict("material HOST key has facts without its original transaction")
            return None
        rows = db.execute("SELECT * FROM objects WHERE transaction_id=?",
                          (transaction["transaction_id"],)).fetchall()
        events = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal",
                            (transaction["transaction_id"],)).fetchall()
        relations = db.execute("SELECT 1 FROM relations WHERE transaction_id=?",
                               (transaction["transaction_id"],)).fetchall()
        if (transaction["status"] != "committed" or len(rows) != 1 or relations
                or [item["event_type"] for item in events]
                != ["object_version_published/v1", "transaction_committed/v1"]
                or rows[0]["object_type"] != kind
                or rows[0]["producer_invocation_id"] is not None
                or any(item["idempotency_key"] != key or item["command_id"] != key
                       or item["branch_id"] != core.branch_id
                       or item["producer_invocation_id"] is not None for item in events)
                or json.loads(events[-1]["payload_json"])
                != {"object_count": 1, "relation_count": 0, "fact_count": 1}):
            raise RegistryConflict("material HOST key lacks its exact static publication")
        members = [("transaction", transaction["transaction_id"]),
                   ("object", rows[0]["version_id"])]
        members.extend(("event", item["event_id"]) for item in events)
        if any(db.execute("SELECT 1 FROM firing_temporary_members "
                          "WHERE member_kind=? AND member_identity=?", member).fetchone()
               is not None for member in members):
            raise RegistryConflict("material HOST static publication cannot belong to a firing")
        store._verified_persisted_event_record(db, events[-1])
        reference = {"entity_type": kind, "logical_id": rows[0]["logical_id"],
                     "version_id": rows[0]["version_id"]}
        prepared = exact_prepared(db, core.object_store, core.task_id, reference)
        archive = kind == "workspace_revision/v1"
        if (prepared.schema_ref != f"registry_v1/{kind}"
                or prepared.media_type != ("application/x-tar" if archive else "application/json")
                or core.object_store.read_registered(prepared)
                != (_full_archive({}) if archive else canonical_json(dict(prepared.metadata)))):
            raise RegistryConflict("material HOST publication lost its original exact bytes")
        return prepared


def material_host_bindings(*, core, plan, source_ref, bootstrap_ref, compiled):
    """Publish true agents and workspace templates, with no model or loop."""
    if core.read_only or not isinstance(plan, ModuleHostBindingPlan):
        raise TypeError("material HOST binding requires the original writable Owner")
    if (json.loads(core.event_store.get_meta("native_run_ref") or "null") != ref_payload(plan.run_ref)
            or json.loads(core.event_store.get_meta("bootstrap_command_ref") or "null") != ref_payload(bootstrap_ref)):
        raise ValueError("material HOST plan differs from this original run/bootstrap")
    _, run = _registered(core, plan.run_ref, "native_run_identity/v1")
    _, round_data = _registered(core, plan.task_round_ref, "task_round/v1")
    if (run["task_ref"] != ref_payload(plan.task_ref) or plan.task_ref.entity_id != core.task_id
            or run["branch_id"] != core.branch_id or round_data["task_id"] != str(core.task_id)
            or round_data.get("task_branch_ref") != run["task_branch_ref"]
            or any(core.event_store.canonical_object_row(ref.version_id) is None
                   for ref in (plan.run_ref, plan.task_round_ref, bootstrap_ref))):
        raise ValueError("material HOST plan differs from its original task/round/branch")
    _, source = _registered(core, source_ref.as_version_ref(), "resource_version/v1")
    authored = ModuleDeclaration.from_json(core.object_store.read_registered(
        core.get_version(source_ref.resource_version_id)).decode("utf-8"))
    if (source["content_schema_ref"] != "rpnh/module_declaration/v1"
            or source["task_ref"] != ref_payload(plan.task_ref)
            or source["producer_ref"] != ref_payload(bootstrap_ref)
            or source["branch_id"] != core.branch_id or authored != compiled.source
            or core.event_store.canonical_object_row(source_ref.resource_version_id) is None):
        raise ValueError("material HOST requires the exact canonical authored Module")
    _registered(core, bootstrap_ref, "bootstrap_command/v1")
    names = {item.name for item in compiled.symbolic.transitions}
    if set(plan.node_refs) != names or set(plan.binding_refs) != names:
        raise ValueError("material HOST plan differs from its exact transitions")
    net_row = core.event_store.canonical_object_row(plan.net_ref.version_id)
    if net_row is not None:
        _, net = _registered(core, plan.net_ref, "net_instance/v1")
        if (net["plan_ref"] != ref_payload(plan.plan_ref)
                or net["team_design_root_ref"] != ref_payload(plan.root_ref)
                or net["task_round_ref"] != ref_payload(plan.task_round_ref)
                or set(map(canonical_json, net["node_refs"]))
                != {canonical_json(ref_payload(ref)) for ref in plan.node_refs.values()}
                or set(map(canonical_json, net["operation_binding_refs"]))
                != {canonical_json(ref_payload(ref)) for ref in plan.binding_refs.values()}
                or set(map(canonical_json, net["output_binding_refs"]))
                != {canonical_json(ref_payload(ref)) for ref in plan.output_refs.values()}):
            raise ValueError("material HOST replay changed its original published graph plan")
    operations = {item.declaration.name: item for item in compiled.operations}
    managed = {}
    for transition in compiled.symbolic.transitions:
        operation = operations[transition.operation]
        contracts = operation.executor_declaration["contracts"]
        if (contracts.get("owner_material_commands") == "rpnh/material_command/v1"
                and contracts.get("config_schema") == MATERIAL_CONFIG_SCHEMA
                and contracts.get("transport") == "deterministic"):
            managed[transition.name] = operation
    if not managed:
        return MappingProxyType({})
    key = f"material-host:{plan.net_ref.version_id}"

    def publish(kind, logical_kind, version_kind, build, suffix, payload=None):
        saved = _material_host_publication(core, f"{key}:{suffix}", kind)
        ref = (VersionRef(kind, saved.logical_id, saved.version_id) if saved is not None
               else VersionRef(kind, new_id(logical_kind), new_id(version_kind)))
        metadata = build(ref)
        core.catalog.validate_instance(kind, category="object", instance=metadata)
        body = canonical_json(metadata) if payload is None else payload
        media_type = "application/json" if payload is None else "application/x-tar"
        if saved is not None:
            if (ref.entity_id.kind != logical_kind or ref.version_id.kind != version_kind
                    or saved.metadata != metadata or saved.media_type != media_type
                    or saved.schema_ref != f"registry_v1/{kind}"
                    or core.object_store.read_registered(saved) != body):
                raise RegistryConflict("material HOST replay changed its original static object")
            return ref
        core.publish_bytes(object_type=kind, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=body, metadata=metadata, media_type=media_type,
            schema_ref=f"registry_v1/{kind}", idempotency_key=f"{key}:{suffix}")
        return ref

    # A committed template fixes its original genesis even after adoption or
    # later Success changes the live workspace head. Never replace that base
    # with whatever happens to be current when this factory is called again.
    templates = tuple(saved for name in managed if (saved := _material_host_publication(
        core, f"{key}:workspace:{name}", "workspace_binding/v1")) is not None)
    retained = {_version_from_payload(saved.metadata["base_revision_ref"]) for saved in templates}
    if (len(retained) > 1 or any(saved.metadata["workspace_lineage_ref"]
                               != saved.metadata["base_revision_ref"] for saved in templates)):
        raise RegistryConflict("material HOST templates do not retain one exact genesis")
    saved_genesis = _material_host_publication(core, f"{key}:workspace-genesis", "workspace_revision/v1")
    from .registry.event_store import verified_adoption_head, verified_checkpoint_head
    current_net = (verified_adoption_head(core.event_store, core.catalog, core.task_id)
                   if core.event_store.list_events_by_type(("net_adopted/v1",)) else None)
    if retained:
        genesis, = retained
        if (saved_genesis is not None
                and genesis != VersionRef("workspace_revision/v1", saved_genesis.logical_id, saved_genesis.version_id)):
            raise RegistryConflict("material HOST template changed its original genesis transaction")
    elif saved_genesis is None and current_net is not None and current_net != plan.net_ref:
        checkpoint_ref = verified_checkpoint_head(
            core.event_store, core.catalog, core.task_id, current_net)
        _, checkpoint = _registered(core, checkpoint_ref, "marking_checkpoint/v1")
        heads = tuple(_version_from_payload(item)
                      for item in checkpoint["workspace_revision_refs"])
        if len(heads) != 1:
            raise ValueError("material replacement requires one exact workspace lineage")
        genesis, _evidence = preserved_workspace_authority(core, plan, heads[0])
    else:
        genesis = publish("workspace_revision/v1", "workspace_lineage", "workspace_revision",
            lambda ref: {
                "workspace_lineage_id": str(ref.entity_id),
                "workspace_revision_id": str(ref.version_id),
                "workspace_revision_ref": ref_payload(ref),
                "run_ref": ref_payload(plan.run_ref), "task_ref": ref_payload(plan.task_ref),
                "net_instance_ref": ref_payload(plan.net_ref),
                "parent_revision_ref": None, "base_revision_ref": None,
                "producer_invocation_ref": None, "transition_firing_ref": None,
                "firing_workspace_binding_ref": None, "reopen_authorization_ref": None,
                "disposition": "genesis", "changed_paths": [], "deleted_paths": [],
                "path_deltas": [], "inventory_paths": [], "conflict_paths": [],
                "semantic_output_refs": [], "trace_summary_refs": [],
                "payload_kind": "full_workspace_tar", "settled": True,
            }, "workspace-genesis", _full_archive({}))
    result = {}
    for name, operation in managed.items():
        agent = publish("agent/v1", "agent", "agent_version", lambda ref: {
            "agent_id": str(ref.entity_id), "agent_version_id": str(ref.version_id),
            "agent_ref": ref_payload(ref),
            "team_net_declaration_ref": ref_payload(source_ref.as_version_ref()),
            "declared_agent_id": operation.declaration.name, "shadow_transition_id": name,
        }, f"agent:{name}")
        template = publish("workspace_binding/v1", "workspace_binding", "workspace_binding_version",
            lambda ref: {
                "workspace_binding_id": str(ref.entity_id),
                "workspace_binding_version_id": str(ref.version_id),
                "operation_binding_ref": ref_payload(plan.binding_refs[name]),
                "binding_kind": "lineage_template", "workspace_lineage_ref": ref_payload(genesis),
                "base_revision_ref": ref_payload(genesis), "template_binding_ref": None,
                "invocation_ref": None, "transition_firing_ref": None,
                "allowed_root": "workspace/views", "allowed_prefixes": ["*"],
                "mode": "read_write", "workspace_seed_manifest_ref": None,
                "workspace_runtime_ref": None,
            }, f"workspace:{name}")
        publish("workspace_write_intent/v1", "write_intent", "write_intent_version",
            lambda ref: {
                "write_intent_id": str(ref.entity_id),
                "write_intent_version_id": str(ref.version_id),
                "operation_binding_ref": ref_payload(plan.binding_refs[name]),
                "source_binding_ref": ref_payload(template),
                "allowed_relative_root": "", "one_lineage": True,
            }, f"workspace-intent:{name}")
        result[name] = HostExecutionBinding(agent_ref=agent, workspace_binding_ref=template)
    validate_host_execution_bindings(core, plan, result)
    return MappingProxyType(result)


def _operation_config(owner, kernel, execution):
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    operation = execution.operation
    binding = kernel._exact_object(operation.transition.binding_ref,
                                   expected_type="executable_transition_binding/v1")
    declaration_ref = _resource_from_payload(binding.metadata["declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(kernel._read_firing_registered(
        operation.canonical.context, declaration_ref)))
    declared = next(item for item in compiled.operations
                    if item.operation_id == operation.spec.operation_id)
    registered = owner.registration.declaration("executor", operation.spec.executor_key)
    if (declared.executor_declaration != registered
            or any(registered["contracts"].get(key) != value
                   for key, value in EXECUTOR_CONTRACTS.items())
            or tuple(declared.declaration.tools) != operation.spec.allowed_tool_ids
            or MATERIAL_TOOL not in operation.spec.allowed_tool_ids):
        raise OperationAuthorityError("material operation lacks its exact registered executor/tool ABI")
    tool = owner.registration.declaration("tool", MATERIAL_TOOL)
    if (compiled.registrations["tool"].get(MATERIAL_TOOL) != tool
            or tool["identity"] != TOOL_IDENTITY or tool["contracts"] != TOOL_CONTRACTS):
        raise OperationAuthorityError("material tool differs from its exact registered ABI")
    config = dict(declared.declaration.config)
    owner._core.catalog.validate_schema_ref(MATERIAL_CONFIG_SCHEMA, config)
    path = WorkspaceExecutionMixin._strict_relative_path(config["workspace_relative_path"])
    if path.as_posix() != config["workspace_relative_path"]:
        raise ValueError("material workspace path must be canonical relative text")
    outcomes = {item.name: item for item in declared.declaration.outcomes}
    selected = outcomes.get(config["outcome_id"])
    if selected is None or tuple(item.port for item in selected.products) != (config["output_port_id"],):
        raise OperationAuthorityError("material config requires its one declared output/outcome")
    # Authored config uses the declared symbolic port. The exact compiled
    # inventory alone maps that name to the mechanical output binding handle.
    config["output_port_id"] = compiled.port_handles[config["output_port_id"]]
    return config


def _ensure_workspace(core, kernel, execution, key):
    context = execution.operation.canonical.context
    template_ref = execution.operation.operation_binding.workspace_binding_ref
    if template_ref is None:
        raise OperationAuthorityError("material operation requires its original workspace binding")
    template = dict(kernel._exact_object(template_ref,
        expected_type="workspace_binding/v1").metadata)
    if (template.get("binding_kind") != "lineage_template"
            or template.get("operation_binding_ref") != ref_payload(context.operation_binding_ref)):
        raise ResourceIntegrityFault("material workspace template differs from the original operation")
    lineage_ref = _version_from_payload(template["workspace_lineage_ref"])
    admission = kernel._exact_object(context.admission_marking_checkpoint_ref,
        expected_type="marking_checkpoint/v1").metadata
    bases = tuple(_version_from_payload(item) for item in admission["workspace_revision_refs"]
                  if item.get("logical_id") == str(lineage_ref.entity_id))
    if len(bases) != 1:
        raise ResourceIntegrityFault("material admission lacks one exact workspace lineage head")
    base_ref, = bases
    expected = dict(template, binding_kind="firing_view",
        template_binding_ref=ref_payload(template_ref), invocation_ref=ref_payload(context.invocation_ref),
        transition_firing_ref=ref_payload(context.own_transition_firing_ref),
        base_revision_ref=ref_payload(base_ref),
        allowed_root="workspace/views/" + context.own_transition_firing_ref.version_id.value)
    rows = [row for row in core.event_store.object_rows_by_producer(context.invocation_ref.entity_id,
                object_types=("workspace_binding/v1",))
            if json.loads(row["metadata_json"]).get("binding_kind") == "firing_view"]
    if rows:
        view_ref, view = _firing_workspace_view(core, kernel, context)
        expected.update(workspace_binding_id=str(view_ref.entity_id),
                        workspace_binding_version_id=str(view_ref.version_id))
        if len(rows) != 1 or view != expected:
            raise ResourceIntegrityFault("material firing view differs from its admitted workspace")
    else:
        view_ref = VersionRef("workspace_binding/v1", new_id("workspace_binding"),
                              new_id("workspace_binding_version"))
        expected.update(workspace_binding_id=str(view_ref.entity_id),
                        workspace_binding_version_id=str(view_ref.version_id))
        core.catalog.validate_instance("workspace_binding/v1", category="object", instance=expected)
        tx = core.begin(idempotency_key=key + ":workspace-view",
            task_round_id=context.task_round_ref.entity_id, net_instance_id=context.net_instance_ref.entity_id)
        tx.prewrite(object_type="workspace_binding/v1", logical_id=view_ref.entity_id,
            version_id=view_ref.version_id, payload=canonical_json(expected), metadata=expected,
            media_type="application/json", schema_ref="registry_v1/workspace_binding/v1",
            producer_invocation_id=context.invocation_ref.entity_id)
        tx.commit()
    intents = [row for row in core.event_store.canonical_object_rows(object_type="workspace_write_intent/v1")
        if json.loads(row["metadata_json"]).get("operation_binding_ref") == ref_payload(context.operation_binding_ref)
        and json.loads(row["metadata_json"]).get("source_binding_ref") == ref_payload(template_ref)]
    if len(intents) != 1:
        raise ResourceIntegrityFault("material workspace lacks its one original write intent")
    intent, = intents
    intent_ref = VersionRef("workspace_write_intent/v1",
        TypedId.parse(intent["logical_id"], expected="write_intent"),
        TypedId.parse(intent["version_id"], expected="write_intent_version"))
    root = _resolve_registry_workspace_root(core, expected["allowed_root"])
    # Use directory descriptors so no existing symlink can redirect the fixed
    # firing-private root while preparing its original checkpoint projection.
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(core.run_dir, flags)
    try:
        for part in ("workspace", "views", context.own_transition_firing_ref.version_id.value):
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    finally:
        os.close(descriptor)
    return root, base_ref, intent_ref


@dataclass(frozen=True)
class _WrittenSemanticResources:
    """Exact finalizer input only; no AgentLoop state or simulated lifecycle."""
    written_resource_refs: tuple[ResourceVersionRef, ...]


def _completed_material_preparation(core, kernel, repository, execution, config):
    """Rehydrate a complete original executor return without any new effect.

    An attached child without the original completion is deliberately outside
    this replay boundary. It needs checkpoint recovery; calling its tool again
    could repeat an unrecorded rename or conflict with already staged products.
    """
    from .registry.firing_recovery import (
        COMPLETION_EVENT_TYPE, registered_operation_completion_payload,
    )
    context = execution.operation.canonical.context
    completed = tuple(core.event_store.list_events_by_aggregate(
        str(execution.operation_execution_lease_ref.entity_id),
        event_types=(COMPLETION_EVENT_TYPE,)))
    children = execution_states_for_parent(core, execution_parent(context))
    if not completed:
        if children:
            raise ResourceIntegrityFault(
                "RECOVERY_REQUIRED: material execution has children without an original completion")
        return None
    if (len(completed) != 1
            or completed[0].producer_invocation_id != context.invocation_ref.entity_id
            or len(children) != 2
            or {child.definition for child in children}
            != {FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET}
            or any(not child.checkpoint.map_ready for child in children)):
        raise ResourceIntegrityFault("material completion lacks its exact map-ready child closure")
    file_state, = (child for child in children
                   if child.definition == FILE_MATERIALIZATION_NET)
    refs = file_state.checkpoint.evidence_refs
    if len(refs) != 2 or any(ref.entity_type != "resource_version/v1" for ref in refs):
        raise ResourceIntegrityFault("material completion lacks its original output pair")
    semantic_ref, file_ref = tuple(ResourceVersionRef(ref.entity_id, ref.version_id) for ref in refs)
    artifact = verify_resource(core, kernel, execution.operation.canonical, semantic_ref)
    workspace_artifact = verify_resource(core, kernel, execution.operation.canonical, file_ref)
    semantic_bytes = kernel._read_firing_registered(context, artifact.header.ref)
    value = decode_agent_text(semantic_bytes,
        content_schema_ref=artifact.header.content_schema_ref,
        media_type=artifact.header.media_type)
    file_bytes = kernel._read_firing_registered(context, workspace_artifact.header.ref)
    file_metadata = kernel._firing_prepared(context, file_ref).metadata
    _state, _candidate_ref, candidate, files = workspace_finalization_candidate(
        core, kernel, context, required=True)
    path = config["workspace_relative_path"]
    resource_data = {"resource_id": str(semantic_ref.resource_id),
                     "resource_version_id": str(semantic_ref.resource_version_id)}
    if (file_metadata.get("origin_kind") != "workspace_write"
            or file_metadata.get("descriptors", {}).get("workspace_path") != path
            or candidate.get("semantic_output_refs") != [resource_data]
            or path not in files or files[path][0] != file_bytes
            or file_bytes != agent_text_workspace_bytes(value)):
        raise ResourceIntegrityFault("material completion differs from its immutable literal candidate")
    key = "material-operation:" + str(context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, (artifact,),
        selected_outcome_id=config["outcome_id"], idempotency_key=key + ":outputs")
    if dict(completed[0].payload) != registered_operation_completion_payload(core, outputs):
        raise ResourceIntegrityFault("material completion differs from its exact original executor return")
    return OperationExecutionResult((artifact,), selected_outcome_id=config["outcome_id"])


def _frozen_material_baseline(kernel, data):
    """Prove create/replace intent against its frozen exact binding only."""
    address = ResourceAddress(_version_from_payload(data["address"]["scope_ref"]),
                              data["address"]["opaque_name"])
    expected, binding = None, None
    if data["expected_binding_ref"] is not None:
        ref = _version_from_payload(data["expected_binding_ref"])
        binding = kernel._exact_object(ref,
            expected_type="resource_address_binding/v1").metadata
        if (binding.get("scope_ref") != data["address"]["scope_ref"]
                or binding.get("opaque_name") != address.opaque_name
                or binding.get("lifecycle_state") not in {"bound", "tombstoned"}):
            raise OperationAuthorityError("material baseline binding differs from its frozen address")
        expected = ResourceAddressBindingRef(ref.entity_id, ref.version_id,
                                             binding["lifecycle_state"] == "tombstoned")
    base = None
    if data["base_resource_ref"] is not None:
        ref = _version_from_payload(data["base_resource_ref"])
        if ref.entity_type != "resource_version/v1":
            raise OperationAuthorityError("material base must be one exact resource version")
        base = ResourceVersionRef(ref.entity_id, ref.version_id)
    if data["operation"] == "create_text":
        if (base is not None or (binding is not None and (
                binding["lifecycle_state"] != "tombstoned" or binding.get("resource_ref") is not None))):
            raise OperationAuthorityError("create_text cannot replace an existing bound material")
    elif data["operation"] == "replace_text":
        if (binding is None or binding["lifecycle_state"] != "bound" or base is None
                or _resource_from_payload(binding["resource_ref"]) != base):
            raise OperationAuthorityError("replace_text requires its exact bound baseline resource")
    else:
        raise OperationAuthorityError("unsupported frozen material operation")
    return address, expected, base


def prepare_material_operation(owner, execution):
    """Prepare exact provisional products and workspace; never call Success."""
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    from cpn.components.execution_services import invoke_registered_tool
    from .run import RunOwner
    if not isinstance(owner, RunOwner):
        raise TypeError("material execution requires the existing RunOwner")
    core = owner._core
    kernel, repository = owner.operation_repository()
    execution = verify_operation_execution(core, kernel, repository, execution)
    config = _operation_config(owner, kernel, execution)
    context = execution.operation.canonical.context
    completed = _completed_material_preparation(core, kernel, repository, execution, config)
    if completed is not None:
        return completed
    command, _receipt, payload = owner.control.materials.deliver_input(execution)
    data = command["data"]
    value = decode_agent_text(payload, content_schema_ref=AGENT_TEXT_SCHEMA,
                              media_type="application/json")
    address, expected, base = _frozen_material_baseline(kernel, data)
    path = config["workspace_relative_path"]
    if address.scope_ref == context.task_ref and address.opaque_name == path:
        raise ValueError("semantic material address must differ from the workspace file address")
    key = "material-operation:" + str(context.invocation_ref.version_id)
    semantic_command = agent_text_publication(execution,
        output_port_id=config["output_port_id"], outcome_id=config["outcome_id"],
        value=value, address=address, expected_binding_ref=expected,
        base_resource_ref=base, summary="Owner-submitted literal text",
        idempotency_key=key + ":semantic")
    kernel._preflight_publish(context, semantic_command)
    root, workspace_base, intent_ref = _ensure_workspace(core, kernel, execution, key)
    WorkspaceExecutionMixin._preflight_workspace_destination(root, path)
    children = execution_states_for_parent(core, execution_parent(context))
    if not children:
        _restore_tree(root, _archive_files(core, workspace_base))
    WorkspaceExecutionMixin._preflight_workspace_destination(root, path)
    prior = _workspace_resource_state(core, workspace_base).get(path)
    supersedes = None
    if prior is not None:
        origin = kernel._firing_prepared(context, prior).metadata.get("origin", {})
        if (origin.get("primary_ref") == ref_payload(context.operation_binding_ref)
                and origin.get("secondary_ref") == ref_payload(intent_ref)):
            supersedes = prior
    file_address = ResourceAddress(context.task_ref, path)
    file_binding = kernel._current_binding(file_address,
        through_ordinal=execution.admission_head.ordinal, include_tombstone=True)
    if file_binding is not None and not file_binding.tombstone:
        binding = kernel._exact_object(file_binding.as_version_ref(),
            expected_type="resource_address_binding/v1").metadata
        previous = kernel._firing_prepared(context, _resource_from_payload(binding["resource_ref"])).metadata
        if (previous.get("origin_kind") != "workspace_write"
                or previous.get("descriptors", {}).get("workspace_path") != path):
            raise ResourceIntegrityFault("workspace file address belongs to an unrelated resource")
    file_command = PublishResource(
        origin=WorkspaceWriteOrigin(context.operation_binding_ref, intent_ref),
        payload=agent_text_workspace_bytes(value), media_type="text/plain; charset=utf-8",
        content_schema_ref=None, summary=f"Material workspace file {path}"[:600],
        lifetime_ref=context.invocation_ref,
        address_bindings=(AddressBindingIntent(file_address, context.operation_binding_ref, file_binding),),
        supersedes=supersedes, derived_from=(() if prior is None else (prior,)),
        descriptors={"content_role": "workspace_file", "workspace_path": path},
        idempotency_key=key + ":file")
    kernel._preflight_publish(context, file_command)

    def stage_resources():
        tx = core.begin(idempotency_key=key + ":products",
            task_round_id=context.task_round_ref.entity_id, net_instance_id=context.net_instance_ref.entity_id)
        semantic = kernel._publish_bytes_in_transaction(context, semantic_command, tx)
        workspace_file = kernel._publish_bytes_in_transaction(context, file_command, tx)
        tx.commit()
        return semantic.as_version_ref(), workspace_file.as_version_ref()

    def materialize(_state):
        return invoke_registered_tool(owner, kernel, repository, execution, MATERIAL_TOOL,
            identity=TOOL_IDENTITY, contracts=TOOL_CONTRACTS,
            kwargs={"root": root, "relative_path": path, "value": value,
                    "before_replace": stage_resources})

    _state, refs = execute_idempotent_materialization(core, context=context,
        definition=FILE_MATERIALIZATION_NET, transition_id="materialize_file",
        identity_key=key + ":materialize", materialize=materialize)
    if len(refs) != 2 or any(ref.entity_type != "resource_version/v1" for ref in refs):
        raise ResourceIntegrityFault("material file execution lacks its exact output pair")
    semantic_ref, file_ref = tuple(ResourceVersionRef(ref.entity_id, ref.version_id) for ref in refs)
    artifact = verify_resource(core, kernel, execution.operation.canonical, semantic_ref)
    workspace_artifact = verify_resource(core, kernel, execution.operation.canonical, file_ref)
    if (kernel._read_firing_registered(context, artifact.header.ref) != semantic_command.payload
            or kernel._read_firing_registered(context, workspace_artifact.header.ref) != file_command.payload
            or root.joinpath(path).read_bytes() != file_command.payload):
        raise ResourceIntegrityFault("material execution differs from its frozen literal input")
    finalize_firing_workspace_candidate(core, kernel, execution,
        _WrittenSemanticResources((semantic_ref,)), idempotency_key=key + ":finalize",
        synchronize=lambda: None)
    return OperationExecutionResult((artifact,), selected_outcome_id=config["outcome_id"])


def run_material_operation(owner, execution):
    """Synchronous Owner entry through original output/completion registrars."""
    from .registry.firing_recovery import record_registered_operation_completion
    result = prepare_material_operation(owner, execution)
    kernel, repository = owner.operation_repository()
    key = "material-operation:" + str(execution.operation.canonical.context.invocation_ref.version_id)
    outputs = register_operation_outputs(repository, execution, result.outputs,
        selected_outcome_id=result.selected_outcome_id, idempotency_key=key + ":outputs")
    record_registered_operation_completion(owner._core, kernel, repository, outputs,
        idempotency_key=key + ":executor-completed")
    return outputs


__all__ = (
    "MATERIAL_EXECUTOR", "MATERIAL_TOOL", "MATERIAL_CONFIG_SCHEMA",
    "EXECUTOR_IDENTITY", "EXECUTOR_CONTRACTS", "TOOL_IDENTITY", "TOOL_CONTRACTS",
    "register_material_execution", "material_host_bindings", "material_operation_executor",
    "prepare_material_operation", "run_material_operation",
)
