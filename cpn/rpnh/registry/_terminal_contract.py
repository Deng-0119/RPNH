"""Conservative per-hop compatibility for an already verified owner adoption.

This reader compares immutable registered facts. It neither grants authority to
execute an old net nor treats a changed, otherwise valid contract as corruption.
The caller owns each complete net/adoption closure and the stable terminal cut.
"""
from __future__ import annotations

from dataclasses import asdict

from ..petri_contracts import same_operation_contract
from ._event_store.adoption_reads import AdoptionPrefixReads
from .errors import ResourceIntegrityFault, TerminalReadUnsupported
from .publication import _ref_payload, _resource_from_payload, _version_from_payload
from .schema_catalog import canonical_json


_BINDING_FIELDS = (
    "principal_ref", "authority_decision_ref", "code_artifact_ref",
    "restartability_policy", "agent_loop_role", "task_header_query",
    "allowed_publication_origins", "resource_read_contracts",
    "llm_input_target_ref", "workspace_binding_ref", "module_artifact_refs",
    "permitted_write_intent_factory_refs",
)
_PLACE_FIELDS = (
    "name", "schema", "schema_variants", "channel", "token_kind", "reusable",
    "colours", "capacity",
)


def _same(left, right):
    # Inputs have already passed their original schemas. In particular, bools
    # and numbers, and arbitrary array order, cannot collapse under Python ==.
    return canonical_json(left) == canonical_json(right)


def _index(values, field, label):
    result = {}
    for value in values:
        name = getattr(value, field)
        if type(name) is not str or name in result:
            raise ResourceIntegrityFault("terminal contract has duplicate/invalid " + label)
        result[name] = value
    return result


class _ContractReads:
    """Finite exact reads through the caller's same-Registry prefix reader."""

    def __init__(self, core, prefix):
        self.core, self.prefix = core, prefix
        self.objects, self.resources, self.hosts = {}, {}, {}

    def object(self, ref, kind):
        if ref.entity_type != kind:
            raise ResourceIntegrityFault("terminal contract reference has the wrong type")
        if ref not in self.objects:
            data = dict(self.prefix.metadata(ref, kind))
            self.core.catalog.validate_instance(kind, category="object", instance=data)
            self.objects[ref] = data
        return self.objects[ref]

    def resource(self, ref, media_type):
        if ref.entity_type != "resource_version/v1":
            raise ResourceIntegrityFault("terminal contract requires an exact schema/HOST Resource")
        if ref not in self.resources:
            prepared = self.prefix.prepared(ref, "resource_version/v1")
            metadata = dict(prepared.metadata)
            payload = self.prefix._payload(ref, prepared)
            if (metadata["resource_id"] != str(ref.entity_id)
                    or metadata["resource_version_id"] != str(ref.version_id)
                    or metadata["size"] != prepared.size
                    or metadata["media_type"] != prepared.media_type
                    or len(payload) != prepared.size):
                raise ResourceIntegrityFault("terminal contract Resource lacks its exact envelope")
            self.resources[ref] = metadata, payload
        metadata, payload = self.resources[ref]
        if metadata["media_type"] != media_type:
            raise ResourceIntegrityFault("terminal contract Resource has the wrong media type")
        return metadata, payload

    def host(self, kind, key, declaration, task_ref):
        cache_key = kind, key
        if cache_key not in self.hosts:
            # This is the original canonical-object visibility predicate, using
            # the same DB as every other new terminal dependency read.
            db, store = self.prefix.db, self.core.event_store
            upper = db.execute("SELECT COALESCE(MAX(ordinal), 0) FROM events").fetchone()[0]
            predicate = store._canonical_member_sql(member_kind="object",
                member_identity_sql="o.version_id",
                published_event_sql="published_event.ordinal")
            rows = db.execute("SELECT o.* FROM objects o JOIN events published_event "
                "ON published_event.event_id=o.published_event_id WHERE " + predicate
                + " AND o.object_type='resource_version/v1' "
                "AND json_extract(o.metadata_json,'$.descriptors.host_registration_kind')=? "
                "AND json_extract(o.metadata_json,'$.descriptors.registered_key')=?",
                (upper, upper, upper, upper, kind, key)).fetchall()
            if len(rows) != 1:
                raise ResourceIntegrityFault("terminal contract lacks one exact stored HOST declaration")
            row = rows[0]
            ref = _resource_from_payload({"resource_id": row["logical_id"],
                "resource_version_id": row["version_id"]}).as_version_ref()
            metadata, payload = self.resource(ref, "application/json")
            self.hosts[cache_key] = metadata, self.prefix._json(payload)
        metadata, document = self.hosts[cache_key]
        if (not _same(metadata["task_ref"], task_ref)
                or metadata["origin_kind"] != "private_system"
                or not _same(document, declaration)):
            raise ResourceIntegrityFault("terminal contract differs from its registered HOST declaration")
        return document


def _schema_authority(reads, compiled, port, spec_port, task_ref):
    """Reclose the two original schema references, never select by schema ID."""
    from .content_schemas import _validate_schema_bytes

    document_ref = _version_from_payload(spec_port["schema_ref"])
    metadata, payload = reads.resource(document_ref, "application/schema+json")
    # Preserve duplicate-key/nonfinite rejection for schema Resources too.
    reads.prefix._json(payload)
    schema_id, document = _validate_schema_bytes(payload)
    if (not _same(metadata["task_ref"], task_ref) or schema_id != port.schema
            or not _same(document, compiled.registrations["schema"][port.schema]["schema"])):
        raise ResourceIntegrityFault("terminal port schema differs from exact document authority")
    source = spec_port["content_schema_ref"]
    if "resource_id" in source:
        authority_ref = _resource_from_payload(source).as_version_ref()
        if authority_ref != document_ref:
            raise ResourceIntegrityFault("terminal resource schema source is not its exact document")
    else:
        authority_ref = _version_from_payload(source)
        catalog = reads.object(authority_ref, "registry_type_catalog/v1")
        entry = catalog["schemas"][schema_id]
        if entry["schema_id"] != schema_id or entry["source"].encode("utf-8") != payload:
            raise ResourceIntegrityFault("terminal schema document differs from its exact catalog source")
    return _ref_payload(document_ref), _ref_payload(authority_ref)


def _budget(binding, operation):
    declared = operation.declaration.budget_binding
    actual = {name: binding[name] for name in
        ("budget_bucket_id", "budget_scope", "finalization_scope")}
    if declared is None:
        expected = {"budget_bucket_id": operation.operation_id,
            "budget_scope": operation.operation_id, "finalization_scope": None}
        if not _same(actual, expected):
            raise ResourceIntegrityFault("terminal default budget is not derived from its local operation handle")
        return {"default_for_operation": operation.declaration.name}
    expected = {"budget_bucket_id": declared.bucket_id,
        "budget_scope": declared.budget_scope, "finalization_scope": declared.finalization_scope}
    if not _same(actual, expected):
        raise ResourceIntegrityFault("terminal explicit budget differs from its declared binding")
    return expected


def _spec_material(reads, compiled, operation, ports, binding, task_ref):
    spec_ref = _version_from_payload(binding["operation_spec_ref"])
    spec = reads.object(spec_ref, "operation_spec/v1")
    host = operation.executor_declaration
    declaration = operation.declaration
    prompt = None if declaration.request_port is None else ports[declaration.request_port].port_id
    if (not _same(spec["operation_spec_ref"], _ref_payload(spec_ref))
            or spec["operation_spec_id"] != str(spec_ref.entity_id)
            or spec["operation_spec_version_id"] != str(spec_ref.version_id)
            or spec["operation_id"] != operation.operation_id
            or spec["executor_key"] != operation.executor_key
            or spec["transport"] != host["contracts"]["transport"]
            or not _same(spec["implementation_identity"], host["identity"])
            or not _same(spec["implementation_contracts"], host["contracts"])
            or not _same(spec["allowed_tool_ids"], sorted(declaration.tools))
            or spec["llm_prompt_port_id"] != prompt):
        raise ResourceIntegrityFault("terminal operation spec differs from its own compiled operation")
    result = {key: value for key, value in spec.items() if key not in
        {"operation_spec_id", "operation_spec_version_id", "operation_spec_ref"}}
    result["operation_id"] = declaration.name
    result["llm_prompt_port_id"] = declaration.request_port
    for direction, names in (("input", declaration.inputs), ("output", declaration.outputs)):
        values = spec[direction + "_ports"]
        ids = [value["port_id"] for value in values]
        if len(set(ids)) != len(ids) or ids != [ports[name].port_id for name in names]:
            raise ResourceIntegrityFault("terminal spec ports differ from their ordered local handles")
        normalized = []
        for name, value in zip(names, values):
            port = ports[name]
            if direction == "input":
                minimum, maximum = port.minimum, port.maximum
            else:
                products = [next((p for p in outcome.products if p.port == name), None)
                    for outcome in declaration.outcomes]
                minimum = min(product.minimum if product is not None else 0 for product in products)
                maximum = max(product.maximum if product is not None else 0 for product in products)
            if (value["place"] != port.place
                    or not _same(value["cardinality"], {"minimum": minimum, "maximum": maximum})):
                raise ResourceIntegrityFault("terminal spec port differs from compiled place/cardinality")
            schema_ref, authority_ref = _schema_authority(reads, compiled, port, value, task_ref)
            normalized.append({**value, "port_id": name,
                "schema_ref": schema_ref, "content_schema_ref": authority_ref})
        result[direction + "_ports"] = normalized
    return spec_ref, spec, result


def _runtime_material(reads, executable, compiled, symbolic, transition, operation, ports, root):
    document = reads.object(transition.binding_ref, "executable_transition_binding/v1")
    expected = {"executable_transition_binding_ref": _ref_payload(transition.binding_ref),
        "net_instance_ref": _ref_payload(executable.net_ref),
        "declaration_resource_ref": {"resource_id": str(executable.declaration_resource_ref.resource_id),
            "resource_version_id": str(executable.declaration_resource_ref.resource_version_id)},
        "declaration_schema_ref": compiled.schema_version, "transition_id": symbolic.name,
        "node_ref": _ref_payload(transition.node_ref),
        "activation_ref": None if transition.activation_ref is None else _ref_payload(transition.activation_ref),
        "agent_ref": None if transition.agent_ref is None else _ref_payload(transition.agent_ref),
        "operation_binding_ref": _ref_payload(transition.operation_binding_ref),
        "principal_ref": _ref_payload(transition.principal_ref),
        "input_place_ids": sorted({arc.place for arc in compiled.symbolic.arcs
            if arc.transition == symbolic.name and arc.direction == "input"}),
        "output_place_ids": sorted({arc.place for arc in compiled.symbolic.arcs
            if arc.transition == symbolic.name and arc.direction == "output"})}
    if not _same(document, expected):
        raise ResourceIntegrityFault("terminal executable binding differs from its exact local transition")
    binding = reads.object(transition.operation_binding_ref, "operation_binding/v1")
    if (not _same(binding["operation_binding_ref"], _ref_payload(transition.operation_binding_ref))
            or binding["operation_binding_id"] != str(transition.operation_binding_ref.entity_id)
            or binding["operation_binding_version_id"] != str(transition.operation_binding_ref.version_id)
            or not _same(binding["node_ref"], _ref_payload(transition.node_ref))
            or not _same(binding["principal_ref"], _ref_payload(transition.principal_ref))
            or not _same(binding["team_design_root_ref"], _ref_payload(executable.team_design_root_ref))):
        raise ResourceIntegrityFault("terminal operation binding differs from its exact local transition")
    spec_ref, spec, normalized_spec = _spec_material(
        reads, compiled, operation, ports, binding, root["task_ref"])
    outputs, seen = {}, set()
    for value in binding["output_binding_refs"]:
        ref = _version_from_payload(value)
        if ref in seen:
            raise ResourceIntegrityFault("terminal output binding inventory repeats an exact reference")
        seen.add(ref)
        output = reads.object(ref, "output_binding/v1")
        matching = [name for name in operation.declaration.outputs
            if ports[name].port_id == output["output_port_id"]]
        if len(matching) != 1 or matching[0] in outputs:
            raise ResourceIntegrityFault("terminal output lacks its unique local logical port")
        name = matching[0]
        port = ports[name]
        spec_port = next(value for value in spec["output_ports"] if value["port_id"] == port.port_id)
        expected = {"output_binding_id": str(ref.entity_id), "output_binding_version_id": str(ref.version_id),
            "output_port_id": port.port_id, "task_round_ref": root["task_round_ref"],
            "net_ref": _ref_payload(executable.net_ref), "node_ref": _ref_payload(transition.node_ref),
            "team_design_root_ref": _ref_payload(executable.team_design_root_ref),
            "opaque_action_ref": _ref_payload(spec_ref), "place": port.place,
            "place_ref": spec_port["schema_ref"], "content_schema_ref": spec_port["content_schema_ref"],
            "content_schema_id": port.schema, "normal_output_cardinality": spec_port["cardinality"]}
        outcomes = [outcome.name for outcome in operation.declaration.outcomes
            if any(product.port == name for product in outcome.products)]
        if len(outcomes) == 1:
            expected["declared_outcome_id"] = outcomes[0]
        if not _same(output, expected):
            raise ResourceIntegrityFault("terminal output differs from its own exact per-net binding")
        outputs[name] = {key: item for key, item in output.items() if key not in {
            "output_binding_id", "output_binding_version_id", "task_round_ref", "net_ref",
            "node_ref", "team_design_root_ref", "opaque_action_ref"}}
        outputs[name]["output_port_id"] = name
    if set(outputs) != set(operation.declaration.outputs):
        raise ResourceIntegrityFault("terminal operation lacks its complete output binding inventory")
    return {"binding": {field: binding[field] for field in _BINDING_FIELDS},
        "agent_ref": document["agent_ref"], "activation_ref": document["activation_ref"],
        "budget": _budget(binding, operation), "spec": normalized_spec, "outputs": outputs}


def _material(core, kernel, reads, executable, structure, terminal, transition_id):
    if terminal is None:
        return None
    compiled = structure.compiled
    terminals = (compiled.source.terminal, *compiled.source.terminal_alternatives)
    if sum(_same(asdict(value), asdict(terminal)) for value in terminals) != 1:
        raise ResourceIntegrityFault("terminal selector is not uniquely declared in its own net")
    root = reads.object(executable.team_design_root_ref, "team_design_root/v1")
    from .module_terminal import _registered_terminal_key
    if not _registered_terminal_key(core, kernel, compiled, root["task_ref"], terminal,
            _prefix_reads=reads.prefix):
        raise ResourceIntegrityFault("terminal selector lacks its registered publication key")
    terminal_host = compiled.registrations["tool"][terminal.key]
    reads.host("tool", terminal.key, terminal_host, root["task_ref"])
    ports = _index(compiled.ports, "name", "logical port")
    operations = _index(compiled.operations, "operation_id", "operation handle")
    by_name = {}
    for item in operations.values():
        name = item.declaration.name
        if name in by_name:
            raise ResourceIntegrityFault("terminal contract repeats a logical operation")
        by_name[name] = item
    if (not _same(dict(compiled.operation_handles), {name: item.operation_id for name, item in by_name.items()})
            or not _same(dict(compiled.port_handles), {name: item.port_id for name, item in ports.items()})
            or len({port.port_id for port in ports.values()}) != len(ports)):
        raise ResourceIntegrityFault("terminal contract handles are not one-to-one local bindings")
    operation_name = f"{terminal.source.component}.{terminal.operation}"
    port_name = f"{terminal.source.component}.{terminal.source.port}"
    operation = by_name[operation_name]
    host = compiled.registrations["executor"][operation.executor_key]
    if not _same(host, operation.executor_declaration):
        raise ResourceIntegrityFault("terminal compiled executor differs from its registered declaration")
    reads.host("executor", operation.executor_key, host, root["task_ref"])
    tools = {}
    for key in operation.declaration.tools:
        declaration = compiled.registrations["tool"][key]
        tools[key] = reads.host("tool", key, declaration, root["task_ref"])
    selected_names = (*operation.declaration.inputs, *operation.declaration.outputs, port_name)
    place_inventory = _index(compiled.symbolic.places, "name", "logical place")
    selected_ports, selected_places = {}, {}
    for name in selected_names:
        port = ports[name]
        selected_ports[name] = {"name": name, "direction": port.direction, "schema": port.schema,
            "channel": port.channel, "public": port.public, "minimum": port.minimum,
            "maximum": port.maximum, "place": port.place}
        place = asdict(place_inventory[port.place])
        selected_places[port.place] = {field: place[field] for field in _PLACE_FIELDS}
    symbolic = _index(compiled.symbolic.transitions, "name", "symbolic transition").get(transition_id)
    executable_transitions = _index(executable.transitions, "transition_id", "executable transition")
    runtime = None
    if symbolic is not None:
        transition_operation = by_name[symbolic.operation]
        runtime = _runtime_material(reads, executable, compiled, symbolic,
            executable_transitions[transition_id], transition_operation, ports, root)
    elif transition_id in executable_transitions:
        raise ResourceIntegrityFault("terminal executable transition is outside its compiled net")
    return {"operation": operation.declaration, "contract": {
        "terminal": asdict(terminal), "terminal_host": terminal_host,
        "operation_name": operation_name, "executor_key": operation.executor_key,
        "executor": host, "tools": tools, "ports": selected_ports, "places": selected_places,
        "transition": None if symbolic is None else asdict(symbolic),
        "output_arcs": [asdict(arc) for arc in compiled.symbolic.arcs
            if arc.transition == transition_id and arc.direction == "output"],
        "runtime": runtime},
        "direct_source": port_name in operation.declaration.outputs and ports[port_name].direction == "output",
        "selected_transition": symbolic is not None and symbolic.operation == operation_name}


def same_terminal_contract(core, kernel, old_executable, old_structure,
        new_executable, new_structure, old_terminal, new_terminal, transition_id, *,
        _db=None, _prefix_reads=None):
    """Compare one actual owner hop after both per-net closures are verified.

    Mechanical operation/port IDs are checked against each net, then normalized
    to qualified logical names. New publication refs and input/control arc edits
    are permitted; exact HOST/schema/binding changes are not inferred equivalent.
    """
    if _db is None:
        if _prefix_reads is not None:
            raise TypeError("terminal compatibility needs its prefix reader's exact DB")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            prefix = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, terminal=True)
            return same_terminal_contract(core, kernel, old_executable, old_structure,
                new_executable, new_structure, old_terminal, new_terminal, transition_id,
                _db=db, _prefix_reads=prefix)
    prefix = _prefix_reads
    if (type(prefix) is not AdoptionPrefixReads or prefix.db is not _db
            or prefix.store is not core.event_store or prefix.catalog is not core.catalog
            or prefix.task_id != core.task_id or not prefix.terminal):
        raise TypeError("terminal compatibility requires its owning same-cut terminal reader")
    reads = _ContractReads(core, prefix)
    # Do not short-circuit a changed old selector before reading the new facts.
    old = _material(core, kernel, reads, old_executable, old_structure, old_terminal, transition_id)
    new = _material(core, kernel, reads, new_executable, new_structure, new_terminal, transition_id)
    if any(outcome.effects for item in (old, new) if item is not None
            for outcome in item["operation"].outcomes):
        raise TerminalReadUnsupported("EFFECTS_UNSUPPORTED")
    if old is None or new is None:
        return False
    return (old["direct_source"] and new["direct_source"]
        and old["selected_transition"] and new["selected_transition"]
        and same_operation_contract(old["operation"], new["operation"])
        and _same(old["contract"], new["contract"]))


__all__ = ("same_terminal_contract",)
