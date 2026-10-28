"""Exact SQL candidate narrowing; raw rows isolate canonical visibility.

The raw SQLite fixtures do not establish admission. A separate test uses the
original Start/admission and ResourceService authority with deterministic bytes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cpn.rpnh.registry._resource_service import queries
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.resources import ResourceQuery, ResourceVersionRef
from test_canonical_head_projection import add_event, ledger, mark_firing


def add_object(store, task, ordinal, logical, *, kind="resource_version/v1",
               version=None):
    event, transaction = add_event(store, task, ordinal)
    version = version or new_id("resource_version")
    with store.connection_scope() as db:
        db.execute("INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            str(logical), str(version), kind, ordinal, "text/plain", "fixture",
            None, "fixture", "{}", str(transaction), str(event)))
    return version, event, transaction


def visible_dicts(store, **kwargs):
    return tuple(dict(row) for row in store.canonical_object_rows(**kwargs))


def test_exact_optional_predicate_matches_unfiltered_python_selection(ledger):
    store, task, _ = ledger
    target, other = new_id("resource"), new_id("resource")
    for ordinal in range(1, 31):
        add_object(store, task, ordinal, target if ordinal % 3 else other,
                   kind="resource_version/v1" if ordinal % 5 else "fixture/v1")
    for cut in (0, 1, 5, 17, 30, None):
        for kind in (None, "resource_version/v1", "fixture/v1", "absent/v1"):
            broad = visible_dicts(store, through_ordinal=cut, object_type=kind)
            for logical in (target, str(target), other, new_id("resource"), ""):
                assert visible_dicts(store, through_ordinal=cut, object_type=kind,
                                     logical_id=logical) == tuple(
                    row for row in broad if row["logical_id"] == str(logical))
            assert visible_dicts(store, through_ordinal=cut, object_type=kind,
                                 logical_id=None) == broad


def test_optional_logical_type_does_not_coerce_arbitrary_objects(ledger):
    store, _, _ = ledger
    for value in (False, 1, [], {}, object()):
        with pytest.raises(TypeError, match="TypedId or string"):
            store.canonical_object_rows(logical_id=value)


def test_visibility_cuts_late_promotion_multiple_and_orphan_membership(ledger):
    store, task, _ = ledger
    target = new_id("resource")
    rows = [add_object(store, task, ordinal, target) for ordinal in range(1, 6)]
    _, promote_early = add_event(store, task, 6, event_type="transaction_committed/v1")
    _, promote_late = add_event(store, task, 9, event_type="transaction_committed/v1")
    for index, publication in ((0, promote_early), (0, promote_late), (1, None),
                               (2, promote_early)):
        version, event, transaction = rows[index]
        firing = mark_firing(store, event, transaction, publication_tx=publication)
        with store.connection_scope() as db:
            db.execute("INSERT INTO firing_temporary_members VALUES (?,?,?,?)",
                       (firing, "object", str(version), str(transaction)))
            if index == 2:
                db.execute("UPDATE firing_publications SET state='SUPERSEDED', operation_result_version_id=NULL "
                           "WHERE firing_version_id=?", (firing,))
    with store.connection_scope() as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("INSERT INTO firing_temporary_members VALUES (?,?,?,?)",
                   ("missing-publication", "object", str(rows[3][0]), str(rows[3][2])))
    for cut in (0, 1, 5, 6, 8, 9, None):
        broad = store.canonical_object_rows(through_ordinal=cut, object_type="resource_version/v1")
        narrow = store.canonical_object_rows(through_ordinal=cut, object_type="resource_version/v1",
                                             logical_id=target)
        assert tuple(map(dict, narrow)) == tuple(dict(row) for row in broad
                                                 if row["logical_id"] == str(target))
    final = {row["version_id"] for row in store.canonical_object_rows(logical_id=target)}
    assert str(rows[0][0]) in final  # both publication gates eventually passed
    assert str(rows[1][0]) not in final
    assert str(rows[2][0]) not in final
    assert str(rows[3][0]) in final  # preserve the original inner-join behavior


def legacy_history(kernel, context, resource_id):
    kernel._query_authority(context, observer_fields=("history",))
    if resource_id.kind != "resource":
        raise TypeError("resource history requires a typed resource id")
    store = kernel._ResourceServiceKernel__core.event_store
    candidates = sorted((ResourceVersionRef(resource_id,
        TypedId.parse(row["version_id"], expected="resource_version"))
        for row in store.canonical_object_rows(object_type="resource_version/v1")
        if row["object_type"] == "resource_version/v1" and row["logical_id"] == str(resource_id)),
        key=lambda ref: kernel._canonical_published_ordinal(ref.resource_version_id,
                                                           through_ordinal=store.max_ordinal()))
    for _ref in candidates:
        # This isolated fixture is observer-shaped, so the original receives None.
        kernel._query_authority(context, None, observer_fields=("history",))
    return tuple(candidates)


def test_history_output_authority_order_and_materialized_candidate_reduction(ledger, monkeypatch):
    store, task, _ = ledger
    target = new_id("resource")
    for ordinal in range(1, 101):
        add_object(store, task, ordinal, target if ordinal in (1, 25, 99) else new_id("resource"))
    observed, authority = [], []
    original = store.canonical_object_rows
    def capture(**kwargs):
        rows = original(**kwargs)
        observed.append((kwargs, len(rows)))
        return rows
    monkeypatch.setattr(store, "canonical_object_rows", capture)
    kernel = SimpleNamespace(_ResourceServiceKernel__core=SimpleNamespace(event_store=store),
        _query_authority=lambda *args, **kwargs: authority.append((args, kwargs)),
        _canonical_published_ordinal=store.canonical_object_publication_ordinal)
    context = object()
    expected = legacy_history(kernel, context, target)
    expected_authority = list(authority)
    authority.clear()
    actual = queries.history(kernel, context, target)
    assert actual == expected
    assert authority == expected_authority
    assert [count for _, count in observed] == [100, 3]
    assert observed[1][0]["logical_id"] == target
    assert store.max_ordinal() == 100


def test_matching_malformed_identity_still_fails_unrelated_stays_ignored(ledger):
    store, task, _ = ledger
    target, other = new_id("resource"), new_id("resource")
    add_object(store, task, 1, target)
    add_object(store, task, 2, other, version="malformed-version")
    kernel = SimpleNamespace(_ResourceServiceKernel__core=SimpleNamespace(event_store=store),
        _query_authority=lambda *args, **kwargs: None,
        _canonical_published_ordinal=store.canonical_object_publication_ordinal)
    assert queries.history(kernel, object(), target) == legacy_history(kernel, object(), target)
    for method in (queries.history, legacy_history):
        with pytest.raises(ValueError):
            method(kernel, object(), other)


def test_original_resource_history_authority_on_real_registered_inputs(tmp_path):
    from test_source_set_query import _owner, _readonly
    from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
    owner, contexts = _owner(tmp_path / "registered", "history-fixture")
    service = _ResourceServiceKernel(_readonly(owner))
    context = contexts["main"]
    before = owner._core.event_store.max_ordinal()
    page = service.query(context, ResourceQuery(task_ref=context.task_ref, scope_refs=(), limit=1000))
    assert page.headers
    for header in page.headers:
        expected = tuple(ResourceVersionRef(header.ref.resource_id,
            TypedId.parse(row["version_id"], expected="resource_version"))
            for row in owner._core.event_store.canonical_object_rows(object_type="resource_version/v1")
            if row["logical_id"] == str(header.ref.resource_id))
        assert service.history(context, header.ref.resource_id) == expected
    with pytest.raises(TypeError):
        service.history(context, new_id("task"))
    assert owner._core.event_store.max_ordinal() == before


@pytest.mark.parametrize("deny_at", (1, 2, 3))
def test_history_preserves_authority_denial_and_call_order(ledger, deny_at):
    store, task, _ = ledger
    target = new_id("resource")
    for ordinal in range(1, 4):
        add_object(store, task, ordinal, target)
    outcomes = []
    context = object()
    for method in (legacy_history, queries.history):
        calls = []
        def authority(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == deny_at:
                raise PermissionError("fixture authority denied")
        kernel = SimpleNamespace(_ResourceServiceKernel__core=SimpleNamespace(event_store=store),
            _query_authority=authority,
            _canonical_published_ordinal=store.canonical_object_publication_ordinal)
        with pytest.raises(PermissionError, match="fixture authority denied"):
            method(kernel, context, target)
        outcomes.append(calls)
    assert outcomes[0] == outcomes[1]


def test_actual_history_sql_uses_existing_compound_index(ledger, monkeypatch):
    store, task, _ = ledger
    target = new_id("resource")
    add_object(store, task, 1, target)
    # Capture SQLite's actual executed statement, not a separately invented query.
    statements = []
    original_connect = store.connect
    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(store, "connect", traced_connect)
    store.canonical_object_rows(object_type="resource_version/v1", logical_id=target)
    statement = next(sql for sql in statements if sql.startswith("SELECT o.* FROM objects"))
    with store.connection_scope() as db:
        plan = tuple(row[3] for row in db.execute("EXPLAIN QUERY PLAN " + statement))
    assert any("objects_type_logical" in detail and "logical_id=?" in detail for detail in plan), plan
