"""Versioned ordinary-v3 source reconstruction, using the existing sole builder.

This contract has no implicit defaults or native/managed plugin selectors. It
constructs declarations only; the material consumer separately checks exact HOST
resources and compiles through its explicitly trusted Registration.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import uuid

from jsonschema import ValidationError

from ..agent_workflows import AgentWorkflowGraph, build_agent_workflow_module
from ..module import MechanicalValidator
from ..registry.schema_catalog import canonical_json, canonical_text
from .materials import _ELEMENT_ID, _element_map

GRAPH_SOURCE_SCHEMA = "rpnh/collaboration/graph_author_source/v1"
GRAPH_RECIPE_SCHEMA = "rpnh/collaboration/graph_build_recipe/v1"
GRAPH_MAP_SCHEMA = "rpnh/collaboration/graph_source_map/v1"
GRAPH_BUILDER = "rpnh/ordinary_workflow_graph_builder/v1"
GRAPH_COMPONENT = "rpnh/agent-workflow-graph/v3"
GRAPH_CONFIG_SCHEMA = "application/rpnh_agent_workflow_graph_config/v3"


def _validate(schema, document):
    path = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    contract = json.loads((path / (schema.rsplit("/", 2)[1] + ".v1.schema.json")).read_text(encoding="utf-8"))
    # Strict JSON bytes preserve bool/int and missing/null distinctions. No
    # remote schema resolution: every reference in these contracts is local.
    value = json.loads(canonical_json(document))
    try:
        MechanicalValidator(contract).validate(value)
    except ValidationError as exc:
        raise ValueError(f"invalid {schema}: {exc.message}") from exc
    return value


def make_graph_source(graph):
    """Pin explicit v3 wire before its compatible loader can supply defaults."""
    return _validate(GRAPH_SOURCE_SCHEMA, {
        "schema_version": GRAPH_SOURCE_SCHEMA,
        "component_key": GRAPH_COMPONENT, "config_schema": GRAPH_CONFIG_SCHEMA,
        "graph": deepcopy(graph),
    })


def make_graph_recipe(*, executor_key, terminal_key, tools, required_schemas,
                      max_attempts_per_node, declaration_refs=()):
    """All builder choices are explicit; exact HOST refs are fixed on publish."""
    return _validate(GRAPH_RECIPE_SCHEMA, {
        "schema_version": GRAPH_RECIPE_SCHEMA, "builder_contract": GRAPH_BUILDER,
        "executor_key": executor_key, "terminal_key": terminal_key,
        "tools": list(tools), "required_schemas": list(required_schemas),
        "max_attempts_per_node": max_attempts_per_node,
        "plugin_catalog": None, "managed_tools": {}, "managed_tool_names": {},
        "declaration_refs": list(declaration_refs),
    })


def rebuild_graph_module(source, recipe):
    """Rebuild the whole Module from durable inputs, never target operations."""
    source = _validate(GRAPH_SOURCE_SCHEMA, source)
    recipe = _validate(GRAPH_RECIPE_SCHEMA, recipe)
    graph = AgentWorkflowGraph.from_mapping(source["graph"])
    return build_agent_workflow_module(graph,
        executor_key=recipe["executor_key"], terminal_key=recipe["terminal_key"],
        tools=recipe["tools"], required_schemas=recipe["required_schemas"],
        max_attempts_per_node=recipe["max_attempts_per_node"],
        plugin_catalog=None, managed_tools={}, managed_tool_names={})


def graph_source_elements(source):
    """Current name-based locators locate identities, but never mint them."""
    source = _validate(GRAPH_SOURCE_SCHEMA, source)
    result = {"/": "graph", "/ingress": "ingress", "/egress": "egress"}
    for node in source["graph"]["nodes"]:
        base = f"/nodes/{node['node_id']}"
        result[base] = "node"
        for direction in ("input", "output"):
            for port in node[f"{direction}_ports"]:
                result[f"{base}/{direction}_ports/{port['port_id']}"] = f"{direction}_port"
    for arc in source["graph"]["arcs"]:
        result[f"/arcs/{arc['arc_id']}"] = "arc"
    return result


def make_graph_source_map(source, source_ids, parent=None, copy_sources=None):
    """Retain explicit identities; copying records one exact same-kind parent."""
    expected, copies = graph_source_elements(source), dict(copy_sources or {})
    if set(source_ids) != set(expected):
        raise ValueError("source IDs must cover exactly graph, node, port, arc and boundary locators")
    ids = tuple(source_ids.values())
    if (any(not isinstance(value, str) or _ELEMENT_ID.fullmatch(value) is None for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("graph source IDs must be unique canonical stable identities")
    old = {} if parent is None else {row["element_id"]: row for row in parent.source_map["elements"]}
    if set(copies) - set(ids):
        raise ValueError("graph copy sources must name current source IDs")
    rows = []
    for locator, kind in sorted(expected.items()):
        identity, copied = source_ids[locator], copies.get(source_ids[locator])
        if identity in old and old[identity]["kind"] != kind:
            raise ValueError("retained graph source identity cannot change kind")
        if copied is not None and (parent is None or identity in old or copied not in old or old[copied]["kind"] != kind):
            raise ValueError("graph copy requires a new identity and an exact same-kind parent")
        rows.append({"element_id": identity, "kind": kind, "locator": locator,
            "copied_from": None if copied is None else {
                "revision_ref": parent.revision.revision_ref.to_dict(), "element_id": copied}})
    return _validate(GRAPH_MAP_SCHEMA, {"schema_version": GRAPH_MAP_SCHEMA,
        "source_contract": GRAPH_SOURCE_SCHEMA, "elements": rows})


def _derived_identity(source_identity, role):
    return "element:" + uuid.uuid5(uuid.UUID(source_identity.split(":", 1)[1]),
        canonical_text({"contract": GRAPH_BUILDER, "role": role})).hex


def graph_element_map(module, source, source_map, parent=None):
    """Project source identities to every Module element using semantic roles."""
    by_locator = {row["locator"]: row for row in source_map["elements"]}
    bindings = {
        "/": ("/", "module"), "/components/team": ("/", "component"),
        "/components/team/ports/request": ("/ingress", "public_port"),
        "/components/team/ports/result": ("/egress", "public_port"),
        "/entry/request": ("/ingress", "entry"),
        "/exit/result": ("/egress", "exit"), "/terminal": ("/egress", "terminal"),
    }
    feedback_nodes = {arc["target"]["node_id"] for arc in source["graph"]["arcs"] if arc["kind"] == "feedback"}
    for node in source["graph"]["nodes"]:
        name = node["node_id"]
        bindings[f"/components/team/operations/{name}"] = (f"/nodes/{name}", "operation:initial")
        if name in feedback_nodes:
            bindings[f"/components/team/operations/{name}__rework"] = (f"/nodes/{name}", "operation:feedback")
    ids, copies = {}, {}
    old_ids = set() if parent is None else {row["element_id"] for row in parent.element_map["elements"]}
    for locator, (source_locator, role) in bindings.items():
        row = by_locator[source_locator]
        identity = _derived_identity(row["element_id"], role)
        ids[locator] = identity
        if row["copied_from"] is not None:
            copied = _derived_identity(row["copied_from"]["element_id"], role)
            if copied in old_ids:
                copies[identity] = copied
    return _element_map(module, ids, parent, copies)
