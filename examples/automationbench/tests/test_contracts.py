"""Self-contained contract tests. Doubles do NOT certify real RPNH integration."""
import copy
import json
from pathlib import Path
import socket
import struct
import sys
import threading
from types import SimpleNamespace, ModuleType
import zipfile
import pytest

from rpnh_ab import io
from rpnh_ab.broker import Broker
from rpnh_ab.constants import PUBLIC_DOMAINS, TOOLS
from rpnh_ab.evidence import crosscheck
from rpnh_ab.experiment import plan_for
from rpnh_ab.exporting import export_return
from rpnh_ab.native import build_spec, profile_identity
from rpnh_ab.plugin import bindings, configuration, api_fetch_handler
from rpnh_ab.scoring import score_attempt, summarize
from rpnh_ab.upstream import information, public_messages, split_prompt, Upstream


def row():
    return {"prompt": [{"role": "system", "content": "Native public rules."},
                       {"role": "user", "content": "Public task."}],
            "info": {"task_name": "demo", "initial_state": {"secret": "not a prompt"},
                     "assertions": [{"type": "secret_answer"}], "zapier_tools": []}}


def case(i=1, domain="sales"):
    return {"id": f"{domain}-{i:04d}", "domain": domain, "task_name": f"task-{domain}-{i}",
            "domain_index": i-1, "example_id": i-1, "example_id_assigned_by_adapter": True,
            "task_contract_sha256": "a"*64, "row": row()}


def planned():
    return plan_for([case(i, d) for d in PUBLIC_DOMAINS for i in range(1, 101)], "public")


class FakeUpstream:
    def __init__(self):
        self.calls = []
    def dispatch(self, state, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        state["writes"] = state.get("writes", 0) + 1
        if args.get("raise"):
            raise ValueError("simulated exception after state mutation")
        return '{"ok": true, "payload": "原文  ", "args": ' + io.dumps(args) + '}'
    def dump_world(self, state):
        return copy.deepcopy(state)


def request(call="c1", **args):
    return {"run_id": "run", "tool": "api_fetch", "operation_id": "op",
            "invocation_id": "inv", "firing_id": "fire", "call_id": call, "arguments": args}


def prepared_attempt(path):
    path.mkdir(parents=True)
    io.write_new(path / "attempt.json", {"id": "sales-0001"})
    io.write_new(path / "final_world.json", {"state": "after"})
    io.write_new(path / "scoring_input.json", {"initial_state": {"state": "before"}, "info": {"assertions": []}})


def test_public_projection_never_contains_private_info():
    source = row()
    projected = public_messages(source)
    assert "assertions" not in io.dumps(projected)
    assert "not a prompt" not in io.dumps(projected)
    assert split_prompt(projected) == ("Native public rules.", "Public task.")
    projected[0]["content"] = "changed"
    assert source["prompt"][0]["content"] == "Native public rules."


def test_info_json_string_and_copy():
    source = row()
    source["info"] = json.dumps(source["info"])
    assert information(source)["task_name"] == "demo"


@pytest.mark.parametrize("bad", [[], [{"role": "assistant", "content": "x"}],
                                  [{"role": "user", "content": ["image"]}],
                                  [{"role": "system", "content": "only system"}]])
def test_unsupported_prompt_is_explicit_error(bad):
    with pytest.raises((ValueError, TypeError)):
        public_messages({"prompt": bad})


def test_full600_manifest_contains_no_hidden_row():
    plan = planned()
    assert len(plan["tasks"]) == 600
    assert all(sum(t["domain"] == d for t in plan["tasks"]) == 100 for d in PUBLIC_DOMAINS)
    assert "initial_state" not in io.dumps(plan)
    assert "secret_answer" not in io.dumps(plan)


def test_partial_manifest_rejected():
    with pytest.raises(ValueError):
        plan_for([case()], "public")


def test_immutable_json_evidence(tmp_path):
    p = tmp_path / "record.json"
    io.write_new(p, {"a": 1})
    with pytest.raises(FileExistsError):
        io.write_new(p, {"a": 2})
    assert io.load(p) == {"a": 1}


def test_nonfinite_json_rejected():
    with pytest.raises(ValueError):
        io.dumps({"score": float("nan")})


def test_socket_framing_roundtrip():
    a, b = socket.socketpair()
    try:
        io.send(a, {"text": "中文", "body": '{"x":1}'})
        assert io.receive(b) == {"text": "中文", "body": '{"x":1}'}
    finally:
        a.close(); b.close()


def test_truncated_socket_frame_is_error():
    a, b = socket.socketpair()
    a.sendall(struct.pack("!I", 20) + b"short"); a.close()
    try:
        with pytest.raises(EOFError):
            io.receive(b)
    finally:
        b.close()


def test_dedup_preserves_exact_raw_result_and_state(tmp_path):
    upstream, state = FakeUpstream(), {}
    broker = Broker(upstream, state, tmp_path, "run")
    req = request(body='{"spacing":  "keep"}', params=None)
    first = broker.call(req)
    assert broker.call(req) == first
    assert len(upstream.calls) == 1 and state["writes"] == 1
    assert upstream.calls[0][1]["body"] == req["arguments"]["body"]
    assert "原文  " in first["result"]


def test_changed_payload_same_native_call_is_rejected(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run")
    broker.call(request(body="one"))
    assert broker.call(request(body="two"))["ok"] is False
    assert len(up.calls) == 1


def test_failed_call_with_partial_side_effect_not_replayed(tmp_path):
    up, state = FakeUpstream(), {}
    broker = Broker(up, state, tmp_path, "run")
    failure = broker.call(request(raise_=False, **{"raise": True}))
    assert failure["ok"] is False
    broker.call(request(raise_=False, **{"raise": True}))
    assert len(up.calls) == 1 and io.load(tmp_path / "world.latest.json")["writes"] == 1


def test_missing_or_unbound_fields_do_not_dispatch(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run")
    assert broker.call({})["ok"] is False
    req = request(); req["run_id"] = "wrong"
    assert broker.call(req)["ok"] is False
    assert not up.calls


def test_distinct_calls_are_not_subject_to_cumulative_quota(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run")
    for i in range(55):
        assert broker.call(request(call=f"c{i}"))["ok"] is True
    assert len(up.calls) == 55


def test_checkpoint_failure_blocks_further_side_effects(tmp_path, monkeypatch):
    import rpnh_ab.broker as module
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run")
    def fail(*args):
        raise OSError("storage unavailable")
    monkeypatch.setattr(module, "replace_checkpoint", fail)
    with pytest.raises(OSError):
        broker.call(request())
    assert broker.poisoned
    assert broker.call(request(call="different"))["ok"] is False
    assert len(up.calls) == 1


def test_plugin_to_real_unix_socket_transport(tmp_path):
    up, state = FakeUpstream(), {}
    with Broker(up, state, tmp_path, "run") as broker:
        context = SimpleNamespace(check_cancelled=lambda: None, remaining_seconds=5,
            config={"endpoint": broker.endpoint, "run_id": "run"}, operation_id="api_fetch",
            invocation_id="inv", firing_id="fire", call_id="c1")
        output = api_fetch_handler(context, {"body": '{"untouched": true}'})
        assert output["witness_request_sequence"] == 1
        assert "raw_result" in output
    assert broker.closed and state["writes"] == 1


def test_closed_broker_cannot_mutate(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run"); broker.closed = True
    assert broker.call(request())["ok"] is False and not up.calls


def test_dispatch_uses_native_argument_normalization_without_body_rewrite():
    obj = object.__new__(Upstream)
    world = object(); source_args = {"body": '{"x": 2}', "params": {}, "count": 0, "flag": False}
    seen = {}
    def update(tool, args, messages, state):
        seen["raw"] = copy.deepcopy(args)
        updated = {k: v for k, v in args.items() if v != {} or not isinstance(v, dict)}
        updated["world"] = state["world"]
        return updated
    def native(**kwargs):
        seen["effective"] = kwargs
        return "exact source string"
    obj.env = SimpleNamespace(update_tool_args=update)
    obj.functions = {"api_fetch": native}
    assert obj.dispatch({"world": world}, "api_fetch", source_args) == "exact source string"
    assert seen["effective"]["world"] is world and "params" not in seen["effective"]
    assert seen["effective"]["body"] == source_args["body"]
    assert source_args["params"] == {} and seen["effective"]["count"] == 0


def test_model_cannot_supply_world():
    obj = object.__new__(Upstream); obj.functions = {"api_fetch": lambda **k: "no"}
    with pytest.raises(ValueError):
        obj.dispatch({}, "api_fetch", {"world": {}})


def test_managed_schemas_preserved_verbatim():
    schema = {"type": "function", "function": {"name": "api_fetch", "description": "native",
                         "parameters": {"type": "object", "properties": {"body": {"type": "string"}}}}}
    result = bindings([schema])["executor"]
    assert result["tools"]["api_fetch"]["input_schema"] == schema["function"]["parameters"]
    assert "external_write" in result["admitted_effects"]


def test_spec_uses_native_scheduler_and_no_per_stage_quota(tmp_path, monkeypatch):
    # Stub only the external SDK declarations. This test makes NO claim that a
    # real native worker, Registry or plugin catalog ran.
    graphmod = ModuleType("cpn.rpnh.agent_workflows")
    class Data:
        def __init__(self, *args, **kwargs):
            self.args = args; self.__dict__.update(kwargs)
    for name in ("AgentWorkflowGraph", "AgentWorkflowNode", "AgentWorkflowPort", "AgentWorkflowEndpoint", "AgentWorkflowExecution"):
        setattr(graphmod, name, Data)
    taskmod = ModuleType("cpn.rpnh.agent_tasks"); taskmod.AgentTaskSpec = Data
    catmod = ModuleType("cpn.plugins.catalog"); catmod.load_catalog = lambda config: SimpleNamespace(digest="a"*64)
    for name, obj in (("cpn.rpnh.agent_workflows", graphmod), ("cpn.rpnh.agent_tasks", taskmod), ("cpn.plugins.catalog", catmod)):
        monkeypatch.setitem(sys.modules, name, obj)
    spec = build_spec(tmp_path / "native", tmp_path / "selection.json", "/tmp/not-used", "r", row()["prompt"], [])
    assert spec.max_attempts_per_stage is None and spec.max_parallel_nodes == 1
    assert spec.stages == ()
    node = spec.workflow_graph.args[0][0]
    assert node.args[-1].tools == ("complete_interaction", "read_file", "write_file")
    assert "initial_state" not in spec.prompt and "secret_answer" not in spec.prompt


def test_profile_sanitizer_does_not_copy_credentials(tmp_path, monkeypatch):
    p = tmp_path / "profile.json"; p.write_text("{}")
    private = tmp_path / "secret.json"; private.write_text('{"api_key":"SECRET"}')
    selection = SimpleNamespace(adapter_config_path=private, adapter_kind="external_provider",
        input_target=SimpleNamespace(model_condition="gpt-5.6-terra"), reasoning_effort="medium",
        physical_profile="terra", logical_selection_id="my-route", timeout_seconds=30,
        as_registry_policy=lambda: {"route_provenance": [{"outbound_model": "gpt-5.6-terra",
          "endpoint": "https://user:pass@example.com/v1?secret=SECRET", "headers": {"Authorization":"SECRET"}}]})
    mod = ModuleType("cpn.llm_adapters"); mod.load_llm_execution_selection = lambda p: selection
    monkeypatch.setitem(sys.modules, "cpn.llm_adapters", mod)
    identity = profile_identity(p)
    text = io.dumps(identity)
    assert "SECRET" not in text and "Authorization" not in text and "user:pass" not in text
    assert identity["routes"][0]["endpoint_host"] == "example.com"
    assert identity["reasoning_effort"] == "medium"


def test_scoring_uses_given_official_function_and_exact_evidence(tmp_path):
    attempt = tmp_path / "a"; prepared_attempt(attempt)
    seen = []
    def scorer(world, initial, info):
        seen.append((world, initial, info)); return {"partial_credit": 0.75, "task_completed_correctly": 0.0}
    score = score_attempt(attempt, scorer)
    assert score["partial_credit"] == 0.75 and seen[0][0] == {"state": "after"}
    assert seen[0][1] == {"state": "before"}
    score_attempt(attempt, lambda *args: pytest.fail("existing score must not reexecute evaluator"))
    assert len(seen) == 1


def test_score_exception_is_not_zero(tmp_path):
    attempt = tmp_path / "a"; prepared_attempt(attempt)
    def bad(*args):
        raise KeyError("upstream assertion bug")
    score = score_attempt(attempt, bad)
    assert score["status"] == "score_error"
    assert score["partial_credit"] is None and score["task_completed_correctly"] is None


def test_scoring_revision_retains_old_score(tmp_path):
    attempt = tmp_path / "a"; prepared_attempt(attempt)
    score_attempt(attempt, lambda *args: {"partial_credit": 0.0, "task_completed_correctly": 0.0})
    score_attempt(attempt, lambda *args: {"partial_credit": 1.0, "task_completed_correctly": 1.0}, revision=True)
    assert len(list(attempt.glob("score-*.json"))) == 2
    assert io.load(attempt / "score-0001.json")["partial_credit"] == 0.0


def test_no_score_from_live_checkpoint(tmp_path):
    io.write_new(tmp_path / "world.latest.json", {})
    with pytest.raises(ValueError):
        score_attempt(tmp_path, lambda *args: pytest.fail("cannot score live state"))


def test_missing599_scores_never_label_one_success_as_full600(tmp_path):
    plan = planned(); attempt = tmp_path / "attempts/sales-0001/a0001"; prepared_attempt(attempt)
    score_attempt(attempt, lambda *args: {"partial_credit": 1.0, "task_completed_correctly": 1.0})
    report = summarize(plan, tmp_path)
    assert report["benchmark_pass_rate"] is None and not report["headline_is_complete"]
    assert report["observed_scored_subset_pass_rate"] == 1.0 and len(report["not_attempted"]) == 599


def test_simple_split_never_yields_official_score(tmp_path):
    plan = {"split": "simple", "tasks": []}
    report = summarize(plan, tmp_path)
    assert report["benchmark_pass_rate"] is None and not report["headline_is_complete"]


def test_crosscheck_distinguishes_environment_and_native_return(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run")
    req = request(body="x"); result = broker.call(req)
    check = crosscheck(tmp_path)
    assert check["environment_returned"] == 1
    assert not check["all_environment_returns_registered"]
    io.append(tmp_path / "registry_objects.jsonl", {"object_type": "agent_action/v3", "version_id": "v",
        "document": {"selector": "ab_api/api_fetch", "outcome": "returned", "arguments": req["arguments"],
                     "output": {"raw_result": result["result"], "witness_request_sequence": 1}}})
    check = crosscheck(tmp_path)
    assert check["all_environment_returns_registered"]
    assert not check["proves_subsequent_model_consumption"]


def test_crosscheck_modified_response_flagged(tmp_path):
    up = FakeUpstream(); broker = Broker(up, {}, tmp_path, "run"); broker.call(request())
    io.append(tmp_path / "registry_objects.jsonl", {"object_type": "agent_action/v3", "version_id": "v",
        "document": {"selector": "ab_api/api_fetch", "outcome": "returned", "arguments": {},
                     "output": {"raw_result": "wrong", "witness_request_sequence": 1}}})
    assert crosscheck(tmp_path)["mismatches"]


def test_return_export_omits_private_stores(tmp_path):
    work = tmp_path / "work"; work.mkdir(); plan = planned(); io.write_new(work / "plan.json", plan)
    attempt = work / "attempts/sales-0001/a0001"; prepared_attempt(attempt)
    (attempt / "native").mkdir(); (attempt / "native/credentials.json").write_text("SECRET")
    (attempt / "registry_objects.jsonl").write_text("SECRET")
    out = tmp_path / "return.zip"; export_return(work, out)
    with zipfile.ZipFile(out) as z:
        assert "attempts/sales-0001/a0001/final_world.json" in z.namelist()
        assert not any("credentials" in n or "registry_objects" in n for n in z.namelist())
        assert all(b"SECRET" not in z.read(n) for n in z.namelist())
    with pytest.raises(FileExistsError):
        export_return(work, out)


def test_batch_retains_failed_attempt_and_continues_all_other_tasks(tmp_path, monkeypatch):
    import rpnh_ab.experiment as exp
    cases = [case(i, d) for d in PUBLIC_DOMAINS for i in range(1, 101)]
    plan = plan_for(cases, "public")
    io.write_new(tmp_path / "plan.json", plan)
    io.write_new(tmp_path / "conditions.json", {"environment": {}, "rpnh": {}, "configured_model": {}, "business_tool_helper": {}, "benchmark_spec": {}, "execution_spec": {"executor_host":"native"}})
    io.write_new(tmp_path / "doctor.json", {"status": "passed", "ready_for_live_batch": True})
    old = tmp_path / "attempts/sales-0001/a0001"; prepared_attempt(old)
    io.write_new(old / "lifecycle.json", {"execution_status":"host_terminal"})
    import rpnh_ab.run_spec as rs
    monkeypatch.setattr(rs,"validate_acceptance",lambda *a: {})
    io.write_new(old / "score-0001.json", {"status": "scored", "partial_credit": 0.0, "task_completed_correctly": 0.0})
    for name in ("environment_identity", "installed_rpnh_identity", "helper_environment"):
        monkeypatch.setattr(exp, name, lambda: {})
    monkeypatch.setattr(exp, "profile_identity", lambda p: {})
    monkeypatch.setattr(exp, "summarize", lambda *a: {"contract_double": True})
    monkeypatch.setattr(exp, "replace_checkpoint", lambda *a: None)
    executed = []
    def one(up, case, attempt, profile, **kwargs):
        executed.append(case["id"])
        return False
    monkeypatch.setattr(exp, "one_attempt", one)
    exp.run_batch(SimpleNamespace(cases=lambda split: cases), tmp_path, tmp_path / "fake-profile", launch={"acceptance":"unit-double-only"})
    assert len(executed) == 599 and "sales-0001" not in executed
    assert io.load(old / "score-0001.json")["partial_credit"] == 0.0


def test_helper_backend_mode_is_explicit_and_never_contains_key(monkeypatch):
    from rpnh_ab.experiment import helper_environment
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert helper_environment()["mode"] == "native_no_key_simulated_response"
    monkeypatch.setenv("OPENAI_API_KEY", "TOPSECRET")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://u:p@api.example.test/v1?secret=TOPSECRET")
    record = helper_environment()
    assert record["mode"] == "native_real_api_configured"
    assert "TOPSECRET" not in io.dumps(record) and "u:p" not in io.dumps(record)


def test_interleaved_prompt_is_not_silently_reordered():
    with pytest.raises(ValueError):
        public_messages({"prompt": [{"role": "user", "content": "x"},
                                    {"role": "system", "content": "later"}]})


def test_return_redacts_known_credentials_only_in_export(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "exact-long-test-credential")
    work = tmp_path / "work"; work.mkdir()
    io.write_new(work / "plan.json", planned())
    attempt = work / "attempts/sales-0001/a0001"; prepared_attempt(attempt)
    text = '{"error":"exact-long-test-credential","record_id":"12345678901"}\n'
    (attempt / "tool_events.jsonl").write_text(text)
    out = tmp_path / "return.zip"; report = export_return(work, out)
    name = "attempts/sales-0001/a0001/tool_events.jsonl"
    with zipfile.ZipFile(out) as z:
        assert b"exact-long-test-credential" not in z.read(name)
        assert b"12345678901" in z.read(name)
        assert name in report["known_credential_redactions"]
    assert (attempt / "tool_events.jsonl").read_text() == text
