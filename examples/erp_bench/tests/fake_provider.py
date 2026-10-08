"""Synthetic stdin/stdout adapter. No provider, ERP, shell, or official task data."""
from __future__ import annotations

import json
from pathlib import Path
import sys

MODEL = "erp-synthetic-local-process-v1"
TOOLS = {"validate_plan", "erp_python", "write_file", "complete_interaction", "read_managed_output"}


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
    if "validate_plan" not in seen:
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
    if ordinal > 3:
        raise SystemExit("synthetic fixture permits only three model submissions")
    with (transcript / f"request-{ordinal}.json").open("x", encoding="utf-8") as stream:
        json.dump(request, stream, sort_keys=True)
    sys.stdout.write(json.dumps(response(request), ensure_ascii=True, allow_nan=False,
                                sort_keys=True, separators=(",", ":")))
