"""Owner broker integration with real Registry receipts and a mocked runner.

Physical namespace isolation is W6's test boundary. Test HOST wiring calls the
existing mechanical single-parent settlement with actual broker results.
"""
from dataclasses import asdict, replace
import json
from threading import get_ident
import time
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.program_execution import (
    CALL_TYPE, PROGRAM_TYPE, ProgramExecutionMixin,
    program_execution_schema_data, validate_program_policy,
)
from cpn.plugins import BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation
from cpn.plugins.api import PluginError, json_copy
from cpn.plugins.controlled_script import (
    IsolatedProgramBudget, IsolatedProgramResult, ProgramBrokerCall, ProgramBrokerObservation,
)
from cpn.plugins.managed_scheduler import ManagedToolCall, ManagedToolScheduler
from cpn.plugins.managed_tools import (
    ManagedPluginInvocationConflict, ManagedPluginInvocationReconciliationRequired,
)
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload


INPUT = {"type": "object", "additionalProperties": False,
         "properties": {"value": {"type": "integer"}}, "required": ["value"]}


def synthetic(context, arguments):
    return {"createdUniqueId": "PROGRAM-CREATED-ID-" + str(arguments["value"]),
            "value": arguments["value"] * 2, "middle": "中\\\n" * 200}


def _policy(**changes):
    budget = asdict(IsolatedProgramBudget(max_frame_bytes=1400))
    budget.update(changes)
    return {"profile_id": "linux_isolated_python/v1", "tools": ["double_value"], "budget": budget}


def _graph():
    from cpn.rpnh.agent_workflows import (
        AgentWorkflowEndpoint as E, AgentWorkflowExecution,
        AgentWorkflowGraph, AgentWorkflowNode as N, AgentWorkflowPort as P,
    )
    return AgentWorkflowGraph((N("main", "Run a synthetic registered tool program.",
        (P("request", "task"),), (P("result", "result"),),
        AgentWorkflowExecution(tools=("complete_interaction", "read_tool_program_output",
                                     "run_tool_program", "write_file"))),),
        (), E("main", "request"), E("main", "result"))


class RecordingProvider:
    def __init__(self):
        self.requests = []

    def request_once(self, attempt):
        from test_optional_context_compaction import _response
        request = json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        assert "run_tool_program" in [t["function"]["name"] for t in request["tools"]]
        if len(self.requests) == 1:
            return _response(tool_calls=[{"id": "original-program", "name": "run_tool_program",
                "arguments": json.dumps({"source": "result({'synthetic': True})", "arguments": {}})}],
                finish_reason="tool_calls")
        results = [json.loads(m["content"]) for m in request["messages"] if m.get("role") == "tool"]
        original = next(r for r in results if r["kind"] == "tool_program_result/v1")
        if len(self.requests) == 2:
            return _response(tool_calls=[{"id": "read-program", "name": "read_tool_program_output",
                "arguments": json.dumps({"agent_action_ref": original["agent_action_ref"],
                                          "output_resource_ref": original["output_resource_ref"]})}],
                finish_reason="tool_calls")
        page = next(r for r in results if r["kind"] == "tool_program_output_page/v1")
        assert page["source_status"] == original["status"]
        assert "PROGRAM-CREATED-ID" in page["content"] or original["call_count"] == 0
        return _response(tool_calls=[{"id": "write-program-result", "name": "write_file",
            "arguments": json.dumps({"path": "result.json", "description": "Synthetic program observation",
                                      "content": "done", "output_port_id": "team.result", "outcome_id": "complete"})},
            {"id": "complete-program-result", "name": "complete_interaction", "arguments": "{}"}],
            finish_reason="tool_calls")

    def close(self):
        pass


def _exercise(tmp_path, monkeypatch, scenario, *, policy=None, effect="pure", worker_behavior=None):
    """Supply the still C-owned wiring in an explicit test HOST registration."""
    from cpn.components.agent_loop import optional_execution
    from cpn.components.agent_loop.service import AgentLoopService
    from cpn.rpnh import agent_tasks
    from test_optional_context_compaction import _configure_offline_task
    policy = policy or _policy()
    selected = PluginCatalog((BoundPlugin(PluginDefinition("programsynthetic", "1", (
        PluginOperation("double", "Synthetic result", INPUT, {}, synthetic,
                        effect=effect, max_result_bytes=65536),)), {}),))
    monkeypatch.setattr("cpn.plugins.catalog.load_catalog", lambda _: selected)
    _documents, registered_types = agent_tasks.optional_agent_loop_schema_data()
    assert {PROGRAM_TYPE, CALL_TYPE}.issubset({t.name for t in registered_types})
    captured, writes, workers = {}, [], []
    base_service = optional_execution.OptionalAgentLoopRegistryService

    assert issubclass(base_service, ProgramExecutionMixin)

    class TestProgramGateway(base_service):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured["backend"] = self
            original_begin = self.core.begin

            def begin(*args, **kwargs):
                writes.append(get_ident())
                return original_begin(*args, **kwargs)
            self.core.begin = begin

        def gateway_methods(self):
            methods = super().gateway_methods()
            methods.update({name: getattr(self, name) for name in (
                "begin_tool_program_v1", "prepare_program_child_v1", "finish_program_child_v1",
                "complete_tool_program_v1", "read_program_child_output_v1", "reject_program_child_v1",
                "settle_program_fixture_parent", "read_program_fixture_parent")})
            return methods

        def settle_program_fixture_parent(self, execution, loop, turn, prepared, completed, key):
            from cpn.components.agent_loop.models import AgentActionRecord, AgentLoopState
            self._execution(execution, loop)
            validation, call = prepared.validation, prepared.tool_call
            record = AgentActionRecord(
                validation.action_id, loop.loop_id, turn.sequence, call.tool_call_ordinal,
                call.tool_call_id, call.action_identity_kind, call.action_identity_key,
                call.tool_name, call.raw_arguments, validation.arguments, loop.revision,
                AgentLoopState.ACTION_APPLIED, completed.result_refs, None, completed.metadata)
            return self.mechanical_lifecycle.settle_action_batch(
                loop=loop, turn_ref=self.mechanical_lifecycle.turn_ref_for(loop.loop_id, turn.sequence),
                turn_id=turn.turn_id, records=(record,), error_documents={},
                written_resource_refs=loop.written_resource_refs,
                final_state=AgentLoopState.WAITING_FOR_LLM, idempotency_key=key)

        def read_program_fixture_parent(self, execution, loop, arguments, *, sequence=None):
            return self._read_tool_program_output(execution, loop,
                SimpleNamespace(sequence=loop.next_turn_sequence if sequence is None else sequence), arguments, "fixture-read")

    monkeypatch.setattr(optional_execution, "OptionalAgentLoopRegistryService", TestProgramGateway)

    def worker(handler, packet, **kwargs):
        workers.append(packet["context"]["call_id"])
        assert get_ident() != captured["owner_thread"]
        if worker_behavior is not None:
            return worker_behavior(handler, packet)
        return handler(None, packet["arguments"])

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", worker)
    original_settle = AgentLoopService._settle_stored_turn

    def intercept(driver, execution, loop, turn, *, idempotency_key):
        prepared = driver._registry.prepare_agent_turn_actions_v1(loop, turn)
        if prepared[0].tool_call.tool_name != "run_tool_program":
            return original_settle(driver, execution, loop, turn, idempotency_key=idempotency_key)
        captured["owner_thread"] = pytest_thread
        captured.update(execution=execution, loop=loop, turn=turn,
                        gateway=driver._registry, settlement_key=idempotency_key + ":actions",
                        prepared_parent=prepared[0], policy=policy)
        captured["program"] = driver._registry.begin_tool_program_v1(
            execution, loop, turn, prepared[0], policy, captured["settlement_key"])
        scenario(captured)
        captured["finished"] = True
        completed = captured["completed"]
        settled, records = driver._registry.settle_program_fixture_parent(
            execution, loop, turn, prepared[0], completed, captured["settlement_key"])
        captured["settled_loop"] = settled
        locator = {"agent_action_ref": completed.metadata["agent_action_ref"],
                   "output_resource_ref": completed.metadata["output_resource_ref"]}
        with pytest.raises(ValueError):
            driver._registry.read_program_fixture_parent(execution, settled, locator, sequence=turn.sequence)
        with pytest.raises(ValueError):
            driver._registry.read_program_fixture_parent(execution, settled, dict(locator, offset_chars=10**9))
        with pytest.raises(ValueError, match="minimum locator"):
            driver._registry.read_program_fixture_parent(execution, settled, dict(locator, max_bytes=128))
        if completed.block_authority is not None:
            _refs, page = driver._registry.read_program_fixture_parent(execution, settled, {
                "agent_action_ref": completed.metadata["agent_action_ref"],
                "output_resource_ref": completed.metadata["output_resource_ref"]})
            assert page["source_status"] == "outcome_unknown"
            captured["partial_page"] = page
            from cpn.components.agent_loop.service import _LLMExecutionBlock
            raise _LLMExecutionBlock(completed.block_authority, settled)
        return settled, records

    monkeypatch.setattr(AgentLoopService, "_settle_stored_turn", intercept)
    port = RecordingProvider()
    config = _configure_offline_task(tmp_path, monkeypatch, port)
    pytest_thread = get_ident()
    managed_policy = {"policy_id": "managed_pure_parallel/v1", "max_in_flight": 2}
    if effect != "pure":
        from cpn.plugins.managed_tools import ManagedPluginToolCatalog
        catalog = ManagedPluginToolCatalog(selected, {"double_value": "programsynthetic/double"}, admitted_effects=[effect])
        managed_policy = {"policy_id": "managed_conflict_domains/v1", "max_in_flight": 2,
            "conflict_domains": [{"registration_key": catalog.declaration("double_value").registration_key,
                                  "effect": effect, "reads": [], "writes": ["synthetic-domain"], "unknown": False}]}
    result = agent_tasks.run_agent_task(agent_tasks.AgentTaskSpec(
        tmp_path / "run", "Use only synthetic managed tools.", (), config,
        workflow_graph=_graph(), max_attempts_per_stage=4,
        plugin_configuration={}, plugin_catalog_digest=selected.digest,
        managed_tool_policy=managed_policy, tool_program_policy=policy,
        managed_bindings={"main": {"tools": {"double_value": {"selector": "programsynthetic/double"}},
                                   "admitted_effects": [effect]}}))
    assert captured.get("finished"), result
    assert len(port.requests) == (1 if captured["completed"].block_authority else 3)
    assert writes and set(writes) == {pytest_thread}
    parents = [json.loads(r["metadata_json"]) for r in captured["backend"].core.event_store.object_rows_by_type("agent_action/v2")
               if json.loads(r["metadata_json"])["tool_name"] == "run_tool_program"]
    assert len(parents) == 1 and parents[0]["state"] == "ACTION_APPLIED"
    assert not captured["backend"].core.event_store.object_rows_by_type("agent_action/v3")
    captured.update(workers=workers, provider=port, task_result=result)
    (tmp_path / "actual_fake_provider_requests.json").write_text(json.dumps(port.requests, indent=2))
    return captured


def _call(context, key, value=3):
    return ProgramBrokerCall(context["program"].parent_identity, key, "double_value",
                             {"value": value}, time.monotonic() + 30, lambda: False)


def _dispatch(context, call):
    """Existing W6 shared scheduler, no owner-thread wait or second queue."""
    gateway, backend = context["gateway"], context["backend"]
    prepared = {}

    def prepare(scheduled):
        child = gateway.prepare_program_child_v1(context["execution"], context["loop"], context["turn"],
                                                 context["program"].program_ref, call)
        prepared["child"] = child
        return child.managed_prepared

    def finish(managed, completion):
        return gateway.finish_program_child_v1(context["execution"], context["loop"], context["turn"],
                                               context["program"].program_ref, call, prepared["child"], completion)

    declaration = backend.managed_plugin_services["main"].catalog.declaration(call.tool)
    batch = ManagedToolScheduler(backend.managed_scheduler_policy, backend.managed_run_capacity).run(
        (ManagedToolCall(0, call.tool, f"tool-program-child/v1:{context['program'].program_ref.entity_id}:{call.key}",
                         call.arguments, declaration.registration_key),),
        operation_key=str(context["execution"].operation_execution_lease_ref.version_id),
        prepare=prepare, finish=finish, cancelled=lambda: False)
    assert batch.outcomes[0].error is None
    return prepared["child"], batch.outcomes[0].result


def _complete(context, observations, aggregate=None, status="returned"):
    result = IsolatedProgramResult(status, 0 if status == "returned" else 1, "", "", tuple(observations), None,
                                   {"profile_id": "linux_isolated_python/v1", "test_execution": "mocked-runner"}, aggregate)
    completed = context["gateway"].complete_tool_program_v1(context["execution"], context["loop"], context["turn"],
        context["program"].program_ref, result, settlement_key=context["settlement_key"])
    context["completed"] = completed
    return completed


def test_real_owner_receipts_large_reply_paging_and_complete_provenance(tmp_path, monkeypatch):
    def scenario(c):
        call = _call(c, "logical-result")
        child, reply = _dispatch(c, call)
        assert reply.value["kind"] == "agent_tool_program_child_output_page/v1"
        assert len(json.dumps({"type": "reply", "key": call.key, "ok": True, "value": json_copy(reply.value)},
                              ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()) <= 1400
        offset, pieces = 0, []
        while True:
            page = c["gateway"].read_program_child_output_v1(c["execution"], c["loop"], c["turn"],
                c["program"].program_ref, _version_from_payload(reply.value["program_call_ref"]),
                offset_chars=offset, max_bytes=1400)
            pieces.append(page["content"])
            if page["next_offset_chars"] is None:
                break
            offset = page["next_offset_chars"]
        assert json.loads("".join(pieces))["value"] == 6
        replay = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call)
        assert replay.managed_prepared is None and replay.closed_reply == reply
        done = _complete(c, [ProgramBrokerObservation(call, reply)], {"summary": "filtered aggregate"})
        assert done.metadata["call_count"] == 1 and done.metadata["status"] == "returned"
        assert done.metadata["agent_action_ref"] == c["program"].parent_identity["agent_action_ref"]
        resource = _version_from_payload({"entity_type": "resource_version/v1", "logical_id": done.metadata["output_resource_ref"]["resource_id"],
                                          "version_id": done.metadata["output_resource_ref"]["resource_version_id"]})
        payload = json.loads(c["backend"].core.object_store.read_registered(c["backend"].core.get_version(resource.version_id)))
        assert payload["children"][0]["output"]["value"] == 6
        assert payload["children"][0]["started_receipt_ref"] and payload["children"][0]["terminal_receipt_ref"]
        assert payload["aggregate"] == {"summary": "filtered aggregate"}
    context = _exercise(tmp_path, monkeypatch, scenario)
    assert len(context["workers"]) == 1


def test_argument_rejection_budget_and_logical_key_identity_conflict(tmp_path, monkeypatch):
    def scenario(c):
        with pytest.raises(PluginError, match="allowlist"):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"],
                c["program"].program_ref, replace(_call(c, "forbidden"), tool="workspace"))
        with pytest.raises(PluginError, match="identity"):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"],
                c["program"].program_ref, replace(_call(c, "forged"), parent_identity={}))
        with pytest.raises(ResourceIntegrityFault, match="HOST"):
            changed = _policy(max_calls=1)
            c["gateway"].begin_tool_program_v1(c["execution"], c["loop"], c["turn"],
                c["prepared_parent"], changed, c["settlement_key"])
        invalid = _call(c, "bad-args", "not integer")
        rejected = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, invalid)
        assert rejected.closed_reply.error_code == "program_arguments_rejected"
        call = _call(c, "stable-key")
        child, reply = _dispatch(c, call)
        with pytest.raises(ManagedPluginInvocationConflict):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, _call(c, "stable-key", 9))
        with pytest.raises(PluginError, match="budget"):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, _call(c, "over-budget"))
        done = _complete(c, [ProgramBrokerObservation(invalid, rejected.closed_reply), ProgramBrokerObservation(call, reply)], 6)
        assert done.metadata["call_count"] == 2
    c = _exercise(tmp_path, monkeypatch, scenario, policy=_policy(max_calls=2))
    assert len(c["workers"]) == 1


def test_script_failure_after_returned_child_keeps_created_id_in_actual_read_request(tmp_path, monkeypatch):
    def scenario(c):
        call = _call(c, "created-before-exception")
        _child, reply = _dispatch(c, call)
        done = _complete(c, [ProgramBrokerObservation(call, reply)], status="failed")
        assert done.metadata["reader"] == "read_tool_program_output"
        assert done.metadata["status"] == "failed"
        with pytest.raises(ManagedPluginInvocationConflict, match="closed"):
            c["gateway"].begin_tool_program_v1(c["execution"], c["loop"], c["turn"],
                c["prepared_parent"], c["policy"], c["settlement_key"])
    c = _exercise(tmp_path, monkeypatch, scenario)
    assert len(c["workers"]) == 1
    pages = [json.loads(m["content"]) for m in c["provider"].requests[-1]["messages"] if m.get("role") == "tool"]
    page = next(p for p in pages if p["kind"] == "tool_program_output_page/v1")
    assert page["source_status"] == "failed" and "PROGRAM-CREATED-ID-3" in page["content"]


def test_cancelled_accepted_frame_records_non_admission_without_receipts(tmp_path, monkeypatch):
    def scenario(c):
        successful = _call(c, "successful-sibling")
        _child, reply = _dispatch(c, successful)
        cancelled = replace(_call(c, "cancelled-frame"), deadline_monotonic=time.monotonic()-1,
                            cancelled=lambda: True)
        with pytest.raises(PluginError, match="cancelled"):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"],
                                                  c["program"].program_ref, cancelled)
        rejected = c["gateway"].reject_program_child_v1(c["execution"], c["loop"], c["turn"],
            c["program"].program_ref, cancelled, error_code="cancelled_before_admission")
        assert rejected.error_code == "cancelled_before_admission"
        done = _complete(c, [ProgramBrokerObservation(successful, reply), ProgramBrokerObservation(cancelled, rejected)], status="cancelled")
        assert done.metadata["status"] == "cancelled" and done.metadata["call_count"] == 2
        children = c["backend"]._program_latest_calls(c["program"].program_ref)
        assert children[-1]["status"] == "rejected"
        assert children[-1]["started_receipt_ref"] is None and children[-1]["terminal_receipt_ref"] is None
    c = _exercise(tmp_path, monkeypatch, scenario)
    assert len(c["workers"]) == 1


def test_known_managed_failure_is_closed_and_not_reexecuted(tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    def failure(handler, packet):
        if packet["arguments"]["value"] == 9:
            raise WorkerFailure("handler_failed")
        return handler(None, packet["arguments"])
    def scenario(c):
        good = _call(c, "good")
        _child, good_reply = _dispatch(c, good)
        failed = _call(c, "known-failure", 9)
        _child, reply = _dispatch(c, failed)
        assert reply.error_code == "handler_failed" and not reply.outcome_unknown
        replay = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, failed)
        assert replay.closed_reply == reply and replay.managed_prepared is None
        _complete(c, [ProgramBrokerObservation(good, good_reply), ProgramBrokerObservation(failed, reply)], 6)
    c = _exercise(tmp_path, monkeypatch, scenario, worker_behavior=failure)
    assert len(c["workers"]) == 2


def test_unknown_collects_real_successful_sibling_and_rejected_pending_frame(tmp_path, monkeypatch):
    from threading import Barrier, Event
    from cpn.plugins.controlled_script import ProgramBrokerReply
    barrier, unknown_published = Barrier(2), Event()

    def worker(handler, packet):
        barrier.wait(timeout=15)
        if packet["arguments"]["value"] == 99:
            raise RuntimeError("synthetic lost worker observation")
        assert unknown_published.wait(timeout=15)
        return handler(None, packet["arguments"])

    def scenario(c):
        calls = [_call(c, "uncertain", 99), _call(c, "successful-sibling", 4)]
        by_id, prepared = {}, {}
        declaration = c["backend"].managed_plugin_services["main"].catalog.declaration("double_value")
        scheduled = []
        for i, call in enumerate(calls):
            identity = f"tool-program-child/v1:{c['program'].program_ref.entity_id}:{call.key}"
            by_id[identity] = call
            scheduled.append(ManagedToolCall(i, call.tool, identity, call.arguments, declaration.registration_key))

        def prepare(item):
            call = by_id[item.call_id]
            child = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call)
            prepared[item.call_id] = child
            return child.managed_prepared

        def finish(managed, completion):
            identity = json.loads(managed.receipt_material)["call_id"]
            reply = c["gateway"].finish_program_child_v1(c["execution"], c["loop"], c["turn"],
                c["program"].program_ref, by_id[identity], prepared[identity], completion)
            if reply.outcome_unknown:
                unknown_published.set()
                raise ManagedPluginInvocationReconciliationRequired("synthetic program uncertainty", evidence={
                    "program_reply": {"value": json_copy(reply.value), "error_code": reply.error_code, "outcome_unknown": True}})
            return reply

        batch = ManagedToolScheduler(c["backend"].managed_scheduler_policy, c["backend"].managed_run_capacity).run(
            tuple(scheduled), operation_key=str(c["execution"].operation_execution_lease_ref.version_id),
            prepare=prepare, finish=finish, cancelled=lambda: False)
        assert batch.reconciliation_required
        assert isinstance(batch.outcomes[0].error, ManagedPluginInvocationReconciliationRequired)
        first = ProgramBrokerReply(**batch.outcomes[0].error.evidence["program_reply"])
        successful = batch.outcomes[1].result
        assert successful is not None and not successful.outcome_unknown
        pending = _call(c, "accepted-but-not-started")
        with pytest.raises(ManagedPluginInvocationReconciliationRequired):
            c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, pending)
        rejected = c["gateway"].reject_program_child_v1(c["execution"], c["loop"], c["turn"],
            c["program"].program_ref, pending, error_code="reconciliation_before_admission")
        done = _complete(c, [ProgramBrokerObservation(calls[0], first), ProgramBrokerObservation(calls[1], successful),
                            ProgramBrokerObservation(pending, rejected)], status="reconciliation_required")
        assert done.metadata["status"] == "outcome_unknown" and done.block_authority is not None
        children = c["backend"]._program_latest_calls(c["program"].program_ref)
        assert [child["status"] for child in children] == ["outcome_unknown", "returned", "rejected"]
        assert children[0]["terminal_receipt_ref"] and children[1]["terminal_receipt_ref"]
        assert children[2]["terminal_receipt_ref"] is None
    c = _exercise(tmp_path, monkeypatch, scenario, worker_behavior=worker)
    assert len(c["workers"]) == 2 and c["task_result"]["stop_reason"] == "blocked_or_waiting"
    assert "PROGRAM-CREATED-ID-4" in c["partial_page"]["content"]


def test_active_observer_uses_real_receipts_after_finish_without_wait_or_rerun(tmp_path, monkeypatch):
    from cpn.plugins.managed_tools import ManagedInvocationObservation, execute_prepared_invocation
    def scenario(c):
        call = _call(c, "one-real-dispatch")
        capacity = c["backend"].managed_run_capacity
        policy = c["backend"].managed_scheduler_policy
        declaration = c["backend"].managed_plugin_services["main"].catalog.declaration(call.tool)
        scheduled = ManagedToolCall(0, call.tool, "one-owner-capacity-reservation", call.arguments, declaration.registration_key)
        lease = capacity._reserve(policy._access(scheduled))
        assert lease is not None
        try:
            first = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call)
            observer = c["gateway"].prepare_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call)
            assert isinstance(observer.managed_prepared, ManagedInvocationObservation)
            with pytest.raises(PluginError, match="wait outside"):
                c["gateway"].finish_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call, observer, None)
            completion = execute_prepared_invocation(first.managed_prepared, cancelled=lambda: False)
            reply = c["gateway"].finish_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call, first, completion)
            observed = c["gateway"].finish_program_child_v1(c["execution"], c["loop"], c["turn"], c["program"].program_ref, call, observer, None)
            assert observed == reply
        finally:
            capacity._release(lease)
        _complete(c, [ProgramBrokerObservation(call, reply)], 6)
    c = _exercise(tmp_path, monkeypatch, scenario)
    assert len(c["workers"]) == 1


@pytest.mark.parametrize("mutation", [{"profile_id": "arbitrary_python"}, {"tools": ["workspace"]}, {"tools": ["z", "a"]}, {"extra": True}])
def test_host_policy_contract_rejects_invalid_fields(mutation):
    policy = _policy()
    policy.update(mutation)
    if mutation == {"tools": ["workspace"]}:
        # Syntactically valid names are checked against selected managed tools
        # by the real begin scope; they are not guessed from naming.
        assert validate_program_policy(policy)[0]["tools"] == ["workspace"]
    else:
        with pytest.raises(ValueError):
            validate_program_policy(policy)
