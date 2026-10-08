"""Synthetic stdin/stdout adapter. No provider, ERP, shell, or official task data."""
from __future__ import annotations

import json
from pathlib import Path
import sys

MODEL = "erp-synthetic-local-process-v1"
TOOLS = {"validate_plan", "erp_python", "read_file", "write_file", "complete_interaction", "read_managed_output"}
ORIGINAL_INPUT = ("  Synthetic fixture only: validate the supplied ordinary order, run the harmless script,\n"
                  "and report wiring evidence. Preserve this full original input, including whitespace.\n"
                  "Input delivery marker: 原始输入末尾 — synthetic only.\n\n")
OUTSIDE_INPUTS = {"synthetic-denied-home": "/home", "synthetic-denied-traversal": "../outside.txt"}


def input_locator(request):
    located = []
    for message in request["messages"]:
        if message["role"] != "system":
            continue
        for line in message.get("content", "").splitlines():
            if not line.startswith("- {"):
                continue
            item = json.loads(line[2:])
            if item.get("summary") == "RPNH agent task request" and item.get("sandbox_path"):
                located.append(item["sandbox_path"])
    if len(located) != 1:
        raise ValueError("expected one exact Located-input request path")
    return located[0]


def assert_original_input(request):
    results = {message["tool_call_id"]: json.loads(message["content"])
               for message in request["messages"] if message["role"] == "tool"}
    original = results.get("synthetic-original-input", {})
    if (original.get("kind") != "registered_file_read/v1" or original.get("content") != ORIGINAL_INPUT
            or original.get("path") != input_locator(request)
            or not original.get("resource_ref") or not original.get("use_receipt_ref")):
        raise ValueError("full unchanged original input must arrive through read_file before planning")
    for call_id in OUTSIDE_INPUTS:
        if "read_file path must be an exact registered input" not in json.dumps(results.get(call_id, {})):
            raise ValueError("outside input path was not rejected by the registered read boundary")
    return {"full_original_input_read": True, "path": original["path"],
            "resource_ref": original["resource_ref"], "use_receipt_ref": original["use_receipt_ref"],
            "outside_input_paths_rejected": list(OUTSIDE_INPUTS.values())}


def plan():
    return {"orders": [{"order_ref": "synthetic-order", "quantity": 4,
                        "list_price": 10, "budget": 40, "due_days": 5}],
            "routes": [{"route_ref": "synthetic-route", "kind": "buy", "capacity": 4,
                        "minimum_quantity": 2, "unit_cost": 6, "lead_days": 3}],
            "allocations": [{"order_ref": "synthetic-order", "route_ref": "synthetic-route", "quantity": 4}],
            "minimum_margin": 0.4}


def response(request):
    if request["protocol"] != "llm_request_envelope/v1" or request["model_condition"] != MODEL:
        raise ValueError("expected the explicit synthetic condition")
    names = {tool["function"]["name"] for tool in request["tools"]}
    if names != TOOLS:
        raise ValueError(f"unexpected actor capability inventory: {sorted(names)}")
    seen = set()
    for message in request["messages"]:
        if message["role"] == "assistant":
            for call in message.get("tool_calls", []):
                seen.add(call.get("function", call)["name"])
    def call(identity, name, arguments):
        return {"id": identity, "name": name, "arguments": json.dumps(arguments, separators=(",", ":"))}
    if "read_file" not in seen:
        calls = [call("synthetic-original-input", "read_file", {"path": input_locator(request)})]
        calls += [call(call_id, "read_file", {"path": path}) for call_id, path in OUTSIDE_INPUTS.items()]
    elif "validate_plan" not in seen:
        assert_original_input(request)
        calls = [call("synthetic-plan", "validate_plan", plan())]
    elif "erp_python" not in seen:
        calls = [call("synthetic-script", "erp_python", {"source": 'print("synthetic")', "timeout_seconds": 60})]
    else:
        calls = [call("synthetic-report", "write_file", {
            "path": "outputs/synthetic.txt", "description": "Synthetic wiring evidence only",
            "content": json.dumps("Synthetic local adapter and fake backend; no real ERP or benchmark score."),
            "output_port_id": "team.result", "outcome_id": "complete"}),
            call("synthetic-complete", "complete_interaction", {})]
    return {"protocol": "llm_response_envelope/v1", "tool_calls": calls, "finish_reason": "tool_calls"}


def write_profile(directory):
    """Emit private profile files; merely constructing a profile executes nothing."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    adapter_script = directory / "fake_provider.py"
    adapter_script.write_bytes(Path(__file__).read_bytes())
    transcript = directory / "transcript"
    transcript.mkdir(mode=0o700)
    adapter = directory / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1", "adapter_kind": "local_process",
        "model_condition": MODEL, "argv": ["{python}", "-B", str(adapter_script), str(transcript)],
        "probe_argv": ["{python}", "-B", str(adapter_script)],
        "env": {"PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(directory)}, "inherit_env": [],
    }), encoding="utf-8")
    selection = directory / "execution.json"
    selection.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": MODEL, "adapter_config_path": str(adapter), "timeout_seconds": 30,
        "max_output_tokens": 2048, "max_response_bytes": 262144,
    }), encoding="utf-8")
    return selection


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if len(sys.argv) != 2:
        raise SystemExit("an explicit private transcript directory is required")
    # Unit fixtures may exercise response() without filesystem effects.
    transcript = Path(sys.argv[1])
    prior = list(transcript.glob("request-*.json"))
    ordinal = len(prior) + 1
    if ordinal > 4:
        raise SystemExit("synthetic fixture permits only four model submissions")
    with (transcript / f"request-{ordinal}.json").open("x", encoding="utf-8") as stream:
        json.dump(request, stream, sort_keys=True)
    sys.stdout.write(json.dumps(response(request), ensure_ascii=True, allow_nan=False,
                                sort_keys=True, separators=(",", ":")))
