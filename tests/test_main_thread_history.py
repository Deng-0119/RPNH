"""Deterministic native Registry history; no frontend, model or child execution."""

from dataclasses import asdict, replace
import hashlib
import json
import sqlite3

import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import CanonicalView
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.main_thread import MainThreadAuthorityError, MainThreadRegistry
from cpn.rpnh.registry.main_thread_history import MainThreadHistoryAnchor
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json

from test_main_thread_registry import (
    _commit_turn, _install_observation, _payload, _start_turn,
)


def _service(tmp_path):
    core = _RegistryCore(tmp_path / "main", create=True)
    return MainThreadRegistry(core, session_root=tmp_path)


def _history(service, monkeypatch, count=3):
    head = service.create_thread(idempotency_key="create")
    for ordinal in range(1, count + 1):
        head, _ = _commit_turn(
            monkeypatch, service, _start_turn(service, head, ordinal), ordinal)
        head = head.thread_ref
    return head


def _walk(service, cut, query, **kwargs):
    method = getattr(service, f"page_{query}_at")
    anchor = None
    seen_anchors = set()
    result = []
    while True:
        page = method(cut, anchor=anchor, **kwargs)
        assert page.cut == cut
        assert len(page.entries) <= kwargs.get("limit", 50)
        result.extend(page.entries)
        anchor = page.next_anchor
        if anchor is None:
            break
        assert page.entries and anchor not in seen_anchors
        assert anchor.inclusive is False
        seen_anchors.add(anchor)
    assert len(set(result)) == len(result)
    return tuple(result)


def _fingerprint(path):
    # SQLite read transactions update WAL-index read marks. The -shm file is
    # volatile coordination, not persisted Registry authority or user state.
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file() and not p.name.endswith("-shm")}


def test_pages_cover_all_native_turns_and_field_slots_both_orders(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 4)
    cut = service.capture_read_cut()
    projection = service.project_thread_at(cut)
    turns = projection.turns
    items = tuple(item for turn in turns for item in turn.items)
    assert [(i.turn_ordinal, i.item_index, i.field) for i in items] == [
        (ordinal, position, field)
        for ordinal in range(1, 5)
        for position, field in enumerate(("user_input", "answer"))]
    for order in ("asc", "desc"):
        for limit in (1, 2, 3, 100):
            for query, expected in (("turns", turns), ("items", items)):
                wanted = expected if order == "asc" else tuple(reversed(expected))
                assert _walk(service, cut, query, limit=limit, order=order) == wanted
        for turn in turns:
            wanted = turn.items if order == "asc" else tuple(reversed(turn.items))
            assert _walk(service, cut, "items", order=order, limit=1,
                         turn_filter=turn.turn_ref) == wanted


def test_append_does_not_change_old_cut_or_interleaved_readers(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = _history(service, monkeypatch)
    old_cut = service.capture_read_cut()
    expected = service.project_thread_at(old_cut)
    first = service.page_items_at(old_cut, limit=1)
    _commit_turn(monkeypatch, service, _start_turn(service, head, 4), 4)
    new_cut = service.capture_read_cut()
    assert new_cut != old_cut
    assert len(service.project_thread_at(new_cut).turns) == 4
    assert service.project_thread_at(old_cut) == expected
    assert service.page_items_at(old_cut, limit=1, anchor=first.next_anchor).entries == (
        expected.turns[0].items[1],)
    reopened = MainThreadRegistry(
        _RegistryCore(service.core.run_dir, create=False, read_only=True),
        session_root=tmp_path)
    assert reopened.project_thread_at(old_cut) == expected
    for cut in (new_cut, old_cut, new_cut, old_cut):
        assert _walk(reopened, cut, "turns", limit=1) == service.project_thread_at(cut).turns


def test_current_projection_uses_one_cut_even_if_writer_appends_between_reads(
        tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = _history(service, monkeypatch, 1)
    expected = service.project_current_thread()
    original = service._thread_versions
    appended = False

    def versions(*, view=None, max_bytes=None):
        nonlocal appended
        result = original(view=view, max_bytes=max_bytes)
        if view is not None and not appended:
            appended = True
            _commit_turn(monkeypatch, service, _start_turn(service, head, 2), 2)
        return result

    monkeypatch.setattr(service, "_thread_versions", versions)
    assert service.project_current_thread() == expected
    assert len(service.recover_thread()["committed_history"]) == 2


def test_empty_partial_stopped_failed_membership_matches_current(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = service.create_thread(idempotency_key="create")
    cuts = [service.capture_read_cut()]
    assert service.page_turns_at(cuts[-1]).entries == ()
    assert service.page_items_at(cuts[-1]).next_anchor is None
    accepted = service.accept_turn(
        thread_ref=head, user_input="pending private input", expected_ordinal=1,
        idempotency_key="accept")
    cuts.append(service.capture_read_cut())
    running = service.attach_attempt(
        thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref,
        idempotency_key="attempt")
    cuts.append(service.capture_read_cut())
    _install_observation(monkeypatch, service, "stopped_by_owner")
    receipt = service.record_execution_receipt(
        turn_ref=running.turn_ref, idempotency_key="stopped-receipt")
    stopped = service.commit_interruption(
        thread_ref=running.thread_ref, turn_ref=running.turn_ref,
        receipt_ref=receipt, idempotency_key="stopped")
    cuts.append(service.capture_read_cut())
    second = _start_turn(service, stopped.thread_ref, 2)
    _install_observation(monkeypatch, service, "running")
    receipt = service.record_execution_receipt(
        turn_ref=second.turn_ref, idempotency_key="failed-receipt", allow_running=True)
    failed = service.commit_execution_failure(
        thread_ref=second.thread_ref, turn_ref=second.turn_ref,
        receipt_ref=receipt, idempotency_key="failed")
    cuts.append(service.capture_read_cut())
    _commit_turn(monkeypatch, service, _start_turn(service, failed.thread_ref, 3), 3)
    for cut in cuts:
        assert service.project_thread_at(cut).turns == ()
    assert [service.project_thread_at(cut).state for cut in cuts] == [
        "idle", "turn_active", "turn_active", "stopped", "idle"]
    assert service.project_thread_at(cuts[1]).active_turn_ref == accepted.turn_ref
    assert service.project_thread_at(cuts[2]).active_turn_ref == running.turn_ref
    projection = service.project_thread_at(service.capture_read_cut())
    current = service.project_current_thread()
    assert [t.ordinal for t in projection.turns] == [3]
    assert [t.ordinal for t in projection.turns] == [
        t["ordinal"] for t in current["committed_history"]]
    assert [_payload(t.turn_ref) for t in projection.turns] == [
        t["turn_ref"] for t in current["committed_history"]]


def test_read_cuts_reject_wrong_source_thread_boundary_future_and_raw_views(
        tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 1)
    cut = service.capture_read_cut()
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = _service(other_root)
    _history(other, monkeypatch, 1)
    bad = (
        other.capture_read_cut(),
        replace(cut, task_id=new_id("task")),
        replace(cut, branch_id="foreign"),
        replace(cut, boundary_event_id=new_id("event")),
        replace(cut, view=CanonicalView(cut.view.through_ordinal + 1)),
        replace(cut, view=CanonicalView(-1)),
        replace(cut, view=CanonicalView(True)),
        replace(cut, view=CanonicalView(0)),
        replace(cut, thread_ref=replace(cut.thread_ref, entity_id=new_id("resource"))),
        replace(cut, thread_ref=replace(cut.thread_ref, version_id=new_id("resource_version"))),
        replace(cut, thread_ref=replace(cut.thread_ref, entity_type="task/v1")),
    )
    for invalid in bad:
        with pytest.raises(MainThreadAuthorityError):
            service.project_thread_at(invalid)
    with pytest.raises(TypeError, match="source-bound"):
        service.project_thread_at(cut.view)
    with service.core.event_store.connect() as db:
        db.execute("UPDATE registry_meta SET value=? WHERE key='branch_id'", ("changed",))
    with pytest.raises(MainThreadAuthorityError, match="Registry"):
        service.project_thread_at(cut)


def test_anchor_binds_query_cut_filter_direction_and_exact_identity(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = _history(service, monkeypatch, 2)
    cut = service.capture_read_cut()
    turns = service.project_thread_at(cut).turns
    anchor = service.page_items_at(cut, limit=1).next_anchor
    assert anchor is not None
    bad = (
        replace(anchor, query="turns"), replace(anchor, order="desc"),
        replace(anchor, turn_filter=turns[0].turn_ref),
        replace(anchor, turn_ordinal=999), replace(anchor, turn_ordinal=True),
        replace(anchor, item_index=None), replace(anchor, item_index=2),
        replace(anchor, item_index=False), replace(anchor, inclusive=1),
        replace(anchor, turn_ref=turns[1].turn_ref),
        replace(anchor, turn_ref=replace(turns[0].turn_ref, entity_type="task/v1")),
    )
    for invalid in bad:
        with pytest.raises(MainThreadAuthorityError):
            service.page_items_at(cut, anchor=invalid)
    with pytest.raises(MainThreadAuthorityError):
        service.page_turns_at(cut, anchor=anchor)
    with pytest.raises(MainThreadAuthorityError):
        service.page_items_at(cut, order="desc", anchor=anchor)
    with pytest.raises(MainThreadAuthorityError):
        service.page_items_at(cut, turn_filter=turns[1].turn_ref, anchor=anchor)
    committed, _ = _commit_turn(monkeypatch, service, _start_turn(service, head, 3), 3)
    with pytest.raises(MainThreadAuthorityError):
        service.page_items_at(service.capture_read_cut(), anchor=anchor)
    with pytest.raises(MainThreadAuthorityError, match="not committed"):
        service.page_items_at(cut, turn_filter=committed.turn_ref)
    for invalid in (0, -1, 101, True, 1.0, "2"):
        with pytest.raises(ValueError, match="limit"):
            service.page_items_at(cut, limit=invalid)
    with pytest.raises(ValueError, match="order"):
        service.page_turns_at(cut, order="backwards")
    with pytest.raises(TypeError):
        service.page_items_at(cut, turn_filter="unknown")


def test_inclusive_initial_and_exclusive_continuation_do_not_skip_or_repeat(
        tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 3)
    cut = service.capture_read_cut()
    for order in ("asc", "desc"):
        for query in ("turns", "items"):
            expected = _walk(service, cut, query, order=order, limit=1)
            item = expected[1]
            anchor = MainThreadHistoryAnchor(
                cut, query, order, None, item.turn_ref,
                item.ordinal if query == "turns" else item.turn_ordinal,
                None if query == "turns" else item.item_index, True)
            method = getattr(service, f"page_{query}_at")
            assert method(cut, order=order, anchor=anchor).entries == expected[1:]
            assert method(cut, order=order,
                          anchor=replace(anchor, inclusive=False)).entries == expected[2:]


def test_refs_only_projection_does_not_expose_private_json_or_follow_child_refs(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cpn.rpnh.main_session import MainSession

    service = _service(tmp_path)
    head = service.create_thread(idempotency_key="create")
    accepted = service.accept_turn(
        thread_ref=head, expected_ordinal=1, idempotency_key="accept",
        user_input={"text": "public question", "required_task_kind": None,
                    "native_plugins": {"configuration": "private plugin config",
                                       "profile": "secret profile"}})
    running = service.attach_attempt(
        thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref,
        idempotency_key="attempt")
    _install_observation(monkeypatch, service, "terminal")
    receipt = service.record_execution_receipt(
        turn_ref=running.turn_ref, idempotency_key="receipt")
    service.commit_terminal_answer(
        thread_ref=running.thread_ref, turn_ref=running.turn_ref,
        receipt_ref=receipt, idempotency_key="commit",
        answer={"reply": "public answer", "protocol_valid": True,
                "task": {"kind": "single_agent", "prompt": "private raw task prompt",
                         "stages": [{"stage_id": "work", "instruction": "private instruction"}],
                         "workflow_graph": None}})
    # The existing public renderer still selects safe text and retains its
    # explicitly live annotation. It is not silently replaced by refs-only data.
    session = MainSession.__new__(MainSession)
    session._refresh_from_authority = service.project_current_thread
    live_reads = []

    def live_child(task, *, ordinal):
        live_reads.append(ordinal)
        return SimpleNamespace(task_id="task-visible", kind=task.kind)

    session._registered_decision_child = live_child
    assert session.display_history == [
        ("user", "public question"),
        ("assistant", "public answer\n\n[launched task-visible: single_agent]"),
    ]
    assert live_reads == [1]
    cut = service.capture_read_cut()
    seen_types = []
    original = service._read_registered_bytes

    def read(core, ref, **kwargs):
        assert core is service.core
        seen_types.append(ref.entity_type)
        return original(core, ref, **kwargs)

    monkeypatch.setattr(service, "_read_registered_bytes", read)
    projection = service.project_thread_at(cut)
    serialized = json.dumps(asdict(projection))
    assert set(seen_types) == {"main_thread/v1", "main_turn/v1"}
    for private in ("private plugin config", "private raw task prompt", "secret profile",
                    "private instruction", "task-visible",
                    "public question", "public answer", "attempt_relative_path",
                    "execution_receipt_ref", "answer_ref", "decision_ref"):
        assert private not in serialized
    assert [i.field for i in projection.turns[0].items] == ["user_input", "answer"]
    assert live_reads == [1]


def test_child_links_are_at_cut_and_never_current_taskcontrol_annotations(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 1)
    before = service.capture_read_cut()
    turn = service.project_thread_at(before).turns[0]
    link = service.register_child_registry_link(
        task_control_id="task-child", task_kind="workflow",
        registry_relative_path="tasks/runs/child", origin_main_turn_ref=turn.turn_ref,
        idempotency_key="child")
    after = service.capture_read_cut()
    assert before.thread_ref == after.thread_ref
    assert before != after

    def forbidden(*args, **kwargs):
        pytest.fail("read-only history attempted execution or a child read")

    for name in ("_inspect_linked_child_identity", "_inspect_child_registry",
                 "reconcile_child_registry_links"):
        monkeypatch.setattr(service, name, forbidden)
    assert service.project_thread_at(before).child_links == ()
    fact, = service.project_thread_at(after).child_links
    assert fact.link_ref == link.link_ref
    assert fact.origin_turn_ref == turn.turn_ref
    assert fact.state == "launch_registered"
    assert not hasattr(fact, "registry_relative_path")
    assert not (tmp_path / "tasks" / "runs" / "child").exists()


def test_readonly_core_counters_files_and_no_writer_calls(tmp_path, monkeypatch):
    writer = _service(tmp_path)
    _history(writer, monkeypatch, 2)
    cut = writer.capture_read_cut()
    profile = tmp_path / "execution_profile.json"
    profile.write_text('{"sentinel":"never read or changed"}')
    lease = tmp_path / ".main-session-owner.lock"
    lease.write_text("existing-owner")
    service = MainThreadRegistry(
        _RegistryCore(writer.core.run_dir, create=False, read_only=True),
        session_root=tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("history reader attempted a writer or execution operation")

    monkeypatch.setattr(service.core, "begin", forbidden)
    monkeypatch.setattr(service.core.object_store, "prewrite", forbidden)
    monkeypatch.setattr(service.core.event_store, "acquire_writer", forbidden)
    monkeypatch.setattr(service.core.event_store, "get_or_create_meta", forbidden)
    connect = service.core.event_store.connect
    writes = []

    def read_connection():
        db = connect()

        def authorize(action, *unused):
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                          sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                          sqlite3.SQLITE_ALTER_TABLE}:
                writes.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        return db

    monkeypatch.setattr(service.core.event_store, "connect", read_connection)
    for name in ("observe_turn_execution", "reconcile_child_registry_links",
                 "_inspect_child_registry", "_inspect_linked_child_identity"):
        monkeypatch.setattr(service, name, forbidden)
    before = _fingerprint(tmp_path)
    counters = (service.core.event_store.max_ordinal(), service.core.event_store.writer_epoch,
                len(service.core.event_store.object_rows()), len(service.core.event_store.list_events()))
    assert service.capture_read_cut() == cut
    _walk(service, cut, "turns", limit=1)
    _walk(service, cut, "items", limit=1)
    assert service.recover_thread() == service.project_current_thread()
    assert before == _fingerprint(tmp_path)
    assert writes == []
    assert counters == (service.core.event_store.max_ordinal(), service.core.event_store.writer_epoch,
                        len(service.core.event_store.object_rows()), len(service.core.event_store.list_events()))


def test_object_body_bound_and_corrupt_exact_bytes_fail_closed(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 1)
    cut = service.capture_read_cut()
    original = service.core.object_store.read_registered
    bounds = []

    def bounded(prepared, *, max_bytes=None):
        bounds.append((max_bytes, prepared.size))
        return original(prepared, max_bytes=max_bytes)

    monkeypatch.setattr(service.core.object_store, "read_registered", bounded)
    service.project_thread_at(cut)
    assert bounds and all(limit == size for limit, size in bounds)
    bounds.clear()
    service.project_thread_at(cut, max_object_bytes=123456)
    assert bounds and all(limit == size for limit, size in bounds)
    with pytest.raises(MainThreadAuthorityError, match="unavailable"):
        service.project_thread_at(cut, max_object_bytes=1)
    for invalid in (-1, True, 1.5, "unbounded"):
        with pytest.raises(TypeError, match="budget"):
            service.project_thread_at(cut, max_object_bytes=invalid)
    path = service.core.object_store.path_for_version(cut.thread_ref.version_id)
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"idle"', b'"fake"'))
    with pytest.raises(MainThreadAuthorityError, match="differ"):
        service.project_thread_at(cut)


def test_large_registered_history_has_no_implicit_reader_cap(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = service.create_thread(idempotency_key="create")
    large = "x" * (4 * 1024 * 1024 + 1)
    user_input = {"text": large, "required_task_kind": None}
    answer = {"reply": large, "protocol_valid": True, "task": None}
    accepted = service.accept_turn(
        thread_ref=head, user_input=user_input, expected_ordinal=1,
        idempotency_key="large-accept")
    running = service.attach_attempt(
        thread_ref=accepted.thread_ref, turn_ref=accepted.turn_ref,
        idempotency_key="large-attempt")
    _install_observation(monkeypatch, service, "terminal")
    receipt = service.record_execution_receipt(
        turn_ref=running.turn_ref, idempotency_key="large-receipt")
    service.commit_terminal_answer(
        thread_ref=running.thread_ref, turn_ref=running.turn_ref,
        receipt_ref=receipt, answer=answer, idempotency_key="large-commit")
    expected = service.project_current_thread()
    assert expected["committed_history"][0]["user_input"] == user_input
    assert expected["committed_history"][0]["answer"] == answer
    assert service.recover_thread() == expected
    cut = service.capture_read_cut()
    assert len(service.project_thread_at(cut).turns) == 1
    assert len(service.page_items_at(cut).entries) == 2
    with pytest.raises(MainThreadAuthorityError, match="unavailable"):
        service.page_items_at(cut, max_object_bytes=4 * 1024 * 1024)
    path = service.core.object_store.path_for_version(cut.thread_ref.version_id)
    path.write_bytes(path.read_bytes() + b" " * 1024)
    with pytest.raises(MainThreadAuthorityError, match="unavailable"):
        service.project_thread_at(cut)


def test_lineage_damage_is_not_hidden_by_paging(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 2)
    cut = service.capture_read_cut()
    turn = service.project_thread_at(cut).turns[-1]
    document = service._read_exact(service.core, turn.turn_ref)
    document["previous_version_ref"] = _payload(turn.turn_ref)
    data = canonical_json(document)
    service.core.object_store.path_for_version(turn.turn_ref.version_id).write_bytes(data)
    with service.core.event_store.connect() as db:
        db.execute("UPDATE objects SET metadata_json=?,size=? WHERE version_id=?",
                   (data.decode(), len(data), str(turn.turn_ref.version_id)))
    with pytest.raises(MainThreadAuthorityError, match="predecessor chain"):
        service.page_turns_at(cut, limit=1)


def test_future_and_provisional_objects_cannot_enter_fixed_history(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = _history(service, monkeypatch, 1)
    cut = service.capture_read_cut()
    expected = service.project_thread_at(cut)
    committed, _ = _commit_turn(monkeypatch, service, _start_turn(service, head, 2), 2)
    with pytest.raises(MainThreadAuthorityError, match="not canonical"):
        service._read_exact(service.core, committed.turn_ref, view=cut.view)
    before = service.capture_read_cut()
    document = service._read_exact(service.core, committed.turn_ref)
    fake = VersionRef("main_turn/v1", new_id("resource"), new_id("resource_version"))
    document.update(main_turn_ref=_payload(fake), ordinal=3, state="accepted",
                    previous_version_ref=None, predecessor_turn_ref=_payload(committed.turn_ref),
                    attempt_relative_path=None, execution_receipt_ref=None,
                    answer_ref=None, decision_ref=None)
    del document["answer"]
    transaction = service.core.begin(idempotency_key="test-provisional-fixture")
    service._prewrite(transaction, fake, document)
    transaction.commit()
    # Read-side provisional-membership fault fixture, not a native firing run.
    root = str(new_id("transition_firing_version"))
    with service.core.event_store.connect() as db:
        tx = str(transaction.transaction_id)
        db.execute(
            "INSERT INTO firing_publications(firing_version_id,firing_logical_id,invocation_version_id,"
            "invocation_logical_id,net_version_id,operation_binding_version_id,admission_checkpoint_version_id,"
            "state,opened_transaction_id) VALUES(?,?,?,?,?,?,?,'PROVISIONAL',?)",
            (root, str(new_id("transition_firing")), str(new_id("invocation_version")),
             str(new_id("invocation")), str(new_id("net_instance_version")),
             str(new_id("operation_binding_version")), str(new_id("marking_checkpoint_version")), tx))
        db.execute("INSERT INTO firing_temporary_members VALUES(?,?,?,?)",
                   (root, "object", str(fake.version_id), tx))
    after = service.capture_read_cut()
    assert service.project_thread_at(after).turns == service.project_thread_at(before).turns
    assert service.project_thread_at(cut) == expected
    with pytest.raises(MainThreadAuthorityError, match="not canonical"):
        service._read_exact(service.core, fake, view=after.view)
    with pytest.raises(MainThreadAuthorityError, match="not committed"):
        service.page_items_at(after, turn_filter=fake)
