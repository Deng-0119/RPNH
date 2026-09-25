from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import (
    NativeBootstrapManifest,
    _bootstrap_identity,
)
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.main_thread import (
    MainThreadAuthorityError,
    MainThreadRegistry,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json


def _ref(entity_type: str, logical_kind: str, version_kind: str) -> VersionRef:
    return VersionRef(entity_type, new_id(logical_kind), new_id(version_kind))


def _payload(ref: VersionRef) -> dict[str, str]:
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _observed(turn_ref: VersionRef, relative_path: str, outcome: str) -> dict[str, Any]:
    task = _ref("task/v1", "task", "task_version")
    run = _ref("native_run_identity/v1", "run", "run_version")
    authority = _ref(
        "run_execution_authority/v1",
        "run_execution_authority",
        "run_execution_authority_version",
    )
    checkpoint = _ref(
        "marking_checkpoint/v1", "marking_checkpoint",
        "marking_checkpoint_version",
    )
    result = {
        "main_turn_ref": _payload(turn_ref),
        "attempt_relative_path": relative_path,
        "validated_through_ordinal": 17,
        "child_task_ref": _payload(task),
        "child_run_ref": _payload(run),
        "run_execution_authority_ref": _payload(authority),
        "final_checkpoint_ref": _payload(checkpoint),
        "outcome": outcome,
        "run_outcome": None,
        "terminal_evidence_ref": None,
        "terminal_result_ref": None,
        "final_result_index_ref": None,
    }
    if outcome == "terminal":
        result.update({
            "run_outcome": "complete",
            "terminal_evidence_ref": _payload(_ref(
                "run_terminal_evidence/v1", "terminal_evidence",
                "terminal_evidence_version")),
            "terminal_result_ref": _payload(_ref(
                "resource_version/v1", "resource", "resource_version")),
            "final_result_index_ref": _payload(_ref(
                "final_result_index/v1", "resource", "resource_version")),
        })
    return result


def _start_turn(service: MainThreadRegistry, thread_ref: VersionRef, ordinal: int):
    accepted = service.accept_turn(
        thread_ref=thread_ref,
        user_input={"role": "user", "content": f"question {ordinal}"},
        expected_ordinal=ordinal,
        idempotency_key=f"accept-{ordinal}",
    )
    return service.attach_attempt(
        thread_ref=accepted.thread_ref,
        turn_ref=accepted.turn_ref,
        idempotency_key=f"attempt-{ordinal}",
    )


def _install_observation(monkeypatch, service, outcome):
    observations: dict[str, dict[str, Any]] = {}

    def inspect(*, turn_ref, turn, allow_running=False):
        assert allow_running is (outcome == "running")
        key = str(turn_ref)
        observations.setdefault(
            key,
            _observed(turn_ref, turn["attempt_relative_path"], outcome),
        )
        return observations[key]

    monkeypatch.setattr(service, "_inspect_child_registry", inspect)


def _commit_turn(monkeypatch, service, attachment, ordinal):
    _install_observation(monkeypatch, service, "terminal")
    receipt = service.record_execution_receipt(
        turn_ref=attachment.turn_ref,
        idempotency_key=f"receipt-{ordinal}",
    )
    return service.commit_terminal_answer(
        thread_ref=attachment.thread_ref,
        turn_ref=attachment.turn_ref,
        receipt_ref=receipt,
        answer={"role": "assistant", "content": f"answer {ordinal}"},
        idempotency_key=f"commit-{ordinal}",
    ), receipt


def test_two_committed_turns_recover_one_thread_and_history(tmp_path, monkeypatch):
    run_dir = tmp_path / "main"
    core = _RegistryCore(run_dir, create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    created = service.create_thread(idempotency_key="create")

    first, _ = _commit_turn(
        monkeypatch, service, _start_turn(service, created, 1), 1)
    second, _ = _commit_turn(
        monkeypatch, service, _start_turn(service, first.thread_ref, 2), 2)

    recovered = MainThreadRegistry(
        _RegistryCore(run_dir, create=False, read_only=True),
        session_root=tmp_path,
    ).recover_thread()
    assert recovered["thread_id"] == str(created.entity_id)
    assert recovered["thread_ref"] == _payload(second.thread_ref)
    assert recovered["state"] == "idle"
    assert [item["ordinal"] for item in recovered["committed_history"]] == [1, 2]
    assert [item["answer"]["content"] for item in recovered["committed_history"]] == [
        "answer 1", "answer 2"]


def test_terminal_receipt_commit_is_idempotent(tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    attached = _start_turn(
        service, service.create_thread(idempotency_key="create"), 1)
    _install_observation(monkeypatch, service, "terminal")
    receipt = service.record_execution_receipt(
        turn_ref=attached.turn_ref, idempotency_key="receipt")
    arguments = dict(
        thread_ref=attached.thread_ref,
        turn_ref=attached.turn_ref,
        receipt_ref=receipt,
        answer="done",
        idempotency_key="commit",
    )
    first = service.commit_terminal_answer(**arguments)
    object_count = len(core.event_store.object_rows())
    second = service.commit_terminal_answer(**arguments)
    assert second == first
    assert len(core.event_store.object_rows()) == object_count


def test_stopped_receipt_commits_interruption_without_history_pair(
        tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    attached = _start_turn(
        service, service.create_thread(idempotency_key="create"), 1)
    _install_observation(monkeypatch, service, "stopped_by_owner")
    receipt = service.record_execution_receipt(
        turn_ref=attached.turn_ref, idempotency_key="receipt")
    interrupted = service.commit_interruption(
        thread_ref=attached.thread_ref,
        turn_ref=attached.turn_ref,
        receipt_ref=receipt,
        idempotency_key="interrupt",
    )
    projection = service.project_current_thread()
    assert projection["state"] == "stopped"
    assert projection["committed_history"] == []
    assert projection["latest_turn_ref"] == _payload(interrupted.turn_ref)
    assert "answer" not in service._read_exact(core, interrupted.turn_ref)

    # The durable interruption remains visible, while the next user turn
    # resumes from the last committed conversational history (empty here).
    resumed = _start_turn(service, interrupted.thread_ref, 2)
    committed, _receipt = _commit_turn(
        monkeypatch, service, resumed, 2)
    projection = service.project_current_thread()
    assert projection["state"] == "idle"
    assert [item["ordinal"] for item in projection["committed_history"]] == [2]
    assert projection["latest_turn_ref"] == _payload(committed.turn_ref)


def test_running_receipt_commits_failed_main_turn_without_child_terminal(
        tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    attached = _start_turn(
        service, service.create_thread(idempotency_key="create"), 1)
    _install_observation(monkeypatch, service, "running")

    receipt = service.record_execution_receipt(
        turn_ref=attached.turn_ref,
        idempotency_key="failure-receipt",
        allow_running=True,
    )
    failed = service.commit_execution_failure(
        thread_ref=attached.thread_ref,
        turn_ref=attached.turn_ref,
        receipt_ref=receipt,
        idempotency_key="fail",
    )

    projection = service.project_current_thread()
    assert projection["state"] == "idle"
    assert projection["active_turn_ref"] is None
    assert projection["committed_history"] == []
    assert projection["latest_turn_ref"] == _payload(failed.turn_ref)
    failed_turn = service._read_exact(core, failed.turn_ref)
    assert failed_turn["state"] == "failed"
    registered_receipt = service._read_exact(core, receipt)
    assert registered_receipt["outcome"] == "running"
    assert registered_receipt["terminal_evidence_ref"] is None

    resumed = _start_turn(service, failed.thread_ref, 2)
    resumed_turn = service._read_exact(core, resumed.turn_ref)
    assert resumed_turn["ordinal"] == 2


def test_path_escape_and_mismatched_receipt_have_no_partial_write(
        tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    created = service.create_thread(idempotency_key="create")
    accepted = service.accept_turn(
        thread_ref=created,
        user_input="question",
        expected_ordinal=1,
        idempotency_key="accept",
    )
    before_escape = len(core.event_store.object_rows())
    with pytest.raises(ValueError, match="escapes"):
        service.attach_attempt(
            thread_ref=accepted.thread_ref,
            turn_ref=accepted.turn_ref,
            attempt_relative_path="../outside",
            idempotency_key="escape",
        )
    assert len(core.event_store.object_rows()) == before_escape

    attached = service.attach_attempt(
        thread_ref=accepted.thread_ref,
        turn_ref=accepted.turn_ref,
        idempotency_key="attach",
    )
    _install_observation(monkeypatch, service, "terminal")
    receipt = service.record_execution_receipt(
        turn_ref=attached.turn_ref, idempotency_key="receipt")
    valid = service._read_exact(core, receipt)
    forged_ref = VersionRef(
        "main_turn_execution_receipt/v1", new_id("resource"),
        new_id("resource_version"))
    forged = {
        **valid,
        "main_turn_execution_receipt_ref": _payload(forged_ref),
        "main_turn_ref": _payload(_ref(
            "main_turn/v1", "resource", "resource_version")),
    }
    core.publish_bytes(
        object_type=forged_ref.entity_type,
        logical_id=forged_ref.entity_id,
        version_id=forged_ref.version_id,
        payload=canonical_json(forged),
        metadata=forged,
        media_type="application/json",
        schema_ref="registry_v1/main_turn_execution_receipt/v1",
        idempotency_key="forged-receipt",
    )
    before_mismatch = len(core.event_store.object_rows())
    with pytest.raises(RegistryConflict, match="does not belong"):
        service.commit_terminal_answer(
            thread_ref=attached.thread_ref,
            turn_ref=attached.turn_ref,
            receipt_ref=forged_ref,
            answer="must not commit",
            idempotency_key="mismatched",
        )
    assert len(core.event_store.object_rows()) == before_mismatch


def test_main_thread_indexes_independent_child_registry_without_copying_it(
        tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    created = service.create_thread(idempotency_key="create")
    committed, _receipt = _commit_turn(
        monkeypatch, service, _start_turn(service, created, 1), 1)

    registered = service.register_child_registry_link(
        task_control_id="task-child",
        task_kind="workflow",
        registry_relative_path="tasks/runs/child",
        origin_main_turn_ref=committed.turn_ref,
        idempotency_key="register-child",
    )
    projected = service.project_current_thread()["child_registry_links"]
    assert len(projected) == 1
    assert projected[0]["task_control_id"] == "task-child"
    assert projected[0]["task_kind"] == "workflow"
    assert projected[0]["registry_relative_path"] == "tasks/runs/child"
    assert projected[0]["origin_main_turn_ref"] == _payload(
        committed.turn_ref)
    assert projected[0]["state"] == "launch_registered"
    assert projected[0]["child_task_ref"] is None
    assert projected[0]["child_run_ref"] is None

    child_core = _RegistryCore(
        tmp_path / "tasks" / "runs" / "child", create=True)
    pending = service.attach_child_registry_link(
        task_control_id="task-child",
        idempotency_key="attach-child",
    )
    assert pending.link_ref == registered.link_ref
    assert service.project_current_thread()["child_registry_links"][0][
        "state"] == "launch_registered"
    child_identity = _bootstrap_identity(
        child_core, NativeBootstrapManifest(("test-protocol/v1",)),
    )
    attached = service.attach_child_registry_link(
        task_control_id="task-child",
        idempotency_key="attach-child",
    )
    assert attached.registry_path == tmp_path / "tasks" / "runs" / "child"
    projected = service.project_current_thread()["child_registry_links"]
    assert projected[0]["state"] == "registry_attached"
    assert projected[0]["child_task_ref"] == _payload(
        child_identity.task_ref)
    assert projected[0]["child_run_ref"] == _payload(
        child_identity.run_ref)

    object_count = len(core.event_store.object_rows())
    retried = service.register_child_registry_link(
        task_control_id="task-child",
        task_kind="workflow",
        registry_relative_path="tasks/runs/child",
        origin_main_turn_ref=committed.turn_ref,
        idempotency_key="register-child",
    )
    service.attach_child_registry_link(
        task_control_id="task-child",
        idempotency_key="attach-child",
    )
    assert retried.link_ref == attached.link_ref
    assert len(core.event_store.object_rows()) == object_count

    with pytest.raises(ValueError, match="escapes"):
        service.register_child_registry_link(
            task_control_id="task-outside",
            task_kind="single_agent",
            registry_relative_path="../outside",
            origin_main_turn_ref=None,
            idempotency_key="outside-child",
        )
    assert len(core.event_store.object_rows()) == object_count


def test_empty_child_sqlite_startup_window_is_not_registry_corruption(
        tmp_path):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    service.create_thread(idempotency_key="create")
    registered = service.register_child_registry_link(
        task_control_id="task-starting",
        task_kind="single_agent",
        registry_relative_path="tasks/runs/starting",
        origin_main_turn_ref=None,
        idempotency_key="register-starting",
    )
    database = (
        tmp_path / "tasks" / "runs" / "starting"
        / ".registry_v1" / "registry.sqlite3")
    database.parent.mkdir(parents=True)
    database.touch()

    pending = service.attach_child_registry_link(
        task_control_id="task-starting",
        idempotency_key="attach-starting",
    )

    assert pending.link_ref == registered.link_ref
    projected = service.project_current_thread()["child_registry_links"]
    assert projected[0]["state"] == "launch_registered"
    assert projected[0]["child_task_ref"] is None
    assert projected[0]["child_run_ref"] is None


def test_published_malformed_child_identity_is_rejected(tmp_path):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    service.create_thread(idempotency_key="create")
    service.register_child_registry_link(
        task_control_id="task-malformed",
        task_kind="single_agent",
        registry_relative_path="tasks/runs/malformed",
        origin_main_turn_ref=None,
        idempotency_key="register-malformed",
    )
    child = _RegistryCore(
        tmp_path / "tasks" / "runs" / "malformed", create=True)
    child.event_store.get_or_create_meta("native_run_ref", "not-json")

    with pytest.raises(
            MainThreadAuthorityError, match="identity is malformed"):
        service.attach_child_registry_link(
            task_control_id="task-malformed",
            idempotency_key="attach-malformed",
        )

    projected = service.project_current_thread()["child_registry_links"]
    assert projected[0]["state"] == "launch_registered"


@pytest.mark.parametrize("damage", ["missing_object_root", "missing_task_id"])
def test_published_child_pointer_does_not_hide_registry_damage(
        tmp_path, damage):
    core = _RegistryCore(tmp_path / "main", create=True)
    service = MainThreadRegistry(core, session_root=tmp_path)
    service.create_thread(idempotency_key="create")
    service.register_child_registry_link(
        task_control_id="task-damaged",
        task_kind="workflow",
        registry_relative_path="tasks/runs/damaged",
        origin_main_turn_ref=None,
        idempotency_key="register-damaged",
    )
    child = _RegistryCore(
        tmp_path / "tasks" / "runs" / "damaged", create=True)
    _bootstrap_identity(
        child, NativeBootstrapManifest(("test-protocol/v1",)))
    if damage == "missing_object_root":
        child.object_store.root.rename(
            child.object_store.root.with_name("objects-hidden"))
    else:
        with child.event_store.connect() as database:
            database.execute(
                "DELETE FROM registry_meta WHERE key='task_id'")

    with pytest.raises(
            MainThreadAuthorityError, match="identity is malformed"):
        service.attach_child_registry_link(
            task_control_id="task-damaged",
            idempotency_key="attach-damaged",
        )

    projected = service.project_current_thread()["child_registry_links"]
    assert projected[0]["state"] == "launch_registered"
