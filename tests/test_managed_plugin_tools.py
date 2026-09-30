from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.components.execution_services import invoke_registered_tool
from cpn.plugins import (
    BoundPlugin,
    ManagedPluginInvocationConflict,
    ManagedPluginInvocationFailed,
    ManagedPluginInvocationReconciliationRequired,
    ManagedPluginInvocationService,
    ManagedPluginToolAdapter,
    ManagedPluginToolCatalog,
    ManagedToolSelector,
    PluginCatalog,
    PluginDefinition,
    PluginError,
    PluginOperation,
)
from cpn.plugins.api import canonical, json_copy
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore


OBJECT_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
}
INTEGER_OUTPUT = {"type": "integer"}
CALLER_INPUT = "application/managed_tool_caller_input/v1"
CALLER_OUTPUT = "application/managed_tool_caller_output/v1"
CALLER_EXECUTOR = "tests/managed-tool-caller-executor/v1"
CALLER_TERMINAL = "tests/managed-tool-caller-terminal/v1"


def managed_double(context, arguments):
    assert context.operation_id == "synthetic/double"
    assert context.call_id
    return arguments["value"] * 2


def _unused_caller_executor(**_kwargs):
    raise AssertionError("focused managed-tool tests invoke only the registered tool")


def _plugin_catalog(*, effect="pure"):
    definition = PluginDefinition("synthetic", "1", (
        PluginOperation(
            "double", "Double one integer", OBJECT_INPUT, INTEGER_OUTPUT,
            managed_double, effect=effect),
    ))
    return PluginCatalog((BoundPlugin(definition, {}),))


def _managed_catalog(plugin_catalog=None):
    selected = plugin_catalog or _plugin_catalog()
    return ManagedPluginToolCatalog(selected, (
        ManagedToolSelector("double_value", "synthetic/double"),
    ))


def _registration(managed):
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(CALLER_INPUT, {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": CALLER_INPUT,
        "type": "object",
        "additionalProperties": False,
        "properties": {"caller_marker": {"type": "string"}},
        "required": ["caller_marker"],
    })
    registration.register_schema(CALLER_OUTPUT, {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": CALLER_OUTPUT,
        "type": "object",
    })
    registration.register_executor(
        CALLER_EXECUTOR,
        _unused_caller_executor,
        identity={"implementation_id": "tests.managed_tool_caller", "revision": "v1"},
        contracts={
            "transport": "deterministic",
            "input_ports": None,
            "output_ports": None,
        },
    )
    registration.register_tool(
        CALLER_TERMINAL,
        dict,
        identity={"implementation_id": "tests.managed_tool_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"},
    )
    ManagedPluginToolAdapter(managed).register(registration)
    return registration


def _caller_module(registration_key):
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": "ManagedToolCaller",
        "components": [{
            "name": "caller",
            "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID,
            "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": CALLER_INPUT},
                {"name": "result", "direction": "output", "schema": CALLER_OUTPUT},
            ],
            "operations": [{
                "name": "run",
                "executor": CALLER_EXECUTOR,
                "inputs": ["request"],
                "outputs": ["result"],
                "request_port": None,
                "tools": [registration_key],
                "config": {},
                "outcomes": [{
                    "name": "complete",
                    "products": [{"port": "result"}],
                }],
                "budget_binding": {
                    "bucket_id": "caller",
                    "budget_scope": "module",
                    "finalization_scope": None,
                },
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "caller", "port": "request"}},
        "exit": {"result": {"component": "caller", "port": "result"}},
        "terminal": {
            "key": CALLER_TERMINAL,
            "source": {"component": "caller", "port": "result"},
            "operation": "run",
            "outcome": "complete",
            "config": {"run_outcome": "complete"},
        },
        "required_schemas": [CONFIG_SCHEMA_ID, CALLER_INPUT, CALLER_OUTPUT],
        "budgets": {},
        "budget_buckets": [{
            "bucket_id": "caller",
            "budget_scope": "module",
            "finalization_scope": None,
            "max_attempts": 1,
        }],
    })


def _prepared_invocation(tmp_path, *, selected=None, managed=None):
    selected = selected or _plugin_catalog()
    managed = managed or _managed_catalog(selected)
    declaration = managed.declaration("double_value")
    registration = _registration(managed)
    module = _caller_module(declaration.registration_key)

    from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
    from cpn.rpnh.run import OwnerInput, start_run

    request = OwnerInput(
        CALLER_INPUT,
        canonical({"caller_marker": "not native plugin arguments"}),
        "non-native managed tool caller",
    )
    owner = start_run(
        module,
        registration,
        run_dir=tmp_path / "run",
        task_input=request,
        entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(
            tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="no-model",
        owner_statement="managed native tool test",
        command_id="test:managed:fresh",
    )
    admitted = owner.admit(
        "caller.run", logical_tau=0, command_id="test:managed:admit")
    execution = owner.start(admitted, command_id="test:managed:start")
    kernel, repository = owner.operation_repository()
    service = ManagedPluginInvocationService(
        owner, kernel, repository, managed)
    return {
        "owner": owner,
        "execution": execution,
        "kernel": kernel,
        "repository": repository,
        "selected": selected,
        "managed": managed,
        "service": service,
        "declaration": declaration,
    }


def _invoke(prepared, *, service=None, call_id="provider-call-1",
            arguments=None):
    declaration = prepared["declaration"]
    return invoke_registered_tool(
        prepared["owner"],
        prepared["kernel"],
        prepared["repository"],
        prepared["execution"],
        declaration.registration_key,
        identity=json_copy(declaration.identity),
        contracts=json_copy(declaration.contracts),
        kwargs={
            "service": service or prepared["service"],
            "execution": prepared["execution"],
            "call_id": call_id,
            "arguments": {"value": 4} if arguments is None else arguments,
        },
    )


def _managed_receipts(run_dir):
    core = _RegistryCore(run_dir, create=False, read_only=True)
    documents = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        metadata = json.loads(row["metadata_json"])
        if "managed_plugin_call" not in metadata.get("descriptors", {}):
            continue
        prepared = core.get_version(row["version_id"])
        documents.append(json.loads(core.object_store.read_registered(prepared)))
    return documents


def test_explicit_allowlist_projects_one_pure_plugin_operation():
    selected = _plugin_catalog()
    managed = _managed_catalog(selected)

    assert managed.provider_declarations == ({
        "type": "function",
        "function": {
            "name": "double_value",
            "description": "Double one integer",
            "parameters": OBJECT_INPUT,
        },
    },)
    document = managed.document()
    assert document["schema_version"] == (
        "rpnh/managed_native_plugin_tool_catalog/v2")
    assert document["plugin_catalog_digest"] == selected.digest
    registration = document["tools"][0]["registration"]
    assert registration["identity"]["selector"] == "synthetic/double"
    assert registration["identity"]["binding_digest"] == (
        selected.plugins[0].digest)
    assert registration["contracts"]["invocation_protocol"] == (
        "rpnh/managed_native_plugin_tool_invocation/v2")

    with pytest.raises(PluginError, match="owner-selected"):
        ManagedPluginToolCatalog(selected, ("synthetic/missing",))


def test_managed_worker_packet_carries_exact_call_identity(tmp_path):
    prepared = _prepared_invocation(tmp_path)
    declaration = prepared["declaration"]
    plugin, operation = prepared["service"].catalog.binding("double_value")

    packet = prepared["service"]._packet(
        prepared["execution"], declaration.selector, plugin, operation,
        "second-legitimate-call", {"value": 4})

    assert packet["context"]["call_id"] == "second-legitimate-call"


def test_worker_prestart_failures_report_no_possible_execution(monkeypatch):
    from cpn.plugins.worker import WorkerFailure, execute_worker

    with pytest.raises(WorkerFailure) as serialization:
        execute_worker(
            lambda _context, _arguments: None, {}, environment_names=(),
            timeout_seconds=1, cancelled=lambda: False)
    assert serialization.value.code == "handler_failed"
    assert serialization.value.may_have_executed is False

    missing = "RPNH_TEST_MISSING_MANAGED_CREDENTIAL"
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(WorkerFailure) as credential:
        execute_worker(
            managed_double, {}, environment_names=(missing,),
            timeout_seconds=1, cancelled=lambda: False)
    assert credential.value.code == "credential_environment_missing"
    assert credential.value.may_have_executed is False


def test_non_native_caller_executes_and_persists_started_returned(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    import cpn.plugins.worker as worker
    original = worker.execute_worker
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, "execute_worker", counted)
    result = _invoke(prepared)

    assert result["output"] == 8
    assert result["schema_version"] == (
        "rpnh/managed_native_plugin_tool_result/v2")
    assert result["terminal_receipt_ref"]["resource_version_id"].startswith(
        "resource_version:")
    assert calls == [True]
    receipts = _managed_receipts(tmp_path / "run")
    assert [item["state"] for item in receipts] == ["started", "returned"]
    assert {item["call_id"] for item in receipts} == {"provider-call-1"}
    assert receipts[0]["arguments"] == {"value": 4}
    assert receipts[0]["registration"]["key"] == (
        prepared["declaration"].registration_key)


def test_returned_call_replays_from_registry_after_service_reconstruction(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    import cpn.plugins.worker as worker
    original = worker.execute_worker
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, "execute_worker", counted)
    first = _invoke(prepared, call_id="durable-call")

    rebuilt_catalog = _managed_catalog(prepared["selected"])
    ManagedPluginToolAdapter(rebuilt_catalog)
    rebuilt_service = ManagedPluginInvocationService(
        prepared["owner"], prepared["kernel"], prepared["repository"],
        rebuilt_catalog)
    replayed = _invoke(
        prepared, service=rebuilt_service, call_id="durable-call")

    assert replayed == first
    assert calls == [True]


def test_concurrent_same_call_observer_waits_for_executor_terminal(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    entered = Event()
    release = Event()
    calls = []

    def controlled_worker(*_args, **_kwargs):
        calls.append(True)
        entered.set()
        assert release.wait(timeout=5)
        return 8

    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker", controlled_worker)
    rebuilt_service = ManagedPluginInvocationService(
        prepared["owner"], prepared["kernel"], prepared["repository"],
        _managed_catalog(prepared["selected"]))
    observer_entered = Event()
    original_enter = rebuilt_service._enter_active_call

    def enter_as_observer(key):
        observed = original_enter(key)
        observer_entered.set()
        return observed

    monkeypatch.setattr(
        rebuilt_service, "_enter_active_call", enter_as_observer)

    with ThreadPoolExecutor(max_workers=2) as workers:
        executor = workers.submit(
            _invoke, prepared, call_id="concurrent-call")
        assert entered.wait(timeout=5)
        observer = workers.submit(
            _invoke, prepared, service=rebuilt_service,
            call_id="concurrent-call")
        assert observer_entered.wait(timeout=5)
        assert not observer.done()
        release.set()
        executed = executor.result(timeout=5)
        observed = observer.result(timeout=5)

    assert observed == executed
    assert executed["outcome"] == "returned"
    assert calls == [True]
    assert [item["state"] for item in _managed_receipts(
        tmp_path / "run")] == ["started", "returned"]


def test_changed_arguments_conflict_with_durable_call_identity(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    import cpn.plugins.worker as worker
    original = worker.execute_worker
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(worker, "execute_worker", counted)
    assert _invoke(prepared, call_id="fixed-call")["output"] == 8
    with pytest.raises(ManagedPluginInvocationConflict, match="reused"):
        _invoke(
            prepared, call_id="fixed-call", arguments={"value": 5})
    assert calls == [True]


def test_started_without_terminal_publishes_unknown_and_never_redispatches(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    calls = []

    def crash_after_claim(*args, **kwargs):
        calls.append(True)
        raise SystemExit("simulated process loss after durable claim")

    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker", crash_after_claim)
    with pytest.raises(SystemExit, match="simulated"):
        _invoke(prepared, call_id="started-only")

    rebuilt_service = ManagedPluginInvocationService(
        prepared["owner"], prepared["kernel"], prepared["repository"],
        _managed_catalog(prepared["selected"]))
    with pytest.raises(
            ManagedPluginInvocationReconciliationRequired,
            match="requires reconciliation") as blocked:
        _invoke(
            prepared, service=rebuilt_service, call_id="started-only")
    assert blocked.value.evidence["error"] == {
        "code": "managed_terminal_observation_missing"}
    assert calls == [True]
    assert [item["state"] for item in _managed_receipts(
        tmp_path / "run")] == ["started", "outcome_unknown"]


def test_provider_override_is_additional_to_plugin_input_schema(
        tmp_path, monkeypatch):
    selected = _plugin_catalog()
    managed = ManagedPluginToolCatalog(selected, {
        "double_value": {
            "selector": "synthetic/double",
            "input_schema": {"type": "object"},
        },
    })
    prepared = _prepared_invocation(
        tmp_path, selected=selected, managed=managed)
    calls = []
    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker",
        lambda *_args, **_kwargs: calls.append(True))

    with pytest.raises(PluginError, match="declared plugin schema"):
        _invoke(prepared, arguments={"value": "provider-only-value"})

    assert calls == []
    assert _managed_receipts(tmp_path / "run") == []


def test_legacy_v1_declaration_is_the_exact_base_shape():
    selected = _plugin_catalog()
    managed = ManagedPluginToolCatalog(
        selected, {"double_value": "synthetic/double"},
        protocol_version="v1")
    declaration = managed.declaration("double_value")
    operation_key = selected.operation_key("synthetic/double")

    assert managed.document()["schema_version"] == (
        "rpnh/managed_native_plugin_tool_catalog/v1")
    assert declaration.registration_key == (
        f"{operation_key}/managed-tool/v1/double_value")
    assert json_copy(declaration.identity) == {
        "implementation_id": "rpnh.managed_native_plugin_tool",
        "revision": "v1",
        "provider_name": "double_value",
        "selector": "synthetic/double",
        "plugin_version": "1",
        "binding_digest": selected.plugins[0].digest,
        "plugin_catalog_digest": selected.digest,
    }
    assert declaration.contracts["invocation_protocol"] == (
        "rpnh/managed_native_plugin_tool_invocation/v1")
    assert "capability" not in declaration.contracts["managed_plugin"]
    assert "effect" not in declaration.contracts
    assert "max_result_bytes" not in declaration.contracts


def test_invalid_arguments_reject_before_started_claim_or_worker(
        tmp_path, monkeypatch):
    prepared = _prepared_invocation(tmp_path)
    calls = []
    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker",
        lambda *args, **kwargs: calls.append(True))

    with pytest.raises(PluginError, match="declared plugin schema"):
        _invoke(prepared, arguments={"value": "four"})
    assert calls == []
    assert _managed_receipts(tmp_path / "run") == []


def test_failed_call_is_durable_and_never_silently_retried(
        tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    prepared = _prepared_invocation(tmp_path)
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise WorkerFailure("handler_failed")

    monkeypatch.setattr("cpn.plugins.worker.execute_worker", fail)
    with pytest.raises(ManagedPluginInvocationFailed) as first:
        _invoke(prepared, call_id="failed-call")
    assert first.value.code == "handler_failed"

    rebuilt_service = ManagedPluginInvocationService(
        prepared["owner"], prepared["kernel"], prepared["repository"],
        _managed_catalog(prepared["selected"]))
    with pytest.raises(ManagedPluginInvocationFailed) as replayed:
        _invoke(prepared, service=rebuilt_service, call_id="failed-call")
    assert replayed.value.code == "handler_failed"
    assert calls == [True]
    assert [item["state"] for item in _managed_receipts(
        tmp_path / "run")] == ["started", "failed"]


def test_external_write_prestart_failure_is_durable_failed_not_unknown(
        tmp_path, monkeypatch):
    from cpn.plugins.worker import WorkerFailure
    selected = _plugin_catalog(effect="external_write")
    managed = ManagedPluginToolCatalog(
        selected, (ManagedToolSelector(
            "double_value", "synthetic/double"),),
        admitted_effects=("external_write",))
    prepared = _prepared_invocation(
        tmp_path, selected=selected, managed=managed)
    calls = []

    def prestart_failure(*_args, **_kwargs):
        calls.append(True)
        raise WorkerFailure(
            "credential_environment_missing", may_have_executed=False)

    monkeypatch.setattr(
        "cpn.plugins.worker.execute_worker", prestart_failure)
    with pytest.raises(ManagedPluginInvocationFailed) as first:
        _invoke(prepared, call_id="prestart-external-write")
    assert first.value.evidence["outcome"] == "failed"

    rebuilt = ManagedPluginInvocationService(
        prepared["owner"], prepared["kernel"], prepared["repository"],
        managed)
    with pytest.raises(ManagedPluginInvocationFailed) as replayed:
        _invoke(
            prepared, service=rebuilt,
            call_id="prestart-external-write")
    assert replayed.value.evidence["outcome"] == "failed"
    assert calls == [True]
    assert [item["state"] for item in _managed_receipts(
        tmp_path / "run")] == ["started", "failed"]


def test_non_pure_operation_is_not_managed():
    selected = _plugin_catalog(effect="external_write")
    with pytest.raises(PluginError, match="explicitly admitted"):
        _managed_catalog(selected)


def test_effect_policy_and_provider_descriptor_overrides_are_explicit():
    selected = _plugin_catalog(effect="external_read")
    override = {
        "type": "object", "additionalProperties": False,
        "properties": {"value": {"type": "integer", "minimum": 2}},
        "required": ["value"],
    }
    managed = ManagedPluginToolCatalog(
        selected, {"double_value": {
            "selector": "synthetic/double",
            "description": "Host-scoped exact read",
            "input_schema": override,
        }}, admitted_effects=("pure", "external_read"))

    declaration = managed.declaration("double_value")
    assert declaration.effect == "external_read"
    assert declaration.description == "Host-scoped exact read"
    assert json_copy(declaration.input_schema) == override
    assert declaration.contracts["effect"] == "external_read"
    assert json_copy(
        declaration.contracts["managed_plugin"]["capability"]) == {
        "schema_version": "rpnh/plugin_capability/v1",
        "binding_digest": selected.plugins[0].digest,
        "plugin": selected.plugins[0].definition.descriptor(),
        "operation": "double",
        "config": {},
        "environment": [],
        "assets": [],
    }
