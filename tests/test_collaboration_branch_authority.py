"""Branch-local source/bootstrap descriptor bytes, retaining static owner trust.

F01 reproduced an inherited metadata-only gap. These fixtures alter only
throwaway Registry authority storage; no runtime or firing execution is started.
"""
from copy import deepcopy
import json

import pytest

from cpn.rpnh.collaboration import current_branch, read_branch_version
from cpn.rpnh.registry._event_store import branch_publication
from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
from cpn.rpnh.registry._event_store.source_identity import SOURCE_BINDING_TYPE
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict, RegistryCorruptError
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_collaboration_graph_branches import (
    fixture, descriptor, counts, successor, stage, _direct_batch, _temporary_member,
)


def prepared_branch(fixture, legacy):
    core, gateway, _ = fixture
    root = descriptor(fixture, legacy=legacy)
    create = gateway.create_author_branch if legacy else gateway.create_graph_author_branch
    first = create(head_revision_ref=root.revision_ref, command_id="authority:first")
    target = descriptor(fixture, root, legacy=legacy)
    value = successor(core, first, target, "authority:next")
    tx, obj = stage(core, value)
    return first, target, value, tx, obj


def submit(fixture, prepared, route):
    core, gateway, _ = fixture
    first, target, value, tx, obj = prepared
    if route == "gateway":
        advance = (gateway.advance_author_branch if first.branch_ref.ref.entity_type == branch_publication.BRANCH_TYPE
                   else gateway.advance_graph_author_branch)
        return advance(expected_branch_version_ref=first.branch_ref,
            expected_head_revision_ref=first.head_revision_ref, expected_stream_head=first.sequence,
            next_revision_ref=target.revision_ref, command_id=value.command_id)
    if route == "transaction":
        return tx.commit()
    if route == "direct_batch":
        return _direct_batch(core, tx, obj)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    if route == "current":
        return current_branch(reader, first.branch_ref.ref.entity_id)
    assert route == "history"
    return read_branch_version(reader, first.branch_ref)


def authority_ref(fixture, authority):
    core, gateway, _ = fixture
    if authority == "bootstrap":
        return {"entity_type": gateway._bootstrap_ref.entity_type,
                "logical_id": str(gateway._bootstrap_ref.entity_id),
                "version_id": str(gateway._bootstrap_ref.version_id)}
    rows = core.event_store.object_rows_by_type(SOURCE_BINDING_TYPE)
    assert len(rows) == 1
    return {"entity_type": SOURCE_BINDING_TYPE, "logical_id": rows[0]["logical_id"], "version_id": rows[0]["version_id"]}


@pytest.mark.parametrize("legacy", [False, True], ids=["v2", "v1"])
@pytest.mark.parametrize("authority", ["bootstrap", "source_binding"])
@pytest.mark.parametrize("route", ["gateway", "transaction", "direct_batch", "current", "history"])
def test_missing_static_authority_bytes_fail_closed_through_all_branch_routes(fixture, legacy, authority, route):
    core, _, _ = fixture
    prepared = prepared_branch(fixture, legacy)
    ref = authority_ref(fixture, authority)
    core.object_store.path_for_version(TypedId.parse(ref["version_id"])).unlink()
    before = counts(core)
    with pytest.raises((RegistryConflict, RegistryCorruptError, ObjectIntegrityError)):
        submit(fixture, prepared, route)
    assert counts(core) == before


@pytest.mark.parametrize("legacy", [False, True], ids=["v2", "v1"])
@pytest.mark.parametrize("authority", ["bootstrap", "source_binding"])
@pytest.mark.parametrize("damage", ["payload", "media", "schema", "locator", "size", "publication"])
def test_adjacent_static_authority_descriptor_damage_is_rejected(fixture, legacy, authority, damage):
    core, _, _ = fixture
    prepared = prepared_branch(fixture, legacy)
    ref = authority_ref(fixture, authority)
    with core.event_store.connect() as db:
        obj = db.execute("SELECT * FROM objects WHERE version_id=?", (ref["version_id"],)).fetchone()
        event = db.execute("SELECT * FROM events WHERE event_id=?", (obj["published_event_id"],)).fetchone()
        publication = json.loads(event["payload_json"])
        if damage == "payload":
            path = core.object_store.path_for_version(TypedId.parse(ref["version_id"]))
            original = path.read_bytes(); document = json.loads(original)
            if authority == "bootstrap":
                value = document["bootstrap_command_id"]
                document["bootstrap_command_id"] = value[:-1] + ("0" if value[-1] != "0" else "1")
            else:
                document["source_id"] = "source-forge"
            altered = canonical_json(document)
            assert len(altered) == len(original) and altered != original
            path.write_bytes(altered)
        elif damage == "publication":
            # Bootstrap's old static projection did not compare this envelope;
            # the exact readable-descriptor helper does so at the same cut.
            db.execute("UPDATE events SET aggregate_type='incorrect/v1' WHERE event_id=?", (event["event_id"],))
        else:
            field, value = {
                "media": ("media_type", "text/plain"),
                "schema": ("schema_ref", "registry_v1/principal/v1"),
                "locator": ("storage_locator", "registry-object:" + str(new_id("resource_version"))),
                "size": ("size", obj["size"] + 1),
            }[damage]
            # Preserve publication-vs-row equality so this is not merely a
            # mismatch in the static projection's envelope comparison.
            publication[field] = value
            db.execute(f"UPDATE objects SET {field}=? WHERE version_id=?", (value, ref["version_id"]))
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(publication), event["event_id"]))
    before = counts(core)
    for route in ("direct_batch", "history"):
        with pytest.raises((RegistryConflict, RegistryCorruptError, ObjectIntegrityError)):
            submit(fixture, prepared, route)
        assert counts(core) == before


@pytest.mark.parametrize("legacy", [False, True], ids=["v2", "v1"])
@pytest.mark.parametrize("authority", ["bootstrap", "source_binding"])
@pytest.mark.parametrize("member", ["object", "commit"])
def test_readable_published_member_does_not_relax_static_source_authority(fixture, legacy, authority, member):
    core, _, _ = fixture
    prepared = prepared_branch(fixture, legacy)
    ref = authority_ref(fixture, authority)
    promote = core.begin(idempotency_key="authority:promotion")
    promote.commit()
    identity = ref["version_id"]
    if member == "commit":
        with core.event_store.connect() as db:
            identity = db.execute("SELECT event_id FROM events WHERE event_type='transaction_committed/v1' "
                "AND transaction_id=(SELECT transaction_id FROM objects WHERE version_id=?)", (ref["version_id"],)).fetchone()[0]
    root = _temporary_member(core, "object" if member == "object" else "event", identity)
    with core.event_store.connect() as db:
        db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
            "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
            (str(promote.transaction_id), str(new_id("operation_result_version")),
             str(new_id("marking_checkpoint_version")), root))
    before = counts(core)
    # This wider material/descriptor helper alone would accept a canonically
    # promoted member. The retained static owner predicate must still reject it.
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        assert exact_descriptor(db, core.object_store, core.task_id, ref)
    for route in ("direct_batch", "current", "history"):
        with pytest.raises((RegistryConflict, RegistryCorruptError)):
            submit(fixture, prepared, route)
        assert counts(core) == before


@pytest.mark.parametrize("legacy", [False, True], ids=["v2", "v1"])
def test_authority_reading_keeps_the_original_commit_cut_and_readonly_history(fixture, legacy, monkeypatch):
    core, _, _ = fixture
    prepared = prepared_branch(fixture, legacy)
    expected = None
    seen = []
    original_validate = branch_publication.validate_branch_publication
    original_read = branch_publication.exact_descriptor
    def exact(db, store, task_id, ref):
        if expected is not None:
            assert db is expected and db.in_transaction
            seen.append(ref["entity_type"])
        return original_read(db, store, task_id, ref)
    def validate(context):
        nonlocal expected
        assert context.db.in_transaction
        expected = context.db
        try:
            return original_validate(context)
        finally:
            expected = None
    with monkeypatch.context() as patch:
        patch.setattr(branch_publication, "exact_descriptor", exact)
        patch.setattr(branch_publication, "validate_branch_publication", validate)
        submit(fixture, prepared, "direct_batch")
    assert SOURCE_BINDING_TYPE in seen and "bootstrap_command/v1" in seen
    before = counts(core)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert current_branch(reader, prepared[0].branch_ref.ref.entity_id) == prepared[2]
    assert read_branch_version(reader, prepared[0].branch_ref) == prepared[0]
    assert counts(reader) == before
