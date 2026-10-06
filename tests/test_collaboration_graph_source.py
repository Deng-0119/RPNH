"""Ordinary-v3 explicit source/recipe and shared-builder oracle, no execution."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

from cpn.rpnh.agent_workflows import AgentWorkflowGraph, build_agent_workflow_module
from cpn.rpnh.collaboration.graph_source import (
    graph_element_map, graph_source_elements, make_graph_recipe,
    make_graph_source, make_graph_source_map, rebuild_graph_module,
)
from cpn.rpnh.registry.schema_catalog import canonical_json


def graph_wire(feedback=False):
    def port(name, artifact=None):
        return {"port_id": name, "artifact_id": artifact or name}
    def endpoint(node, port):
        return {"node_id": node, "port_id": port}
    return {
        "nodes": [
            {"node_id": "draft", "instruction": "Draft the result.",
             "input_ports": [port("request"), *([port("changes")] if feedback else [])],
             "output_ports": [port("candidate")],
             "execution": {"role": "actor", "tools": None, "profile_id": None}},
            {"node_id": "review", "instruction": "Review and deliver.",
             "input_ports": [port("candidate")],
             "output_ports": [port("result"), *([port("changes")] if feedback else [])],
             "execution": {"role": "critic", "tools": ["complete_interaction", "write_file"], "profile_id": "reviewer"}},
        ],
        "arcs": [{"arc_id": "draft_to_review", "source": endpoint("draft", "candidate"),
                  "target": endpoint("review", "candidate"), "kind": "dependency"},
                 *([{"arc_id": "review_to_draft", "source": endpoint("review", "changes"),
                     "target": endpoint("draft", "changes"), "kind": "feedback"}] if feedback else [])],
        "ingress": endpoint("draft", "request"), "egress": endpoint("review", "result"),
        "max_rework_cycles": 2 if feedback else 0,
    }


def recipe(**changes):
    return make_graph_recipe(**{
        "executor_key": "test/ordinary-agent/v1", "terminal_key": "test/graph-terminal/v1",
        "tools": ("write_file", "complete_interaction"),
        "required_schemas": ("application/graph_test_config/v1",), "max_attempts_per_node": 12,
        **changes,
    })


def source_ids(source):
    return {key: "element:" + uuid.uuid4().hex for key in graph_source_elements(source)}


@pytest.mark.parametrize("feedback", [False, True])
@pytest.mark.parametrize("limit", [None, 1, 12])
def test_source_recipe_is_exact_existing_builder_oracle(feedback, limit):
    wire, options = graph_wire(feedback), recipe(max_attempts_per_node=limit)
    source = make_graph_source(wire)
    actual = rebuild_graph_module(source, options)
    expected = build_agent_workflow_module(AgentWorkflowGraph.from_mapping(wire),
        executor_key=options["executor_key"], terminal_key=options["terminal_key"],
        tools=options["tools"], required_schemas=options["required_schemas"], max_attempts_per_node=limit)
    assert canonical_json(actual.to_dict()) == canonical_json(expected.to_dict())
    assert source["graph"] == wire  # Store source wire; typed sorting is derivation.
    if limit == 12:
        default = build_agent_workflow_module(AgentWorkflowGraph.from_mapping(wire),
            executor_key=options["executor_key"], terminal_key=options["terminal_key"],
            tools=options["tools"], required_schemas=options["required_schemas"])
        assert canonical_json(actual.to_dict()) == canonical_json(default.to_dict())


@pytest.mark.parametrize("mutation", ["execution", "kind", "rework", "plugin", "boolean", "unknown"])
def test_source_requires_complete_exact_v3_wire(mutation):
    wire = graph_wire()
    if mutation == "execution": del wire["nodes"][0]["execution"]
    if mutation == "kind": del wire["arcs"][0]["kind"]
    if mutation == "rework": del wire["max_rework_cycles"]
    if mutation == "plugin": wire["nodes"][0]["execution"]["plugin"] = "native/op"
    if mutation == "boolean": wire["max_rework_cycles"] = False
    if mutation == "unknown": wire["unknown"] = None
    with pytest.raises(ValueError): make_graph_source(wire)


@pytest.mark.parametrize("key,value", [
    ("schema_version", "rpnh/collaboration/graph_build_recipe/v2"),
    ("builder_contract", "unknown/v1"), ("plugin_catalog", {}),
    ("managed_tools", {"draft": ["managed"]}), ("managed_tool_names", {"draft": ["managed"]}),
    ("max_attempts_per_node", True), ("max_attempts_per_node", 1.0),
])
def test_recipe_rejects_unknown_or_nonordinary_contract(key, value):
    options = recipe(); options[key] = value
    with pytest.raises(ValueError): rebuild_graph_module(make_graph_source(graph_wire()), options)


def test_recipe_missing_null_and_opaque_array_order_do_not_coalesce():
    first, second = recipe(), recipe()
    del second["max_attempts_per_node"]
    with pytest.raises(ValueError): rebuild_graph_module(make_graph_source(graph_wire()), second)
    second = recipe(max_attempts_per_node=None)
    assert canonical_json(first) != canonical_json(second)
    second = deepcopy(first); second["tools"].reverse()
    assert canonical_json(first) != canonical_json(second)
    assert canonical_json(rebuild_graph_module(make_graph_source(graph_wire()), first).to_dict()) == canonical_json(
        rebuild_graph_module(make_graph_source(graph_wire()), second).to_dict())


def test_source_array_reorder_preserves_explicit_identity_and_derived_module():
    first = make_graph_source(graph_wire(True)); ids = source_ids(first)
    second = deepcopy(first)
    second["graph"]["nodes"].reverse(); second["graph"]["arcs"].reverse()
    for node in second["graph"]["nodes"]:
        node["input_ports"].reverse(); node["output_ports"].reverse()
    assert canonical_json(first) != canonical_json(second)
    m1, m2 = (rebuild_graph_module(value, recipe()) for value in (first, second))
    sm1, sm2 = (make_graph_source_map(value, ids) for value in (first, second))
    assert canonical_json(m1.to_dict()) == canonical_json(m2.to_dict())
    assert sm1 == sm2
    assert graph_element_map(m1, first, sm1) == graph_element_map(m2, second, sm2)


def test_rename_keeps_ids_copy_requires_fresh_ids_and_exact_provenance():
    source = make_graph_source(graph_wire(True)); ids = source_ids(source)
    module = rebuild_graph_module(source, recipe()); source_map = make_graph_source_map(source, ids)
    element_map = graph_element_map(module, source, source_map)
    ref = {"schema_version": "rpnh/collaboration/source_version_ref/v1", "source_id": "source-a",
           "ref": {"entity_type": "collaboration_net_revision/v2", "logical_id": "resource:" + "1" * 32,
                   "version_id": "resource_version:" + "2" * 32}}
    parent = SimpleNamespace(source_map=source_map, element_map=element_map,
        revision=SimpleNamespace(revision_ref=SimpleNamespace(to_dict=lambda: ref)))
    changed = deepcopy(source)
    # Rename node and every endpoint, without deriving identity from the new name.
    for node in changed["graph"]["nodes"]:
        if node["node_id"] == "draft": node["node_id"] = "author"
    for arc in changed["graph"]["arcs"]:
        for end in (arc["source"], arc["target"]):
            if end["node_id"] == "draft": end["node_id"] = "author"
    changed["graph"]["ingress"]["node_id"] = "author"
    changed_ids = {key.replace("/nodes/draft", "/nodes/author"): value for key, value in ids.items()}
    new_map = make_graph_source_map(changed, changed_ids, parent)
    new_elements = graph_element_map(rebuild_graph_module(changed, recipe()), changed, new_map, parent)
    assert {row["element_id"] for row in new_elements["elements"]} == {row["element_id"] for row in element_map["elements"]}
    fresh = source_ids(source); copies = {fresh[key]: ids[key] for key in ids}
    copied_map = make_graph_source_map(source, fresh, parent, copies)
    copied_elements = graph_element_map(module, source, copied_map, parent)
    assert all(row["copied_from"]["revision_ref"] == ref for row in copied_map["elements"])
    assert all(row["copied_from"]["revision_ref"] == ref for row in copied_elements["elements"])
    with pytest.raises(ValueError): make_graph_source_map(source, ids, parent, {ids["/"]: ids["/"]})
    wrong = dict(ids); wrong["/"] = ids["/nodes/draft"]; wrong["/nodes/draft"] = ids["/"]
    with pytest.raises(ValueError): make_graph_source_map(source, wrong, parent)
