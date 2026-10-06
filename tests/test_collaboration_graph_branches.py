"""Graph-v2 Branch descriptor authority and real author-to-reader integration.

No runtime is started. Real material proof is invoked explicitly after selecting
an immutable exact Branch head; Branch commit never compiles or runs HOST code.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from threading import Barrier

import pytest

from cpn.rpnh.collaboration import (
    BranchVersion, GraphBranchVersion, GraphModuleAuthor, GraphNetRevision, NetRevision,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef, ValidatedGraphRevision,
    branch_schema_data, graph_author_schema_data, graph_branch_schema_data,
    current_branch, read_branch_version, validate_closed_revision, make_graph_source,
)
from cpn.rpnh.registry._event_store import branch_publication
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, SchemaGovernanceError, canonical_json
from test_collaboration_graph_materials import registration, forbidden_execution
from test_collaboration_graph_source import graph_wire, recipe, source_ids


@pytest.fixture
def fixture(tmp_path):
    schemas, types, paths = graph_branch_schema_data()
    core = _RegistryCore(tmp_path / "graph-branches", create=True,
        catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(("graph-branch-test/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="source-graph", command_id="bind:graph")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    body = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id),
            "display_name": "Graph Branch fixture"}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json",
        schema_ref="registry_v1/principal/v1", idempotency_key="fixture:principal")
    return core, gateway, SourceQualifiedVersionRef("source-graph", principal)


def counts(core):
    return (len(core.event_store.object_rows()), len(core.event_store.list_events()),
            len(core.event_store.outbox_rows()), core.event_store.stream_heads())


def advance(gateway, branch, target, command):
    return gateway.advance_graph_author_branch(expected_branch_version_ref=branch.branch_ref,
        expected_head_revision_ref=branch.head_revision_ref, expected_stream_head=branch.sequence,
        next_revision_ref=target.revision_ref, command_id=command)


def persist(core, record, *, payload=None, media_type="application/json"):
    ref, body = record.revision_ref.ref, record.to_dict()
    core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body if payload is None else payload), metadata=body, media_type=media_type,
        schema_ref="registry_v1/" + ref.entity_type, idempotency_key=record.command_id)
    return record


def descriptor(fixture, parent=None, *, legacy=False, publish=True):
    """Canonical descriptors with deliberately absent materials, for protocol tests."""
    core, gateway, principal = fixture
    source = principal.source_id
    kind = "collaboration_net_revision/v1" if legacy else "collaboration_net_revision/v2"
    ref = SourceQualifiedVersionRef(source, VersionRef(kind,
        parent.revision_ref.ref.entity_id if parent else new_id("resource"), new_id("resource_version")))
    resource = lambda: SourceQualifiedResourceRef(source, ResourceVersionRef(new_id("resource"), new_id("resource_version")))
    owner = SourceQualifiedVersionRef(source, gateway._task_ref)
    parents = () if parent is None else (parent.revision_ref,)
    if legacy:
        value = NetRevision(ref, owner, principal, f"revision:{ref.ref.version_id}", "closed_module", resource(),
            parents, (), resource(), resource(), resource(), None)
    else:
        value = GraphNetRevision(ref, owner, principal, f"revision:{ref.ref.version_id}", parents,
            *(resource() for _ in range(7)))
    return persist(core, value) if publish else value


def test_real_graph_author_branch_current_and_history_reconstruct_full_materials(fixture, monkeypatch):
    core, gateway, principal = fixture
    selected = registration()
    author = GraphModuleAuthor(gateway, selected, principal)
    source = make_graph_source(graph_wire(True))
    ids = source_ids(source)
    revisions = []
    for index in range(3):
        changed = deepcopy(source)
        changed["graph"]["nodes"][0]["instruction"] = f"Author revision {index}: inspect carefully."
        revisions.append(author.publish(source=changed, recipe=recipe(max_attempts_per_node=3 + index),
            source_ids=ids, command_id=f"graph:r{index}",
            parent_ref=None if not revisions else revisions[-1].revision.revision_ref))
    material_count = len(core.event_store.object_rows_by_type("resource_version/v1"))
    # No Branch code may quietly acquire material-proof or HOST authority.
    with monkeypatch.context() as patch:
        from cpn.rpnh.collaboration import graph_authoring, materials
        patch.setattr(graph_authoring, "compile_module", forbidden_execution)
        patch.setattr(materials, "compile_module", forbidden_execution)
        patch.setattr(materials, "validate_closed_revision", forbidden_execution)
        branches = [gateway.create_graph_author_branch(head_revision_ref=revisions[0].revision.revision_ref,
            command_id="branch:create")]
        for index, revision in enumerate(revisions[1:], 1):
            branches.append(advance(gateway, branches[-1], revision.revision, f"branch:a{index}"))
        fork = gateway.create_graph_author_branch(head_revision_ref=branches[1].head_revision_ref,
            command_id="branch:real-descendant-fork", upstream_branch_ref=branches[1].branch_ref)
    assert len(core.event_store.object_rows_by_type("resource_version/v1")) == material_count
    assert all(type(branch) is GraphBranchVersion for branch in branches)
    assert [branch.sequence for branch in branches] == [1, 2, 3]
    assert len({branch.branch_ref.ref.entity_id for branch in branches}) == 1
    assert all(branch.fork_base_revision_ref == revisions[0].revision.revision_ref for branch in branches)
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    before = counts(reader)
    observed = [read_branch_version(reader, branch.branch_ref) for branch in branches[:-1]]
    observed.append(current_branch(reader, branches[0].branch_ref.ref.entity_id))
    for expected, branch in zip(revisions, observed, strict=True):
        # The tested input comes from current/history, not the author result.
        result = validate_closed_revision(reader, branch.head_revision_ref, registration())
        assert isinstance(result, ValidatedGraphRevision)
        assert result.source == expected.source
        assert result.recipe == expected.recipe
        assert result.source_map == expected.source_map
        assert result.module.to_dict() == expected.module.to_dict()
        assert result.element_map == expected.element_map
        assert result.boundary_map == expected.boundary_map
        assert result.host_requirements == expected.host_requirements
    observed_fork = current_branch(reader, fork.branch_ref.ref.entity_id)
    assert observed_fork.upstream_branch_ref == branches[1].branch_ref
    assert observed_fork.fork_base_revision_ref == revisions[1].revision.revision_ref
    fork_proof = validate_closed_revision(reader, observed_fork.head_revision_ref, registration())
    assert fork_proof.module.to_dict() == revisions[1].module.to_dict()
    assert fork_proof.source_map == revisions[1].source_map
    assert counts(reader) == before
    assert core.event_store.object_rows_by_type("net_instance/v1") == ()
    assert core.event_store.list_events_by_type(("net_adopted/v1", "marking_checkpoint_committed/v1",
        "firing_started/v1", "execution_instance_created/v1")) == ()
    assert branches[-1].owner_task_ref.ref == gateway._task_ref
    assert branches[-1].publisher_bootstrap_ref.ref == gateway._bootstrap_ref
    # Both source and recipe truly changed, while stable source IDs persisted.
    assert revisions[0].source != revisions[1].source != revisions[2].source
    assert revisions[0].recipe != revisions[1].recipe != revisions[2].recipe
    assert [row["element_id"] for row in revisions[0].source_map["elements"]] == [
        row["element_id"] for row in revisions[2].source_map["elements"]]


def test_descriptor_branch_is_not_a_full_material_proof(fixture):
    core, gateway, _ = fixture
    revision = descriptor(fixture)
    branch = gateway.create_graph_author_branch(head_revision_ref=revision.revision_ref, command_id="branch:descriptor")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    assert current_branch(reader, branch.branch_ref.ref.entity_id) == branch
    assert read_branch_version(reader, branch.branch_ref) == branch
    with pytest.raises((RegistryConflict, ValueError)):
        validate_closed_revision(reader, branch.head_revision_ref, registration())


@pytest.mark.parametrize("first_legacy", [False, True])
def test_branch_command_domain_is_shared_across_protocol_versions(fixture, first_legacy):
    core, gateway, _ = fixture
    graph, legacy = descriptor(fixture), descriptor(fixture, legacy=True)
    producers = [(gateway.create_graph_author_branch, graph), (gateway.create_author_branch, legacy)]
    if first_legacy:
        producers.reverse()
    first = producers[0][0](head_revision_ref=producers[0][1].revision_ref, command_id="branch:shared")
    before = counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        producers[1][0](head_revision_ref=producers[1][1].revision_ref, command_id="branch:shared")
    assert counts(core) == before
    assert current_branch(core, first.branch_ref.ref.entity_id) == first


def test_exact_descendant_fork_pins_upstream_without_tracking_latest(fixture):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    r1, r2 = descriptor(fixture, r0), None
    upstream = gateway.create_graph_author_branch(head_revision_ref=r1.revision_ref, command_id="branch:upstream")
    fork = gateway.create_graph_author_branch(head_revision_ref=r1.revision_ref, command_id="branch:fork",
        upstream_branch_ref=upstream.branch_ref)
    r2 = descriptor(fixture, r1)
    newer = advance(gateway, upstream, r2, "branch:upstream-next")
    assert current_branch(core, upstream.branch_ref.ref.entity_id) == newer
    assert current_branch(core, fork.branch_ref.ref.entity_id) == fork
    assert fork.fork_base_revision_ref == r1.revision_ref
    assert fork.upstream_branch_ref == upstream.branch_ref
    with pytest.raises(RegistryConflict, match="fork base"):
        gateway.create_graph_author_branch(head_revision_ref=r2.revision_ref, command_id="branch:wrong-fork",
            upstream_branch_ref=upstream.branch_ref)


def test_catalogs_and_legacy_decoder_remain_strict_and_unknown_versions_fail_closed(fixture):
    core, gateway, _ = fixture
    branch = gateway.create_graph_author_branch(head_revision_ref=descriptor(fixture).revision_ref, command_id="branch:new")
    assert GraphBranchVersion.from_dict(branch.to_dict(), catalog=core.catalog) == branch
    with pytest.raises(SchemaGovernanceError):
        BranchVersion.from_dict(branch.to_dict(), catalog=core.catalog)
    for schema_data in (branch_schema_data, graph_author_schema_data):
        schemas, types, paths = schema_data()
        assert "registry_v1/collaboration_branch/v2" not in schemas
        reader = _RegistryCore(core.run_dir, create=False, read_only=True,
            catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
        with pytest.raises(SchemaGovernanceError):
            current_branch(reader, branch.branch_ref.ref.entity_id)
        with pytest.raises(SchemaGovernanceError):
            read_branch_version(reader, branch.branch_ref)
    unknown = replace(branch.branch_ref, ref=replace(branch.branch_ref.ref, entity_type="collaboration_branch/v3"))
    with pytest.raises(ValueError, match="unsupported"):
        read_branch_version(core, unknown)


@pytest.mark.parametrize("field", ["branch", "head", "stream"])
def test_each_graph_branch_cas_axis_is_checked_in_commit(fixture, field):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    r1 = descriptor(fixture, r0)
    second = advance(gateway, first, r1, "branch:next")
    r2 = descriptor(fixture, r1)
    args = dict(expected_branch_version_ref=second.branch_ref, expected_head_revision_ref=r1.revision_ref,
        expected_stream_head=2, next_revision_ref=r2.revision_ref, command_id="branch:stale")
    args[{"branch": "expected_branch_version_ref", "head": "expected_head_revision_ref", "stream": "expected_stream_head"}[field]] = {
        "branch": first.branch_ref, "head": r0.revision_ref, "stream": 1}[field]
    before = counts(core)
    with pytest.raises(RegistryConflict, match="stale"):
        gateway.advance_graph_author_branch(**args)
    assert counts(core) == before


@pytest.mark.parametrize("same_command", [False, True])
def test_graph_branch_concurrent_commands_compare_and_append_once(fixture, same_command):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    targets = [descriptor(fixture, r0), descriptor(fixture, r0)]
    barrier = Barrier(2)
    def submit(index):
        barrier.wait()
        try:
            return advance(gateway, first, targets[0 if same_command else index],
                "branch:same" if same_command else f"branch:{index}")
        except RegistryConflict as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (0, 1)))
    winners = [result for result in results if type(result) is GraphBranchVersion]
    assert len(winners) == (2 if same_command else 1)
    assert all(winner == winners[0] for winner in winners)
    assert current_branch(core, first.branch_ref.ref.entity_id) == winners[0]
    assert len(core.event_store.object_rows_by_type(branch_publication.GRAPH_BRANCH_TYPE)) == 2


def test_graph_old_command_replay_survives_progress_and_writer_reopen(fixture):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    r1 = descriptor(fixture, r0)
    second = advance(gateway, first, r1, "branch:next")
    third = advance(gateway, second, descriptor(fixture, r1), "branch:third")
    before = counts(core)
    assert gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first") == first
    assert advance(gateway, first, r1, "branch:next") == second
    assert counts(core) == before
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    owner = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    assert advance(owner, first, r1, "branch:next") == second
    assert current_branch(reopened, first.branch_ref.ref.entity_id) == third
    assert counts(reopened) == before


@pytest.mark.parametrize("change", ["head", "stream", "target", "predecessor"])
def test_graph_changed_command_material_conflicts(fixture, change):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    r1, sibling = descriptor(fixture, r0), descriptor(fixture, r0)
    second = advance(gateway, first, r1, "branch:once")
    args = dict(expected_branch_version_ref=first.branch_ref, expected_head_revision_ref=r0.revision_ref,
        expected_stream_head=1, next_revision_ref=r1.revision_ref, command_id="branch:once")
    args[{"head": "expected_head_revision_ref", "stream": "expected_stream_head", "target": "next_revision_ref",
          "predecessor": "expected_branch_version_ref"}[change]] = {
        "head": r1.revision_ref, "stream": 2, "target": sibling.revision_ref, "predecessor": second.branch_ref}[change]
    before = counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        gateway.advance_graph_author_branch(**args)
    assert counts(core) == before


@pytest.mark.parametrize("cut", ["prewrite", "publication", "lost_reply"])
def test_graph_branch_failure_cuts_recover_original_command(fixture, monkeypatch, cut):
    from cpn.rpnh.collaboration import branches
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    target = descriptor(fixture, r0)
    before = counts(core)
    with monkeypatch.context() as patch:
        if cut == "publication":
            original = core.event_store._insert_event
            def interrupted(db, event):
                original(db, event)
                if event.payload.get("object_type") == branch_publication.GRAPH_BRANCH_TYPE:
                    raise RuntimeError("injected publication cut")
            patch.setattr(core.event_store, "_insert_event", interrupted)
        elif cut == "prewrite":
            original = core.object_store.prewrite
            def interrupted(*args, **kwargs):
                result = original(*args, **kwargs)
                if result.object_type == branch_publication.GRAPH_BRANCH_TYPE:
                    raise RuntimeError("injected prewrite cut")
                return result
            patch.setattr(core.object_store, "prewrite", interrupted)
        else:
            original = branches.read_branch_version
            def interrupted(selected, reference):
                result = original(selected, reference)
                if result.command_id == "branch:cut":
                    raise RuntimeError("injected lost reply")
                return result
            patch.setattr(branches, "read_branch_version", interrupted)
        with pytest.raises(RuntimeError, match="injected"):
            advance(gateway, first, target, "branch:cut")
    if cut != "lost_reply":
        assert counts(core) == before
    committed = counts(core)
    reopened = _RegistryCore(core.run_dir, create=False, catalog=core.catalog)
    owner = RegistryRegistrationGateway(reopened, gateway._task_ref, gateway._bootstrap_ref)
    result = advance(owner, first, target, "branch:cut")
    assert result.sequence == 2
    assert current_branch(reopened, first.branch_ref.ref.entity_id) == result
    if cut == "lost_reply":
        assert counts(reopened) == committed
    assert len(reopened.event_store.object_rows_by_type(branch_publication.GRAPH_BRANCH_TYPE)) == 2


def successor(core, first, target, command="branch:direct"):
    return replace(first, branch_ref=SourceQualifiedVersionRef(first.branch_ref.source_id,
        VersionRef(first.branch_ref.ref.entity_type, first.branch_ref.ref.entity_id,
                   branch_publication.branch_version_id(core.task_id, command))),
        head_revision_ref=target.revision_ref, predecessor_branch_ref=first.branch_ref,
        expected_head_revision_ref=first.head_revision_ref, expected_stream_head=first.sequence,
        sequence=first.sequence + 1, command_id=command)


def stage(core, value, *, document=None, payload=None, media_type="application/json", key=None):
    body = value.to_dict() if document is None else document
    ref = value.branch_ref.ref
    tx = core.begin(idempotency_key=key or branch_publication.branch_command_key(value.command_id))
    obj = tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body if payload is None else payload), metadata=body, media_type=media_type,
        schema_ref="registry_v1/" + ref.entity_type)
    return tx, obj


from test_collaboration_branches import _direct_batch, _temporary_member


@pytest.mark.parametrize("route", ["transaction", "batch"])
@pytest.mark.parametrize("damage", ["media", "payload", "size", "locator", "missing", "schema"])
def test_graph_branch_prepared_bytes_cannot_bypass_either_commit_route(fixture, route, damage):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    value = successor(core, first, descriptor(fixture, r0))
    body = value.to_dict()
    payload = {**body, "command_id": "altered"} if damage == "payload" else body
    tx, obj = stage(core, value, payload=payload, media_type="text/plain" if damage == "media" else "application/json")
    if damage == "size":
        bad = replace(obj, size=obj.size + 1)
    elif damage == "locator":
        bad = replace(obj, storage_locator="registry-object:" + str(new_id("resource_version")))
    elif damage == "schema":
        bad = replace(obj, schema_ref=branch_publication.BRANCH_SCHEMA)
    else:
        bad = obj
    if damage == "missing":
        core.object_store.path_for_version(obj.version_id).unlink()
    if bad is not obj:
        # Mutate only this disposable transaction's staged prepared record.
        tx._objects[0] = bad
    before = counts(core)
    with pytest.raises((RegistryConflict, SchemaGovernanceError, ObjectIntegrityError)):
        tx.commit() if route == "transaction" else _direct_batch(core, tx, bad)
    assert counts(core) == before


@pytest.mark.parametrize("damage", ["owner", "source", "bootstrap", "stream", "key"])
@pytest.mark.parametrize("route", ["transaction", "batch"])
def test_graph_branch_owner_and_exact_fact_set_survive_direct_routes(fixture, route, damage):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    value = successor(core, first, descriptor(fixture, r0))
    body = value.to_dict()
    if damage in {"owner", "bootstrap"}:
        field, kind = ("owner_task_ref", "task_version") if damage == "owner" else ("publisher_bootstrap_ref", "bootstrap_command_version")
        body[field]["ref"]["version_id"] = str(new_id(kind))
    elif damage == "source":
        for key, entry in body.items():
            if key.endswith("_ref") and entry is not None:
                entry["source_id"] = "different-source"
    elif damage == "stream":
        body["expected_stream_head"], body["sequence"] = 4, 5
    tx, obj = stage(core, value, document=body, key="wrong:key" if damage == "key" else None)
    before = counts(core)
    with pytest.raises((RegistryConflict, ValueError)):
        tx.commit() if route == "transaction" else _direct_batch(core, tx, obj)
    assert counts(core) == before


@pytest.mark.parametrize("target", ["revision", "principal", "task"])
@pytest.mark.parametrize("damage", ["missing_commit", "observational_commit", "missing_publication", "provisional_object", "provisional_commit"])
def test_graph_branch_exact_target_authorities_require_canonical_closure(fixture, target, damage):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    revision = descriptor(fixture, r0)
    ref = {"revision": revision.revision_ref, "principal": revision.producer_principal_ref, "task": revision.owner_task_ref}[target]
    tx, obj = stage(core, successor(core, first, revision))
    with core.event_store.connect() as db:
        row = db.execute("SELECT * FROM objects WHERE version_id=?", (str(ref.ref.version_id),)).fetchone()
        terminal = db.execute("SELECT event_id FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
            (row["transaction_id"],)).fetchone()[0]
        if damage in {"missing_commit", "missing_publication"}:
            db.execute("DELETE FROM events WHERE event_id=?", (terminal if damage == "missing_commit" else row["published_event_id"],))
        elif damage == "observational_commit":
            db.execute("UPDATE events SET criticality='observational' WHERE event_id=?", (terminal,))
    if damage == "provisional_object":
        _temporary_member(core, "object", str(ref.ref.version_id))
    elif damage == "provisional_commit":
        _temporary_member(core, "event", terminal)
    before = counts(core)
    with pytest.raises(RegistryConflict, match="canonical|provisional"):
        _direct_batch(core, tx, obj)
    assert counts(core) == before


@pytest.mark.parametrize("promotion", ["valid", "missing_commit", "provisional_commit"])
def test_graph_branch_accepts_promoted_target_only_with_exact_promotion_closure(fixture, promotion):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    revision = descriptor(fixture, r0)
    promote = core.begin(idempotency_key="fixture:promotion")
    terminal = promote.commit()[-1]
    for ref in (revision.revision_ref, revision.producer_principal_ref):
        root = _temporary_member(core, "object", str(ref.ref.version_id))
        with core.event_store.connect() as db:
            db.execute("UPDATE firing_publications SET state='PUBLISHED',published_transaction_id=?,"
                "operation_result_version_id=?,marking_checkpoint_version_id=? WHERE firing_version_id=?",
                (str(promote.transaction_id), str(new_id("operation_result_version")), str(new_id("marking_checkpoint_version")), root))
            db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
                (root, "event", str(terminal.event_id), str(promote.transaction_id)))
    if promotion == "missing_commit":
        with core.event_store.connect() as db:
            db.execute("DELETE FROM events WHERE event_id=?", (str(terminal.event_id),))
    elif promotion == "provisional_commit":
        _temporary_member(core, "event", str(terminal.event_id))
    value = successor(core, first, revision)
    tx, obj = stage(core, value)
    before = counts(core)
    if promotion == "valid":
        _direct_batch(core, tx, obj)
        assert current_branch(core, first.branch_ref.ref.entity_id) == value
    else:
        with pytest.raises(RegistryConflict, match="canonical|provisional"):
            _direct_batch(core, tx, obj)
        assert counts(core) == before


@pytest.mark.parametrize("target", ["revision", "principal"])
@pytest.mark.parametrize("damage", ["payload", "media", "self"])
def test_graph_branch_target_and_producer_exact_descriptors_cannot_lie(fixture, target, damage):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    revision = descriptor(fixture, r0, publish=False)
    if target == "principal":
        ref = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
        body = {"principal_id": str(new_id("principal") if damage == "self" else ref.entity_id),
                "principal_version_id": str(ref.version_id), "display_name": "Original"}
        payload = {**body, "display_name": "Different"} if damage == "payload" else body
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(payload), metadata=body, media_type="text/plain" if damage == "media" else "application/json",
            schema_ref="registry_v1/principal/v1", idempotency_key="fixture:bad-principal")
        persist(core, replace(revision, producer_principal_ref=SourceQualifiedVersionRef("source-graph", ref)))
    else:
        body = revision.to_dict()
        if damage == "self":
            body["revision_ref"]["ref"]["version_id"] = str(new_id("resource_version"))
        payload = {**body, "command_id": "different-payload"} if damage == "payload" else body
        ref = revision.revision_ref.ref
        core.publish_bytes(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(payload), metadata=body, media_type="text/plain" if damage == "media" else "application/json",
            schema_ref="registry_v1/" + ref.entity_type, idempotency_key=revision.command_id)
    tx, obj = stage(core, successor(core, first, revision))
    before = counts(core)
    with pytest.raises(RegistryConflict, match="descriptor|identity"):
        _direct_batch(core, tx, obj)
    assert counts(core) == before


@pytest.mark.parametrize("length", [2, 5])
def test_graph_branch_full_lineage_rejects_real_cyclic_descriptor_reset(fixture, length):
    core, gateway, _ = fixture
    records = [descriptor(fixture, publish=False)]
    for _ in range(length - 1):
        records.append(descriptor(fixture, records[-1], publish=False))
    records[0] = replace(records[0], parent_revision_refs=(records[-1].revision_ref,))
    for record in records:
        persist(core, record)
    branch = gateway.create_graph_author_branch(head_revision_ref=records[0].revision_ref, command_id="branch:cycle")
    for index, record in enumerate(records[1:], 1):
        branch = advance(gateway, branch, record, f"branch:cycle:{index}")
    before = counts(core)
    with pytest.raises(RegistryConflict, match="reset to a prior exact head"):
        advance(gateway, branch, records[0], "branch:reset")
    assert counts(core) == before
    assert current_branch(core, branch.branch_ref.ref.entity_id) == branch
    with pytest.raises((RegistryConflict, ValueError)):
        validate_closed_revision(core, branch.head_revision_ref, registration())


@pytest.mark.parametrize("damage", ["missing", "bytes", "sequence", "protocol", "source"])
def test_graph_branch_checks_older_predecessors_not_just_previous_head(fixture, damage):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    r1 = descriptor(fixture, r0)
    second = advance(gateway, first, r1, "branch:second")
    r2 = descriptor(fixture, r1)
    third = advance(gateway, second, r2, "branch:third")
    target = descriptor(fixture, r2)
    tx, obj = stage(core, successor(core, third, target))
    # Only the oldest Branch version is damaged. The current and immediate
    # predecessor descriptor remain individually readable; full traversal fails.
    if damage == "missing":
        with core.event_store.connect() as db:
            db.execute("DELETE FROM objects WHERE version_id=?", (str(first.branch_ref.ref.version_id),))
    elif damage == "bytes":
        path = core.object_store.path_for_version(first.branch_ref.ref.version_id)
        path.write_bytes(path.read_bytes().replace(b'branch:first', b'branch:badxx'))
    else:
        body = first.to_dict()
        if damage == "sequence":
            body["sequence"], body["expected_stream_head"] = 2, 1
        elif damage == "protocol":
            body["branch_ref"]["ref"]["entity_type"] = branch_publication.BRANCH_TYPE
        else:
            body["branch_ref"]["source_id"] = "other-source"
        rewrite_descriptor(core, first.branch_ref.ref.version_id, body)
    assert current_branch(core, first.branch_ref.ref.entity_id) == third
    before = counts(core)
    with pytest.raises((RegistryConflict, ValueError, SchemaGovernanceError)):
        _direct_batch(core, tx, obj)
    assert counts(core) == before


def rewrite_descriptor(core, version_id, document):
    """Explicit corruption of canonical metadata and bytes in a throwaway DB."""
    payload = canonical_json(document)
    core.object_store.path_for_version(version_id).write_bytes(payload)
    with core.event_store.connect() as db:
        row = db.execute("SELECT published_event_id FROM objects WHERE version_id=?", (str(version_id),)).fetchone()
        event = db.execute("SELECT payload_json FROM events WHERE event_id=?", (row[0],)).fetchone()
        publication = json.loads(event[0]); publication["metadata"] = document; publication["size"] = len(payload)
        db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?", (json.dumps(document), len(payload), str(version_id)))
        db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(publication), row[0]))


@pytest.mark.parametrize("target_kind", ["skip", "same_head", "other_lineage", "legacy"])
def test_graph_branch_rejects_non_direct_or_wrong_protocol_targets(fixture, target_kind):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    if target_kind == "skip":
        target = descriptor(fixture, descriptor(fixture, r0))
    elif target_kind == "same_head":
        target = r0
    elif target_kind == "other_lineage":
        target = descriptor(fixture)
    else:
        target = descriptor(fixture, legacy=True)
    before = counts(core)
    with pytest.raises((RegistryConflict, ValueError, TypeError)):
        advance(gateway, first, target, "branch:bad")
    assert counts(core) == before


def test_v1_branch_still_accepts_open_and_multi_parent_descriptors(fixture):
    core, gateway, _ = fixture
    a, b = descriptor(fixture, legacy=True), descriptor(fixture, legacy=True)
    first = gateway.create_author_branch(head_revision_ref=a.revision_ref, command_id="branch:legacy")
    raw = descriptor(fixture, a, legacy=True, publish=False)
    opened = replace(raw, definition_kind="open_region", parent_revision_refs=(a.revision_ref, b.revision_ref),
        open_region_contract_ref=SourceQualifiedResourceRef("source-graph", ResourceVersionRef(new_id("resource"), new_id("resource_version"))))
    persist(core, opened)
    second = gateway.advance_author_branch(expected_branch_version_ref=first.branch_ref,
        expected_head_revision_ref=a.revision_ref, expected_stream_head=1, next_revision_ref=opened.revision_ref,
        command_id="branch:legacy-open")
    assert type(second) is BranchVersion
    assert current_branch(core, first.branch_ref.ref.entity_id) == second
    assert BranchVersion.from_dict(second.to_dict(), catalog=core.catalog) == second
    with pytest.raises(TypeError):
        gateway.create_graph_author_branch(head_revision_ref=opened.revision_ref, command_id="branch:not-upgrade")
    with pytest.raises(TypeError):
        gateway.advance_graph_author_branch(expected_branch_version_ref=second.branch_ref,
            expected_head_revision_ref=opened.revision_ref, expected_stream_head=2,
            next_revision_ref=descriptor(fixture).revision_ref, command_id="branch:not-migration")


@pytest.mark.parametrize("route", ["transaction", "batch"])
@pytest.mark.parametrize("occupied", ["graph_branch", "legacy_branch", "revision"])
def test_graph_branch_namespace_cannot_be_retyped_or_preoccupied(fixture, route, occupied):
    core, gateway, _ = fixture
    graph = descriptor(fixture)
    command = "branch:collision"
    if occupied == "revision":
        logical = branch_publication.branch_id_for_command(core.task_id, "source-graph", command)
        prior = replace(graph, revision_ref=replace(graph.revision_ref,
            ref=replace(graph.revision_ref.ref, entity_id=logical, version_id=new_id("resource_version"))), command_id="revision:occupant")
        persist(core, prior)
        value = GraphBranchVersion(SourceQualifiedVersionRef("source-graph", VersionRef(
            branch_publication.GRAPH_BRANCH_TYPE, logical, branch_publication.branch_version_id(core.task_id, command))),
            SourceQualifiedVersionRef("source-graph", gateway._task_ref),
            SourceQualifiedVersionRef("source-graph", gateway._bootstrap_ref), graph.revision_ref, graph.revision_ref,
            None, None, None, 0, 1, command)
        tx, obj = stage(core, value)
        before = counts(core)
        with pytest.raises(RegistryConflict, match="incompatible"):
            tx.commit() if route == "transaction" else _direct_batch(core, tx, obj)
        with pytest.raises(RegistryConflict, match="incompatible"):
            gateway.create_graph_author_branch(head_revision_ref=graph.revision_ref, command_id=command)
        assert counts(core) == before
        return
    legacy = occupied == "legacy_branch"
    revision = descriptor(fixture, legacy=True) if legacy else graph
    method = gateway.create_author_branch if legacy else gateway.create_graph_author_branch
    prior = method(head_revision_ref=revision.revision_ref, command_id="branch:occupied")
    # A valid third type must not steal either version's stream.
    collision = replace(graph, revision_ref=replace(graph.revision_ref,
        ref=replace(graph.revision_ref.ref, entity_id=prior.branch_ref.ref.entity_id, version_id=new_id("resource_version"))),
        command_id="revision:collision")
    ref, body = collision.revision_ref.ref, collision.to_dict()
    tx = core.begin(idempotency_key=collision.command_id)
    obj = tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
        payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/" + ref.entity_type)
    before = counts(core)
    with pytest.raises(RegistryConflict, match="Branch publication"):
        tx.commit() if route == "transaction" else _direct_batch(core, tx, obj)
    assert counts(core) == before
    assert current_branch(core, prior.branch_ref.ref.entity_id) == prior


@pytest.mark.parametrize("legacy_first", [False, True])
def test_same_logical_branch_cannot_upgrade_or_downgrade_protocol(fixture, legacy_first):
    core, gateway, _ = fixture
    graph, legacy = descriptor(fixture), descriptor(fixture, legacy=True)
    create = gateway.create_author_branch if legacy_first else gateway.create_graph_author_branch
    previous = create(head_revision_ref=(legacy if legacy_first else graph).revision_ref, command_id="branch:original")
    record = GraphBranchVersion if legacy_first else BranchVersion
    target = graph if legacy_first else legacy
    # A well-formed new-protocol record at the old logical ID cannot reset the stream.
    value = record(SourceQualifiedVersionRef("source-graph", VersionRef(record._branch_type,
        previous.branch_ref.ref.entity_id, branch_publication.branch_version_id(core.task_id, "branch:retype"))),
        previous.owner_task_ref, previous.publisher_bootstrap_ref, target.revision_ref, target.revision_ref,
        None, None, None, 0, 1, "branch:retype")
    tx, obj = stage(core, value)
    before = counts(core)
    with pytest.raises(RegistryConflict, match="creation"):
        _direct_batch(core, tx, obj)
    assert counts(core) == before


@pytest.mark.parametrize("damage", ["publication", "commit", "aggregate", "payload_type", "unknown", "redirect", "stream"])
def test_graph_branch_current_and_history_fail_closed_on_damaged_authority(fixture, damage):
    core, gateway, _ = fixture
    revision = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=revision.revision_ref, command_id="branch:first")
    other = gateway.create_graph_author_branch(head_revision_ref=revision.revision_ref, command_id="branch:other")
    with core.event_store.connect() as db:
        obj = db.execute("SELECT * FROM objects WHERE version_id=?", (str(first.branch_ref.ref.version_id),)).fetchone()
        event = db.execute("SELECT * FROM events WHERE event_id=?", (obj["published_event_id"],)).fetchone()
        if damage == "publication":
            db.execute("DELETE FROM events WHERE event_id=?", (event["event_id"],))
        elif damage == "commit":
            db.execute("DELETE FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'", (obj["transaction_id"],))
        elif damage == "aggregate":
            db.execute("UPDATE events SET aggregate_type=? WHERE event_id=?", (branch_publication.BRANCH_TYPE, event["event_id"]))
        elif damage == "stream":
            db.execute("UPDATE stream_heads SET sequence=sequence+1 WHERE stream_id=?", (event["stream_id"],))
        else:
            payload = json.loads(event["payload_json"])
            if damage == "redirect":
                payload["metadata"]["branch_ref"] = other.branch_ref.to_dict()
            else:
                payload["object_type"] = branch_publication.BRANCH_TYPE if damage == "payload_type" else "collaboration_branch/v99"
            db.execute("UPDATE events SET payload_json=? WHERE event_id=?", (json.dumps(payload), event["event_id"]))
    with pytest.raises(RegistryConflict):
        current_branch(core, first.branch_ref.ref.entity_id)
    if damage != "stream":  # Exact history does not claim the current stream head.
        with pytest.raises(RegistryConflict):
            read_branch_version(core, first.branch_ref)
    assert current_branch(core, other.branch_ref.ref.entity_id) == other


@pytest.mark.parametrize("damage", ["missing_publication", "missing_commit", "extra_object", "aggregate_type", "producer", "source_schema"])
def test_graph_direct_batch_requires_exact_publication_commit_envelope(fixture, monkeypatch, damage):
    core, gateway, _ = fixture
    root = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=root.revision_ref, command_id="branch:first")
    tx, obj = stage(core, successor(core, first, descriptor(fixture, root)))
    extra = None
    if damage == "extra_object":
        record = descriptor(fixture, root, publish=False)
        ref, body = record.revision_ref.ref, record.to_dict()
        extra = core.object_store.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id, version_id=ref.version_id,
            payload=canonical_json(body), metadata=body, media_type="application/json", schema_ref="registry_v1/" + ref.entity_type)
    original = core.event_store.publish_batch
    def mutate(**kwargs):
        events = list(kwargs["events"])
        if damage == "missing_publication": events = events[1:]
        elif damage == "missing_commit": events = events[:1]
        elif damage == "extra_object": kwargs["objects"] = (*kwargs["objects"], extra)
        elif damage == "aggregate_type": events[0] = replace(events[0], aggregate_type=branch_publication.BRANCH_TYPE)
        elif damage == "producer": events[0] = replace(events[0], producer_principal="not-the-framework")
        else: events[0] = replace(events[0], payload_schema_ref="registry_v1/transaction_committed/v1")
        kwargs["events"] = tuple(events)
        return original(**kwargs)
    monkeypatch.setattr(core.event_store, "publish_batch", mutate)
    before = counts(core)
    with pytest.raises((RegistryConflict, ValueError, SchemaGovernanceError)):
        _direct_batch(core, tx, obj)
    assert counts(core) == before


def test_graph_cas_validator_keeps_one_writer_snapshot_and_rejects_aba_version(fixture, monkeypatch):
    core, gateway, _ = fixture
    r0 = descriptor(fixture)
    first = gateway.create_graph_author_branch(head_revision_ref=r0.revision_ref, command_id="branch:first")
    target = descriptor(fixture, r0)
    original = branch_publication.validate_branch_publication
    connections = []
    def validate(context):
        assert context.db.in_transaction
        observed = branch_publication.current_branch_document(context.db, core.catalog, core.task_id,
            str(first.branch_ref.ref.entity_id), core.object_store)
        assert observed["branch_ref"] == first.branch_ref.to_dict()
        connections.append(context.db)
        return original(context)
    with monkeypatch.context() as patch:
        patch.setattr(branch_publication, "validate_branch_publication", validate)
        advance(gateway, first, target, "branch:next")
    assert len(connections) == 1
    observed = first.to_dict()
    observed["branch_ref"]["ref"]["version_id"] = str(new_id("resource_version"))
    observed["sequence"] = 3
    with monkeypatch.context() as patch:
        patch.setattr(branch_publication, "current_branch_document", lambda *args: deepcopy(observed))
        with pytest.raises(RegistryConflict, match="stale"):
            gateway.advance_graph_author_branch(expected_branch_version_ref=first.branch_ref,
                expected_head_revision_ref=r0.revision_ref, expected_stream_head=3,
                next_revision_ref=target.revision_ref, command_id="branch:aba")


@pytest.mark.parametrize("damage", ["source", "recipe", "module", "host_selection", "command", "source_map"])
def test_branch_read_head_requires_separate_full_source_material_consumer(fixture, damage):
    from cpn.rpnh.collaboration.graph_source import GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA
    from cpn.rpnh.collaboration.materials import MODULE_SCHEMA
    from test_collaboration_graph_materials import publish_mutation, publish_forged_revision
    core, gateway, principal = fixture
    selected = registration()
    author = GraphModuleAuthor(gateway, selected, principal)
    source = make_graph_source(graph_wire(True))
    good = author.publish(source=source, recipe=recipe(), source_ids=source_ids(source), command_id="graph:good")
    changes = {}
    if damage == "source":
        document = deepcopy(good.source); document["graph"]["nodes"][0]["instruction"] = "Altered source only"
        changes["graph_source_ref"] = publish_mutation(author, document, GRAPH_SOURCE_SCHEMA)
    elif damage == "recipe":
        document = deepcopy(good.recipe); document["max_attempts_per_node"] = 99
        changes["graph_recipe_ref"] = publish_mutation(author, document, GRAPH_RECIPE_SCHEMA)
    elif damage == "module":
        document = good.module.to_dict(); document["components"][0]["operations"][0]["config"]["node_synopsis"] = "Altered derived only"
        changes["definition_ref"] = publish_mutation(author, document, MODULE_SCHEMA)
    elif damage == "host_selection":
        document = deepcopy(good.recipe); document["declaration_refs"].pop()
        changes["graph_recipe_ref"] = publish_mutation(author, document, GRAPH_RECIPE_SCHEMA)
    elif damage == "source_map":
        document = deepcopy(good.source_map); document["elements"].pop()
        changes["graph_source_mapping_ref"] = publish_mutation(author, document, GRAPH_MAP_SCHEMA)
    # "command" changes only the revision's complete command identity, keeping
    # original material documents intact, so a compile-only check is insufficient.
    forged = publish_forged_revision(author, good.revision, **changes)
    branch = gateway.create_graph_author_branch(head_revision_ref=forged.revision_ref, command_id="branch:descriptor-only")
    reader = _RegistryCore(core.run_dir, create=False, read_only=True, catalog=core.catalog)
    for selected_branch in (current_branch(reader, branch.branch_ref.ref.entity_id), read_branch_version(reader, branch.branch_ref)):
        assert selected_branch == branch
        before = counts(reader)
        with pytest.raises((RegistryConflict, ValueError)):
            validate_closed_revision(reader, selected_branch.head_revision_ref, registration())
        assert counts(reader) == before


@pytest.mark.parametrize("field", ["owner", "source", "upstream"])
def test_same_graph_branch_create_command_cannot_change_scope_or_fork(fixture, field):
    from cpn.rpnh.collaboration.branches import _publish_branch
    core, gateway, _ = fixture
    revision = descriptor(fixture)
    upstream = gateway.create_graph_author_branch(head_revision_ref=revision.revision_ref, command_id="branch:upstream")
    first = gateway.create_graph_author_branch(head_revision_ref=revision.revision_ref, command_id="branch:first")
    if field == "upstream":
        value = replace(first, upstream_branch_ref=upstream.branch_ref)
    elif field == "owner":
        value = replace(first, owner_task_ref=replace(first.owner_task_ref,
            ref=replace(first.owner_task_ref.ref, version_id=new_id("task_version"))))
    else:
        value = replace(first, **{name: replace(getattr(first, name), source_id="other-source")
            for name in first.__dataclass_fields__ if name.endswith("_ref") and getattr(first, name) is not None})
    before = counts(core)
    with pytest.raises(RegistryConflict, match="conflict"):
        _publish_branch(core, value)
    assert counts(core) == before


def test_graph_target_schema_rejects_multi_parent_cross_source_and_unknown_refs(fixture):
    core, gateway, _ = fixture
    a = descriptor(fixture)
    b, c = descriptor(fixture, a), descriptor(fixture, a)
    before = counts(core)
    with pytest.raises(ValueError, match="one exact graph-v2 parent"):
        replace(c, parent_revision_refs=(a.revision_ref, b.revision_ref))
    with pytest.raises(ValueError, match="same|share"):
        replace(c, parent_revision_refs=(replace(a.revision_ref, source_id="other-source"),))
    with pytest.raises(TypeError, match="graph|collaboration_net_revision/v2"):
        replace(c, parent_revision_refs=(replace(a.revision_ref,
            ref=replace(a.revision_ref.ref, entity_type="collaboration_net_revision/v99")),))
    with pytest.raises(ValueError, match="lineage"):
        replace(c, parent_revision_refs=(descriptor(fixture, publish=False).revision_ref,))
    assert counts(core) == before
