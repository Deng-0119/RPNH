"""Check this sample's explicit private package-run result projection.

This reads the genuine command's output file, not Registry internals. It does
not independently authenticate an arbitrary user-edited JSON file as Registry
evidence. Never substitute a handwritten result for a package run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cpn.plugins.api import canonical
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.share_packages import PackageResolutionLock, strict_json

ROOT = Path(__file__).resolve().parent


def exact_ref(value, kind):
    if (type(value) is not dict or set(value) != {"entity_type", "logical_id", "version_id"}
            or value["entity_type"] != kind
            or any(type(value[key]) is not str or not value[key] for key in ("logical_id", "version_id"))):
        raise ValueError("missing or mismatched exact " + kind + " reference")


def verify(result, expected, target):
    if result.get("schema_version") != "rpnh/package_run_result/v1" or result.get("target") != target:
        raise ValueError("result is not for the selected exact v2 package")
    for key, kind in (("run_ref", "native_run_identity/v1"), ("task_ref", "task/v1"), ("net_ref", "net_instance/v1"),
                      ("terminal_evidence_ref", "run_terminal_evidence/v1")):
        exact_ref(result.get(key), kind)
    if result.get("stop_reason") != "terminal" or result.get("actual_model_call_counts") != [0, 0]:
        raise ValueError("a genuine no-model business terminal is required")
    terminal = result.get("terminal_result", {})
    if terminal.get("status") != "available" or terminal.get("run_outcome") != "complete":
        raise ValueError("terminal output is unavailable; preparation alone is not a result")
    exact_ref(terminal.get("terminal_result_ref"), "resource_version/v1")
    exact_ref(terminal.get("final_checkpoint_ref"), "marking_checkpoint/v1")
    exact_ref(terminal.get("run_execution_authority_ref"), "run_execution_authority/v1")
    if terminal.get("terminal_evidence_ref") != result["terminal_evidence_ref"]:
        raise ValueError("terminal output names different terminal evidence")
    if type(expected) is not dict or set(expected) != {"value"} or type(expected["value"]) is not int:
        raise ValueError("expected output must contain exactly one integer value")
    if terminal.get("output") != expected or type(terminal.get("output", {}).get("value")) is not int:
        raise ValueError("actual registered output does not match the selected expected value")
    payload = canonical(terminal["output"])
    if (terminal.get("media_type") != "application/json" or terminal.get("byte_count") != len(payload)
            or terminal.get("content_sha256") != hashlib.sha256(payload).hexdigest()):
        raise ValueError("native result content identity does not match its output")
    return {"status": "PASS", "output": terminal["output"], "actual_model_call_counts": result["actual_model_call_counts"],
            "terminal_evidence_ref": result["terminal_evidence_ref"], "terminal_result_ref": terminal["terminal_result_ref"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--expected", type=Path, default=ROOT / "expected-output.json")
    parser.add_argument("--archive", type=Path, default=ROOT / "native-add-v2.zip")
    parser.add_argument("--lock", type=Path, default=ROOT / "native-add-v2.lock.json")
    args = parser.parse_args()
    requirements = read_package_environment(args.archive, package_lock=PackageResolutionLock(args.lock.read_bytes()))
    result = strict_json(args.result.read_bytes(), path="private-package-run-result")
    expected = strict_json(args.expected.read_bytes(), path="sample-expected-output")
    print(json.dumps(verify(result, expected, requirements.target.to_dict()), sort_keys=True))


if __name__ == "__main__":
    main()
