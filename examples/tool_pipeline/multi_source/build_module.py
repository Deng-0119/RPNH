"""Materialize the reviewable declaration; runtime consumes module.json only."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import SCHEMAS, STEPS, RULES, schema_id
from .host import TERMINAL, executor_key, tool_key


def declaration_dict():
    components = []
    for step, (inputs, outputs) in STEPS.items():
        outcomes = [{"name": "complete", "products": [{"port": p} for p in outputs
                                                     if p not in {"blocked", "rejected"}]}]
        outcomes += [{"name": p, "products": [{"port": p}]} for p in ("blocked", "rejected")
                     if p in outputs]
        components.append({"name": step, "key": "operation",
            "config_schema": "application/operation_component_config/v1", "config": {},
            "ports": [{"name": role, "direction": direction, "schema": schema_id(schema)}
                      for direction, roles in (("input", inputs), ("output", outputs))
                      for role, schema in roles.items()],
            "operations": [{"name": "run", "executor": executor_key(step),
                "inputs": list(inputs), "outputs": list(outputs), "request_port": None,
                "tools": [tool_key(step)], "config": {}, "outcomes": outcomes,
                "budget_binding": {"bucket_id": "multi_source", "budget_scope": "module",
                                   "finalization_scope": None}}]})
    wires = []
    def wire(source, source_port, target, target_port):
        wires.append({"source": {"component": source, "port": source_port},
                      "target": {"component": target, "port": target_port}})
    for source in RULES["priority"]:
        wire("read_" + source, "parsed", "merge_users", source)
    wire("merge_users", "merged", "encode_parquet", "merged")
    wire("merge_users", "conflicts", "encode_conflicts", "conflicts")
    wire("encode_parquet", "artifact", "finish", "parquet")
    wire("encode_conflicts", "artifact", "finish", "conflicts")
    return {"schema_version": "rpnh/module_declaration/v1", "name": "MultiSourceDataMerger",
        "components": components, "links": wires,
        "entry": {source: {"component": "read_" + source, "port": "source"}
                  for source in RULES["priority"]},
        "exit": {"report": {"component": "finish", "port": "final"},
            **{step + "_" + outcome: {"component": step, "port": outcome}
               for step, (_, outputs) in STEPS.items() for outcome in ("blocked", "rejected")
               if outcome in outputs}},
        "terminal": {"key": TERMINAL, "source": {"component": "finish", "port": "final"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": ["application/operation_component_config/v1", *SCHEMAS],
        "budgets": {}, "budget_buckets": [{"bucket_id": "multi_source", "budget_scope": "module",
                                          "finalization_scope": None, "max_attempts": len(STEPS)}]}


if __name__ == "__main__":
    Path(__file__).with_name("module.json").write_text(json.dumps(declaration_dict(), indent=2) + "\n")
