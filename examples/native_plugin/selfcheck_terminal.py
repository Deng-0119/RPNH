"""Run one explicitly selected pure operation via the public native runtime.

Requires trusted installed factory code and supported local process/IPC. Saves
the genuine runtime result before verifying it; never synthesizes terminal data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cpn.plugins import PluginError, load_catalog
from cpn.plugins.api import canonical, validate
from cpn.plugins.catalog import read_config
from cpn.plugins.runtime import run_plugin


def verify(result, expected, selector, catalog):
    if (result.get("schema_version") != "rpnh/native_plugin_task_result/v1"
            or result.get("selector") != selector or result.get("catalog_digest") != catalog.digest):
        raise ValueError("runtime result does not match the selected catalog/operation")
    ref = result.get("terminal_evidence_ref")
    if (not isinstance(ref, dict) or set(ref) != {"entity_type", "logical_id", "version_id"}
            or ref["entity_type"] != "run_terminal_evidence/v1"
            or any(not isinstance(ref[k], str) or not ref[k] for k in ref)):
        raise ValueError("real terminal evidence reference is required")
    if result.get("stop_reason") != "terminal" or result.get("actual_model_call_counts") != [0, 0]:
        raise ValueError("a terminal with zero model calls is required")
    _, operation = catalog.resolve(selector)
    validate(operation.output_schema, result["output"])
    if canonical(result["output"]) != canonical(expected):
        raise ValueError("real runtime output differs from the author's expected output")


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "plugins.json")
    parser.add_argument("--operation", default="demo/add")
    parser.add_argument("--input", type=Path, default=root / "input.json")
    parser.add_argument("--expected", type=Path, default=root / "expected-output.json")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.run_dir.exists() or args.result.exists():
        parser.error("run directory and result file must both be absent")
    catalog = load_catalog(read_config(args.config))
    _, operation = catalog.resolve(args.operation)
    if operation.effect != "pure":
        raise PluginError("this author check authorizes only a declared pure operation")
    arguments = validate(operation.input_schema, json.loads(args.input.read_text()))
    expected = validate(operation.output_schema, json.loads(args.expected.read_text()))
    try:
        # Same supported runtime as `rpnh plugins ... run`; no test internals.
        result = run_plugin(catalog, args.operation, arguments, run_dir=args.run_dir.resolve())
    except OSError as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2
    with args.result.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
    try:
        verify(result, expected, args.operation, catalog)
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "result": str(args.result)}))
        return 1
    print(json.dumps({"status": "PASS", "result": str(args.result), "output": result["output"],
                      "terminal_evidence_ref": result["terminal_evidence_ref"],
                      "actual_model_call_counts": result["actual_model_call_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
