"""Mandatory exact resource/slot membership for registered Module net closure."""
from __future__ import annotations

from typing import Mapping


def validate_module_bindings(net, root, outputs, nodes, exact, corrupt):
    bindings = net.get("module_resource_bindings")
    if not isinstance(bindings, Mapping):
        raise corrupt("registered Module net lacks exact resource binding authority")
    root_resources = root["resource_refs"]
    artifacts = root["artifact_refs"]
    owner = bindings["owner_resource_inputs"]
    slots = bindings["slot_refs"]
    leases = bindings["lease_refs"]
    owner_refs = []
    for ref in owner.values():
        value = {"entity_type": "resource_version/v1", "logical_id": ref["resource_id"],
                 "version_id": ref["resource_version_id"]}
        metadata = exact(value, "resource_version/v1")
        source = metadata["content_schema_authority_ref"]
        source_ref = ({"entity_type": "resource_version/v1", "logical_id": source["resource_id"],
                       "version_id": source["resource_version_id"]}
                      if "resource_id" in source else source)
        exact(source_ref, "resource_version/v1")
        if (metadata["task_ref"] != root["task_ref"] or value not in root_resources
                or source_ref not in root_resources):
            raise corrupt("Module owner resource/source is outside exact task/root membership")
        owner_refs.append(value)
    output_values = list(outputs.values())
    node_values = list(nodes.values())
    slot_bindings = bindings.get("slot_bindings", {})
    if not isinstance(slot_bindings, Mapping) or set(slot_bindings) != set(slots):
        raise corrupt("Module current slot bindings differ from exact slot symbols")
    for symbol, ref in slots.items():
        metadata = exact(ref, "logical_artifact_slot/v1")
        current = slot_bindings[symbol]
        matches = [output for output in output_values
                   if {"entity_type": "output_binding/v1", "logical_id": output["output_binding_id"],
                       "version_id": output["output_binding_version_id"]}
                   == current["producer_output_binding_ref"]]
        if (len(matches) != 1 or ref not in artifacts or current["slot_ref"] != ref
                or metadata["logical_artifact_slot_ref"] != ref
                or metadata["lexical_slot_id"] != symbol
                or metadata["artifact_id"] != current["artifact_id"]
                or metadata["content_kind"] != current["content_kind"]
                or current["producer_node_ref"] != matches[0]["node_ref"]
                or current["output_port_id"] != matches[0]["output_port_id"]
                or current["content_schema_id"] != matches[0]["content_schema_id"]
                or not any(node["node_ref"] == current["producer_node_ref"]
                    and node["transition_id"] == current["producer_transition_id"] for node in node_values)):
            raise corrupt("Module slot differs from exact current producer/output/schema authority")
        # Creation evidence is immutable, including its old producer/root.
        # Verify that closure independently rather than accepting a stale
        # producer in the new graph, or skipping authority for preserved slots.
        creation_root = exact(metadata["team_design_root_ref"], "team_design_root/v1")
        declaration = exact(metadata["authored_index_ref"], "resource_version/v1")
        producer = exact(metadata["producer_node_ref"], "node_declaration/v1")
        output = exact(metadata["producer_output_binding_ref"], "output_binding/v1")
        creation_net = exact(output["net_ref"], "net_instance/v1")
        operation = exact(producer["producer_operation_binding_ref"], "operation_binding/v1")
        spec = exact(operation["operation_spec_ref"], "operation_spec/v1")
        port = [p for p in spec["output_ports"] if p["port_id"] == metadata["output_port_id"]]
        if (creation_root["task_ref"] != root["task_ref"]
                or creation_root["run_ref"] != root["run_ref"]
                or declaration["task_ref"] != root["task_ref"]
                or creation_root["llm_macro_net_ref"] != metadata["authored_index_ref"]
                or creation_net["llm_macro_net_ref"] != metadata["authored_index_ref"]
                or creation_net["team_design_root_ref"] != metadata["team_design_root_ref"]
                or ref not in creation_root["artifact_refs"]
                or creation_net["module_resource_bindings"]["slot_refs"].get(symbol) != ref
                or metadata["producer_node_ref"] not in creation_root["node_refs"]
                or metadata["producer_node_ref"] not in creation_net["node_refs"]
                or metadata["producer_output_binding_ref"] not in creation_root["output_binding_refs"]
                or metadata["producer_output_binding_ref"] not in creation_net["output_binding_refs"]
                or producer["team_design_root_ref"] != metadata["team_design_root_ref"]
                or output["team_design_root_ref"] != metadata["team_design_root_ref"]
                or output["node_ref"] != metadata["producer_node_ref"]
                or output["output_port_id"] != metadata["output_port_id"]
                or producer["producer_operation_binding_ref"] not in creation_net["operation_binding_refs"]
                or operation["node_ref"] != metadata["producer_node_ref"]
                or operation["team_design_root_ref"] != metadata["team_design_root_ref"]
                or metadata["producer_output_binding_ref"] not in operation["output_binding_refs"]
                or metadata["producer_output_binding_ref"] not in producer["offered_output_binding_refs"]
                or len(port) != 1 or port[0]["content_schema_ref"] != output["content_schema_ref"]
                or port[0]["cardinality"] != output["normal_output_cardinality"]
                or output["content_schema_id"] != current["content_schema_id"]
                or output["content_schema_ref"] != matches[0]["content_schema_ref"]):
            raise corrupt("Module slot loses exact immutable creation/task/run/schema authority")
        schema = output["content_schema_ref"]
        exact({"entity_type": "resource_version/v1", "logical_id": schema["resource_id"],
               "version_id": schema["resource_version_id"]}, "resource_version/v1")
    for ref in leases.values():
        if ref not in owner_refs and ref not in slots.values():
            raise corrupt("Module lease identity is outside exact declared resource/slot authority")
        exact(ref, ref["entity_type"])
