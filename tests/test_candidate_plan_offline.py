"""No implicit retrieval anywhere in the candidate's pure wire/read path."""
from copy import deepcopy
import json
import socket
import urllib.request

import pytest
from referencing.exceptions import Unresolvable

from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.executable_net import load_compiled_net, _load_compiled_net_offline
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.collaboration.candidate_plans import read_candidate_plan
from test_candidate_plan_persistence import plan_fixture, authored, candidate_request, _replace_record
from test_compiler_json_contract import _registration, _module, DATA_SCHEMA, EXECUTOR, EFFECT, TERMINAL


@pytest.fixture
def no_retrieval(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("offline contract attempted external retrieval")
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    return attempts


@pytest.mark.parametrize("surface", ["component", "terminal", "executor", "effect", "initial_token"])
def test_offline_wire_entry_closes_every_instance_validation_path(no_retrieval, surface):
    original = compile_module(_module(True), _registration()).to_dict()
    wire = deepcopy(original)
    remote = "application/offline_probe_" + surface + "/v1"
    wire["source"]["required_schemas"].append(remote)
    wire["symbolic"]["required_schemas"].append(remote)
    wire["registrations"]["schema"][remote] = {"kind": "schema", "key": remote,
        "schema": {"$id": remote, "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "https://invalid.example.invalid/never-retrieve"}}
    if surface == "component":
        wire["source"]["components"][0]["config_schema"] = remote
        wire["registrations"]["component"]["operation"]["contracts"]["config_schema"] = remote
    elif surface == "initial_token":
        place = wire["fragments"]["step"]["places"][0]
        place["schema_variants"].append(remote)
        place["initial_tokens"] = [{"count": 1, "schema": remote, "colour": None, "value": "initial"}]
    else:
        kind, key = ("executor", EXECUTOR) if surface == "executor" else ("tool", TERMINAL if surface == "terminal" else EFFECT)
        wire["registrations"][kind][key]["contracts"]["config_schema"] = remote
    with pytest.raises(Unresolvable):
        _load_compiled_net_offline(wire)
    assert no_retrieval == []
    assert canonical_json(load_compiled_net(original).to_dict()) == canonical_json(_load_compiled_net_offline(original).to_dict())


def test_raw_foreign_wire_schema_is_rejected_before_wire_load(plan_fixture, no_retrieval, monkeypatch):
    from cpn.rpnh.collaboration import candidate_plans
    f = plan_fixture
    first = f[-1].publish(**candidate_request(f, authored(f)))
    def changed(body):
        body["compiled"]["registrations"]["schema"][CONFIG_SCHEMA_ID]["schema"] = {
            "$id": CONFIG_SCHEMA_ID, "$schema": "http://json-schema.org/draft-07/schema#",
            "$ref": "https://invalid.example.invalid/never-retrieve"}
    _replace_record(f[0], first.plan_ref, changed)
    def forbidden(*args, **kwargs):
        raise AssertionError("foreign raw data reached the wire loader")
    monkeypatch.setattr(candidate_plans, "_load_compiled_net_offline", forbidden)
    with pytest.raises(RegistryConflict, match="raw wire"):
        read_candidate_plan(f[0], first.plan_ref)
    assert no_retrieval == []


def _replace_resource_payload(core, ref, payload):
    row = core.event_store.object_row(ref.resource_version_id)
    metadata = json.loads(row["metadata_json"])
    metadata["size"] = len(payload)
    metadata["reference_provenance"]["publication"]["size"] = len(payload)
    core.object_store.path_for_version(ref.resource_version_id).write_bytes(payload)
    with core.event_store.connect() as db:
        event = json.loads(db.execute("SELECT payload_json FROM events WHERE event_id=?", (row["published_event_id"],)).fetchone()[0])
        event.update(metadata=metadata, size=len(payload))
        db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (json.dumps(metadata), len(payload), str(ref.resource_version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(event), row["published_event_id"]))


def test_matching_canonical_author_bad_schema_is_preflighted_before_load_or_host_compile(plan_fixture, no_retrieval, monkeypatch):
    from cpn.rpnh.collaboration import candidate_plans
    from cpn.rpnh.collaboration.materials import _command_material, _validate_source_at, AuthorMaterialReadContext
    from cpn.rpnh.registry._candidate_plan_reads import PlanReadClosure
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    from test_candidate_plan_resources import mutate_resource_metadata
    f = plan_fixture
    revision = authored(f)
    request = candidate_request(f, revision)
    first = f[-1].publish(**request)
    remote_schema = {"$id": CONFIG_SCHEMA_ID, "$schema": "http://json-schema.org/draft-07/schema#",
        "$ref": "https://invalid.example.invalid/never-retrieve"}
    host = deepcopy(revision.host_requirements)
    host["registrations"]["schema"][CONFIG_SCHEMA_ID]["schema"] = remote_schema
    _replace_resource_payload(f[0], f[2].schema_refs[CONFIG_SCHEMA_ID], canonical_json(remote_schema))
    _replace_resource_payload(f[0], revision.revision.host_requirements_ref.ref, canonical_json(host))
    command = _command_material(source_id="source-a", owner=revision.revision.owner_task_ref,
        producer=revision.revision.producer_principal_ref, command_id=revision.revision.command_id,
        parents=revision.revision.parent_revision_refs,
        documents=(revision.module.to_dict(), revision.element_map, revision.boundary_map, host))
    mutate_resource_metadata(f[0], revision.revision.definition_ref.ref,
        lambda body: body["descriptors"].__setitem__("closed_author_command_v1", command))
    _replace_record(f[0], first.plan_ref, lambda body: (
        body.__setitem__("host_requirements", host),
        body["compiled"]["registrations"].__setitem__("schema", host["registrations"]["schema"])))
    # The specimen passes the entire pure author source/HREQ/command relation;
    # rejection must come from actual schema-byte preflight, not a mismatch.
    with f[0].event_store.connect() as db:
        db.execute("BEGIN")
        closure = PlanReadClosure(db, f[0])
        pure = _validate_source_at(db, AuthorMaterialReadContext(closure.store, f[0].catalog, f[0].task_id, f[0].branch_id),
            revision.revision.revision_ref, closure.binding, set())
        assert pure.host_requirements == host
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe schemas reached wire loading or actual HOST compilation")
    monkeypatch.setattr(candidate_plans, "_load_compiled_net_offline", forbidden)
    monkeypatch.setattr(candidate_plans, "validate_closed_revision", forbidden)
    with pytest.raises(ContentSchemaAuthorityError, match="local JSON pointers"):
        read_candidate_plan(f[0], first.plan_ref)
    with pytest.raises(ContentSchemaAuthorityError, match="local JSON pointers"):
        f[-1].publish(**request)
    assert no_retrieval == []
