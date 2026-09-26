"""Build a two-stage definition without starting a run or calling a model."""

from __future__ import annotations

import json

from cpn.rpnh import ComposePlan, ModuleDeclaration, compose_modules


TEXT = "application/example_net_operation_text/v1"
CONFIG = "application/operation_component_config/v1"
EXECUTOR = "example/deterministic-text/v1"
TERMINAL = "example/text-terminal/v1"


def stage(name: str) -> ModuleDeclaration:
    bucket = {"bucket_id": "shared", "budget_scope": "module",
              "finalization_scope": None}
    return ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1",
        "name": name,
        "components": [{
            "name": "step", "key": "operation", "config_schema": CONFIG,
            "config": {},
            "ports": [
                {"name": "request", "direction": "input", "schema": TEXT},
                {"name": "result", "direction": "output", "schema": TEXT},
            ],
            "operations": [{
                "name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"],
                "outcomes": [{"name": "complete",
                              "products": [{"port": "result"}]}],
                "tools": [], "config": {}, "request_port": None,
                "budget_binding": bucket,
            }],
        }],
        "links": [],
        "entry": {"request": {"component": "step", "port": "request"}},
        "exit": {"result": {"component": "step", "port": "result"}},
        "terminal": {"key": TERMINAL,
                     "source": {"component": "step", "port": "result"},
                     "operation": "run", "outcome": "complete",
                     "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG, TEXT], "budgets": {},
        "budget_buckets": [{**bucket, "max_attempts": 4}],
    })


if __name__ == "__main__":
    result = compose_modules(
        {"prepare": stage("Prepare"), "finish": stage("Finish")},
        ComposePlan("SerialExample", "finish", mode="serial"),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
