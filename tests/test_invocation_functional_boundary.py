import ast
import inspect
import subprocess
import sys
from types import SimpleNamespace

import cpn.rpnh.registry.invocations as invocations
from cpn.rpnh.registry._invocation import admission, context, execution, recovery, terminal


def test_invocation_facade_preserves_type_identity_and_delegates(monkeypatch):
    lifecycle = invocations.InvocationLifecycle(SimpleNamespace())
    seen = []

    def record(name, result):
        def implementation(actual_lifecycle, *args, **kwargs):
            seen.append((name, actual_lifecycle, args, kwargs))
            return result
        return implementation

    monkeypatch.setattr(context, "hydrate_context", record("context", "hydrated"))
    monkeypatch.setattr(admission, "admit_firing", record("admission", "admitted"))
    monkeypatch.setattr(execution, "revalidate_io", record("execution", None))
    monkeypatch.setattr(recovery, "_revalidate_committed_agent_terminal", record("recovery", None))
    assert lifecycle.hydrate_context("invocation") == "hydrated"
    assert lifecycle.admit_firing("claim", idempotency_key="key") == "admitted"
    assert lifecycle.revalidate_io("context", boundary="boundary") is None
    assert lifecycle._revalidate_committed_agent_terminal("context", "package") is None
    assert [item[0] for item in seen] == ["context", "admission", "execution", "recovery"]
    assert all(item[1] is lifecycle for item in seen)
    assert context.InvocationContext is invocations.InvocationContext
    assert admission.FiringClaim is invocations.FiringClaim
    assert terminal.TerminalResultPackage is invocations.TerminalResultPackage


def test_context_dto_decoders_delegate_without_changing_type_identity(monkeypatch):
    invocation_value = object()
    tool_value = object()

    monkeypatch.setattr(
        context, "decode_invocation_context",
        lambda actual_class, payload: (actual_class, payload, invocation_value),
    )
    monkeypatch.setattr(
        context, "decode_tool_execution_context",
        lambda actual_class, payload: (actual_class, payload, tool_value),
    )

    assert invocations.InvocationContext.from_serialized({"context": 1}) == (
        invocations.InvocationContext, {"context": 1}, invocation_value)
    assert invocations.ToolExecutionContext.from_serialized({"tool": 1}) == (
        invocations.ToolExecutionContext, {"tool": 1}, tool_value)


def test_terminal_facade_does_not_take_transaction_ownership(monkeypatch):
    calls = []

    class Service:
        def begin(self, **kwargs):
            calls.append(("begin", kwargs))
            raise AssertionError("facade must not begin a transaction")

    lifecycle = invocations.InvocationLifecycle(Service())

    def implementation(actual_lifecycle, actual_context, actual_package, **kwargs):
        calls.append(("implementation", actual_lifecycle, actual_context, actual_package, kwargs))
        return "operation-result"

    monkeypatch.setattr(terminal, "mark_operation_terminal_ready", implementation)
    assert lifecycle.mark_operation_terminal_ready("context", "package", idempotency_key="terminal") == "operation-result"
    assert calls == [("implementation", lifecycle, "context", "package", {"idempotency_key": "terminal", "recovering_committed_agent_actions": False})]


def test_transaction_ownership_stays_in_functional_implementations():
    ownership = (
        (admission.admit_firing, "admit_firing"),
        (execution._begin_registered_operation_execution, "operation start"),
        (terminal._mark_operation_terminal_ready, "terminal-ready"),
    )
    for implementation, label in ownership:
        source = inspect.getsource(implementation)
        assert source.count("lifecycle.service.begin(") == 1, label
        assert source.count("tx.commit()") == 1, label

    for method in (
        invocations.InvocationLifecycle.admit_firing,
        invocations.InvocationLifecycle._begin_registered_operation_execution,
        invocations.InvocationLifecycle._mark_operation_terminal_ready,
    ):
        source = inspect.getsource(method)
        assert ".begin(" not in source
        assert ".commit(" not in source


def test_functional_modules_import_without_preloading_invocations():
    code = "\n".join((
        "import importlib",
        "import sys",
        "assert 'cpn.rpnh.registry.invocations' not in sys.modules",
        "for name in ('context', 'admission', 'execution', 'terminal', 'recovery'):",
        "    importlib.import_module('cpn.rpnh.registry._invocation.' + name)",
    ))
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=".",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_functional_modules_reject_dynamic_facade_global_loading():
    modules = (context, admission, execution, terminal, recovery)
    for module in modules:
        source = inspect.getsource(module)
        tree = ast.parse(source)
        assert "_load_facade_symbols" not in source
        assert "globals().update" not in source
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "globals"
            and node.func.attr == "update"
            for node in ast.walk(tree)
        )
