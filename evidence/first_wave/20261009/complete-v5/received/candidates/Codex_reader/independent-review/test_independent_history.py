from dataclasses import replace
import importlib.util
import shutil
from pathlib import Path

import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.main_thread import MainThreadRegistry, MainThreadAuthorityError
from cpn.rpnh.registry.main_thread_history import MainThreadHistoryAnchor
from test_main_thread_registry import _commit_turn, _start_turn, _install_observation
from test_main_thread_history import _service, _history, _walk


def test_distinct_registry_with_same_task_branch_still_rejects_cut(tmp_path, monkeypatch):
    original = _service(tmp_path)
    _history(original, monkeypatch, 1)
    cut = original.capture_read_cut()
    other_root = tmp_path / 'other'
    other_root.mkdir()
    other_core = _RegistryCore(other_root / 'main', create=True,
        project_task_id=original.core.task_id, branch_id=original.core.branch_id)
    other = MainThreadRegistry(other_core, session_root=other_root)
    _history(other, monkeypatch, 1)
    assert other.core.task_id == original.core.task_id
    assert other.core.branch_id == original.core.branch_id
    with pytest.raises(MainThreadAuthorityError, match='boundary'):
        other.project_thread_at(cut)


def test_equivalent_copied_registry_accepts_identity_but_is_not_a_grant(tmp_path, monkeypatch):
    original = _service(tmp_path)
    _history(original, monkeypatch, 1)
    cut = original.capture_read_cut()
    copied = tmp_path / 'copy'
    shutil.copytree(original.core.run_dir, copied)
    reader = MainThreadRegistry(_RegistryCore(copied, create=False, read_only=True),
                                session_root=tmp_path)
    assert reader.project_thread_at(cut) == original.project_thread_at(cut)


def test_old_cut_ignores_corrupt_future_object_and_rejects_future_thread(tmp_path, monkeypatch):
    service = _service(tmp_path)
    head = _history(service, monkeypatch, 1)
    old = service.capture_read_cut()
    expected = service.project_thread_at(old)
    committed, _ = _commit_turn(monkeypatch, service, _start_turn(service, head, 2), 2)
    future = service.capture_read_cut()
    with pytest.raises(MainThreadAuthorityError, match='thread identity'):
        service.project_thread_at(replace(old, thread_ref=future.thread_ref))
    service.core.object_store.path_for_version(committed.turn_ref.version_id).write_bytes(b'corrupt')
    assert service.project_thread_at(old) == expected
    assert _walk(service, old, 'items', limit=1) == expected.turns[0].items
    with pytest.raises(MainThreadAuthorityError):
        service.project_thread_at(future)


def test_current_legacy_dictionary_shape_and_values_equal_baseline(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'cpn.rpnh.registry.baseline_main_thread',
        Path(__file__).parents[1] / 'baseline/cpn/rpnh/registry/main_thread.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = _service(tmp_path)
    head = _history(service, monkeypatch, 2)
    turn = service.project_thread_at(service.capture_read_cut()).turns[-1]
    service.register_child_registry_link(task_control_id='task-child', task_kind='workflow',
        registry_relative_path='tasks/child', origin_main_turn_ref=turn.turn_ref,
        idempotency_key='child')
    baseline = module.MainThreadRegistry(service.core, session_root=tmp_path)
    assert service.recover_thread() == baseline.recover_thread()
    running = _start_turn(service, head, 3)
    assert service.project_current_thread() == baseline.project_current_thread()
    _install_observation(monkeypatch, service, 'stopped_by_owner')
    receipt = service.record_execution_receipt(turn_ref=running.turn_ref, idempotency_key='stop')
    service.commit_interruption(thread_ref=running.thread_ref, turn_ref=running.turn_ref,
        receipt_ref=receipt, idempotency_key='stopped')
    assert service.recover_thread() == baseline.recover_thread()


def test_limit_changes_do_not_change_anchor_query_identity(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 3)
    cut = service.capture_read_cut()
    expected = _walk(service, cut, 'items', limit=100)
    first = service.page_items_at(cut, limit=1)
    second = service.page_items_at(cut, limit=3, anchor=first.next_anchor)
    last = service.page_items_at(cut, limit=100, anchor=second.next_anchor)
    assert first.entries + second.entries + last.entries == expected
    assert last.next_anchor is None
    final = expected[-1]
    anchor = MainThreadHistoryAnchor(cut, 'items', 'asc', None,
                                    final.turn_ref, final.turn_ordinal, final.item_index)
    empty = service.page_items_at(cut, anchor=anchor)
    assert empty.entries == () and empty.next_anchor is None


def test_current_child_path_validation_is_explicit_fail_closed_limit(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _history(service, monkeypatch, 1)
    service.register_child_registry_link(task_control_id='task-child', task_kind='workflow',
        registry_relative_path='tasks/child', origin_main_turn_ref=None,
        idempotency_key='child')
    cut = service.capture_read_cut()
    assert len(service.project_thread_at(cut).child_links) == 1
    (tmp_path / 'elsewhere').mkdir()
    (tmp_path / 'tasks').symlink_to(tmp_path / 'elsewhere', target_is_directory=True)
    with pytest.raises(ValueError, match='escapes'):
        service.project_thread_at(cut)
