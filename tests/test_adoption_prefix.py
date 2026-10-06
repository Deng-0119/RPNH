"""Canonical exact-event adoption prefixes; no slot preparation/runtime claim."""
import socket
import subprocess
import urllib.request

import pytest

from cpn.rpnh.registry.event_store import verified_adoption_head
from cpn.rpnh.registry.module_nets import publish_module_net
from test_module_graph_projection import graph_arguments


@pytest.fixture
def adopted_fixture(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("adoption-prefix fixture cannot open sockets/processes")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "Request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    core, compiled, registration, arguments = graph_arguments(tmp_path, "basic")
    publication = publish_module_net(core, compiled, registration, **arguments)
    events = core.task_control.adopt_net(net_instance_ref=publication.net_ref, idempotency_key="fixture:initial-adoption")
    anchor = next(event for event in events if event.event_type == "net_adopted/v1")
    assert verified_adoption_head(core.event_store, core.catalog, core.task_id) == publication.net_ref
    assert not core.event_store.object_rows_by_type("marking_checkpoint/v1")
    return core, publication, anchor


def test_real_initial_adoption_prefix_is_exact_and_read_only(adopted_fixture):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    core, publication, anchor = adopted_fixture
    before = len(core.event_store.list_events())
    result = verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)
    assert result == (publication.net_ref,)
    assert len(core.event_store.list_events()) == before


def test_prefix_uses_supplied_read_cut_without_another_connection(adopted_fixture, monkeypatch):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    core, publication, anchor = adopted_fixture
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        def forbidden(*args, **kwargs):
            raise AssertionError("prefix opened a second connection")
        monkeypatch.setattr(core.event_store, "connect", forbidden)
        assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
            adoption_event_id=anchor.event_id, _db=db) == (publication.net_ref,)


@pytest.mark.parametrize("damage", ["missing", "not_adoption", "wrong_task", "nonintegral", "zero_sequence", "provisional"])
def test_prefix_rejects_missing_foreign_or_noncanonical_anchor(adopted_fixture, damage):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    from cpn.rpnh.registry.identities import new_id
    from test_collaboration_authoring import _inject_temporary_member
    core, _, anchor = adopted_fixture
    event_id, task_id = anchor.event_id, core.task_id
    if damage == "missing":
        event_id = new_id("event")
    elif damage == "not_adoption":
        event_id = next(event.event_id for event in core.event_store.list_events() if event.event_type == "transaction_committed/v1")
    elif damage == "wrong_task":
        task_id = new_id("task")
    elif damage in {"nonintegral", "zero_sequence"}:
        with core.event_store.connect() as db:
            db.execute("UPDATE events SET task_control_sequence=? WHERE event_id=?", (1.5 if damage == "nonintegral" else 0, str(event_id)))
    else:
        _inject_temporary_member(core, "event", str(event_id))
    with pytest.raises(RegistryCorruptError):
        verified_adoption_prefix(core.event_store, core.catalog, task_id, adoption_event_id=event_id)


@pytest.mark.parametrize("target", ["net", "node", "spec", "declaration"])
def test_prefix_rechecks_canonical_object_publication_even_with_warm_global_memos(adopted_fixture, target):
    from copy import deepcopy
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryConflict
    from test_collaboration_authoring import _inject_temporary_member
    core, publication, anchor = adopted_fixture
    expected = (publication.net_ref,)
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id) == expected
    before_net, before_adopt = deepcopy(core.event_store._net_closure_memo), deepcopy(core.event_store._adoption_lineage_memo)
    ref = {"net": publication.net_ref, "node": next(iter(publication.node_refs.values())),
        "spec": next(iter(publication.operation_refs.values())), "declaration": publication.declaration_resource_ref.as_version_ref()}[target]
    _inject_temporary_member(core, "object", str(ref.version_id))
    with pytest.raises(RegistryConflict, match="canonical"):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)
    assert core.event_store._net_closure_memo == before_net
    assert core.event_store._adoption_lineage_memo == before_adopt


@pytest.mark.parametrize("damage", ["outbox", "terminal", "stream_head", "task_head"])
def test_prefix_keeps_global_structural_integrity_checks(adopted_fixture, damage):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    core, _, anchor = adopted_fixture
    with core.event_store.connect() as db:
        if damage == "outbox":
            db.execute("UPDATE outbox SET event_ids_json='[]' WHERE transaction_id=?", (str(anchor.transaction_id),))
        elif damage == "terminal":
            db.execute("DELETE FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (str(anchor.transaction_id),))
        elif damage == "stream_head":
            db.execute("UPDATE stream_heads SET sequence=sequence+1 WHERE stream_id=?", (anchor.stream_id,))
        else:
            db.execute("UPDATE task_control_heads SET sequence=sequence+1 WHERE task_id=?", (str(core.task_id),))
    with pytest.raises(RegistryCorruptError):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)


def test_prefix_rejects_autocommit_and_foreign_registry_connections(adopted_fixture, tmp_path):
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    core, _, anchor = adopted_fixture
    with core.event_store.connect() as db:
        with pytest.raises(TypeError, match="existing SQLite read cut"):
            verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id, _db=db)
    other = _RegistryCore(tmp_path / "foreign", create=True, catalog=core.catalog)
    with other.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(RegistryCorruptError, match="another Registry"):
            verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id, _db=db)


def test_prefix_never_coerces_application_identity_objects(adopted_fixture):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    core, _, _ = adopted_fixture
    class Opaque:
        kind = "event"
        def __str__(self):
            raise AssertionError("must not coerce application identity")
    with pytest.raises(TypeError, match="TypedIds"):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=Opaque())


def test_later_ordinary_commit_does_not_change_frozen_prefix(adopted_fixture):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    core, publication, anchor = adopted_fixture
    before = verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)
    core.begin(idempotency_key="fixture:unrelated-later-commit").commit()
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id) == before == (publication.net_ref,)


@pytest.fixture
def owner_replacement_fixture(tmp_path, monkeypatch):
    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.run import OwnerInput, start_run
    from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from test_native_net_operations import TEXT, _registration, _simple_module
    def forbidden(*args, **kwargs):
        raise AssertionError("real owner replacement fixture cannot use sockets/processes")
    for target, field in ((socket, "socket"), (subprocess, "Popen"), (urllib.request, "Request"), (urllib.request, "urlopen")):
        monkeypatch.setattr(target, field, forbidden)
    module, registration = _simple_module(), _registration()
    task = OwnerInput(TEXT, canonical_json("task"), "Task")
    owner = start_run(module, registration, run_dir=tmp_path / "owner-replacement", task_input=task,
        entry_inputs={"request": task}, budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]), (TEXT,), 3, 0, 3, 0),
        model_condition="offline-prefix", owner_statement="Offline owner replacement fixture", command_id="fixture:prefix:fresh")
    first = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[0]
    result = apply_replacement(owner, prepare_replacement(owner, _simple_module("Replacement")), command_id="fixture:prefix:replace")
    assert result["status"] == "ADOPTED"
    last = owner._core.event_store.list_events_by_type(("net_adopted/v1",))[-1]
    assert "owner_command_ref" in last.payload
    return owner._core, first, last


def test_real_owner_adoption_advances_head_but_not_an_old_event_prefix(owner_replacement_fixture):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    from cpn.rpnh.registry.publication import _version_from_payload
    core, first, last = owner_replacement_fixture
    old, current = _version_from_payload(first.payload["net_instance_ref"]), _version_from_payload(last.payload["net_instance_ref"])
    assert verified_adoption_head(core.event_store, core.catalog, core.task_id) == current
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=first.event_id) == (old,)
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=last.event_id) == (old, current)
    assert not core.event_store.object_rows_by_type("transition_firing/v1")
    assert not core.event_store.object_rows_by_type("llm_invocation_attempt/v1")


@pytest.mark.parametrize("damage", ["result_payload", "candidate_declaration_schema", "checkpoint_event", "principal_publication"])
def test_owner_prefix_checks_actual_witness_bytes_events_and_canonical_objects(owner_replacement_fixture, damage):
    import json
    from cpn.components.basic import CONFIG_SCHEMA_ID
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryConflict, RegistryCorruptError
    from cpn.rpnh.registry.content_schemas import ContentSchemaAuthorityError
    from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from test_candidate_plan_offline import _replace_resource_payload
    from test_collaboration_authoring import _inject_temporary_member
    core, first, last = owner_replacement_fixture
    if damage == "result_payload":
        resource = _resource_from_payload({"resource_id": last.payload["owner_command_result_ref"]["logical_id"],
            "resource_version_id": last.payload["owner_command_result_ref"]["version_id"]})
        value = json.loads(core.object_store.read_registered(core.get_version(resource.resource_version_id)))
        value["data"]["status"] = "REJECTED"
        _replace_resource_payload(core, resource, canonical_json(value))
    elif damage == "candidate_declaration_schema":
        resource = _resource_from_payload({"resource_id": last.payload["candidate_ref"]["logical_id"],
            "resource_version_id": last.payload["candidate_ref"]["version_id"]})
        value = json.loads(core.object_store.read_registered(core.get_version(resource.resource_version_id)))
        value["registrations"]["schema"][CONFIG_SCHEMA_ID]["schema"]["$ref"] = "https://invalid.example.invalid/no-retrieval"
        _replace_resource_payload(core, resource, canonical_json(value))
    elif damage == "checkpoint_event":
        with core.event_store.connect() as db:
            db.execute("UPDATE events SET payload_schema_ref='wrong/v1' WHERE transaction_id=? AND event_type='marking_checkpoint_committed/v1'", (str(last.transaction_id),))
    else:
        _inject_temporary_member(core, "object", last.payload["owner_principal_ref"]["version_id"])
    with pytest.raises((RegistryConflict, RegistryCorruptError, ContentSchemaAuthorityError)):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=last.event_id)


@pytest.mark.parametrize("target", ["adoption", "terminal", "publication"])
@pytest.mark.parametrize("field,value", [("criticality", "diagnostic"), ("payload_schema_ref", "wrong/v1"), ("payload_json", "{}")])
def test_consumed_facts_and_initial_commit_terminal_require_declared_contract(adopted_fixture, target, field, value):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError, RegistryConflict
    core, publication, anchor = adopted_fixture
    with core.event_store.connect() as db:
        if target == "adoption":
            event_id = str(anchor.event_id)
        elif target == "terminal":
            event_id = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                (str(anchor.transaction_id),)).fetchone()[0]
        else:
            event_id = db.execute("SELECT published_event_id FROM objects WHERE version_id=?", (str(publication.net_ref.version_id),)).fetchone()[0]
        db.execute(f"UPDATE events SET {field}=? WHERE event_id=?", (value, event_id))
    with pytest.raises((RegistryCorruptError, RegistryConflict)):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)


@pytest.mark.parametrize("field,value", [("criticality", "diagnostic"), ("payload_schema_ref", "wrong/v1"), ("payload_json", "{}")])
def test_owner_checkpoint_witness_uses_the_same_fact_contract(owner_replacement_fixture, field, value):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError, RegistryConflict
    core, first, last = owner_replacement_fixture
    with core.event_store.connect() as db:
        db.execute(f"UPDATE events SET {field}=? WHERE transaction_id=? AND event_type='marking_checkpoint_committed/v1'",
            (value, str(last.transaction_id)))
    with pytest.raises((RegistryCorruptError, RegistryConflict)):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=last.event_id)


def test_metadata_and_publication_cannot_jointly_claim_another_schema(adopted_fixture):
    import json
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    from cpn.rpnh.registry.schema_catalog import canonical_json
    core, publication, anchor = adopted_fixture
    with core.event_store.connect() as db:
        row = db.execute("SELECT e.* FROM objects o JOIN events e ON e.event_id=o.published_event_id WHERE o.version_id=?",
            (str(publication.net_ref.version_id),)).fetchone()
        payload = json.loads(row["payload_json"])
        payload["schema_ref"] = "registry_v1/principal/v1"
        db.execute("UPDATE objects SET schema_ref=? WHERE version_id=?", (payload["schema_ref"], str(publication.net_ref.version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(payload).decode(), row["event_id"]))
    with pytest.raises(RegistryCorruptError, match="declared schema"):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)


def test_old_prefix_excludes_later_owner_semantics_but_not_structural_corruption(owner_replacement_fixture):
    import json
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    from cpn.rpnh.registry.publication import _resource_from_payload, _version_from_payload
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from test_candidate_plan_offline import _replace_resource_payload
    core, first, last = owner_replacement_fixture
    ref = last.payload["owner_command_result_ref"]
    resource = _resource_from_payload({"resource_id": ref["logical_id"], "resource_version_id": ref["version_id"]})
    value = json.loads(core.object_store.read_registered(core.get_version(resource.resource_version_id)))
    value["data"]["status"] = "REJECTED"
    _replace_resource_payload(core, resource, canonical_json(value))
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=first.event_id) == (
        _version_from_payload(first.payload["net_instance_ref"]),)
    with pytest.raises(RegistryCorruptError):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=last.event_id)
    with core.event_store.connect() as db:
        db.execute("UPDATE task_control_heads SET sequence=sequence+1 WHERE task_id=?", (str(core.task_id),))
    with pytest.raises(RegistryCorruptError):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=first.event_id)


@pytest.mark.parametrize("target", ["object", "anchor", "terminal"])
@pytest.mark.parametrize("state", ["published", "provisional", "missing_terminal", "bad_terminal_schema", "bad_terminal_payload"])
def test_prefix_published_promotion_and_terminal_closure(adopted_fixture, target, state):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError, RegistryConflict
    from test_collaboration_authoring import _inject_temporary_member
    from cpn.rpnh.registry.identities import new_id
    core, publication, anchor = adopted_fixture
    if target == "object":
        kind, identity = "object", str(publication.net_ref.version_id)
    elif target == "anchor":
        kind, identity = "event", str(anchor.event_id)
    else:
        kind = "event"
        with core.event_store.connect() as db:
            identity = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                (str(anchor.transaction_id),)).fetchone()[0]
    # Read-side canonical-membership fixture, not a simulated firing execution.
    root = _inject_temporary_member(core, kind, identity)
    if state != "provisional":
        terminal = core.begin(idempotency_key="fixture:prefix:promotion").commit()[-1]
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(terminal.transaction_id), str(new_id("operation_result_version")),
                 str(new_id("marking_checkpoint_version")), root))
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
                (root, "event", str(terminal.event_id), str(terminal.transaction_id)))
            if state == "missing_terminal":
                db.execute("DELETE FROM events WHERE event_id=?", (str(terminal.event_id),))
            elif state == "bad_terminal_schema":
                db.execute("UPDATE events SET payload_schema_ref='wrong/v1' WHERE event_id=?", (str(terminal.event_id),))
            elif state == "bad_terminal_payload":
                db.execute("UPDATE events SET payload_json='{}' WHERE event_id=?", (str(terminal.event_id),))
    if state == "published":
        assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
            adoption_event_id=anchor.event_id) == (publication.net_ref,)
    else:
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)


@pytest.mark.parametrize("axis", ["logical_id", "entity_type"])
def test_prefix_local_net_cache_is_exact_and_cannot_redirect_logical_identity(adopted_fixture, axis):
    from cpn.rpnh.registry._event_store.adoption_reads import AdoptionPrefixReads
    from cpn.rpnh.registry._event_store.net_lineage import validate_registered_net_closure
    from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    core, publication, _ = adopted_fixture
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        reader, memo = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id), {}
        validate_registered_net_closure(core.event_store, core.catalog, publication.net_ref,
            _db=db, _memo=memo, _prefix_reads=reader)
        fake = VersionRef("net_instance/v1" if axis == "logical_id" else "principal/v1",
            new_id("net_instance") if axis == "logical_id" else publication.net_ref.entity_id,
            publication.net_ref.version_id)
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            validate_registered_net_closure(core.event_store, core.catalog, fake,
                _db=db, _memo=memo, _prefix_reads=reader)


def test_prefix_foreign_context_is_not_accepted_as_a_read_policy(adopted_fixture):
    from cpn.rpnh.registry._event_store.net_lineage import validate_registered_net_closure
    core, publication, _ = adopted_fixture
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        with pytest.raises(TypeError, match="fixed same-cut"):
            validate_registered_net_closure(core.event_store, core.catalog, publication.net_ref,
                _db=db, _memo={}, _prefix_reads=object())



def test_real_owner_prefix_remains_on_supplied_cut_without_host_resolution(owner_replacement_fixture, monkeypatch):
    from cpn.rpnh.registration import Registration
    from cpn.rpnh.registry.event_store import verified_adoption_prefix
    core, first, last = owner_replacement_fixture
    def forbidden(*args, **kwargs):
        raise AssertionError("pure owner prefix cannot open another cut or resolve HOST code")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        monkeypatch.setattr(core.event_store, "connect", forbidden)
        monkeypatch.setattr(Registration, "resolve", forbidden)
        assert len(verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
            adoption_event_id=last.event_id, _db=db)) == 2


@pytest.mark.parametrize("target", ["adoption", "publication", "promotion"])
@pytest.mark.parametrize("field", ["object_count", "relation_count", "fact_count"])
def test_prefix_terminal_counts_match_actual_committed_batch(adopted_fixture, target, field):
    import json
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from test_collaboration_authoring import _inject_temporary_member
    core, publication, anchor = adopted_fixture
    if target == "promotion":
        root = _inject_temporary_member(core, "object", str(publication.net_ref.version_id))
        terminal = core.begin(idempotency_key="fixture:prefix:counted-promotion").commit()[-1]
        transaction = str(terminal.transaction_id)
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (transaction, str(new_id("operation_result_version")), str(new_id("marking_checkpoint_version")), root))
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)", (root, "event", str(terminal.event_id), transaction))
    else:
        transaction = str(anchor.transaction_id if target == "adoption" else publication.transaction_id)
    # Each real batch is readable before the single schema-valid corruption.
    assert verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
        adoption_event_id=anchor.event_id) == (publication.net_ref,)
    with core.event_store.connect() as db:
        terminal = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (transaction,)).fetchone()
        value = json.loads(terminal["payload_json"])
        value[field] += 17
        core.catalog.validate_event_payload("transaction_committed/v1", value)
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (canonical_json(value).decode(), terminal["event_id"]))
    with pytest.raises(RegistryCorruptError, match="counts differ"):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=anchor.event_id)


@pytest.mark.parametrize("axis", ["later_adoption_sequence", "task_head", "stream_head", "transaction_epoch",
    "outbox_epoch", "later_stream_sequence", "later_aggregate_version"])
def test_old_prefix_rejects_nonintegral_current_structural_authority(owner_replacement_fixture, axis):
    from cpn.rpnh.registry.event_store import verified_adoption_prefix, RegistryCorruptError
    core, first, last = owner_replacement_fixture
    cases = {
        "later_adoption_sequence": ("events", "task_control_sequence", "event_id", str(last.event_id)),
        "task_head": ("task_control_heads", "sequence", "task_id", str(core.task_id)),
        "stream_head": ("stream_heads", "sequence", "stream_id", first.stream_id),
        "transaction_epoch": ("transactions", "writer_epoch", "transaction_id", str(first.transaction_id)),
        "outbox_epoch": ("outbox", "writer_epoch", "transaction_id", str(first.transaction_id)),
        "later_stream_sequence": ("events", "stream_sequence", "event_id", str(last.event_id)),
        "later_aggregate_version": ("events", "aggregate_version", "event_id", str(last.event_id)),
    }
    table, field, key, identity = cases[axis]
    assert len(verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=first.event_id)) == 1
    with core.event_store.connect() as db:
        original = db.execute(f"SELECT {field} FROM {table} WHERE {key}=?", (identity,)).fetchone()[0]
        db.execute(f"UPDATE {table} SET {field}=? WHERE {key}=?", (original + 0.5, identity))
    with pytest.raises(RegistryCorruptError):
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id, adoption_event_id=first.event_id)
