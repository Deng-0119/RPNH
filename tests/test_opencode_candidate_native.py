"""Opt-in LIMITED native smoke lanes; neither test is full G2/G3 certification.

This module has no import-time native, Registry, socket or provider activity.
The ordinary pinned test_opencode_pty.py is intentionally unchanged.
"""
from __future__ import annotations

import json
import os

import pytest

from opencode_candidate_support import (
    EffectGuard, NativePTY, assert_routes, close_server, json_digest,
    observed_bootstrap, sha256_file,
)


@pytest.mark.opencode_candidate("contract")
def test_candidate_contract_smoke(opencode_candidate):
    """G2 subset: stock bootstrap, help, one prompt, fixture-committed reply."""
    from cpn.frontend.opencode_http import OpenCodeHTTPServer
    from cpn.frontend.opencode_protocol import OpenCodeProtocol
    from test_opencode_frontend import ApplicationDouble, LocalGateway, SID

    run = opencode_candidate
    app = ApplicationDouble()
    app.create_session()
    protocol = OpenCodeProtocol(LocalGateway(app), str(run.display), profile=run.profile)
    server = OpenCodeHTTPServer(protocol)
    committed = False
    answer = "candidate contract answer"
    run.evidence["stage"] = "contract-attach"
    try:
        server.start()
        with NativePTY(run, server, SID) as tui:
            tui.wait(lambda: observed_bootstrap(server), check=lambda: assert_routes(server))
            tui.send_line("/rpnh-help", slash=True, check=lambda: assert_routes(server))
            tui.wait(lambda: any(d["method"] == "POST" and d["route"] == "/session/:session/command"
                                 and d["status"] == 200 for d in server.diagnostics()),
                     check=lambda: assert_routes(server))
            tui.wait(lambda: tui.contains("observation"), check=lambda: assert_routes(server))
            tui.send_line("candidate offline prompt", check=lambda: assert_routes(server))
            tui.wait(lambda: app.calls.count("submit") == 1 and bool(app.views[0]["turns"]),
                     check=lambda: assert_routes(server))
            app.commit(answer)
            committed = True
            protocol.notify()
            tui.wait(lambda: tui.contains(answer), check=lambda: assert_routes(server))
            assert app.calls.count("submit") == 1
            assert app.physical_calls == 0
            assert len(app.views[0]["turns"]) == 1
            assert app.views[0]["turns"][0]["state"] == "committed"
            messages = protocol.route("GET", f"/session/{SID}/message").body
            durable = [m for m in messages if m["info"].get("mode") != "rpnh-observation"]
            assert [m["info"]["role"] for m in durable] == ["user", "assistant"]
            assert durable[-1]["parts"][0]["text"] == answer
            run.evidence["scenarios"] = [
                "stock bootstrap routes", "stock /rpnh-help observation",
                "one stock ordinary prompt / one double submit",
                "fixture-committed answer observed in bounded terminal output",
            ]
        assert_routes(server)
    finally:
        run.evidence["counters"] = {"double_submit_calls": app.calls.count("submit"),
            "double_physical_calls": app.physical_calls, "fixture_commit": committed}
        try:
            close_server(run, server)
        finally:
            app.close()
    run.passed()


def _prepare_committed_registry(run, monkeypatch):
    """Canonical original setup, explicitly outside the zero-effect interval."""
    from cpn.rpnh.agent_tasks import run_agent_task
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh.session_access import MainSessionOwnerLease, stable_frontend_session_id
    from test_main_session_registry import _TerminalPort, _write_execution_profile

    class CountingPort(_TerminalPort):
        calls = 0

        def request_once(self, attempt):
            self.calls += 1
            return super().request_once(attempt)

    # Isolate the Python fixture owner as well as the separately spawned TUI.
    # In particular, do not discover the operator's RPNH profiles/plugins.
    for name in list(os.environ):
        if name not in run.env and name != "PYTEST_CURRENT_TEST":
            monkeypatch.delenv(name, raising=False)
    for name, value in run.env.items():
        monkeypatch.setenv(name, value)
    execution = _write_execution_profile(run.root)
    root = run.root / "synthetic-registry"
    port = CountingPort({"reply": "candidate first answer", "task": None})
    run.evidence["stage"] = "synthetic-fixture-setup"
    # This is an original fixture fake port, not an installed provider or model.
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *_a, **_kw: port)
    attempts = []
    lease = MainSessionOwnerLease.reserve_for_creation(root)
    try:
        basic = MainSession(root, execution, owner_root_reserved=True)
        for prompt, reply in (("candidate first question", "candidate first answer"),
                              ("candidate second question", "candidate second answer")):
            port.output = {"reply": reply, "task": None}
            spec = basic.prepare_turn(prompt)
            attempts.append(spec.run_dir)
            run_agent_task(spec)
            assert basic.reconcile_active_turn().state == "committed"
        assert port.calls == 2
        assert basic.active_turn_snapshot() is None
        assert len(basic._main_thread.recover_thread()["committed_history"]) == 2
    finally:
        lease.close()
        run.evidence["setup"] = {"fake_port_calls": port.calls,
            "provider_transport": "deterministic _TerminalPort only",
            "setup_owner_released": lease._fd is None}
    return basic, root, execution, stable_frontend_session_id(root), attempts, port


def _install_measured_guards(monkeypatch, port):
    """Deny effects only; leave original readers, snapshots, tick and PN intact."""
    from cpn.rpnh.frontend_application import RegistryFrontendApplication
    from cpn.rpnh.main_session import MainSession
    from cpn.rpnh.task_control import TaskControl

    guard = EffectGuard()
    monkeypatch.setattr("cpn.llm_adapters.build_llm_input_port", guard.deny("provider_factory"))
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", guard.deny("agent_provider_factory"))
    monkeypatch.setattr("cpn.rpnh.main_session.run_agent_task", guard.deny("run_agent_task"))
    monkeypatch.setattr("cpn.rpnh.main_session.resume_agent_task", guard.deny("resume_agent_task"))
    monkeypatch.setattr(port, "request_once", guard.deny("fake_port"))
    for name in ("_spawn", "start", "resume", "reopen", "stop", "message"):
        monkeypatch.setattr(TaskControl, name, guard.deny("task_control_" + name))
    for name in ("prepare_turn", "set_execution_config", "reconcile_active_turn",
                 "reconcile_committed_launches", "reconcile_child_registry_links",
                 "activate_for_execution", "rollback_paused_turn", "launch"):
        monkeypatch.setattr(MainSession, name, guard.deny("main_session_" + name))
    for name in ("submit", "create_session", "abort", "launch_agent"):
        monkeypatch.setattr(RegistryFrontendApplication, name, guard.deny("application_" + name))
    original_tick = RegistryFrontendApplication.tick

    def counted_tick(self):
        guard.observations["original_tick_calls"] += 1
        return original_tick(self)

    monkeypatch.setattr(RegistryFrontendApplication, "tick", counted_tick)
    original_command = RegistryFrontendApplication.command

    def checked_command(self, sid, name, *args, **kwargs):
        # /rpnh-tasks reconciles child links and is intentionally excluded.
        if name not in {"rpnh-help", "rpnh-net"}:
            return guard.deny("command_" + name)()
        return original_command(self, sid, name, *args, **kwargs)

    monkeypatch.setattr(RegistryFrontendApplication, "command", checked_command)
    return guard


def _logical_registry(basic, attempts, execution):
    """Original authority reader + logical ordinals; no whole-directory hash."""
    from cpn.rpnh.registry._registry import _RegistryCore
    projection = basic._main_thread.recover_thread()
    profile = basic._persisted_execution_profile(basic.root)
    revision = {"main": basic._registry_core.event_store.max_ordinal()}
    for index, attempt in enumerate(attempts, 1):
        core = _RegistryCore(attempt, create=False, read_only=True)
        revision["turn-" + str(index)] = core.event_store.max_ordinal()
    return {"logical_revisions": revision,
            "thread_projection_sha256": json_digest(projection),
            "committed_history": projection["committed_history"],
            "persisted_execution_identity": profile,
            "execution_config_sha256": sha256_file(execution),
            "adapter_config_sha256": sha256_file(execution.parent / "adapter.json")}


def _observation_payload(message):
    part = next(part for part in message["parts"] if part["type"] == "text")
    assert part["metadata"]["rpnh_observation"] == "rpnh-net"
    _notice, text = part["text"].split("\n", 1)
    return json.loads(text)


@pytest.mark.opencode_candidate("registry-read")
def test_candidate_registry_read_smoke(opencode_candidate, monkeypatch):
    """G3 subset: two committed turns, two cold attaches and exact net views."""
    from cpn.cli import _filter_projection
    from cpn.frontend.opencode_http import OpenCodeHTTPServer
    from cpn.frontend.opencode_protocol import OpenCodeProtocol
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.frontend_application import FrontendGateway, RegistryFrontendApplication
    from cpn.rpnh.inspection import project_registry_net
    from cpn.rpnh.session_access import MainSessionOwnerLease

    run = opencode_candidate
    basic, root, execution, sid, attempts, port = _prepare_committed_registry(run, monkeypatch)
    expected = {flag: _filter_projection(project_registry_net(attempts[-1], catalog=agent_task_catalog()),
                show_resources=show, resources_only=only, node_id=None)
                for flag, show, only in (("", False, False), ("--show-resources", True, False),
                                         ("--resources-only", False, True))}
    baseline = _logical_registry(basic, attempts, execution)
    run.evidence["registry_before"] = baseline
    guard = _install_measured_guards(monkeypatch, port)
    run.evidence["stage"] = "registry-read-measurement"
    snapshots = []
    messages_by_attach = []
    owner_results = []
    try:
        for continue_last in (False, True):
            holder = []
            gateway = FrontendGateway(
                lambda: RegistryFrontendApplication(root, execution, resume=True),
                on_change=lambda: holder[0].notify() if holder else None)
            server = None
            try:
                # Check an actual conflicting lease without constructing a second
                # Application or hiding the original owner's lease behavior.
                with pytest.raises(RuntimeError, match="owner lease"):
                    with MainSessionOwnerLease.acquire(root):
                        pass
                protocol = OpenCodeProtocol(gateway, str(run.display), profile=run.profile)
                holder.append(protocol)
                snapshots.append(gateway.call("snapshot"))
                assert all(t["state"] == "committed" for t in snapshots[-1][0]["turns"])
                assert [t["text"] for t in snapshots[-1][0]["turns"]] == [
                    "candidate first question", "candidate second question"]
                server = OpenCodeHTTPServer(protocol)
                server.start()

                def check():
                    guard.assert_zero()
                    assert_routes(server, registry_read=True)

                with NativePTY(run, server, sid, continue_last=continue_last) as tui:
                    tui.wait(lambda: observed_bootstrap(server), check=check)
                    tui.wait(lambda: all(tui.contains(text) for text in (
                        "candidate first question", "candidate first answer",
                        "candidate second question", "candidate second answer")), check=check)
                    messages = protocol.route("GET", f"/session/{sid}/message").body
                    messages_by_attach.append(messages)
                    assert [m["info"]["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
                    assert [m["parts"][0]["text"] for m in messages] == [
                        "candidate first question", "candidate first answer",
                        "candidate second question", "candidate second answer"]
                    # One real stock slash command each variant; observe the
                    # original protocol's transient response, then compare exact
                    # PN data against the original inspection implementation.
                    if not continue_last:
                        for flag, expected_view in expected.items():
                            previous = len(protocol._observations.get(sid, ()))
                            tui.send_line("/rpnh-net" + (" " + flag if flag else ""), slash=True, check=check)
                            tui.wait(lambda: len(protocol._observations.get(sid, ())) == previous + 1, check=check)
                            observation = list(protocol._observations[sid])[-1]
                            assert _observation_payload(observation) == expected_view
                            tui.wait(lambda: tui.contains("command observation"), check=check)
                            assert _logical_registry(basic, attempts, execution) == baseline
                            run.evidence["scenarios"].append("stock /rpnh-net " + (flag or "default") + " exact projection")
                    check()
                    assert gateway.call("snapshot") == snapshots[-1]
                    assert _logical_registry(basic, attempts, execution) == baseline
            finally:
                try:
                    if server is not None:
                        close_server(run, server)
                finally:
                    gateway.close()
                assert not gateway._thread.is_alive()
            with MainSessionOwnerLease.acquire(root):
                owner_results.append({"mode": "--continue" if continue_last else "--session",
                                      "second_owner_rejected": True, "owner_released_after_close": True})
            assert _logical_registry(basic, attempts, execution) == baseline
        assert snapshots[0] == snapshots[1]
        assert messages_by_attach[0] == messages_by_attach[1]
        guard.assert_zero()
        assert port.calls == 2
        assert guard.observations["original_tick_calls"] > 0
        run.evidence["scenarios"] += ["two-turn --session cold attach",
            "two-turn --continue cold attach", "stable history IDs across restart",
            "logical Registry state and execution identity unchanged", "owner exclusion and release"]
    finally:
        run.evidence["registry_after"] = _logical_registry(basic, attempts, execution)
        run.evidence["measured"] = {"effect_boundary_calls": dict(guard.calls),
            "original_tick_calls": guard.observations["original_tick_calls"],
            "fake_port_calls": port.calls - 2, "owner_checks": owner_results,
            "lease_note": "normal lock and writer-epoch acquisition/release are not Registry logical revisions"}
    run.passed()
