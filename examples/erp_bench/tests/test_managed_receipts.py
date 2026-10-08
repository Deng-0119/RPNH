"""Real Registry receipts with deterministic in-memory worker/bridge transports.

These fixtures run the ERP handler and Bridge dispatch code, but do not establish
spawn, AF_UNIX, installed-owner, provider, Docker, or business acceptance.
"""
import json
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.components.execution_services import invoke_registered_tool
from cpn.plugins import (BoundPlugin, ManagedPluginInvocationConflict,
    ManagedPluginInvocationReconciliationRequired, ManagedPluginInvocationService,
    ManagedPluginToolAdapter, ManagedPluginToolCatalog, PluginCatalog)
from cpn.plugins.api import PluginContext, canonical, json_copy
from cpn.plugins.worker import WorkerFailure
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.run import OwnerInput, start_run
from rpnh_erp_bench.bridge import Bridge
from rpnh_erp_bench.plugin import factory

INPUT = "application/erp_receipt_test_input/v1"
OUTPUT = "application/erp_receipt_test_output/v1"
EXECUTOR = "tests/erp-receipt-caller/v1"
TERMINAL = "tests/erp-receipt-terminal/v1"


def unused_executor(**kwargs):
    raise AssertionError("only the registered managed tool is invoked")


def prepared_invocation(tmp_path):
    selected = PluginCatalog((BoundPlugin(factory(), {
        "endpoint": "/fixture/erp.sock", "trial_id": "trial"}),))
    managed = ManagedPluginToolCatalog(selected, {"erp_python": "erp_bench/erp_python"},
                                       admitted_effects=("external_write",))
    declaration = managed.declaration("erp_python")
    registration = Registration()
    register_basic_components(registration)
    for key in (INPUT, OUTPUT):
        registration.register_schema(key, {"$schema": "http://json-schema.org/draft-07/schema#",
                                           "$id": key, "type": "object"})
    registration.register_executor(EXECUTOR, unused_executor,
        identity={"implementation_id": "tests.erp_receipt_caller", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "tests.erp_receipt_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    ManagedPluginToolAdapter(managed).register(registration)
    module = ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "ERPReceiptCaller",
        "components": [{"name": "caller", "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {}, "ports": [
                {"name": "request", "direction": "input", "schema": INPUT},
                {"name": "result", "direction": "output", "schema": OUTPUT}],
            "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request"],
                "outputs": ["result"], "request_port": None, "tools": [declaration.registration_key],
                "config": {}, "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "caller", "budget_scope": "module",
                                   "finalization_scope": None}}]}],
        "links": [], "entry": {"request": {"component": "caller", "port": "request"}},
        "exit": {"result": {"component": "caller", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "caller", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, INPUT, OUTPUT], "budgets": {},
        "budget_buckets": [{"bucket_id": "caller", "budget_scope": "module",
                            "finalization_scope": None, "max_attempts": 1}]})
    request = OwnerInput(INPUT, canonical({}), "ERP managed receipt fixture")
    owner = start_run(module, registration, run_dir=tmp_path / "run", task_input=request,
        entry_inputs={"request": request}, budgets=ModuleBudgetDeclaration(
            tuple(module.to_dict()["budget_buckets"]), ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="no-model", owner_statement="deterministic ERP receipt test",
        command_id="test:erp:fresh")
    admitted = owner.admit("caller.run", logical_tau=0, command_id="test:erp:admit")
    execution = owner.start(admitted, command_id="test:erp:start")
    kernel, repository = owner.operation_repository()
    return SimpleNamespace(owner=owner, execution=execution, kernel=kernel, repository=repository,
        managed=managed, declaration=declaration,
        service=ManagedPluginInvocationService(owner, kernel, repository, managed))


def invoke(prepared, *, call_id="original-call", source="mutate()", service=None):
    declaration = prepared.declaration
    return invoke_registered_tool(prepared.owner, prepared.kernel, prepared.repository,
        prepared.execution, declaration.registration_key, identity=json_copy(declaration.identity),
        contracts=json_copy(declaration.contracts), kwargs={
            "service": service or prepared.service, "execution": prepared.execution,
            "call_id": call_id, "arguments": {"source": source}})


def receipts(prepared):
    core = prepared.service.core
    documents = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        if "managed_plugin_call" in json.loads(row["metadata_json"]).get("descriptors", {}):
            ref = core.get_version(row["version_id"])
            documents.append(json.loads(core.object_store.read_registered(ref)))
    return documents


class TransportFixture:
    """Replace only IPC transport; never interpret a returned ERP status."""
    def __init__(self, monkeypatch, mode):
        self.mode = mode
        self.worker_calls = self.bridge_calls = self.backend_calls = 0
        self.bridge = Bridge("/fixture/erp.sock", "trial", self)
        monkeypatch.setattr("cpn.plugins.worker.execute_worker", self.execute_worker)
        monkeypatch.setattr("rpnh_erp_bench.plugin.socket.socket", self.connection)
        monkeypatch.setattr("rpnh_erp_bench.bridge.select.select", lambda *args: ([], [], []))

    def execute_worker(self, handler, packet, *, environment_names, timeout_seconds, cancelled):
        self.worker_calls += 1
        context = PluginContext(**packet["context"], cancel=threading.Event(),
                                deadline=time.monotonic() + timeout_seconds)
        try:
            # Normal returns pass through unchanged, exactly as the worker does.
            return json_copy(handler(context, packet["arguments"]))
        except Exception as exc:
            # Production _worker closes arbitrary handler exceptions to this code;
            # execute_worker decodes it with may_have_executed=True.
            raise WorkerFailure("handler_failed") from exc

    def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
        self.backend_calls += 1
        if self.mode == "backend_exception":
            raise RuntimeError("private backend details must not enter receipts")
        status = self.mode if self.mode in {"unknown", "failed", "domain_infeasible", "interrupted"} else "completed"
        return {"status": status, "stdout": "observed output", "stderr": "observed diagnostic",
                "exit_code": 7 if status == "failed" else 0 if status == "completed" else None,
                "execution_id": "observed-execution"}

    def connection(self, *args):
        fixture = self

        class Connection:
            response = b""
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def connect(self, endpoint):
                assert endpoint == "/fixture/erp.sock"
                if fixture.mode == "connect_failure":
                    raise OSError("fixture connect failed before bridge dispatch")
            def settimeout(self, timeout):
                pass
            def sendall(self, frame):
                if fixture.mode == "partial_send":
                    raise OSError("fixture partial send")
                fixture.bridge_calls += 1
                assert struct.unpack("!I", frame[:4])[0] == len(frame[4:])
                client = self

                class Reply:
                    def sendall(self, response):
                        if fixture.mode == "lost_reply":
                            raise OSError("fixture reply lost after backend completion")
                        if fixture.mode in {"bad_schema", "wrong_identity"}:
                            answer = json.loads(response[4:])
                            if fixture.mode == "bad_schema":
                                answer.pop("execution_id")
                            else:
                                answer["identity"]["call_id"] = "wrong-call"
                            body = json.dumps(answer).encode()
                            response = struct.pack("!I", len(body)) + body
                        client.response += response
                # Exercise the actual bridge admission/cache/uncertainty gate.
                try:
                    fixture.bridge._dispatch(json.loads(frame[4:]), Reply())
                except OSError:
                    if fixture.mode != "lost_reply":
                        raise
                    # The request was sent. Failed server delivery leaves EOF
                    # for the client's receive_frame, as on a closed socket.
            def recv(self, size):
                answer, self.response = self.response[:size], self.response[size:]
                return answer
        return Connection()


@pytest.mark.parametrize("mode", ["unknown", "backend_exception", "lost_reply", "bad_schema", "wrong_identity", "partial_send"])
def test_unknown_reaches_registry_and_blocks_replays_and_new_calls(tmp_path, monkeypatch, mode):
    prepared = prepared_invocation(tmp_path)
    transport = TransportFixture(monkeypatch, mode)
    with pytest.raises(ManagedPluginInvocationReconciliationRequired) as first:
        invoke(prepared)
    assert first.value.evidence["outcome"] == "outcome_unknown"
    assert first.value.evidence["error"] == {"code": "handler_failed"}
    original = receipts(prepared)
    assert [row["state"] for row in original] == ["started", "outcome_unknown"]
    assert {row["call_id"] for row in original} == {"original-call"}
    assert "private backend details" not in json.dumps(original)
    expected_dispatches = 0 if mode == "partial_send" else 1
    counts = (1, expected_dispatches, expected_dispatches)
    assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == counts
    if mode in {"unknown", "backend_exception", "lost_reply"}:
        assert transport.bridge._uncertain

    rebuilt = ManagedPluginInvocationService(prepared.owner, prepared.kernel,
                                               prepared.repository, prepared.managed)
    for service in (prepared.service, rebuilt):
        with pytest.raises(ManagedPluginInvocationReconciliationRequired):
            invoke(prepared, service=service)
        with pytest.raises(ManagedPluginInvocationConflict, match="reused"):
            invoke(prepared, service=service, source="different_body()")
        with pytest.raises(ManagedPluginInvocationReconciliationRequired, match="no new dispatch"):
            invoke(prepared, service=service, call_id="new-call", source="dependent_mutation()")
        assert receipts(prepared) == original  # No new started claim or terminal.
        assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == counts


@pytest.mark.parametrize("status", ["completed", "failed", "domain_infeasible"])
def test_known_results_remain_returned_and_allow_later_call(tmp_path, monkeypatch, status):
    prepared = prepared_invocation(tmp_path)
    transport = TransportFixture(monkeypatch, status)
    first = invoke(prepared)
    assert first["outcome"] == "returned"
    assert first["output"]["status"] == status
    assert first["output"]["exit_code"] == (7 if status == "failed" else 0 if status == "completed" else None)
    assert first["output"]["stdout"] == "observed output"
    assert first["output"]["stderr"] == "observed diagnostic"
    assert first["output"]["execution_id"] == "observed-execution"
    assert not transport.bridge._uncertain
    rebuilt = ManagedPluginInvocationService(prepared.owner, prepared.kernel,
                                               prepared.repository, prepared.managed)
    assert invoke(prepared, service=rebuilt) == first
    with pytest.raises(ManagedPluginInvocationConflict, match="reused"):
        invoke(prepared, service=rebuilt, source="different_body()")
    assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == (1, 1, 1)
    transport.mode = "completed"
    assert invoke(prepared, service=rebuilt, call_id="new-call")["output"]["status"] == "completed"
    assert [row["state"] for row in receipts(prepared)] == ["started", "returned", "started", "returned"]
    assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == (2, 2, 2)


def test_interrupted_is_unchanged_and_bridge_safety_gate_remains(tmp_path, monkeypatch):
    prepared = prepared_invocation(tmp_path)
    transport = TransportFixture(monkeypatch, "interrupted")
    assert invoke(prepared)["output"]["status"] == "interrupted"
    assert [row["state"] for row in receipts(prepared)] == ["started", "returned"]
    assert transport.bridge._uncertain
    # This mixed status still reaches the bridge for a new call; it is outside
    # the unknown-only repair. The existing bridge gate prevents another effect.
    following = invoke(prepared, call_id="new-call")
    assert following["output"]["status"] == "interrupted"
    assert following["output"]["stderr"] == "trial requires reconciliation"
    assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == (2, 2, 1)


def test_connect_failure_is_still_conservatively_unknown(tmp_path, monkeypatch):
    prepared = prepared_invocation(tmp_path)
    transport = TransportFixture(monkeypatch, "connect_failure")
    with pytest.raises(ManagedPluginInvocationReconciliationRequired):
        invoke(prepared)
    assert [row["state"] for row in receipts(prepared)] == ["started", "outcome_unknown"]
    assert (transport.worker_calls, transport.bridge_calls, transport.backend_calls) == (1, 0, 0)
