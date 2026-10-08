"""Finite total result disclosure and retirement of historical pages."""
import json

import pytest

from cpn.components.agent_loop.compact import (
    TOOL_BATCH_BYTE_LIMIT, approximate_tokens, bound_tool_result_groups,
    build_replacement_history,
)
from cpn.components.agent_loop.managed_output import render_managed_output


def group(number, *, payload=None, call_id=None):
    action = {"entity_type": "agent_action/v3",
              "logical_id": f"agent_action:{number:032x}",
              "version_id": f"agent_action_version:{number:032x}"}
    receipt = {"resource_id": f"resource:{number:032x}",
               "resource_version_id": f"resource_version:{number:032x}"}
    body = render_managed_output(
        {"kind": "managed_native_plugin_result/v1", "output": payload or "x" * 20000,
         "terminal_receipt_ref": receipt}, action, reader_available=True, max_bytes=10000)
    call_id = call_id or str(number)
    return [{"role": "assistant", "content": "", "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": "synthetic", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(body)}]


def raw(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


CAPSULE = {"kind": "agent_context_fact_capsule", "schema_version": "agent_context_fact_capsule/v1"}


def archived_output(context, arguments):
    return {"id": "EARLY-ARCHIVED-NEEDLE", "body": "中" * 12000}


@pytest.mark.parametrize("count", [8, 32, 64])
def test_old_pages_stay_within_retained_budget_across_three_compactions(count):
    messages = [m for n in range(count) for m in group(n)]
    for _ in range(3):
        result = build_replacement_history(messages, "Continue from archive.",
            retained_history_token_limit=20000, fact_capsule=CAPSULE)
        messages = [entry["message"] for entry in result[2:]]
        assert approximate_tokens(raw(messages)) <= 20000
        assert len(messages) < count * 2


def test_pending_notification_has_one_bounded_exception_reused_ids_do_not_pin_old_pages():
    messages = [m for n in range(64) for m in group(n, call_id="reused")]
    result = build_replacement_history(messages, "Keep current notification.",
        retained_history_token_limit=1, fact_capsule=CAPSULE, pending_tool_call_ids={"reused"})
    kept = [entry["message"] for entry in result[2:]]
    assert len(kept) == 2 and json.loads(kept[1]["content"])["agent_action_ref"]["logical_id"].endswith(f"{63:032x}")
    assert len(raw(kept)) <= TOOL_BATCH_BYTE_LIMIT


@pytest.mark.parametrize("payload", ["x" * 20000, "中😀\\\"\n" * 5000])
def test_parallel_batch_total_includes_call_frames_escaped_unicode_and_locators(payload):
    individual = [group(n, payload=payload) for n in range(32)]
    assistant = dict(individual[0][0], tool_calls=[g[0]["tool_calls"][0] for g in individual])
    messages = [assistant, *(g[1] for g in individual)]
    bounded = bound_tool_result_groups(messages)
    assert len(raw(bounded)) <= TOOL_BATCH_BYTE_LIMIT
    assert bounded[0] == assistant
    assert [m["tool_call_id"] for m in bounded[1:]] == [str(n) for n in range(32)]
    assert all(json.loads(m["content"])["reader"] == "read_managed_output" for m in bounded[1:])
    assert any(json.loads(m["content"])["kind"] == "tool_result_reference/v1" for m in bounded[1:])


def test_minimum_already_executed_call_envelopes_fail_explicitly_without_dropping_calls():
    messages = group(1)
    messages[0]["tool_calls"][0]["function"]["arguments"] = json.dumps({"input": "a" * 41000})
    with pytest.raises(ValueError, match="minimum call/result envelopes"):
        bound_tool_result_groups(messages)
    assert len(messages) == 2 and "content" in messages[1]


def test_retiring_result_without_reader_fails_but_pending_notification_still_fits():
    messages = group(1, payload="small")
    page = json.loads(messages[1]["content"])
    messages[1]["content"] = json.dumps(render_managed_output(
        {"kind": "managed_native_plugin_result/v1", "output": "small",
         "terminal_receipt_ref": page["terminal_receipt_ref"]},
        page["agent_action_ref"], reader_available=False, max_bytes=1000))
    pending = build_replacement_history(messages, "Pending result.",
        retained_history_token_limit=1, fact_capsule=CAPSULE, pending_tool_call_ids={"1"})
    assert len(pending) == 4
    with pytest.raises(ValueError, match="without a callable reader"):
        build_replacement_history(messages, "Retirement would lose recovery.",
            retained_history_token_limit=1, fact_capsule=CAPSULE)


def test_archive_locator_counts_toward_tail_and_pending_hard_limit():
    archive = {"reader": "read_managed_output", "archive_loop_ref": {
        "entity_type": "agent_loop/v1", "logical_id": "agent_loop:" + "a" * 32,
        "version_id": "agent_loop_version:" + "b" * 32},
        "before_turn_sequence": 64, "offset": 0, "max_bytes": 10000}
    capsule = dict(CAPSULE, result_archive=archive)
    history = [m for n in range(64) for m in group(n)]
    for budget, pending in [(20000, set()), (1, {"63"})]:
        result = build_replacement_history(history, "Continue with exact archive.",
            retained_history_token_limit=budget, fact_capsule=capsule,
            pending_tool_call_ids=pending)
        retained = [entry["message"] for entry in result[2:]]
        used = len(raw(archive)) + len(raw(retained))
        assert used <= (40000 if pending else budget * 4)


def test_retired_group_does_not_need_to_fit_the_active_archive_reservation():
    old = group(1, payload="old")
    old[0]["tool_calls"][0]["function"]["arguments"] = json.dumps({"large_argument": "x" * 39200})
    assert len(raw(old)) < TOOL_BATCH_BYTE_LIMIT
    capsule = dict(CAPSULE, result_archive={"reader": "read_managed_output", "locator": "x" * 900})
    latest = group(2, payload="new")
    replacement = build_replacement_history(old + latest, "The old group is archived.",
        retained_history_token_limit=1, fact_capsule=capsule, pending_tool_call_ids={"2"})
    assert [e["message"] for e in replacement[2:]] == latest


def test_registered_archive_restores_retired_original_after_same_loop_rebuild(tmp_path, monkeypatch):
    from dataclasses import replace
    from test_managed_result_readback import _configure_pressure, _explicit_graph, _finish
    from test_optional_context_compaction import _response, _objects
    from cpn.components.agent_loop.compact import CONTEXT_CHECKPOINT_PROMPT
    from cpn.components.agent_loop.compaction import CompactionExecutionMixin
    from cpn.components.agent_loop.mechanical_lifecycle import AgentLoopMechanicalLifecycle
    from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
    from cpn.rpnh.agent_tasks import AgentTaskSpec, agent_task_catalog, run_agent_task
    from cpn.rpnh.registry._registry import _RegistryCore
    calls, rebuilds, requests = [], [], []
    selected = PluginCatalog((BoundPlugin(PluginDefinition("synthetic", "1", (
        PluginOperation("output", "Synthetic archived output", {"type": "object"}, {}, archived_output,
                        max_result_bytes=200000),)), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _: selected)
    def worker(handler, packet, **kwargs):
        calls.append(packet["context"]["call_id"])
        return handler(None, packet["arguments"])
    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    complete = CompactionExecutionMixin.complete_agent_context_compaction_v1
    def rebuild(self, *args, **kwargs):
        completed = complete(self, *args, **kwargs)
        fresh = AgentLoopMechanicalLifecycle(self.core, self.kernel)
        restored = fresh.hydrate_loop(fresh.loop_ref(completed.waiting_loop))
        self.mechanical_lifecycle = fresh
        rebuilds.append(restored.loop_id)
        return replace(completed, waiting_loop=restored)
    monkeypatch.setattr(CompactionExecutionMixin, "complete_agent_context_compaction_v1", rebuild)
    class Provider:
        normal = 0
        def request_once(self, attempt):
            envelope = json.loads(attempt.canonical_request_bytes)
            requests.append(envelope)
            if envelope["messages"][-1].get("content") == CONTEXT_CHECKPOINT_PROMPT:
                return _response(text="Continue via the registered archive.", finish_reason="stop")
            self.normal += 1
            def tool(call_id, args):
                return _response(tool_calls=[{"id": call_id, "name": "read_managed_output",
                                               "arguments": json.dumps(args)}], finish_reason="tool_calls")
            if self.normal == 1:
                return _response(tool_calls=[{"id": "original", "name": "synthetic", "arguments": "{}"}], finish_reason="tool_calls")
            if self.normal == 2:
                capsule = next(json.loads(m["content"]) for m in envelope["messages"]
                               if m.get("role") == "user" and m.get("content", "").startswith('{"adopted_candidate_ref"'))
                archive = capsule["result_archive"]
                assert archive["reader"] == "read_managed_output"
                return tool("archive", {k: v for k, v in archive.items() if k != "reader"})
            results = {m["tool_call_id"]: json.loads(m["content"])
                       for m in envelope["messages"] if m.get("role") == "tool"}
            assert "original" not in results
            if self.normal == 3:
                directory = results["archive"]
                assert directory["kind"] == "tool_result_archive_page/v1"
                entry, = directory["entries"]
                assert entry["tool_call_id"] == "original" and entry["status"] == "returned"
                return tool("restored", {"agent_action_ref": entry["agent_action_ref"],
                    "terminal_receipt_ref": entry["terminal_receipt_ref"], "offset_chars": 72002, "max_bytes": 10000})
            assert self.normal == 4
            assert "EARLY-ARCHIVED-NEEDLE" in results["restored"]["content"]
            return _finish()
        def close(self):
            pass
    port = Provider()
    config = _configure_pressure(tmp_path, monkeypatch, port)
    result = run_agent_task(AgentTaskSpec(tmp_path / "run", "Recover archived evidence.", (), config,
        workflow_graph=_explicit_graph(True), max_attempts_per_stage=8,
        plugin_configuration={}, plugin_catalog_digest=selected.digest,
        managed_bindings={"main": {"tools": {"synthetic": {"selector": "synthetic/output"}}}}))
    (tmp_path / "requests.json").write_text(json.dumps(requests, indent=2))
    assert result["stop_reason"] == "terminal" and calls == ["original"]
    assert len(rebuilds) >= 3 and len(set(rebuilds)) == 1
    core = _RegistryCore(tmp_path / "run", create=False, read_only=True, catalog=agent_task_catalog())
    originals = _objects(core, "agent_action/v3")
    assert len(originals) == 1 and originals[0]["output"]["id"] == "EARLY-ARCHIVED-NEEDLE"
