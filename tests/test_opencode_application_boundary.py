"""Exercise the real application methods against scripted authority boundaries.

These doubles model only the documented MainThread/TaskControl interface; they
are not a replacement for the separately provided real-Registry integration test.
"""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import types

import pytest

from cpn.rpnh.frontend_application import (
    FrontendError, RegistryFrontendApplication, _Session, canonical,
    stable_id,
)

SID = "ses_" + "b" * 32


def ref(number, version):
    return {"entity_type": "main_turn/v1", "logical_id": f"turn-{number}", "version_id": f"turn-{number}-{version}"}


class ScriptedMain:
    def __init__(self, root, execution):
        self.root, self.execution_config_path = root, execution
        self.child_path_root = root
        self.records, self.events = [], []
        self.child_links = []
        self.execution_state = "pending_start"
        self._main_thread = self
        self._registry_core = NS(event_store=NS(list_events=lambda: tuple(self.events)))

    @staticmethod
    def _version_ref(value):
        return NS(entity_id=value["logical_id"], entity_type=value["entity_type"], version_id=value["version_id"])

    @staticmethod
    def _turn_input(value):
        return value["text"], value["required_task_kind"]

    def recover_thread(self):
        history = []
        for lineage in self.records:
            reference, document = lineage[-1]
            if document["state"] == "committed":
                history.append({"ordinal": document["ordinal"], "answer": document["answer"],
                                "turn_ref": ref(document["ordinal"], len(lineage))})
        return {"thread_ref": {"entity_type": "main_thread/v1", "logical_id": "thread", "version_id": "thread-v1"},
                "next_turn_ordinal": len(self.records) + 1, "committed_history": history, "child_registry_links": []}

    def _turn_lineages(self, identity):
        return self.records

    def _publish(self, document, command):
        ordinal = document["ordinal"]
        lineage = self.records[ordinal - 1]
        reference = ref(ordinal, len(lineage) + 1)
        lineage.append((self._version_ref(reference), copy.deepcopy(document)))
        self.events.append(NS(event_type="object_version_published/v1", payload={"version_id": reference["version_id"]},
                              recorded_at="2026-09-25T00:00:00+00:00", command_id=command))

    def accept_turn(self, *, thread_ref, user_input, expected_ordinal, idempotency_key):
        self.records.append([])
        self._publish({"ordinal": expected_ordinal, "user_input": user_input, "state": "accepted", "attempt_relative_path": None},
                      canonical({"authority": "main_thread/v1", "command": "accept_turn",
                                 "caller_idempotency_key": idempotency_key, "material": {"user_input": user_input}}))

    def prepare_turn(self, text, required_task_kind=None):
        document = dict(self.records[-1][-1][1])
        if document["state"] == "accepted":
            document.update(state="running", attempt_relative_path=f"attempts/{document['ordinal']}")
            self._publish(document, "attach")
        return NS(run_dir=self.root / document["attempt_relative_path"], prompt=text)

    def resolve_child_path(self, relative_path):
        return (self.root / relative_path).resolve()

    def relative_child_path(self, child_path):
        return child_path.resolve().relative_to(
            self.child_path_root.resolve()).as_posix()

    def _ordered_documents(self, _core, object_type):
        if object_type == "main_child_registry_link/v1":
            return tuple((None, copy.deepcopy(item))
                         for item in self.child_links)
        return ()

    def _index_child_registry(
            self, handle, *, run_dir, origin_main_turn_ref,
    ):
        self.child_links.append({
            "task_control_id": handle.task_id,
            "task_kind": handle.kind,
            "registry_relative_path": self.relative_child_path(run_dir),
            "origin_main_turn_ref": origin_main_turn_ref,
        })

    def active_turn_snapshot(self):
        if not self.records:
            return None
        document = self.records[-1][-1][1]
        if document["state"] in {"committed", "failed", "interrupted"}:
            return None
        relative = document["attempt_relative_path"]
        return NS(ordinal=document["ordinal"], state="accepted" if relative is None else self.execution_state,
                  attempt_path=self.root / relative if relative else None)

    def reconcile_active_turn(self):
        if self.execution_state == "terminal":
            document = dict(self.records[-1][-1][1])
            document.update(state="committed", answer={"reply": "registered", "protocol_valid": True, "task": None})
            self._publish(document, "commit")
            return NS(state="committed")
        return NS(state=self.execution_state)

    def fail_active_turn(self):
        document = dict(self.records[-1][-1][1])
        document["state"] = "failed"
        self._publish(document, "fail")

    def rollback_paused_turn(self):
        document = dict(self.records[-1][-1][1])
        document["state"] = "interrupted"
        self._publish(document, "rollback")

    def set_execution_config(self, path):
        if self.active_turn_snapshot() is not None:
            raise ValueError("cannot change an active turn")
        self.execution_config_path = path


class ScriptedControl:
    def __init__(self, main):
        self.main = main
        self.handles, self.starts, self.stops, self.resumes = {}, 0, 0, 0
        self.returncode = None
        self.ready = True
        self.checkpoint_on_stop = False

    def list(self):
        return [{"task_id": key, "run_dir": str(h.run_dir)} for key, h in self.handles.items()]

    def get(self, key):
        return self.handles[key]

    def start(self, spec):
        self.starts += 1
        identity = "task-" + str(self.starts)
        handle = NS(task_id=identity, run_dir=spec.run_dir, spec=spec, kind="single_agent",
                    process=NS(poll=lambda: self.returncode))
        self.handles[identity] = handle
        self.main.execution_state = "running"
        return handle

    def stop(self, key, *, startup_safe=False):
        if not self.ready:
            return {"status": "OWNER_CHANNEL_NOT_READY"}
        self.stops += 1
        if self.checkpoint_on_stop:
            self.main.execution_state = "stopped_by_owner"
        return {"status": "STOP_REQUESTED"}

    def resume(self, key):
        self.resumes += 1
        self.main.execution_state = "running"
        return {"status": "RESUME_STARTED"}


@pytest.fixture
def boundary(tmp_path):
    execution = tmp_path / "execution.json"
    execution_two = tmp_path / "execution-two.json"
    main = ScriptedMain(tmp_path / "session", execution)
    main.root.mkdir()
    control = ScriptedControl(main)
    main.task_control = control
    app = RegistryFrontendApplication.__new__(RegistryFrontendApplication)
    app.root, app.execution = tmp_path, execution
    profile = NS(selection_id="one", provider="test", provider_display_name="Test",
                 model_condition="exact", required_environment=(), path=execution)
    profile_two = NS(selection_id="two", provider="test-two", provider_display_name="Test Two",
                     model_condition="exact-two", required_environment=(), path=execution_two)
    app.profiles = (profile, profile_two)
    app.default_selection = profile.selection_id
    app._profiles_by_selection = {p.selection_id: p for p in app.profiles}
    app._profiles_by_path = {p.path: p for p in app.profiles}
    identities = {
        profile.selection_id: {"schema_version": "rpnh/main_session_profile/v2",
                               "execution_config_path": str(execution), "policy": "pinned"},
        profile_two.selection_id: {"schema_version": "rpnh/main_session_profile/v2",
                                   "execution_config_path": str(execution_two), "policy": "pinned-two"},
    }
    app._initial_identities = copy.deepcopy(identities)
    app._execution_identity = lambda path: dict(
        identities[app._profiles_by_path[path].selection_id])
    app._main_session_type = NS(
        _persisted_execution_profile=lambda _: dict(
            identities[app._profiles_by_path[main.execution_config_path].selection_id]))
    app._sessions = {SID: _Session(main, control)}
    app._lock = None
    return app, main, control, identities["one"]


def test_real_submit_method_persists_identity_before_start_and_deduplicates(boundary):
    app, main, control, _ = boundary
    assert app.submit(SID, "request", "explicit:one") == 1
    assert len(main.records) == 1 and control.starts == 1
    assert app._turns(app._sessions[SID])[0]["key"] == "explicit:one"
    assert app.submit(SID, "request", "explicit:one") == 1
    assert control.starts == 1
    main.execution_state = "terminal"
    app.tick()
    assert app.snapshot()[0]["turns"][0]["state"] == "committed"
    assert app.submit(SID, "request", "explicit:one") == 1 and control.starts == 1
    assert app.submit(SID, "request", "explicit:two") == 2 and control.starts == 2


def test_actual_submission_conflict_and_model_drift(boundary):
    app, main, control, identity = boundary
    app.submit(SID, "request", "explicit:one")
    with pytest.raises(FrontendError, match="different input"):
        app.submit(SID, "different", "explicit:one")
    identity["policy"] = "changed"
    with pytest.raises(FrontendError, match="drift"):
        app.submit(SID, "request", "explicit:one")
    assert control.starts == 1 and len(main.records) == 1


def test_agent_launch_does_not_replay_a_parent_relative_registry_link(
        boundary,
) -> None:
    app, main, control, _identity = boundary
    main.child_path_root = main.root / "main"
    main.child_path_root.mkdir()
    key = "registered-agent-request"
    main.child_links.append({
        "registry_relative_path": (
            "tasks/runs/frontend-agent-" + stable_id(key)),
    })

    with pytest.raises(FrontendError, match="lacks its launch handle"):
        app.launch_agent(SID, "do not replay", key)

    assert control.starts == 0


def test_profile_switch_is_per_turn_and_read_only_commands_do_not_switch(boundary):
    app, main, control, _ = boundary
    app.submit(SID, "first", "explicit:first")
    main.execution_state = "terminal"
    app.tick()
    app.command(SID, "rpnh-help", "", "read", selection_id="two")
    assert main.execution_config_path == app._profiles_by_selection["one"].path
    app.submit(SID, "second", "explicit:second", selection_id="two")
    assert main.execution_config_path == app._profiles_by_selection["two"].path
    turns = app.snapshot()[0]["turns"]
    assert [turn["model"]["selection"] for turn in turns] == ["one", "two"]
    assert control.starts == 2


def test_dead_process_is_not_success(boundary):
    app, main, control, _ = boundary
    app.submit(SID, "request", "explicit:one")
    control.returncode = 0
    app.tick()
    turn = app.snapshot()[0]["turns"][0]
    assert turn["state"] == "failed" and turn["answer"] is None


def test_exact_abort_is_coalesced_until_checkpoint_and_resume(boundary):
    app, main, control, _ = boundary
    app.submit(SID, "request", "explicit:one")
    assert app.abort(SID) and app.abort(SID)
    assert control.stops == 1
    assert main.execution_state == "running"  # signal acceptance is not a checkpoint
    main.execution_state = "stopped_by_owner"
    assert app.abort(SID) and control.stops == 1
    assert app.command(SID, "rpnh-resume", "", "ignored")["status"] == "RESUME_STARTED"
    assert control.resumes == 1
    app.abort(SID)
    assert control.stops == 2


def test_not_ready_abort_does_not_send_early_sigint(boundary):
    app, _, control, _ = boundary
    app.submit(SID, "request", "explicit:one")
    control.ready = False
    with pytest.raises(FrontendError, match="not ready"):
        app.abort(SID)
    assert control.stops == 0


def test_shutdown_checkpoints_main_turn_without_stopping_child_registry(boundary):
    app, main, control, _ = boundary
    child_control = NS(stop=lambda *_a, **_kw: pytest.fail("child stop must not be called"))
    main.task_control = child_control
    app.submit(SID, "request", "explicit:one")
    control.checkpoint_on_stop = True
    app.shutdown(timeout=0.5)
    assert control.stops == 1
    assert main.execution_state == "stopped_by_owner"


def test_unknown_populated_attempt_is_not_replayed(boundary):
    app, main, control, _ = boundary
    app.submit(SID, "request", "explicit:one")
    main.execution_state = "pending_start"
    path = next(iter(control.handles.values())).run_dir
    path.mkdir(parents=True)
    (path / "unreconciled").write_text("unknown")
    control.handles.clear()
    with pytest.raises(FrontendError, match="unowned"):
        app.submit(SID, "request", "explicit:one")
    assert control.starts == 1


def test_rollback_retains_attempt_identity(boundary):
    app, main, _, _ = boundary
    app.submit(SID, "request", "explicit:one")
    path = app.snapshot()[0]["turns"][0]["attempt"]
    main.execution_state = "stopped_by_owner"
    app.command(SID, "rpnh-rollback", "", "ignored")
    turn = app.snapshot()[0]["turns"][0]
    assert turn["state"] == "interrupted" and turn["attempt"] == path


@pytest.mark.parametrize("flag,resource,only", [("", False, False), ("--show-resources", True, False), ("--resources-only", False, True)])
def test_net_calls_existing_projection_and_filter(boundary, monkeypatch, flag, resource, only):
    app, main, _, _ = boundary
    app.submit(SID, "request", "explicit:one")
    calls = []
    inspection = types.ModuleType("cpn.rpnh.inspection")
    inspection.project_registry_net = lambda path, catalog: (calls.append((path, catalog)) or {"real": "projection-double"})
    agents = types.ModuleType("cpn.rpnh.agent_tasks")
    agents.agent_task_catalog = lambda: "catalog-double"
    cli = types.ModuleType("cpn.cli")
    cli._filter_projection = lambda net, **kwargs: {"net": net, **kwargs}
    for module in (inspection, agents, cli):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    result = app.command(SID, "rpnh-net", flag, "ignored")
    assert result["show_resources"] is resource and result["resources_only"] is only
    assert calls == [(main.root / "attempts/1", "catalog-double")]


def test_status_redacts_paths_and_private_errors():
    value = RegistryFrontendApplication._status({"task_id": "task-1", "run_dir": "/private", "log_path": "/private/log",
        "control_error": "secret", "registry": {"execution_status": "terminal", "api_key": "secret"}})
    assert value == {"task_id": "task-1", "registry": {"execution_status": "terminal"}}
