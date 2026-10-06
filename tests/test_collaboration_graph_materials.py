"""Real graph author Registry writes and read-only full source reconstruction.

The only invoked HOST callable is the existing deterministic graph lowerer.
Executor/terminal/tool stubs deliberately fail if called. No run is started.
"""
from copy import deepcopy
from dataclasses import replace
import json
import sys
import uuid

import pytest

from cpn.rpnh.agent_workflows import (
    TEXT_SCHEMA, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID,
    WORKFLOW_GRAPH_COMPONENT_V3_KEY, lower_agent_workflow_graph,
)
from cpn.rpnh.collaboration import (
    GraphModuleAuthor, ValidatedGraphRevision, SourceQualifiedVersionRef,
    graph_author_schema_data, make_graph_source, validate_closed_revision,
)
from cpn.rpnh.collaboration.graph_authoring import GRAPH_REF_FIELDS, GRAPH_REVISION_TYPE
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from test_collaboration_graph_source import graph_wire, recipe, source_ids


def forbidden_execution(*args, **kwargs):
    raise AssertionError("author-only fixture must never execute any HOST operation/tool/terminal")


def registration():
    selected = Registration()
    selected.register_schema(TEXT_SCHEMA, {"$id": TEXT_SCHEMA,
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "string"})
    selected.register_schema(WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID, WORKFLOW_GRAPH_CONFIG_SCHEMA_V3)
    selected.register_schema("application/graph_test_config/v1", {
        "$id": "application/graph_test_config/v1", "$schema": "http://json-schema.org/draft-07/schema#", "type": "object"})
    selected.register_component(WORKFLOW_GRAPH_COMPONENT_V3_KEY, lower_agent_workflow_graph,
        identity={"implementation_id": "rpnh.agent_workflow_graph", "revision": "v3"},
        contracts={"config_schema": WORKFLOW_GRAPH_CONFIG_SCHEMA_V3_ID})
    selected.register_executor("test/ordinary-agent/v1", forbidden_execution,
        identity={"implementation_id": "test.graph_agent", "revision": "v1"},
        contracts={"transport": "deterministic", "config_schema": "application/graph_test_config/v1"})
    for key in ("test/graph-terminal/v1", "write_file", "complete_interaction"):
        selected.register_tool(key, forbidden_execution,
            identity={"implementation_id": "test.graph_tool", "revision": "v1"},
            contracts={"binding_protocol": "rpnh/module_terminal/v1"} if key.startswith("test/") else {})
    return selected


@pytest.fixture
def author_fixture(tmp_path):
    schemas, types, paths = graph_author_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    core = _RegistryCore(tmp_path / "graph-author", create=True, catalog=catalog)
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("graph-author-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-graph", command_id="bind:graph")
    ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(ref.entity_id), "principal_version_id": str(ref.version_id), "display_name": "Graph author fixture"}
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/principal/v1",
        idempotency_key="fixture:graph-principal")
    selected = registration()
    author = GraphModuleAuthor(gateway, selected, SourceQualifiedVersionRef("source-graph", ref))
    source = make_graph_source(graph_wire(True))
    return core, gateway, author, selected, source, source_ids(source)


def counts(core):
    return len(core.event_store.object_rows()), len(core.event_store.list_events())


def assert_author_only(core):
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1",
        "firing_started/v1", "execution_instance_created/v1")) == ()
    assert not any(name.startswith("cpn.plugins") for name in sys.modules)


def test_real_graph_publish_readonly_reopen_existing_full_consumer(author_fixture):
    core, gateway, author, selected, source, ids = author_fixture
    result = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    assert isinstance(result, ValidatedGraphRevision)
    assert result.revision.revision_ref.ref.entity_type == GRAPH_REVISION_TYPE
    assert result.source == source
    assert result.recipe["declaration_refs"] == result.host_requirements["declaration_refs"]
    refs = [result.revision.revision_ref.ref.version_id, *[
        getattr(result.revision, field).ref.resource_version_id for field in GRAPH_REF_FIELDS]]
    with core.event_store.connect() as db:
        transactions = {db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (str(ref),)).fetchone()[0] for ref in refs}
    assert len(transactions) == 8
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    reopened = validate_closed_revision(reader, result.revision.revision_ref, registration())
    assert canonical_json(reopened.module.to_dict()) == canonical_json(result.module.to_dict())
    assert reopened.source_map == result.source_map and reopened.recipe == result.recipe
    assert counts(reader) == before
    assert_author_only(core)


def test_graph_root_successor_replay_and_writer_reopen(author_fixture):
    core, gateway, author, selected, source, ids = author_fixture
    first = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    changed = deepcopy(source); changed["graph"]["nodes"][0]["instruction"] = "Revise the draft carefully."
    second = author.publish(source=changed, recipe=recipe(max_attempts_per_node=None), source_ids=ids,
        command_id="graph:r1", parent_ref=first.revision.revision_ref)
    assert second.revision.revision_ref.ref.entity_id == first.revision.revision_ref.ref.entity_id
    assert second.revision.parent_revision_refs == (first.revision.revision_ref,)
    before = counts(core)
    assert author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0").revision == first.revision
    assert counts(core) == before
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_author = GraphModuleAuthor(next_gateway, registration(), author.producer)
    assert next_author.publish(source=changed, recipe=recipe(max_attempts_per_node=None), source_ids=ids,
        command_id="graph:r1", parent_ref=first.revision.revision_ref).revision == second.revision
    assert counts(reopened) == before
    with pytest.raises(RegistryConflict):
        next_author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r1", parent_ref=first.revision.revision_ref)
    assert_author_only(reopened)


class InjectedCrash(RuntimeError):
    """Test-only cut after real durable publication, never fabricated success."""


@pytest.mark.parametrize("cut", range(7))
def test_each_durable_author_material_cut_reopens_reuses_and_locks_complete_command(author_fixture, monkeypatch, cut):
    from cpn.rpnh.collaboration import graph_authoring
    core, gateway, author, selected, source, ids = author_fixture
    original, durable = graph_authoring._publish_private_system, []
    def interrupted(*args, **kwargs):
        ref = original(*args, **kwargs)
        durable.append(ref)
        if len(durable) == cut + 1:
            raise InjectedCrash("after durable graph material")
        return ref
    monkeypatch.setattr(graph_authoring, "_publish_private_system", interrupted)
    with pytest.raises(InjectedCrash):
        author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:cut")
    assert len(core.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)) == 0
    monkeypatch.setattr(graph_authoring, "_publish_private_system", original)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    next_gateway = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    next_author = GraphModuleAuthor(next_gateway, registration(), author.producer)
    # Even when only the source material exists, a change to a later recipe
    # must conflict with the already frozen complete command.
    before = counts(reopened)
    with pytest.raises(RegistryConflict, match="conflict"):
        next_author.publish(source=source, recipe=recipe(max_attempts_per_node=None), source_ids=ids, command_id="graph:cut")
    assert counts(reopened) == before
    result = next_author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:cut")
    assert [getattr(result.revision, field).ref for field in GRAPH_REF_FIELDS[:len(durable)]] == durable
    assert len(reopened.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)) == 1
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert validate_closed_revision(reader, result.revision.revision_ref, registration()).source == source
    assert_author_only(reader)


def test_revision_commit_crash_keeps_all_materials_and_retries(author_fixture, monkeypatch):
    core, _, author, selected, source, ids = author_fixture
    original = core.event_store._insert_event
    def fail(db, event):
        original(db, event)
        if event.payload.get("object_type") == GRAPH_REVISION_TYPE:
            raise InjectedCrash("during revision transaction")
    monkeypatch.setattr(core.event_store, "_insert_event", fail)
    with pytest.raises(InjectedCrash):
        author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:commit-cut")
    assert not core.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)
    resources = len(core.event_store.object_rows_by_type("resource_version/v1"))
    monkeypatch.setattr(core.event_store, "_insert_event", original)
    result = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:commit-cut")
    assert len(core.event_store.object_rows_by_type("resource_version/v1")) == resources
    assert validate_closed_revision(core, result.revision.revision_ref, registration()).source == source


def test_after_revision_success_lost_reply_replays_exact_result(author_fixture, monkeypatch):
    from cpn.rpnh.collaboration import graph_authoring
    core, _, author, selected, source, ids = author_fixture
    original = graph_authoring.validate_closed_revision
    def lose_reply(*args, **kwargs):
        original(*args, **kwargs)
        raise InjectedCrash("lost success response")
    monkeypatch.setattr(graph_authoring, "validate_closed_revision", lose_reply)
    with pytest.raises(InjectedCrash):
        author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:lost-reply")
    assert len(core.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)) == 1
    before = counts(core)
    monkeypatch.setattr(graph_authoring, "validate_closed_revision", original)
    result = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:lost-reply")
    assert counts(core) == before and result.revision.command_id == "graph:lost-reply"


def test_real_rename_and_copy_source_identity_survive_reopen(author_fixture):
    core, _, author, selected, source, ids = author_fixture
    first = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    changed = deepcopy(source)
    for node in changed["graph"]["nodes"]:
        if node["node_id"] == "draft": node["node_id"] = "author"
    for arc in changed["graph"]["arcs"]:
        for endpoint in (arc["source"], arc["target"]):
            if endpoint["node_id"] == "draft": endpoint["node_id"] = "author"
    changed["graph"]["ingress"]["node_id"] = "author"
    next_ids = {key.replace("/nodes/draft", "/nodes/author"): value for key, value in ids.items()}
    second = author.publish(source=changed, recipe=recipe(), source_ids=next_ids,
        command_id="graph:rename", parent_ref=first.revision.revision_ref)
    assert {row["element_id"] for row in first.element_map["elements"]} == {row["element_id"] for row in second.element_map["elements"]}
    fresh = source_ids(changed)
    third = author.publish(source=changed, recipe=recipe(), source_ids=fresh,
        command_id="graph:copy", parent_ref=second.revision.revision_ref,
        copy_sources={fresh[key]: next_ids[key] for key in fresh})
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    result = validate_closed_revision(reader, third.revision.revision_ref, registration())
    assert all(row["copied_from"]["revision_ref"] == second.revision.revision_ref.to_dict() for row in result.source_map["elements"])
    assert all(row["copied_from"]["revision_ref"] == second.revision.revision_ref.to_dict() for row in result.element_map["elements"])


def test_same_command_concurrent_publish_has_one_revision(author_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core, _, author, selected, source, ids = author_fixture
    barrier = Barrier(2)
    def publish(_):
        barrier.wait()
        return author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(publish, (0, 1)))
    assert values[0].revision == values[1].revision
    assert len(core.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)) == 1
    assert_author_only(core)


def publish_mutation(author, document, schema, *, descriptors=None):
    """Adversarial but real canonical material, not SQL-fabricated success."""
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.registry.resource_service import _publish_private_system
    from cpn.rpnh.registry.resources import PrivateSystemOrigin, PublishResource
    resource = _publish_private_system(author.core, author.gateway._task_ref, PublishResource(
        origin=PrivateSystemOrigin(author.gateway._bootstrap_ref), payload=canonical_json(document),
        media_type="application/json", content_schema_ref=schema,
        content_schema_authority_ref=author.schemas.get(schema), summary="Adversarial graph fixture material",
        lifetime_ref=author.gateway._bootstrap_ref, descriptors=descriptors or {},
        idempotency_key="mutation:" + uuid.uuid4().hex))
    return SourceQualifiedResourceRef(author.binding["source_id"], resource)


def publish_forged_revision(author, revision, **changes):
    from cpn.rpnh.collaboration.graph_authoring import _revision_reference, GRAPH_REVISION_SCHEMA
    core = author.core
    command = "forged:" + uuid.uuid4().hex
    ref = _revision_reference(core, author.binding["source_id"], command,
        revision.parent_revision_refs[0] if revision.parent_revision_refs else None)
    record = replace(revision, revision_ref=ref, command_id=command, **changes)
    core.publish_bytes(object_type=GRAPH_REVISION_TYPE, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(record.to_dict()), metadata=record.to_dict(), media_type="application/json",
        schema_ref=GRAPH_REVISION_SCHEMA, idempotency_key=command)
    return record


@pytest.mark.parametrize("axis", ["instruction", "role", "profile", "tools", "budget", "terminal", "schema"])
def test_source_or_recipe_only_changes_reject_complete_module_mismatch(author_fixture, axis):
    from cpn.rpnh.collaboration.graph_source import GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    if axis in {"instruction", "role", "profile", "tools"}:
        document = deepcopy(good.source); node = document["graph"]["nodes"][0]
        if axis == "instruction": node["instruction"] = "Changed author instruction"
        if axis == "role": node["execution"]["role"] = "finalization_reviewer"
        if axis == "profile": node["execution"]["profile_id"] = "different_profile"
        if axis == "tools": node["execution"]["tools"] = ["complete_interaction", "read_file", "write_file"]
        field, schema = "graph_source_ref", GRAPH_SOURCE_SCHEMA
    else:
        document = deepcopy(good.recipe)
        if axis == "budget": document["max_attempts_per_node"] = 17
        if axis == "terminal": document["terminal_key"] = "test/other-terminal/v1"
        if axis == "schema": document["required_schemas"].append("application/another_schema/v1")
        field, schema = "graph_recipe_ref", GRAPH_RECIPE_SCHEMA
    record = publish_forged_revision(author, good.revision, **{field: publish_mutation(author, document, schema)})
    before = counts(core)
    with pytest.raises(RegistryConflict, match="complete source/recipe reconstruction"):
        validate_closed_revision(core, record.revision_ref, registration())
    assert counts(core) == before


@pytest.mark.parametrize("axis", ["instruction", "role", "profile", "tools", "budget", "terminal", "schema", "boolean", "missing", "opaque_order"])
def test_derived_module_only_changes_never_replace_the_source(author_fixture, axis):
    from cpn.rpnh.collaboration.materials import MODULE_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(max_attempts_per_node=1 if axis == "boolean" else 12), source_ids=ids, command_id="graph:r0")
    document = deepcopy(good.module.to_dict()); op = document["components"][0]["operations"][0]
    if axis == "instruction": op["config"]["node_synopsis"] = "Forged derived instruction"
    if axis == "role": op["config"]["agent_loop_role"] = "critic"
    if axis == "profile": op["config"]["execution_profile_id"] = "other"
    if axis == "tools": op["tools"].pop()
    if axis == "budget": op["config"]["resource_bounds"]["max_tool_turns"] = 2
    if axis == "terminal": document["terminal"]["config"]["run_outcome"] = "other"
    if axis == "schema": document["required_schemas"].reverse()
    if axis == "boolean": op["config"]["resource_bounds"]["max_llm_attempts"] = True
    if axis == "missing": del op["config"]["execution_profile_id"]
    if axis == "opaque_order": op["config"]["semantic_outcome_ids"].append("complete")
    record = publish_forged_revision(author, good.revision,
        definition_ref=publish_mutation(author, document, MODULE_SCHEMA))
    with pytest.raises(RegistryConflict, match="complete source/recipe reconstruction"):
        validate_closed_revision(core, record.revision_ref, registration())


def test_joint_derived_definition_and_host_mutation_still_cannot_replace_source(author_fixture):
    from cpn.rpnh.collaboration.materials import MODULE_SCHEMA, HOST_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    definition = deepcopy(good.module.to_dict()); definition["components"][0]["operations"][0]["config"]["node_synopsis"] = "Jointly forged"
    host = deepcopy(good.host_requirements); host["registrations"]["executor"]["test/ordinary-agent/v1"]["identity"]["revision"] = "forged"
    record = publish_forged_revision(author, good.revision,
        definition_ref=publish_mutation(author, definition, MODULE_SCHEMA),
        host_requirements_ref=publish_mutation(author, host, HOST_SCHEMA))
    with pytest.raises(RegistryConflict, match="complete source/recipe reconstruction"):
        validate_closed_revision(core, record.revision_ref, registration())


def test_joint_source_and_definition_change_cannot_reuse_old_complete_command(author_fixture):
    from cpn.rpnh.collaboration.graph_source import GRAPH_SOURCE_SCHEMA, rebuild_graph_module
    from cpn.rpnh.collaboration.materials import MODULE_SCHEMA
    from cpn.rpnh.collaboration.graph_authoring import _command
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    changed = deepcopy(source); changed["graph"]["nodes"][0]["instruction"] = "Both changed"
    original_command = _command(source_id=author.binding["source_id"], owner=good.revision.owner_task_ref,
        producer=author.producer, command_id=good.revision.command_id, parents=(),
        documents=(good.source, good.recipe, good.source_map, good.module.to_dict(), good.element_map, good.boundary_map, good.host_requirements))
    record = publish_forged_revision(author, good.revision,
        graph_source_ref=publish_mutation(author, changed, GRAPH_SOURCE_SCHEMA, descriptors={"graph_author_command_v1": original_command}),
        definition_ref=publish_mutation(author, rebuild_graph_module(changed, good.recipe).to_dict(), MODULE_SCHEMA))
    with pytest.raises(RegistryConflict, match="immutable complete author command"):
        validate_closed_revision(core, record.revision_ref, registration())


@pytest.mark.parametrize("axis", ["source_missing", "source_duplicate", "source_copy", "derived_identity", "boundary", "host_ref", "recipe_ref", "schema_authority"])
def test_source_mapping_boundaries_and_exact_selection_are_actually_consumed(author_fixture, axis):
    from cpn.rpnh.collaboration.graph_source import GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA
    from cpn.rpnh.collaboration.materials import ELEMENT_SCHEMA, BOUNDARY_SCHEMA, HOST_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    if axis.startswith("source_"):
        field, schema, document = "graph_source_mapping_ref", GRAPH_MAP_SCHEMA, deepcopy(good.source_map)
        if axis == "source_missing": document["elements"].pop()
        if axis == "source_duplicate": document["elements"].append(deepcopy(document["elements"][0]))
        if axis == "source_copy": document["elements"][0]["copied_from"] = {"revision_ref": good.revision.revision_ref.to_dict(), "element_id": ids["/"]}
    elif axis == "derived_identity":
        field, schema, document = "element_mapping_ref", ELEMENT_SCHEMA, deepcopy(good.element_map)
        # Mutate a non-boundary node identity, so ordinary element/boundary
        # checking alone accepts it; graph derivation must reject it.
        row = next(row for row in document["elements"] if row["locator"] == "/components/team/operations/draft")
        row["element_id"] = "element:" + uuid.uuid4().hex
    elif axis == "boundary":
        field, schema, document = "boundary_mapping_ref", BOUNDARY_SCHEMA, deepcopy(good.boundary_map)
        document["entries"] = []
    elif axis == "host_ref":
        field, schema, document = "host_requirements_ref", HOST_SCHEMA, deepcopy(good.host_requirements)
        document["declaration_refs"].pop()
    elif axis == "recipe_ref":
        field, schema, document = "graph_recipe_ref", GRAPH_RECIPE_SCHEMA, deepcopy(good.recipe)
        document["declaration_refs"].pop()
    else:
        field, schema, document = "graph_source_ref", None, deepcopy(good.source)
    record = publish_forged_revision(author, good.revision, **{field: publish_mutation(author, document, schema)})
    before = counts(core)
    with pytest.raises((ValueError, RegistryConflict)):
        validate_closed_revision(core, record.revision_ref, registration())
    assert counts(core) == before


@pytest.mark.parametrize("damage", ["payload", "source_commit", "recipe_schema_commit", "producer_relation", "observational_relation", "revision_commit"])
def test_fault_injection_exact_bytes_producer_and_commit_cannot_be_faked(author_fixture, damage):
    """Explicit storage fault injection; no SQL is used to manufacture success."""
    from cpn.rpnh.collaboration.graph_source import GRAPH_RECIPE_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    target = good.revision.graph_source_ref.ref.resource_version_id
    if damage == "payload":
        path = core.object_store.path_for_version(target)
        path.write_bytes(path.read_bytes().replace(b'Draft the result.', b'Alter the result.'))
    else:
        if damage == "recipe_schema_commit": target = author.schemas[GRAPH_RECIPE_SCHEMA].resource_version_id
        if damage == "revision_commit": target = good.revision.revision_ref.ref.version_id
        with core.event_store.connect() as db:
            if damage == "producer_relation":
                db.execute("DELETE FROM events WHERE event_id IN (SELECT published_event_id FROM relations WHERE json_extract(source_json,'$.version_id')=?)", (str(target),))
            elif damage == "observational_relation":
                db.execute("UPDATE events SET criticality='observational' WHERE event_id IN (SELECT published_event_id FROM relations WHERE json_extract(source_json,'$.version_id')=?)", (str(target),))
            else:
                db.execute("DELETE FROM events WHERE event_type='transaction_committed/v1' AND transaction_id=(SELECT transaction_id FROM objects WHERE version_id=?)", (str(target),))
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    with pytest.raises((ValueError, RegistryConflict)):
        validate_closed_revision(reader, good.revision.revision_ref, registration())
    assert counts(reader) == before


@pytest.mark.parametrize("selection", ["native", "managed", "remote_schema"])
def test_ordinary_profile_rejects_plugin_alias_and_remote_schema_before_lower(author_fixture, monkeypatch, selection):
    from cpn.rpnh.collaboration import graph_authoring
    core, _, author, selected, source, ids = author_fixture
    options = recipe()
    if selection == "native":
        selected.register_executor("test/aliased-native/v1", forbidden_execution,
            identity={"implementation_id": "test.native-alias", "revision": "v1"}, contracts={"native_plugin": {}})
        options["executor_key"] = "test/aliased-native/v1"
    elif selection == "managed":
        selected.register_tool("test/aliased-managed/v1", forbidden_execution,
            identity={"implementation_id": "test.managed-alias", "revision": "v1"}, contracts={"managed_plugin": {}})
        options["tools"].append("test/aliased-managed/v1")
    else:
        selected.register_schema("application/external_schema/v1", {
            "$id": "application/external_schema/v1", "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "https://invalid.example/remote-schema"})
        options["required_schemas"].append("application/external_schema/v1")
    before = counts(core)
    monkeypatch.setattr(graph_authoring, "compile_module", forbidden_execution)
    with pytest.raises(ValueError, match="excludes native and managed|self-contained offline"):
        author.publish(source=source, recipe=options, source_ids=ids, command_id="graph:unsupported")
    assert counts(core) == before


def test_legacy_graph_is_still_legacy_and_v2_cannot_downgrade_or_enter_branch_assembly(author_fixture):
    from cpn.rpnh.collaboration import ClosedModuleAuthor, AssemblyMember
    core, gateway, author, selected, source, ids = author_fixture
    graph = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    legacy_author = ClosedModuleAuthor(gateway, selected, author.producer)
    element_ids = {row["locator"]: row["element_id"] for row in graph.element_map["elements"]}
    legacy = legacy_author.publish(module=graph.module, element_ids=element_ids, command_id="legacy:graph")
    assert not isinstance(legacy, ValidatedGraphRevision) and not hasattr(legacy, "source")
    assert not isinstance(validate_closed_revision(core, legacy.revision.revision_ref, registration()), ValidatedGraphRevision)
    branch = gateway.create_author_branch(head_revision_ref=legacy.revision.revision_ref, command_id="legacy:branch")
    assert branch.head_revision_ref == legacy.revision.revision_ref
    before = counts(core)
    with pytest.raises(ValueError, match="downgrade"):
        legacy_author.publish(module=graph.module, element_ids=element_ids, command_id="legacy:bad-parent", parent_ref=graph.revision.revision_ref)
    with pytest.raises(ValueError, match="no legacy proof upgrade"):
        author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:bad-parent", parent_ref=legacy.revision.revision_ref)
    with pytest.raises((ValueError, TypeError)):
        gateway.create_author_branch(head_revision_ref=graph.revision.revision_ref, command_id="graph:branch-not-supported")
    with pytest.raises((ValueError, TypeError)):
        AssemblyMember("member:" + uuid.uuid4().hex, "Graph v2", graph.revision.revision_ref)
    assert counts(core) == before
    assert_author_only(core)


def test_unknown_revision_and_incomplete_or_plain_new_contract_fail_closed(author_fixture):
    from cpn.rpnh.collaboration.graph_authoring import GraphNetRevision
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    unknown = SourceQualifiedVersionRef(author.binding["source_id"], VersionRef("collaboration_net_revision/v3",
        good.revision.revision_ref.ref.entity_id, good.revision.revision_ref.ref.version_id))
    with pytest.raises((TypeError, ValueError)):
        validate_closed_revision(core, unknown, registration())
    for field in ("graph_source_ref", "graph_recipe_ref", "graph_source_mapping_ref", "source_contract"):
        document = good.revision.to_dict(); del document[field]
        with pytest.raises(SchemaGovernanceError): GraphNetRevision.from_dict(document, catalog=core.catalog)
    document = good.revision.to_dict(); document["source_contract"] = "plain_module/v1"
    with pytest.raises(SchemaGovernanceError): GraphNetRevision.from_dict(document, catalog=core.catalog)
    document = good.revision.to_dict(); document["definition_kind"] = "open_region"
    with pytest.raises(SchemaGovernanceError): GraphNetRevision.from_dict(document, catalog=core.catalog)


def test_exact_ref_owner_and_producer_mismatches_are_rejected(author_fixture):
    from cpn.rpnh.collaboration import SourceQualifiedResourceRef
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.collaboration.graph_authoring import GRAPH_REVISION_SCHEMA
    core, _, author, selected, source, ids = author_fixture
    good = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:r0")
    absent = SourceQualifiedResourceRef(author.binding["source_id"], ResourceVersionRef(new_id("resource"), new_id("resource_version")))
    record = publish_forged_revision(author, good.revision, graph_source_ref=absent)
    with pytest.raises((ValueError, RegistryConflict)):
        validate_closed_revision(core, record.revision_ref, registration())
    bad_owner = SourceQualifiedVersionRef(author.binding["source_id"], VersionRef("task/v1", new_id("task"), new_id("task_version")))
    record = publish_forged_revision(author, good.revision, owner_task_ref=bad_owner)
    with pytest.raises((ValueError, RegistryConflict)):
        validate_closed_revision(core, record.revision_ref, registration())
    bad_producer = SourceQualifiedVersionRef(author.binding["source_id"], VersionRef("principal/v1", new_id("principal"), new_id("principal_version")))
    record = publish_forged_revision(author, good.revision, producer_principal_ref=bad_producer)
    with pytest.raises((ValueError, RegistryConflict)):
        validate_closed_revision(core, record.revision_ref, registration())
    with pytest.raises(ValueError, match="bound local source"):
        replace(good.revision, graph_source_ref=SourceQualifiedResourceRef("wrong-source", good.revision.graph_source_ref.ref))
    # Canonical duplicate result identity cannot borrow another command's proof.
    ref = SourceQualifiedVersionRef(author.binding["source_id"], VersionRef(GRAPH_REVISION_TYPE, new_id("resource"), new_id("resource_version")))
    duplicate = replace(good.revision, revision_ref=ref)
    core.publish_bytes(object_type=GRAPH_REVISION_TYPE, logical_id=ref.ref.entity_id, version_id=ref.ref.version_id,
        payload=canonical_json(duplicate.to_dict()), metadata=duplicate.to_dict(), media_type="application/json",
        schema_ref=GRAPH_REVISION_SCHEMA, idempotency_key="forged:second-result")
    with pytest.raises(RegistryConflict, match="revision identity"):
        validate_closed_revision(core, ref, registration())


@pytest.mark.parametrize("different", ["source", "source_ids", "source_order", "recipe_order", "parent"])
def test_first_source_alone_freezes_even_semantically_equal_later_inputs(author_fixture, monkeypatch, different):
    from cpn.rpnh.collaboration import graph_authoring
    core, _, author, selected, source, ids = author_fixture
    parent = author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:possible-parent")
    original = graph_authoring._publish_private_system
    def stop(*args, **kwargs):
        original(*args, **kwargs)
        raise InjectedCrash("after first source")
    monkeypatch.setattr(graph_authoring, "_publish_private_system", stop)
    with pytest.raises(InjectedCrash):
        author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:frozen")
    monkeypatch.setattr(graph_authoring, "_publish_private_system", original)
    changed, options, next_ids, parent_ref = deepcopy(source), recipe(), dict(ids), None
    if different == "source": changed["graph"]["nodes"][0]["instruction"] = "Changed"
    if different == "source_ids": next_ids["/arcs/draft_to_review"] = "element:" + uuid.uuid4().hex
    if different == "source_order": changed["graph"]["nodes"].reverse()
    if different == "recipe_order": options["tools"].reverse()
    if different == "parent": parent_ref = parent.revision.revision_ref
    before = counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        author.publish(source=changed, recipe=options, source_ids=next_ids, command_id="graph:frozen", parent_ref=parent_ref)
    assert counts(core) == before
    assert author.publish(source=source, recipe=recipe(), source_ids=ids, command_id="graph:frozen").source == source


def test_concurrent_different_complete_commands_have_one_success(author_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    core, _, author, selected, source, ids = author_fixture
    barrier = Barrier(2)
    def publish(limit):
        barrier.wait()
        try:
            return author.publish(source=source, recipe=recipe(max_attempts_per_node=limit), source_ids=ids, command_id="graph:race")
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (2, 3)))
    assert sum(isinstance(item, ValidatedGraphRevision) for item in results) == 1
    assert sum(isinstance(item, RegistryConflict) for item in results) == 1
    assert len(core.event_store.object_rows_by_type(GRAPH_REVISION_TYPE)) == 1
    winner = next(item for item in results if isinstance(item, ValidatedGraphRevision))
    assert validate_closed_revision(core, winner.revision.revision_ref, registration()).recipe == winner.recipe
