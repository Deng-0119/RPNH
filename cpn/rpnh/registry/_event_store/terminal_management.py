"""Bounded terminal management proofs at the original SQLite write cut.

No shadow authority, lifecycle table, writer or terminal flag is introduced.
The initial variant supports one exact location operation with one original
reusable slot. A pending management firing leaves the committed closure intact.
"""
from __future__ import annotations

import json
import sqlite3

from ..event_store import RegistryConflict, RegistryCorruptError, verified_adoption_head, verified_checkpoint_head
from ..object_store import ObjectIntegrityError
from ..schema_catalog import SchemaGovernanceError
from ..execution_child_closure import ClosureSnapshot
from ..publication import _version_from_payload
from ..schema_catalog import canonical_json
from ..strict_contracts import ref_payload
from .collaboration_descriptors import descriptor_store, exact_prepared
from .material_control import canonical_rows, reference, row_ref


def _fail(message):
    raise RegistryConflict("TERMINAL_MANAGEMENT_UNPROVED: " + message)


def _original_terminal(store, db, task_id, *, _verify_physical_heads=True):
    """Read the exact current A/E/I/C, retaining its original business identity."""
    from ...executable_net import load_compiled_net
    rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not rows or len({row["logical_id"] for row in rows}) != 1:
        _fail("requires one original authority lineage")
    snap = ClosureSnapshot(store, db, task_id)
    authority_ref = row_ref(rows[-1])
    authority = snap.read(authority_ref, "run_execution_authority/v1")
    if authority["status"] != "terminal" or authority.get("terminal_evidence_ref") is None:
        _fail("requires the current committed business terminal")
    native = db.execute("SELECT value FROM registry_meta WHERE key='native_run_ref'").fetchone()
    run = snap.read(authority["run_ref"], "native_run_identity/v1")
    if (native is None or json.loads(native[0]) != authority["run_ref"]
            or run["run_id"] != authority["run_ref"]["logical_id"]
            or run["run_version_id"] != authority["run_ref"]["version_id"]
            or run["task_ref"] != authority["task_ref"]
            or authority["task_ref"]["logical_id"] != str(task_id)):
        _fail("run/task identity differs")
    # The original parent-bound protocol has no terminal management grant.
    from ..parent_bound import bound_integrity_at
    epoch = int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0])
    bound_integrity_at(store, db, task_id, epoch, for_execution=True)
    evidence = snap.read(authority["terminal_evidence_ref"], "run_terminal_evidence/v1")
    index = snap.read(evidence["final_result_index_ref"], "final_result_index/v1")
    checkpoint = snap.read(authority["latest_checkpoint_ref"], "marking_checkpoint/v1")
    evidence_ref, index_ref = evidence["terminal_evidence_ref"], index["final_result_index_ref"]
    if (authority["run_execution_authority_ref"] != authority_ref
            or evidence_ref != authority["terminal_evidence_ref"]
            or index_ref != evidence["final_result_index_ref"]
            or evidence["run_ref"] != authority["run_ref"]
            or evidence["final_checkpoint_ref"] != authority["latest_checkpoint_ref"]
            or checkpoint["marking_checkpoint_ref"] != authority["latest_checkpoint_ref"]
            or checkpoint["settled"] is not True
            or index["terminal_result_ref"] != evidence["terminal_result_ref"]
            or index["terminal_occurrence_ref"] != evidence["terminal_occurrence_ref"]
            or index["terminal_outcome"] != evidence["run_outcome"]
            or len({snap.rows[ref["version_id"]]["transaction_id"]
                    for ref in (authority_ref, evidence_ref, index_ref)}) != 1):
        _fail("current closure is not one exact A/E/I transaction")
    result = evidence["terminal_result_ref"]
    reference(result, "resource_version/v1")
    resource = {"resource_id": result["logical_id"], "resource_version_id": result["version_id"]}
    if index["entries"] != [resource]:
        _fail("initial variant requires its original nonempty single-result index")
    product = exact_prepared(db, descriptor_store(store), task_id, result)
    if product.metadata["task_ref"] != authority["task_ref"]:
        _fail("original result belongs to another task")
    if _verify_physical_heads:
        adopted = verified_adoption_head(store, store.catalog, task_id, _db=db)
        head = verified_checkpoint_head(store, store.catalog, task_id, adopted, _db=db)
        if (ref_payload(adopted) != checkpoint["net_instance_ref"]
                or ref_payload(head) != checkpoint["marking_checkpoint_ref"]):
            _fail("current closure differs from original adopted net/marking")
    else:
        # The original recovery promotion callback runs after candidate facts
        # are inserted but before task-control heads are advanced. It needs
        # the retained canonical marking, not a second physical-head read.
        # Full head validation runs before append under the same write lock.
        from .accounting import _CANONICAL_EVENT_SQL
        adoption = db.execute("SELECT e.payload_json FROM events e WHERE e.task_id=? "
            "AND e.event_type='net_adopted/v1' AND " + _CANONICAL_EVENT_SQL +
            " ORDER BY e.ordinal DESC LIMIT 1", (str(task_id),)).fetchone()
        if adoption is None or json.loads(adoption[0])["net_instance_ref"] != checkpoint["net_instance_ref"]:
            _fail("retained closure differs from its canonical adopted net")
    net = snap.read(checkpoint["net_instance_ref"], "net_instance/v1")
    root = snap.read(net["team_design_root_ref"], "team_design_root/v1")
    source = net["team_net_declaration_resource_ref"]
    source_ref = {"entity_type": "resource_version/v1", "logical_id": source["resource_id"],
                  "version_id": source["resource_version_id"]}
    if (authority["declaration_ref"] != source_ref
            or authority["declaration_schema_ref"] != "rpnh/executable_net/v1"
            or root["task_ref"] != authority["task_ref"] or root["run_ref"] != authority["run_ref"]
            or checkpoint["team_design_root_ref"] != net["team_design_root_ref"]):
        _fail("current compiled source differs from original run")
    source_object = exact_prepared(db, descriptor_store(store), task_id, source_ref)
    compiled = load_compiled_net(json.loads(descriptor_store(store).read_registered(source_object)))
    terminals = [item for item in (compiled.source.terminal, *compiled.source.terminal_alternatives)
        if item.key == evidence["producer"] == index["authority_kind"]
        and item.config.get("run_outcome") == evidence["run_outcome"]]
    if len(terminals) != 1:
        _fail("business terminal declaration is absent or ambiguous")
    terminal, = terminals
    port_name = terminal.source.component + "." + terminal.source.port
    ports = [item for item in compiled.ports if item.name == port_name]
    if len(ports) != 1:
        _fail("business terminal port is not exact")
    tokens = [snap.read(ref, "petri_token/v1") for ref in checkpoint["token_refs"]]
    if any(token["net_instance_ref"] != checkpoint["net_instance_ref"]
            or token["consumed_by"] is not None or token["epoch"] != checkpoint["epoch"]
            for token in tokens):
        _fail("current marking contains an unavailable occurrence")
    carriers = [token for token in tokens if token["place"] == ports[0].place
        and token["verdict"] == terminal.outcome
        and {canonical_json(value) for value in (token["resource_ref"], token["work_resource_ref"])
             if value is not None} == {canonical_json(resource)}]
    if len(carriers) != 1:
        _fail("original terminal carrier is absent or ambiguous")
    _business_proof(snap, authority, evidence, product.metadata, carriers[0], ports[0], terminal, compiled)
    return {"snapshot": snap, "authority": authority, "evidence": evidence, "index": index,
            "checkpoint": checkpoint, "net": net, "root": root, "compiled": compiled,
            "tokens": tokens, "terminal_carrier": carriers[0]}


def _business_proof(snap, authority, evidence, product, carrier, port, terminal, compiled):
    """Bounded same-net direct-product proof; do not reinterpret result bytes."""
    db = snap.db
    firing_ref = evidence["terminal_occurrence_ref"]
    firing = snap.read(firing_ref, "transition_firing/v1")
    invocation = snap.read(product["producer_ref"], "invocation/v1")
    binding = snap.read(firing["operation_binding_ref"], "operation_binding/v1")
    spec = snap.read(binding["operation_spec_ref"], "operation_spec/v1")
    output = snap.read(product["origin"]["primary_ref"], "output_binding/v1")
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
        (firing_ref["version_id"],)).fetchone()
    events = db.execute("SELECT * FROM events WHERE event_type='transition_firing_settled/v1' "
        "AND json_extract(payload_json,'$.transition_firing_ref.version_id')=?", (firing_ref["version_id"],)).fetchall()
    if root is None or root["state"] != "PUBLISHED" or len(events) != 1:
        _fail("business producer lacks one original normal Success")
    settled = snap.event(events[0])
    completion = snap.read(settled["firing_completion_ref"], "firing_completion/v2")
    result = snap.read(settled["operation_result_ref"], "operation_result/v1")
    delta = snap.read(settled["marking_delta_ref"], "marking_delta/v1")
    deposited = snap.read(settled["successor_checkpoint_ref"], "marking_checkpoint/v1")
    operation_name = terminal.source.component + "." + terminal.operation
    definitions = [item for item in compiled.operations if item.declaration.name == operation_name]
    if len(definitions) != 1:
        _fail("original business operation definition is absent or ambiguous")
    definition, = definitions
    if (firing["transition_firing_ref"] != firing_ref
            or firing["net_instance_ref"] != carrier["net_instance_ref"]
            or firing["task_ref"] != authority["task_ref"]
            or firing["transition_id"] != carrier["producer"]
            or firing["operation_binding_ref"] != invocation["operation_binding_ref"]
            or invocation["own_transition_firing_ref"] != firing_ref
            or invocation["invocation_ref"] != product["producer_ref"]
            or root["invocation_version_id"] != product["producer_ref"]["version_id"]
            or root["net_version_id"] != firing["net_instance_ref"]["version_id"]
            or root["published_transaction_id"] != events[0]["transaction_id"]
            or result["invocation_ref"] != product["producer_ref"]
            or result["transition_firing_ref"] != firing_ref or result["business_outcome"] != "completed"
            or result["selected_outcome_id"] != terminal.outcome
            or evidence["terminal_result_ref"] not in result["output_resource_refs"]
            or completion["operation_result_ref"] != result["operation_result_ref"]
            or completion["invocation_ref"] != product["producer_ref"]
            or deposited["settled"] is not True
            or carrier["petri_token_ref"] not in deposited["token_refs"]
            or carrier["petri_token_ref"] not in delta["deposited_refs"]
            or product["origin"]["kind"] != "petri_output"
            or product["origin"]["primary_ref"] not in binding["output_binding_refs"]
            or output["output_port_id"] != port.port_id or output["place"] != port.place
            or output["net_ref"] != firing["net_instance_ref"]
            or product["content_schema_ref"] != port.schema
            or product["content_schema_authority_ref"] != output["content_schema_ref"]
            or product.get("descriptors", {}).get("output_outcome_id") != terminal.outcome
            or spec["operation_id"] != definition.operation_id
            or spec["executor_key"] != definition.executor_key
            or spec["implementation_identity"] != definition.executor_declaration["identity"]
            or spec["implementation_contracts"] != definition.executor_declaration["contracts"]
            or not any(item.name == firing["transition_id"] and item.operation == operation_name
                       for item in compiled.symbolic.transitions)):
        _fail("business result/direct carrier changed its original producer or outcome")
    transactions = {snap.rows[ref["version_id"]]["transaction_id"] for ref in (
        settled["firing_completion_ref"], settled["operation_result_ref"],
        settled["marking_delta_ref"], settled["successor_checkpoint_ref"], carrier["petri_token_ref"])}
    if transactions != {root["published_transaction_id"]}:
        _fail("business completion/carrier lacks its atomic Success transaction")


def terminal_location_scope(store, db, task_id, *, target, source=None, execution=None):
    """Prove target capability without treating temporary busy capacity as absent."""
    from ...terminal_management import (TERMINAL_CONTRACTS, TERMINAL_IDENTITY,
        TERMINAL_EXECUTOR, TERMINAL_CONFIG_SCHEMA, validate_terminal_location_spec)
    value = _original_terminal(store, db, task_id)
    snap, net, compiled = value["snapshot"], value["net"], value["compiled"]
    transitions = [snap.read(ref, "executable_transition_binding/v1")
        for ref in net["executable_transition_binding_refs"]]
    matches = [item for item in transitions if item["transition_id"] == target]
    if len(matches) != 1 or matches[0].get("agent_ref") is None:
        _fail("requires its exact adopted management agent")
    transition, = matches
    binding = snap.read(transition["operation_binding_ref"], "operation_binding/v1")
    spec = snap.read(binding["operation_spec_ref"], "operation_spec/v1")
    validate_terminal_location_spec(spec, binding)
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        _fail("operation differs from its compiled definition")
    definition, = definitions
    config = dict(definition.declaration.config)
    store.catalog.validate_schema_ref(TERMINAL_CONFIG_SCHEMA, config)
    outcomes = [item for item in definition.declaration.outcomes if item.name == config["outcome_id"]]
    if (definition.executor_declaration.get("identity") != TERMINAL_IDENTITY
            or definition.executor_declaration.get("contracts") != TERMINAL_CONTRACTS
            or definition.declaration.executor != TERMINAL_EXECUTOR
            or definition.declaration.inputs or definition.declaration.outputs or definition.declaration.tools
            or len(outcomes) != 1 or outcomes[0].products or outcomes[0].effects
            or not any(item.name == target and item.operation == definition.declaration.name
                       for item in compiled.symbolic.transitions)):
        _fail("requires its original empty location outcome")
    if (source is not None and source != value["root"]["owner_principal_ref"]
            or execution is not None and execution != {
                "task_ref": value["authority"]["task_ref"], "task_round_ref": net["task_round_ref"],
                "net_instance_ref": value["checkpoint"]["net_instance_ref"]}):
        _fail("command owner or execution differs")
    places = [item for item in compiled.symbolic.places if item.name == config["idle_place"]]
    arcs = [item for item in compiled.symbolic.arcs if item.transition == target]
    incoming = [item for item in arcs if item.direction == "input"]
    outgoing = [item for item in arcs if item.direction == "output"]
    if (len(places) != 1 or places[0].capacity != 1 or not places[0].reusable
            or places[0].token_kind != "agent_resource" or places[0].channel != "control"
            or sum(item.count for item in places[0].initial_tokens) != 1
            or any(port.place == config["idle_place"] for port in compiled.ports)
            or len(incoming) != 1 or len(outgoing) != 1
            or incoming[0].place != config["idle_place"] or incoming[0].mode != "borrow" or incoming[0].weight != 1
            or outgoing[0].place != config["idle_place"] or outgoing[0].mode != "return" or outgoing[0].weight != 1
            or outgoing[0].emit != "content_less" or outgoing[0].outcome != config["outcome_id"]
            or config["idle_place"] == value["terminal_carrier"]["place"]):
        _fail("requires one original private idle slot and exact borrow/return arcs")
    slots = [token for token in value["tokens"] if token["place"] == config["idle_place"]]
    if (len(slots) != 1 or slots[0]["kind"] is not None
            or slots[0]["resource_ref"] is not None or slots[0]["work_resource_ref"] is not None):
        _fail("original idle slot occurrence is absent or ambiguous")
    value.update(transition=transition, binding=binding, spec=spec, definition=definition,
                 config=config, idle_token=slots[0])
    return value


def terminal_literal_scope(store, db, task_id, *, target, source=None, execution=None):
    """Prove the original one-output literal manager and its private PN slot."""
    from ...terminal_management import (TERMINAL_LITERAL_CONTRACTS, TERMINAL_LITERAL_IDENTITY,
        TERMINAL_LITERAL_EXECUTOR, validate_terminal_literal_spec)
    from ...material_execution import MATERIAL_CONFIG_SCHEMA, MATERIAL_TOOL
    from ...material_types import AGENT_TEXT_SCHEMA
    value = _original_terminal(store, db, task_id)
    snap, net, compiled = value["snapshot"], value["net"], value["compiled"]
    transitions = [snap.read(ref, "executable_transition_binding/v1")
        for ref in net["executable_transition_binding_refs"]]
    matches = [item for item in transitions if item["transition_id"] == target]
    if len(matches) != 1 or matches[0].get("agent_ref") is None:
        _fail("requires its exact adopted literal management agent")
    transition, = matches
    binding = snap.read(transition["operation_binding_ref"], "operation_binding/v1")
    spec = snap.read(binding["operation_spec_ref"], "operation_spec/v1")
    validate_terminal_literal_spec(spec, binding)
    definitions = [item for item in compiled.operations if item.operation_id == spec["operation_id"]]
    if len(definitions) != 1:
        _fail("literal operation differs from its compiled definition")
    definition, = definitions
    config = dict(definition.declaration.config)
    store.catalog.validate_schema_ref(MATERIAL_CONFIG_SCHEMA, config)
    outcomes = [item for item in definition.declaration.outcomes if item.name == config["outcome_id"]]
    ports = [item for item in compiled.ports if item.name == config["output_port_id"]]
    if (definition.executor_declaration.get("identity") != TERMINAL_LITERAL_IDENTITY
            or definition.executor_declaration.get("contracts") != TERMINAL_LITERAL_CONTRACTS
            or definition.declaration.executor != TERMINAL_LITERAL_EXECUTOR
            or definition.declaration.inputs or tuple(definition.declaration.tools) != (MATERIAL_TOOL,)
            or tuple(definition.declaration.outputs) != (config["output_port_id"],)
            or len(outcomes) != 1 or outcomes[0].effects
            or len(outcomes[0].products) != 1
            or outcomes[0].products[0].port != config["output_port_id"]
            or outcomes[0].products[0].minimum != 1 or outcomes[0].products[0].maximum != 1
            or len(ports) != 1 or ports[0].direction != "output" or ports[0].schema != AGENT_TEXT_SCHEMA
            or ports[0].minimum != 1 or ports[0].maximum != 1
            or not any(item.name == target and item.operation == definition.declaration.name
                       for item in compiled.symbolic.transitions)):
        _fail("requires its original single literal output and exact outcome")
    port, = ports
    output = snap.read(binding["output_binding_refs"][0], "output_binding/v1")
    template = snap.read(binding["workspace_binding_ref"], "workspace_binding/v1")
    current = [ref for ref in value["checkpoint"]["workspace_revision_refs"]
        if ref["logical_id"] == template["workspace_lineage_ref"]["logical_id"]]
    if (output["output_port_id"] != port.port_id or output["place"] != port.place
            or output["net_ref"] != value["checkpoint"]["net_instance_ref"]
            or template.get("binding_kind") != "lineage_template"
            or template["operation_binding_ref"] != transition["operation_binding_ref"]
            or template.get("mode") != "read_write" or template.get("allowed_root") != "workspace/views"
            or template.get("allowed_prefixes") != ["*"] or len(current) != 1):
        _fail("literal manager differs from its original output or workspace lineage")
    workspace = exact_prepared(db, descriptor_store(store), task_id, current[0]).metadata
    if (workspace.get("settled") is not True or workspace["run_ref"] != value["authority"]["run_ref"]
            or workspace["task_ref"] != value["authority"]["task_ref"]
            or workspace.get("payload_kind") != "full_workspace_tar"):
        _fail("literal manager lacks its original committed workspace")
    if (source is not None and source != value["root"]["owner_principal_ref"]
            or execution is not None and execution != {
                "task_ref": value["authority"]["task_ref"], "task_round_ref": net["task_round_ref"],
                "net_instance_ref": value["checkpoint"]["net_instance_ref"]}):
        _fail("literal command owner or execution differs")
    arcs = [item for item in compiled.symbolic.arcs if item.transition == target]
    incoming = [item for item in arcs if item.direction == "input"]
    outgoing = [item for item in arcs if item.direction == "output"]
    if len(incoming) != 1:
        _fail("requires one original private idle slot and exact literal arcs")
    idle_place = incoming[0].place
    places = [item for item in compiled.symbolic.places if item.name == idle_place]
    returning = [item for item in outgoing if item.place == idle_place]
    producing = [item for item in outgoing if item.place == port.place]
    if (len(places) != 1 or places[0].capacity != 1 or not places[0].reusable
            or places[0].token_kind != "agent_resource" or places[0].channel != "control"
            or sum(item.count for item in places[0].initial_tokens) != 1
            or any(item.place == idle_place for item in compiled.ports)
            or incoming[0].mode != "borrow" or incoming[0].weight != 1
            or len(outgoing) != 2 or len(returning) != 1 or len(producing) != 1
            or returning[0].mode != "return" or returning[0].weight != 1
            or returning[0].emit != "content_less" or returning[0].outcome != config["outcome_id"]
            or producing[0].mode != "produce" or producing[0].weight != 1
            or producing[0].emit != "produced" or producing[0].outcome != config["outcome_id"]
            or idle_place == value["terminal_carrier"]["place"]
            or port.place == value["terminal_carrier"]["place"]):
        _fail("requires one original private idle slot and exact literal arcs")
    slots = [token for token in value["tokens"] if token["place"] == idle_place]
    if (len(slots) != 1 or slots[0]["kind"] is not None
            or slots[0]["resource_ref"] is not None or slots[0]["work_resource_ref"] is not None):
        _fail("original literal idle slot occurrence is absent or ambiguous")
    value.update(transition=transition, binding=binding, spec=spec, definition=definition,
        config={**config, "idle_place": idle_place}, idle_token=slots[0],
        management_kind="literal", output_port=port, workspace_ref=current[0])
    return value


def terminal_management_scope(store, db, task_id, *, target, operation, source=None, execution=None):
    """Select only an explicitly authored action contract at the original cut."""
    if operation in {"bind", "unbind"}:
        return terminal_location_scope(store, db, task_id, target=target, source=source, execution=execution)
    if operation in {"create_text", "replace_text"}:
        return terminal_literal_scope(store, db, task_id, target=target, source=source, execution=execution)
    _fail("terminal source lacks an explicit supported action contract")


def require_single_terminal_firing(store, db, task_id, *, allowed_firing_ref=None):
    """The initial variant permits exactly this responsibility, or no firing."""
    from .material_control import _committed_descriptor, read_committed_material_admission
    # Inventory original objects and their publication events before applying
    # the derived root-state projection. A missing root or opening transaction
    # must not hide a previously accepted responsibility.
    firing_rows = db.execute("SELECT o.* FROM objects o LEFT JOIN events e "
        "ON e.event_id=o.published_event_id LEFT JOIN transactions t ON t.transaction_id=o.transaction_id "
        "WHERE o.object_type='transition_firing/v1' AND (e.task_id=? OR t.task_id=? "
        "OR json_extract(o.metadata_json,'$.task_ref.logical_id')=?)",
        (str(task_id), str(task_id), str(task_id))).fetchall()
    original_ids = {row["version_id"] for row in firing_rows}
    event_ids = {json.loads(row[0])["version_id"] for row in db.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND event_type='object_version_published/v1' "
        "AND json_extract(payload_json,'$.object_type')='transition_firing/v1'", (str(task_id),)).fetchall()}
    if original_ids != event_ids:
        _fail("original firing object/publication inventory is incomplete")
    inventory = db.execute("SELECT p.*,t.task_id AS source_task_id,t.status AS opening_status "
        "FROM firing_publications p LEFT JOIN transactions t "
        "ON t.transaction_id=p.opened_transaction_id").fetchall()
    if not original_ids <= {root["firing_version_id"] for root in inventory}:
        _fail("original firing inventory has lost a publication root")
    roots = []
    for root in inventory:
        if root["source_task_id"] is None or root["opening_status"] != "committed":
            _fail("original firing has no committed opening transaction")
        if root["source_task_id"] != str(task_id):
            if root["firing_version_id"] in original_ids:
                _fail("original firing opening transaction belongs to another task")
            continue
        if root["firing_version_id"] not in original_ids:
            _fail("original root has lost its firing object")
        try:
            record = store.ordered_firing_record(root["firing_version_id"], _db=db)
            firing_row = next(row for row in firing_rows if row["version_id"] == root["firing_version_id"])
            firing = _committed_descriptor(store, firing_row)
            admission_row = db.execute("SELECT * FROM objects WHERE version_id=?",
                (firing["firing_admission_ref"]["version_id"],)).fetchone()
            if admission_row is None:
                _fail("original firing has lost its admission")
            admission = read_committed_material_admission(store, db, task_id, admission_row)
            if (firing != record["firing"]
                    or firing["transition_firing_ref"] != row_ref(firing_row)
                    or firing["transition_firing_ref"]["logical_id"] != root["firing_logical_id"]
                    or firing["task_ref"]["logical_id"] != str(task_id)
                    or firing["net_instance_ref"]["version_id"] != root["net_version_id"]
                    or firing["operation_binding_ref"]["version_id"] != root["operation_binding_version_id"]
                    or firing["admission_marking_checkpoint_ref"]["version_id"] != root["admission_checkpoint_version_id"]
                    or admission["invocation_ref"]["version_id"] != root["invocation_version_id"]
                    or admission["invocation_ref"]["logical_id"] != root["invocation_logical_id"]
                    or root["opened_transaction_id"] != admission_row["transaction_id"]
                    or root["opened_transaction_id"] != firing_row["transaction_id"]):
                _fail("original root differs from its exact admission identity")
            if root["state"] == "PUBLISHED":
                if record["firing_completion"] is None or record["successor_checkpoint"] is None:
                    _fail("closed root has no original Success closure")
            elif root["state"] == "REJECTED":
                if record["firing_rejection"] is None or record["retained_checkpoint"] is None:
                    _fail("closed root has no original rejection closure")
            elif root["state"] == "PROVISIONAL":
                roots.append(root)
            else:
                _fail("initial terminal variant does not cover superseded responsibilities")
        except (RegistryConflict, RegistryCorruptError, SchemaGovernanceError, ObjectIntegrityError,
                sqlite3.DatabaseError, KeyError, TypeError, ValueError, OSError) as exc:
            raise RegistryConflict("TERMINAL_MANAGEMENT_UNPROVED: original firing disposition is not proved") from exc
    if allowed_firing_ref is None:
        if roots:
            raise RegistryConflict("TERMINAL_MANAGEMENT_BUSY: an original firing still owns responsibility")
        return
    reference(allowed_firing_ref, "transition_firing/v1")
    if (len(roots) != 1 or roots[0]["firing_logical_id"] != allowed_firing_ref["logical_id"]
            or roots[0]["firing_version_id"] != allowed_firing_ref["version_id"]):
        _fail("prospective closure cannot exclude another unresolved firing")


def validate_terminal_admissions(store, db, *, task_id, objects):
    """Repeat exact command/capacity proof at the original admission write lock."""
    firings = [item for item in objects if item.object_type == "transition_firing/v1"]
    if not firings:
        return
    authorities = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not authorities or json.loads(authorities[-1]["metadata_json"]).get("status") != "terminal":
        return
    admissions = [item for item in objects if item.object_type == "firing_admission/v1"]
    if len(firings) != 1 or len(admissions) != 1:
        _fail("allows one exact manager admission at a time")
    admission, firing = dict(admissions[0].metadata), dict(firings[0].metadata)
    from .material_control import read_accepted_material_command
    command_ref = admission.get("owner_command_ref")
    if command_ref is None:
        _fail("cannot readmit ordinary business after committed terminal")
    command = read_accepted_material_command(store, db, task_id, command_ref, current=True)
    value = terminal_management_scope(store, db, task_id, target=command["target"],
        operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
    require_single_terminal_firing(store, db, task_id)
    if (firing["transition_id"] != command["target"]
            or firing["operation_binding_ref"] != value["transition"]["operation_binding_ref"]
            or firing.get("agent_ref") != value["transition"]["agent_ref"]
            or firing["claimed_input_refs"] != [value["idle_token"]["petri_token_ref"]]
            or firing["admission_marking_checkpoint_ref"] != value["checkpoint"]["marking_checkpoint_ref"]
            or admission["transition_firing_ref"] != firing["transition_firing_ref"]):
        _fail("admission differs from the exact original command and idle occurrence")


def validate_terminal_successor(store, db, *, task_id, objects, events, publication_commands):
    """Reclose only this completed manager beside one normal Success C1."""
    changed = [item for item in objects if item.object_type in {
        "run_execution_authority/v1", "marking_checkpoint/v1", "run_terminal_evidence/v1", "final_result_index/v1"}]
    if not changed:
        return
    authority_rows = canonical_rows(store, db, task_id, object_type="run_execution_authority/v1")
    if not authority_rows:
        return
    current = json.loads(authority_rows[-1]["metadata_json"])
    if current.get("status") != "terminal":
        return
    settlements = [event for event in events if event.event_type == "transition_firing_settled/v1"]
    if len(settlements) != 1 or len(publication_commands) != 1:
        _fail("checkpoint advancement requires one complete normal management Success")
    settled = settlements[0].payload
    from .material_settlement import material_admission, completion_evidence
    found = material_admission(store, db, task_id, settled["transition_firing_ref"], current=True)
    if found is None:
        _fail("cannot advance for an ordinary business firing")
    admission, command = found
    value = terminal_management_scope(store, db, task_id, target=command["target"],
        operation=command["data"].get("operation"), source=command["source"], execution=command["data"]["execution"])
    require_single_terminal_firing(store, db, task_id, allowed_firing_ref=settled["transition_firing_ref"])
    completion = completion_evidence(store, db, task_id, admission)
    by_kind = {}
    for item in changed:
        by_kind.setdefault(item.object_type, []).append(item)
    if any(len(by_kind.get(kind, ())) != 1 for kind in (
            "run_execution_authority/v1", "marking_checkpoint/v1", "run_terminal_evidence/v1", "final_result_index/v1")):
        _fail("requires one A1/E1/I1/C1 in the same Success transaction")
    authority, checkpoint, evidence, index = (dict(by_kind[kind][0].metadata) for kind in (
        "run_execution_authority/v1", "marking_checkpoint/v1", "run_terminal_evidence/v1", "final_result_index/v1"))
    original = value["authority"]
    if (authority != {**original, "run_execution_authority_ref": authority["run_execution_authority_ref"],
            "latest_checkpoint_ref": checkpoint["marking_checkpoint_ref"],
            "terminal_evidence_ref": evidence["terminal_evidence_ref"]}
            or authority["run_execution_authority_ref"]["logical_id"] != original["run_execution_authority_ref"]["logical_id"]
            or authority["run_execution_authority_ref"] == original["run_execution_authority_ref"]
            or evidence != {**value["evidence"], "terminal_evidence_ref": evidence["terminal_evidence_ref"],
                "final_checkpoint_ref": checkpoint["marking_checkpoint_ref"], "final_result_index_ref": index["final_result_index_ref"]}
            or index != {**value["index"], "final_result_index_ref": index["final_result_index_ref"]}
            or checkpoint["previous_checkpoint_ref"] != original["latest_checkpoint_ref"]
            or checkpoint["net_instance_ref"] != value["checkpoint"]["net_instance_ref"]
            or checkpoint["settled"] is not True
            or checkpoint["transition_firing_refs"] != [settled["transition_firing_ref"]]
            or settled["successor_checkpoint_ref"] != checkpoint["marking_checkpoint_ref"]
            or settled["business_outcome"] != "completed"
            or publication_commands[0]["firing_version_id"] != settled["transition_firing_ref"]["version_id"]):
        _fail("successor changes business identity, generation, lineage or exact settlement")
    original_tokens = {canonical_json(ref) for ref in value["checkpoint"]["token_refs"]}
    idle = value["idle_token"]["petri_token_ref"]
    final_tokens = {canonical_json(ref) for ref in checkpoint["token_refs"]}
    unchanged = original_tokens - {canonical_json(idle)}
    replacements = [item for item in objects if item.object_type == "petri_token/v1"]
    literal = value.get("management_kind") == "literal"
    if (len(replacements) != (2 if literal else 1) or len(final_tokens) != len(checkpoint["token_refs"])
            or not unchanged <= final_tokens):
        _fail("prospective marking changes another original occurrence")
    returned = [dict(item.metadata) for item in replacements
        if item.metadata["place"] == value["config"]["idle_place"]]
    if len(returned) != 1:
        _fail("prospective marking must return one original idle slot")
    replacement, = returned
    new_tokens = {canonical_json(dict(item.metadata)["petri_token_ref"]) for item in replacements}
    if (replacement["place"] != value["config"]["idle_place"]
            or replacement["kind"] != value["idle_token"]["kind"] or replacement["consumed_by"] is not None
            or replacement["resource_ref"] is not None or replacement["work_resource_ref"] is not None
            or replacement["net_instance_ref"] != checkpoint["net_instance_ref"]
            or replacement["epoch"] != checkpoint["epoch"]
            or final_tokens != unchanged | new_tokens
            or canonical_json(value["terminal_carrier"]["petri_token_ref"]) not in final_tokens):
        _fail("prospective marking does not return the sole idle slot and retain business result")
    if literal:
        _validate_terminal_literal_products(value, admission, settled, completion,
            objects, replacements, checkpoint)
    elif (checkpoint["workspace_revision_refs"] != value["checkpoint"]["workspace_revision_refs"]
            or any(item.object_type == "workspace_revision/v1" for item in objects)):
        _fail("location management cannot change the original workspace")
    from .material_recovery import active_claim_records
    claims = active_claim_records(store, db, task_id, value["checkpoint"])
    if (len(claims) != 1 or claims[0]["firing"]["transition_firing_ref"] != settled["transition_firing_ref"]
            or claims[0]["firing"]["claimed_input_refs"] != [idle]):
        _fail("prospective closure does not retain one exact original claim")


def _validate_terminal_literal_products(value, admission, settled, completion,
        objects, replacements, checkpoint):
    """Only the registered literal output and its original workspace advance."""
    port = value["output_port"]
    ordered = completion.payload["ordered_outputs"]
    if (len(ordered) != 1 or ordered[0]["port_id"] != port.port_id
            or ordered[0]["output_binding_ref"] != value["binding"]["output_binding_refs"][0]
            or completion.payload["selected_outcome_id"] != value["config"]["outcome_id"]):
        _fail("terminal literal completion differs from its single declared output")
    resource = ordered[0]["resource_ref"]
    products = [dict(item.metadata) for item in replacements if item.metadata["place"] == port.place]
    if len(products) != 1:
        _fail("terminal literal requires exactly its original output occurrence")
    product, = products
    if (product["consumed_by"] is not None or product["net_instance_ref"] != checkpoint["net_instance_ref"]
            or product["epoch"] != checkpoint["epoch"] or product["verdict"] != value["config"]["outcome_id"]
            or {canonical_json(ref) for ref in (product["resource_ref"], product["work_resource_ref"])
                if ref is not None} != {canonical_json(resource)}):
        _fail("terminal literal output occurrence differs from its original completion")
    revisions = [dict(item.metadata) for item in objects if item.object_type == "workspace_revision/v1"]
    if len(revisions) != 1:
        _fail("terminal literal requires one original workspace revision")
    revision, = revisions
    old = value["workspace_ref"]
    new = revision["workspace_revision_ref"]
    expected = [new if ref == old else ref for ref in value["checkpoint"]["workspace_revision_refs"]]
    if (new["logical_id"] != old["logical_id"] or new == old
            or checkpoint["workspace_revision_refs"] != expected
            or revision["parent_revision_ref"] != old or revision["base_revision_ref"] != old
            or revision["run_ref"] != value["authority"]["run_ref"]
            or revision["task_ref"] != value["authority"]["task_ref"]
            or revision["net_instance_ref"] != checkpoint["net_instance_ref"]
            or revision["transition_firing_ref"] != settled["transition_firing_ref"]
            or revision["producer_invocation_ref"] != admission["invocation_ref"]
            or revision["settled"] is not True or revision["disposition"] != "committed"
            or revision["semantic_output_refs"] != [resource]
            or revision["deleted_paths"] or revision["conflict_paths"]
            or any(path != value["config"]["workspace_relative_path"] for path in revision["changed_paths"])):
        _fail("terminal literal workspace differs from its exact original lineage and result")


__all__ = ("terminal_location_scope", "terminal_literal_scope", "terminal_management_scope",
    "require_single_terminal_firing", "validate_terminal_admissions", "validate_terminal_successor")
