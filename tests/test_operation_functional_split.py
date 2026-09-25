"""Focused Operation façade and transaction-boundary tests."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import subprocess
import sys

from cpn.rpnh.registry import operations
from cpn.rpnh.registry._operation import declaration, inputs, outputs


_ROOT = Path(__file__).resolve().parents[1]
_OPERATION = _ROOT / "cpn/rpnh/registry/_operation"


def _has_commit_call(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
        for node in ast.walk(tree)
    )


def test_operation_facade_wrappers_delegate_and_preserve_contracts(monkeypatch) -> None:
    facade = (_ROOT / "cpn/rpnh/registry/operations.py").read_text(encoding="utf-8")
    assert "Historical pre-split bodies retained" not in facade
    assert "'''Historical" not in facade
    tree = ast.parse(facade)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "_operation.inputs"
        for node in tree.body)
    assert len(facade.splitlines()) < 2_100

    for name in ("declaration.py", "inputs.py", "outputs.py", "legacy_faults.py"):
        source = (_OPERATION / name).read_text(encoding="utf-8")
        assert "_FacadeDependencies" not in source
        assert "__getattr__" not in source

    fresh = subprocess.run(
        [sys.executable, "-c", (
            "from cpn.rpnh.registry._operation import "
            "declaration, inputs, outputs, legacy_faults"
        )],
        cwd=_ROOT, check=False, capture_output=True, text=True,
    )
    assert fresh.returncode == 0, fresh.stderr

    plan_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    plan_result = object()

    def plan_impl(*args, **kwargs):
        plan_calls.append((args, kwargs))
        return plan_result

    monkeypatch.setattr(inputs, "plan_registered_operation_inputs", plan_impl)
    repository, canonical, firing, transition, substitutions = (
        object(), object(), object(), object(), (object(),))
    assert operations.plan_registered_operation_inputs(
        repository, canonical=canonical, firing=firing, transition=transition,
        input_resource_substitutions=substitutions) is plan_result
    assert plan_calls == [((repository,), {
        "canonical": canonical, "firing": firing, "transition": transition,
        "input_resource_substitutions": substitutions,
    })]

    projection_calls: list[tuple[object, str]] = []
    projection_result = object()

    def projection_impl(value, *, label):
        projection_calls.append((value, label))
        return projection_result

    monkeypatch.setattr(
        declaration, "_parse_field_projection", projection_impl)
    projection_value = object()
    assert operations._parse_field_projection(
        projection_value, label="projection") is projection_result
    assert projection_calls == [(projection_value, "projection")]

    output_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    output_result = object()

    def output_impl(*args, **kwargs):
        output_calls.append((args, kwargs))
        return output_result

    monkeypatch.setattr(outputs, "register_operation_outputs", output_impl)
    execution = object()
    assert operations.register_operation_outputs(
        repository, execution, (), idempotency_key="key",
        selected_outcome_id="outcome") is output_result
    assert output_calls == [((repository, execution, ()), {
        "idempotency_key": "key", "selected_outcome_id": "outcome",
    })]

    for function in (
            operations.plan_registered_operation_inputs,
            operations.register_operation_outputs,
            operations.hydrate_operation_authority,
            operations._parse_content_schema_ref,
            operations._parse_field_projection,
            operations._parse_input_projection,
            operations._parse_port):
        assert function.__module__ == operations.__name__
    assert list(inspect.signature(
        operations.plan_registered_operation_inputs).parameters) == [
            "repository", "canonical", "firing", "transition",
            "input_resource_substitutions",
        ]


def test_operation_components_keep_transaction_ownership_with_callers() -> None:
    input_source = (_OPERATION / "inputs.py").read_text(encoding="utf-8")
    assert "_RegistryOperationAuthorityRepository__reverify_input" in input_source
    assert "_RegistryOperationAuthorityRepository__project_input_payload" in input_source
    for name in ("declaration.py", "inputs.py", "outputs.py", "legacy_faults.py"):
        assert not _has_commit_call(ast.parse(
            (_OPERATION / name).read_text(encoding="utf-8"))), name
