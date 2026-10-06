"""Real inert plain analysis producer/strict reader, with forbidden executors."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import subprocess
import uuid

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, lower_operation, register_basic_components
from cpn.rpnh.collaboration import (
    ClosedModuleAuthor, PlainModuleMergeAnalyzer, SourceQualifiedVersionRef,
    plain_merge_schema_data, author_material_schema_data, read_plain_merge_analysis,
)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration.plain_merge import ANALYSIS_SCHEMA, COMMAND_SCHEMA
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.petri_contracts import PNFragment, LeaseIdentityDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json

TEXT = "test/plain_merge_text/v1"
CONFIG = "test/plain_merge_config/v1"
EXECUTOR = "test/plain-merge-executor/v1"
TERMINAL = "test/plain-merge-terminal/v1"


def forbidden(*args, **kwargs):
    raise AssertionError("plain author analysis must not execute business/runtime/external effects")


def lower_multiple(config, context):
    parts = [lower_operation(config, replace(context, operations=(op,))) for op in context.operations]
    return PNFragment(parts[0].places, tuple(t for part in parts for t in part.transitions),
        tuple(a for part in parts for a in part.arcs), parts[0].ports, context.operations)


def registration():
    selected = Registration()
    register_basic_components(selected)
    selected.register_schema(TEXT, {"$schema": "http://json-schema.org/draft-07/schema#", "$id": TEXT, "type": "string"})
    selected.register_schema(CONFIG, {"$schema": "http://json-schema.org/draft-07/schema#", "$id": CONFIG, "type": "object"})
    selected.register_component("test/multiple/v1", lower_multiple,
        identity={"implementation_id": "test.multiple_operations", "revision": "v1"},
        contracts={"config_schema": CONFIG_SCHEMA_ID})
    selected.register_executor(EXECUTOR, forbidden,
        identity={"implementation_id": "test.plain_merge", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None, "config_schema": CONFIG})
    selected.register_tool(TERMINAL, forbidden,
        identity={"implementation_id": "test.plain_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    return selected


def module():
    component = {"name": "a", "key": "operation", "config_schema": CONFIG_SCHEMA_ID, "config": {},
        "ports": [{"name": "request", "direction": "input", "schema": TEXT},
                  {"name": "result", "direction": "output", "schema": TEXT}],
        "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request"], "outputs": ["result"],
            "request_port": None, "tools": [], "config": {},
            "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]}
    other = deepcopy(component); other["name"] = "b"
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1", "name": "Plain",
        "components": [component, other], "links": [],
        "entry": {"request": {"component": "a", "port": "request"}, "second": {"component": "b", "port": "request"}},
        "exit": {"result": {"component": "a", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "a", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {}},
        "required_schemas": [CONFIG_SCHEMA_ID, CONFIG, TEXT], "budgets": {}})


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    selected = registration()
    schemas, types, paths = plain_merge_schema_data()
    core = _RegistryCore(tmp_path / "plain-merge", create=True, catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("plain-merge-fixture/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="plain-source", command_id="bind")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id), "display_name": "Plain author"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1", idempotency_key="principal")
    producer = SourceQualifiedVersionRef("plain-source", principal)
    author = ClosedModuleAuthor(gateway, selected, producer)
    analyzer = PlainModuleMergeAnalyzer(gateway, selected, producer)
    original = module()
    ids = {path: "element:" + uuid.uuid5(uuid.NAMESPACE_URL, path).hex for path in _elements(original)}
    base = author.publish(module=original, element_ids=ids, command_id="base")
    return core, gateway, selected, author, analyzer, base, ids


def publish(fixture, document, command, ids=None, parent=None):
    _, _, _, author, _, base, original_ids = fixture
    return author.publish(module=ModuleDeclaration.from_dict(document), element_ids=original_ids if ids is None else ids,
        parent_ref=base.revision.revision_ref if parent is None else parent, command_id=command)


def request(fixture, left, right, **changes):
    return {"local_ref": left.revision.revision_ref, "incoming_ref": right.revision.revision_ref,
            "base_ref": fixture[5].revision.revision_ref, "command_id": "analysis", **changes}


def counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events())


def assert_inert(core, revisions):
    assert len(core.event_store.object_rows_by_type("collaboration_net_revision/v1")) == revisions
    assert core.event_store.object_rows_by_type("collaboration_branch/v1") == ()
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "firing_started/v1", "execution_instance_created/v1")) == ()


def test_real_analysis_rename_content_pins_reopens_and_replays(fixture):
    core, _, selected, _, analyzer, base, ids = fixture
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["name"] = "renamed"
    left_doc["entry"]["second"]["component"] = "renamed"
    left_ids = {p.replace("/components/b", "/components/renamed"): i for p, i in ids.items()}
    right_doc["components"][1]["operations"][0]["config"] = {"value": 2}
    left, right = publish(fixture, left_doc, "left", left_ids), publish(fixture, right_doc, "right")
    before = counts(core)
    result = analyzer.analyze(**request(fixture, left, right))
    assert result.document["status"] == "analyzed"
    assert result.document["base_revision_ref"] == base.revision.revision_ref.to_dict()
    assert not result.document["conflicts"]
    assert len(result.document["differences"]) == 2
    assert counts(core)[0] == before[0] + 2
    assert result.document["inputs"]["local"]["definition_ref"] == left.revision.definition_ref.to_dict()
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    frozen = counts(core)
    assert read_plain_merge_analysis(reader, result.analysis_ref, selected).document == result.document
    assert analyzer.analyze(**request(fixture, left, right)) == result
    assert counts(core) == frozen
    assert_inert(core, 3)


@pytest.mark.parametrize("left_value,right_value", [(True, 1), (1, 1.0), (1, 2)])
def test_exact_type_sensitive_opaque_conflict(fixture, left_value, right_value):
    analyzer, base = fixture[4:6]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][0]["operations"][0]["config"] = {"value": left_value}
    right_doc["components"][0]["operations"][0]["config"] = {"value": right_value}
    left, right = publish(fixture, left_doc, "left"), publish(fixture, right_doc, "right")
    result = analyzer.analyze(**request(fixture, left, right))
    conflict = next(c for c in result.document["conflicts"] if c["reason"] == "divergent_atom")
    assert type(conflict["local"][0]["value"]["config"]["value"]) is type(left_value)
    assert type(conflict["incoming"][0]["value"]["config"]["value"]) is type(right_value)
    assert_inert(fixture[0], 3)


def test_delete_modify_and_cross_boundary_dependency_are_explicit(fixture):
    analyzer, base, ids = fixture[4:]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"].pop()
    left_doc["entry"].pop("second")
    left_ids = {p: i for p, i in ids.items() if not p.startswith("/components/b") and p != "/entry/second"}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "changed"}
    consumer = deepcopy(right_doc["components"][1]); consumer["name"] = "consumer"
    right_doc["components"].append(consumer)
    right_doc["links"] = [{"source": {"component": "b", "port": "result"},
                           "target": {"component": "consumer", "port": "request"}}]
    right_ids = {p: ids.get(p, "element:" + uuid.uuid5(uuid.NAMESPACE_OID, p).hex)
                 for p in _elements(ModuleDeclaration.from_dict(right_doc))}
    left, right = publish(fixture, left_doc, "left", left_ids), publish(fixture, right_doc, "right", right_ids)
    result = analyzer.analyze(**request(fixture, left, right))
    reasons = {c["reason"] for c in result.document["conflicts"]}
    assert {"delete_modify", "delete_dependency"} <= reasons
    assert all(c["subjects"] and c["base"] and c["local"] and c["incoming"] for c in result.document["conflicts"])


def test_independent_component_content_changes_are_preserved_as_diffs(fixture):
    analyzer, base = fixture[4:6]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][0]["operations"][0]["config"] = {"value": "left"}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "right"}
    left, right = publish(fixture, left_doc, "left"), publish(fixture, right_doc, "right")
    result = analyzer.analyze(**request(fixture, left, right))
    assert len(result.document["differences"]) == 2 and result.document["conflicts"] == []


def test_global_budget_and_terminal_changes_are_coupled(fixture):
    analyzer, base = fixture[4:6]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["budgets"] = {"cost": 3}
    right_doc["terminal"]["config"] = {"value": "new"}
    left, right = publish(fixture, left_doc, "left"), publish(fixture, right_doc, "right")
    result = analyzer.analyze(**request(fixture, left, right))
    assert any(c["reason"] == "coupled_contract" for c in result.document["conflicts"])


def test_unrelated_histories_and_asserted_wrong_base_do_not_invent_three_way(fixture):
    core, _, _, author, analyzer, base, ids = fixture
    root = author.publish(module=base.module, element_ids=ids, command_id="other-root")
    result = analyzer.analyze(local_ref=base.revision.revision_ref, incoming_ref=root.revision.revision_ref, command_id="unrelated")
    assert result.document["status"] == "unrelated_histories"
    assert result.document["base_revision_ref"] is None
    assert not result.document["normalized"] and not result.document["differences"] and not result.document["conflicts"]
    before = counts(core)
    with pytest.raises(ValueError, match="unique nearest"):
        analyzer.analyze(**request(fixture, base, root, command_id="bad-base"))
    assert counts(core) == before


def test_interrupted_first_command_freezes_all_later_request_bytes(fixture, monkeypatch):
    import cpn.rpnh.collaboration.plain_merge as implementation
    core, _, _, _, analyzer, base, _ = fixture
    left_doc = base.module.to_dict(); left_doc["name"] = "Left"
    left = publish(fixture, left_doc, "left")
    original = implementation._publish_private_system
    def interrupted(core, owner, resource):
        if resource.content_schema_ref == ANALYSIS_SCHEMA: raise RuntimeError("cut after command")
        return original(core, owner, resource)
    monkeypatch.setattr(implementation, "_publish_private_system", interrupted)
    with pytest.raises(RuntimeError, match="cut after command"):
        analyzer.analyze(**request(fixture, left, base))
    frozen = counts(core)
    monkeypatch.setattr(implementation, "_publish_private_system", original)
    with pytest.raises(RegistryConflict, match="immutable"):
        analyzer.analyze(**request(fixture, base, left))
    assert counts(core) == frozen
    result = analyzer.analyze(**request(fixture, left, base))
    assert result.document["request"]["local_revision_ref"] == left.revision.revision_ref.to_dict()
    assert_inert(core, 2)


def test_plain_opt_in_does_not_change_existing_catalog():
    prior = author_material_schema_data()
    schemas, types, paths = plain_merge_schema_data()
    assert set(schemas) - set(prior[0]) == {ANALYSIS_SCHEMA, COMMAND_SCHEMA}
    assert types == prior[1]
    assert all(canonical_json(schemas[k]) == canonical_json(v) for k, v in prior[0].items())


@pytest.mark.parametrize("connected", ["link", "same_component"])
def test_unchanged_connections_couple_cross_side_protocol_changes(fixture, connected):
    core, _, _, author, analyzer, base, ids = fixture
    doc = base.module.to_dict()
    if connected == "link":
        doc["links"] = [{"source": {"component": "a", "port": "result"},
                          "target": {"component": "b", "port": "request"}}]
        doc["entry"].pop("second")
        updated_ids = {p: i for p, i in ids.items() if p != "/entry/second"}
        updated_ids["/links/0"] = "element:" + "e" * 32
    else:
        doc["components"][1]["key"] = "test/multiple/v1"
        op = deepcopy(doc["components"][1]["operations"][0]); op["name"] = "again"
        doc["components"][1]["operations"].append(op)
        updated_ids = {**ids, "/components/b/operations/again": "element:" + "e" * 32}
    joint_base = publish(fixture, doc, "joint-base", updated_ids)
    left_doc, right_doc = deepcopy(doc), deepcopy(doc)
    if connected == "link":
        left_doc["components"][0]["operations"][0]["config"] = {"producer_count": 2}
        right_doc["components"][1]["operations"][0]["config"] = {"expected_contributions": 1}
    else:
        left_doc["components"][1]["operations"][0]["config"] = {"feedback": 1}
        right_doc["components"][1]["operations"][1]["config"] = {"expected_feedback": 2}
    left = publish(fixture, left_doc, "left", updated_ids, joint_base.revision.revision_ref)
    right = publish(fixture, right_doc, "right", updated_ids, joint_base.revision.revision_ref)
    result = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=joint_base.revision.revision_ref, command_id="analysis")
    coupled = [c for c in result.document["conflicts"] if c["reason"] == "coupled_contract"]
    assert len(coupled) == 1 and len(coupled[0]["subjects"]) == 2


@pytest.mark.parametrize("damage", ["normalized", "differences", "material_pin", "ancestry"])
def test_strict_read_recomputes_schema_valid_canonical_but_false_analysis(fixture, monkeypatch, damage):
    import cpn.rpnh.collaboration.plain_merge as implementation
    core, _, selected, _, analyzer, base, _ = fixture
    document = base.module.to_dict(); document["name"] = "Left"
    left = publish(fixture, document, "left")
    original = implementation._prepare_at
    def forge(*args, **kwargs):
        analysis, command = original(*args, **kwargs)
        if damage == "normalized":
            key = next(k for k in analysis["normalized"]["local"]["atoms"] if k.endswith("/name"))
            analysis["normalized"]["local"]["atoms"][key] = "forged"
        elif damage == "differences": analysis["differences"] = []
        elif damage == "material_pin": analysis["inputs"]["local"]["definition_ref"] = base.revision.definition_ref.to_dict()
        else: analysis["ancestry"] = []
        return analysis, command
    monkeypatch.setattr(implementation, "_prepare_at", forge)
    result = analyzer.analyze(**request(fixture, left, base))
    monkeypatch.setattr(implementation, "_prepare_at", original)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with pytest.raises(RegistryConflict, match="reconstruction"):
        read_plain_merge_analysis(reader, result.analysis_ref, selected)


@pytest.mark.parametrize("marker", ["unknown", "dual"])
def test_extra_or_dual_analysis_command_markers_fail_closed(fixture, monkeypatch, marker):
    import cpn.rpnh.collaboration.plain_merge as implementation
    core, _, _, _, analyzer, base, _ = fixture
    original = implementation._publish_private_system
    def polluted(core, owner, resource):
        if resource.content_schema_ref == ANALYSIS_SCHEMA:
            extra = "plain_merge_analysis_v999" if marker == "unknown" else "closed_author_command_v1"
            resource = replace(resource, descriptors={**resource.descriptors, extra: "forged"})
        return original(core, owner, resource)
    monkeypatch.setattr(implementation, "_publish_private_system", polluted)
    with pytest.raises(RegistryConflict, match="reconstruction"):
        analyzer.analyze(**request(fixture, base, base))
    assert_inert(core, 1)


def test_opaque_ancestor_cannot_be_laundered_by_clearing_constraints(fixture):
    core, _, _, _, analyzer, base, _ = fixture
    doc = base.module.to_dict(); doc["designer_constraints"] = {"native_composition": "opaque"}
    opaque = publish(fixture, doc, "opaque")
    clean = publish(fixture, base.module.to_dict(), "cleared", parent=opaque.revision.revision_ref)
    before = counts(core)
    with pytest.raises(ValueError, match="excludes"):
        analyzer.analyze(local_ref=clean.revision.revision_ref, incoming_ref=base.revision.revision_ref, command_id="analysis")
    assert counts(core) == before


def test_raw_unproved_multi_parent_remains_unsupported(fixture):
    core, _, _, _, analyzer, base, _ = fixture
    doc = base.module.to_dict(); doc["name"] = "Left"
    left = publish(fixture, doc, "left")
    ref = SourceQualifiedVersionRef("plain-source", VersionRef("collaboration_net_revision/v1", new_id("resource"), new_id("resource_version")))
    record = replace(left.revision, revision_ref=ref, parent_revision_refs=(left.revision.revision_ref, base.revision.revision_ref))
    core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key="raw-multiparent")
    before = counts(core)
    with pytest.raises(ValueError, match="single-parent"):
        analyzer.analyze(local_ref=ref, incoming_ref=left.revision.revision_ref, command_id="analysis")
    assert counts(core) == before


def test_all_declared_reference_roles_survive_port_operation_rename(fixture):
    _, _, _, _, analyzer, base, ids = fixture
    doc = base.module.to_dict()
    op = doc["components"][0]["operations"][0]
    op["request_port"] = "request"
    op["outcomes"][0]["effects"] = [{"key": TERMINAL, "bindings": {"read": "request", "write": "result"}, "config": {}, "references": {}}]
    joint = publish(fixture, doc, "joint")
    left_doc, right_doc = deepcopy(doc), deepcopy(doc)
    component = left_doc["components"][0]
    component["ports"][0]["name"], component["ports"][1]["name"] = "input", "output"
    op = component["operations"][0]
    op.update(name="perform", inputs=["input"], outputs=["output"], request_port="input")
    op["outcomes"][0]["products"][0]["port"] = "output"
    op["outcomes"][0]["effects"][0]["bindings"] = {"read": "input", "write": "output"}
    left_doc["entry"]["request"]["port"] = "input"
    left_doc["exit"]["result"]["port"] = "output"
    left_doc["terminal"]["source"]["port"] = "output"
    left_doc["terminal"]["operation"] = "perform"
    changed_ids = {p.replace("/components/a/ports/request", "/components/a/ports/input")
                    .replace("/components/a/ports/result", "/components/a/ports/output")
                    .replace("/components/a/operations/run", "/components/a/operations/perform"): value for p, value in ids.items()}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "right"}
    left = publish(fixture, left_doc, "left", changed_ids, joint.revision.revision_ref)
    right = publish(fixture, right_doc, "right", parent=joint.revision.revision_ref)
    result = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=joint.revision.revision_ref, command_id="analysis")
    assert result.document["conflicts"] == []
    local_changes = [d for d in result.document["differences"] if d["change"] == "local"]
    assert len(local_changes) == 3 and all(d["subject"].endswith("/name") for d in local_changes)
    body = result.document["normalized"]["local"]["atoms"][ids["/components/a/operations/run"] + "/value"]
    assert body["inputs"] == [ids["/components/a/ports/request"]]
    assert body["request_port"] == ids["/components/a/ports/request"]
    assert body["outcomes"][0]["effects"][0]["bindings"]["write"] == ids["/components/a/ports/result"]


def test_known_graph_component_key_cannot_be_presented_as_plain(fixture):
    core, _, selected, _, analyzer, base, _ = fixture
    selected.register_component("rpnh/agent-workflow-graph/v3", lower_operation,
        identity={"implementation_id": "test.alias_graph_name", "revision": "v1"}, contracts={"config_schema": CONFIG_SCHEMA_ID})
    doc = base.module.to_dict(); doc["components"][1]["key"] = "rpnh/agent-workflow-graph/v3"
    graph_named = publish(fixture, doc, "graph-key")
    before = counts(core)
    with pytest.raises(ValueError, match="excludes"):
        analyzer.analyze(**request(fixture, graph_named, base))
    assert counts(core) == before


def test_same_name_different_stable_ids_reports_conflict(fixture):
    _, _, _, _, analyzer, base, ids = fixture
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["name"] = "shared"
    left_doc["entry"]["second"]["component"] = "shared"
    left_ids = {p.replace("/components/b", "/components/shared"): i for p, i in ids.items()}
    other = deepcopy(right_doc["components"][1]); other["name"] = "shared"
    right_doc["components"].append(other)
    right_doc["entry"]["third"] = {"component": "shared", "port": "request"}
    right_ids = {p: ids.get(p, "element:" + uuid.uuid5(uuid.NAMESPACE_OID, p).hex)
                 for p in _elements(ModuleDeclaration.from_dict(right_doc))}
    left, right = publish(fixture, left_doc, "left", left_ids), publish(fixture, right_doc, "right", right_ids)
    result = analyzer.analyze(**request(fixture, left, right))
    assert any(c["reason"] == "name_collision" for c in result.document["conflicts"])


def test_actual_lowered_resource_carrier_couples_disconnected_author_regions(fixture, monkeypatch):
    core, _, selected, _, analyzer, base, ids = fixture
    def resource_lower(config, context):
        return replace(lower_operation(config, context), lease_identities=(LeaseIdentityDeclaration("shared_author_lease"),))
    selected.register_component("test/resource-bearing/v1", resource_lower,
        identity={"implementation_id": "test.resource_bearing", "revision": "v1"}, contracts={"config_schema": CONFIG_SCHEMA_ID})
    doc = base.module.to_dict(); doc["components"][0]["key"] = "test/resource-bearing/v1"
    resource_base = publish(fixture, doc, "resource-base")
    left_doc, right_doc = deepcopy(doc), deepcopy(doc)
    left_doc["components"][0]["operations"][0]["config"] = {"resource_intent": "left"}
    right_doc["components"][1]["operations"][0]["config"] = {"resource_intent": "right"}
    left = publish(fixture, left_doc, "left", parent=resource_base.revision.revision_ref)
    right = publish(fixture, right_doc, "right", parent=resource_base.revision.revision_ref)
    result = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=resource_base.revision.revision_ref, command_id="analysis")
    hint = result.document["normalized"]["base"]["derived_resource_dependencies"]
    assert hint == [{"component_element_id": ids["/components/a"], "carrier_kinds": ["lease_identities"]}]
    conflict = next(c for c in result.document["conflicts"] if c["reason"] == "lowered_resource_coupling")
    assert len(conflict["subjects"]) == 2
    import cpn.rpnh.collaboration.plain_merge as implementation
    original = implementation._prepare_at
    def erase_hint(*args, **kwargs):
        analysis, command = original(*args, **kwargs)
        for model in analysis["normalized"].values(): model["derived_resource_dependencies"] = []
        analysis["conflicts"] = [c for c in analysis["conflicts"] if c["reason"] != "lowered_resource_coupling"]
        return analysis, command
    monkeypatch.setattr(implementation, "_prepare_at", erase_hint)
    forged = analyzer.analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=resource_base.revision.revision_ref, command_id="forged-analysis")
    monkeypatch.setattr(implementation, "_prepare_at", original)
    with pytest.raises(RegistryConflict, match="reconstruction"):
        read_plain_merge_analysis(core, forged.analysis_ref, selected)
    assert_inert(core, 4)


def test_analysis_rejects_revision_clone_beyond_inherited_full_proof(fixture):
    from cpn.rpnh.collaboration import validate_closed_revision
    core, _, selected, _, analyzer, base, _ = fixture
    ref = SourceQualifiedVersionRef("plain-source", VersionRef("collaboration_net_revision/v1", new_id("resource"), new_id("resource_version")))
    record = replace(base.revision, revision_ref=ref)
    core.publish_bytes(object_type=ref.ref.entity_type, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
        schema_ref="registry_v1/collaboration_net_revision/v1", idempotency_key="raw-clone")
    # Document the inherited reader boundary rather than altering its contract.
    assert validate_closed_revision(core, ref, selected).revision.revision_ref == ref
    assert analyzer.analyze(local_ref=base.revision.revision_ref, incoming_ref=base.revision.revision_ref,
        command_id="legitimate-control").document["status"] == "analyzed"
    before = counts(core)
    with pytest.raises(RegistryConflict, match="producer identit"):
        analyzer.analyze(local_ref=ref, incoming_ref=base.revision.revision_ref, command_id="reject-clone")
    assert counts(core) == before
