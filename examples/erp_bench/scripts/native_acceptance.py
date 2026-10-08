"""Installed owner/worker acceptance using synthetic data and a fake backend.

Run with the isolated environment's Python, outside the product checkout, with
PYTHONPATH unset. --output must name a fresh private directory in the task root.
No provider probe, external model, Docker, official task, or real ERP is used.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
import uuid


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
        stream.write("\n")


class FakeBackend:
    """Never executes source; records exactly one synthetic script admission."""
    def __init__(self, *, block=False):
        self.calls = []
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.block = block
        self.cancel_observed = False
        self.entered_at = None

    def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
        require(source == 'print("synthetic")', "unexpected synthetic source")
        require(not self.calls, "synthetic effect must not be replayed")
        require(timeout_seconds > 0, "deadline was not propagated")
        execution_id = "synthetic-" + uuid.uuid4().hex
        self.calls.append({"identity": identity, "execution_id": execution_id,
                           "source": source, "timeout_seconds": timeout_seconds})
        self.entered_at = time.monotonic()
        self.entered.set()
        try:
            if self.block:
                limit = time.monotonic() + timeout_seconds + 5
                while not cancellation_requested():
                    require(time.monotonic() < limit, "public cancellation did not reach the backend")
                    time.sleep(0.02)
                self.cancel_observed = True
                return {"status": "interrupted", "stdout": "", "stderr": "synthetic cancellation",
                        "exit_code": None, "execution_id": execution_id}
            return {"status": "completed", "stdout": "synthetic\n" + execution_id,
                    "stderr": "", "exit_code": 0, "execution_id": execution_id}
        finally:
            self.exited.set()


def installed_identity():
    import cpn
    import rpnh_erp_bench
    import rpnh_erp_bench.plugin
    prefix = Path(sys.prefix).resolve()
    require(sys.prefix != sys.base_prefix, "an isolated installed environment is required")
    modules = {}
    for module in (cpn, rpnh_erp_bench, rpnh_erp_bench.plugin):
        location = Path(module.__file__).resolve()
        require(location.is_relative_to(prefix), f"{module.__name__} is not installed inside the venv: {location}")
        modules[module.__name__] = str(location)
    command = prefix / "bin" / "rpnh"
    require(command.is_file(), "installed rpnh entry is missing")
    return command, {"prefix": str(prefix), "imports": modules,
                     "rpnh_version": importlib.metadata.version("rpnh-harness"),
                     "plugin_version": importlib.metadata.version("rpnh-erp-bench"),
                     "harbor_version": importlib.metadata.version("harbor")}


def managed_receipts(run_dir, digest):
    """Read actual registered resources; never create a Registry writer."""
    from cpn.rpnh.agent_tasks import agent_task_catalog
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.identities import TypedId
    core = _RegistryCore(run_dir, create=False, read_only=True, catalog=agent_task_catalog())
    receipts = []
    for row in core.event_store.canonical_object_rows(object_type="resource_version/v1"):
        prepared = core.get_version(TypedId.parse(row["version_id"], expected="resource_version"))
        metadata = prepared.metadata
        if "managed_plugin_call" not in metadata.get("descriptors", {}):
            continue
        ref = {"resource_id": row["logical_id"], "resource_version_id": row["version_id"]}
        # Managed receipt resources use their own registered provenance. Read
        # the exact immutable object as the existing native receipt inspector
        # does, rather than treating it as an AgentLoop resource delivery.
        document = json.loads(core.object_store.read_registered(prepared))
        require(document["schema_version"] == "rpnh/managed_native_plugin_tool_receipt/v2", "unexpected receipt protocol")
        require(document["plugin_catalog_digest"] == digest, "receipt catalog differs from spec")
        for key in ("caller_execution_ref", "caller_firing_ref", "producer_invocation_ref"):
            require(document[key].get("entity_type") and document[key].get("version_id"), "untyped caller receipt")
        receipts.append({"ref": ref, "receipt": document})
    return receipts


def timeout_fixture_budget(previous_cases):
    """Synthetic wall budget includes measured installed-owner startup/admission.

    Leave 15 seconds after the observed admission time, below the fixture's
    60-second tool deadline. A deadline that fires before admission is a fixture
    setup failure, not proof that an in-flight writer failed to cancel.
    """
    return math.ceil(max(row["backend_entered_elapsed_seconds"] for row in previous_cases)) + 15


def require_backend_exit(*, entered, exited):
    require(entered, "owner stopped before backend admission; no in-flight cancellation was exercised")
    require(exited, "admitted backend did not exit before bridge teardown")


def actor_run(output, fixture, *, condition="complete", agent_timeout_seconds=90):
    from rpnh_erp_bench.bridge import Bridge
    from rpnh_erp_bench.native import build_spec, run_owner, safe_owner_projection
    directory = output / condition
    directory.mkdir(mode=0o700)
    profile = fixture["write_profile"](directory / "profile")
    # Both plugin and owner sockets must use real AF_UNIX and fit sun_path.
    endpoint = output / (condition + ".sock")
    require(len(os.fsencode(endpoint)) <= 107, "choose a shorter --output path for AF_UNIX")
    require(len(os.fsencode(directory / "r" / "owner.sock")) <= 107, "choose a shorter --output path for owner AF_UNIX")
    backend = FakeBackend(block=condition != "complete")
    prompt = "Synthetic fixture only: validate the supplied ordinary order, run the harmless script, and report wiring evidence."
    with Bridge(endpoint, "synthetic-" + condition, backend):
        spec = build_spec(directory / "r", profile, endpoint, "synthetic-" + condition, prompt)
        require(spec.prompt == prompt and spec.max_attempts_per_stage is None, "native spec altered fixture limits or prompt")
        owner_started = time.monotonic()
        result = run_owner(spec, directory / "control", agent_timeout_seconds=agent_timeout_seconds,
                           stop_requested=backend.entered.is_set if condition == "stop" else lambda: False)
        write_json(directory / "raw-owner.json", result)
        # Cancellation crosses the real Unix channel; bridge teardown must not
        # be what first makes the fake backend quiescent.
        entered = backend.entered.is_set()
        exited = backend.exited.wait(3) if entered else False
        entered_elapsed = backend.entered_at - owner_started if entered else None
        # Preserve diagnostics even when an assertion fails, before teardown can
        # set the bridge cancellation flag and change the observed exit state.
        write_json(directory / "backend.json", {"calls": backend.calls, "cancel_observed": backend.cancel_observed,
            "entered": entered, "exited_before_teardown": exited,
            "entered_elapsed_seconds": entered_elapsed, "agent_timeout_seconds": agent_timeout_seconds})
        require_backend_exit(entered=entered, exited=exited)
    require(result["owner_quiescent"] and result["process_exit_confirmed"], "owner is not quiescent")
    require(not result["forced_termination"] and result["shutdown_error"] is None, "forced or failed shutdown cannot pass")
    require(len(backend.calls) == 1, "expected exactly one synthetic script admission")
    requests = list((directory / "profile" / "transcript").glob("request-*.json"))
    counts = result["result_evidence"]["actual_model_call_counts"]
    require(counts == [len(requests), 0], "Registry counts differ from fake adapter transcript")
    receipts = managed_receipts(spec.run_dir, spec.plugin_catalog_digest)
    write_json(directory / "managed-receipts.json", receipts)
    if condition == "complete":
        terminal = result["terminal"]
        require(terminal is not None and terminal["run_outcome"] == "complete", "actor did not reach actual terminal")
        require(terminal["terminal_evidence_ref"] and terminal["terminal_result_ref"], "terminal refs are missing")
        require(counts == terminal["actual_model_call_counts"] == [3, 0], "expected exactly three fake submissions")
        require("Synthetic local adapter" in json.dumps(terminal["output"]), "actual terminal report missing")
        returned = [row["receipt"] for row in receipts if row["receipt"]["state"] == "returned"]
        require({row["selector"] for row in returned} == {"erp_bench/validate_plan", "erp_bench/erp_python"}, "managed binding receipts missing")
        require(len(returned) == 2, "unexpected managed call count")
        for row in returned:
            require(row["started_receipt_ref"] in [item["ref"] for item in receipts], "terminal receipt has no real admission")
            require(row["output"]["status"] == "completed", "managed domain result was not completed")
            if row["selector"] == "erp_bench/erp_python":
                require(row["output"]["identity"] == backend.calls[0]["identity"], "bridge/worker effect identity differs")
                require(row["output"]["execution_id"] == backend.calls[0]["execution_id"], "bridge execution identity differs")
        require(any(backend.calls[0]["execution_id"] in json.dumps(json.loads(path.read_text()))
                    for path in requests), "unique backend output never reached fake adapter")
    else:
        require(backend.cancel_observed, "backend cancellation was not observed")
        require(result["stop_reason"] == ("official_agent_timeout" if condition == "timeout" else "owner_requested"), "wrong owner stop cause")
        require(result["status"]["registry"]["execution_status"] == "stopped_by_owner", "owner stop not registered")
        require(result["terminal"] is None, "stopped synthetic writer must not claim business terminal")
    summary = safe_owner_projection(result)
    summary.update(condition=condition, plugin_catalog_digest=spec.plugin_catalog_digest,
                   fake_model_submissions=len(requests), synthetic_backend_executions=len(backend.calls),
                   backend_cancel_observed=backend.cancel_observed,
                   backend_entered_elapsed_seconds=entered_elapsed,
                   backend_exited_before_teardown=exited,
                   synthetic_agent_timeout_seconds=agent_timeout_seconds,
                   stop_reason=result["stop_reason"])
    summary["managed_receipt_refs"] = [{"ref": item["ref"], "selector": item["receipt"]["selector"],
                                       "state": item["receipt"]["state"]} for item in receipts]
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-stop", action="store_true", help="also run actual public stop and ordinary wall timeout")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parents[3]
    task = source.parent.resolve()
    output = args.output.resolve()
    require(output.is_relative_to(task) and not output.is_relative_to(source) and output != task,
            "--output must be a private task directory outside product source")
    require(not output.exists(), "--output must be fresh; prior evidence is never overwritten")
    command, identity = installed_identity()
    os.umask(0o077)
    output.mkdir(parents=True, mode=0o700)
    write_json(output / "installed.json", identity)
    (output / "tmp").mkdir(mode=0o700)
    os.environ["TMPDIR"] = str(output / "tmp")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tests" / "fake_provider.py"))
    from rpnh_erp_bench.plugin import configuration
    config = output / "plugins.json"
    write_json(config, configuration(str(output / "cli.sock"), "synthetic-cli"))
    request = output / "plan.json"
    write_json(request, fixture["plan"]())
    cli = {}
    for name, tail in (("list", ["list"]), ("check", ["check"]),
                       ("run", ["run", "erp_bench/validate_plan", "--input", str(request), "--run-dir", str(output / "pure")])):
        argv = [str(command), "plugins", "--config", str(config), *tail]
        completed = subprocess.run(argv, cwd=output, capture_output=True, text=True, timeout=60)
        write_json(output / ("cli-" + name + ".json"), {"argv": argv, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr})
        require(completed.returncode == 0, "installed rpnh plugins " + name + " failed; inspect private CLI log")
        cli[name] = json.loads(completed.stdout)
    pure = cli["run"]
    require(pure["terminal_evidence_ref"] and pure["actual_model_call_counts"] == [0, 0], "pure plugin lacks exact terminal or zero-call evidence")
    require(pure["output"]["status"] == "completed" and pure["output"]["new_spend"] == "24", "pure arithmetic result differs")
    require(cli["check"]["valid"] and cli["check"]["catalog_digest"] == cli["list"]["catalog_digest"] == pure["catalog_digest"], "CLI catalog binding differs")
    actors = [actor_run(output, fixture)]
    if args.include_stop:
        actors.append(actor_run(output, fixture, condition="stop"))
        actors.append(actor_run(output, fixture, condition="timeout",
                                agent_timeout_seconds=timeout_fixture_budget(actors)))
    summary = {"status": "passed", "condition": "explicitly_synthetic_installed_native_acceptance",
               "installed_versions": {key: value for key, value in identity.items() if key.endswith("version")},
               "pure_terminal_evidence_ref": pure["terminal_evidence_ref"],
               "pure_actual_model_call_counts": pure["actual_model_call_counts"], "actors": actors,
               "real_provider_calls": 0, "real_ERP_executions": 0, "official_benchmark_score": None,
               "claim": "Installed owner/worker/AF_UNIX managed wiring only; synthetic fixture is not real provider or ERP success."}
    write_json(output / "export_safe.json", summary)
    print(json.dumps({"status": "passed", "output": str(output), "conditions": [item["condition"] for item in actors]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
