"""Local no-model bridge check using the installed native PluginContext.

This is NOT a native worker/Registry integration test and is never reported as
an agent benchmark score. It does exercise all three real upstream API tools.
"""
from __future__ import annotations
import inspect
import json
from pathlib import Path
import threading
import time
from .broker import Broker
from .constants import TOOLS
from .io import write_new
from .plugin import api_search_handler, api_fetch_handler, base64_encode_handler


def acceptance(upstream, work: Path) -> dict:
    from cpn.plugins.api import PluginContext
    target = work / "bridge-smoke"
    target.mkdir(parents=True, exist_ok=False)
    # Synthetic non-benchmark world, not a hidden solution to any scored task.
    row = {"prompt": [{"role": "user", "content": "Non-scored bridge acceptance."}],
           "info": {"initial_state": {"gmail": {}}, "assertions": [], "zapier_tools": []}}
    state = upstream.start(row)
    signatures = inspect.signature(upstream.functions["base64_encode"]).parameters
    required = [name for name, p in signatures.items() if p.default is inspect.Parameter.empty]
    if len(required) != 1:
        raise ValueError("unexpected base64_encode required-parameter surface")
    arguments = {"api_search": {"query": "gmail list messages", "top_k": 3},
                 "api_fetch": {"method": "GET", "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages"},
                 "base64_encode": {required[0]: "bridge acceptance"}}
    handlers = {"api_search": api_search_handler, "api_fetch": api_fetch_handler,
                "base64_encode": base64_encode_handler}
    direct_state = upstream.start(row)
    records = []
    with Broker(upstream, state, target, "smoke-no-model") as broker:
        for i, tool in enumerate(TOOLS):
            # IDs here are deliberately scripted test identities, not attributed
            # to an actual admitted Petri firing.
            context = PluginContext(config={"endpoint": broker.endpoint, "run_id": broker.run_id},
                    resources=(), operation_id=tool, invocation_id="scripted-invocation",
                    firing_id="scripted-firing", call_id=f"scripted-call-{i}",
                    cancel=threading.Event(), deadline=time.monotonic()+60)
            output = handlers[tool](context, arguments[tool])
            native = upstream.dispatch(direct_state, tool, arguments[tool])
            if output["raw_result"] != native:
                raise AssertionError(f"{tool}: bridge changed the native response")
            if tool == "api_fetch" and "error" in json.loads(native):
                raise AssertionError("synthetic subscribed-service read was not available")
            records.append({"tool": tool, "raw_response_equal_to_native": True,
                            "sequence": output["witness_request_sequence"]})
    if upstream.dump_world(state) != upstream.dump_world(direct_state):
        raise AssertionError("bridge and direct upstream calls produced different worlds")
    report = {"schema": "rpnh-ab/bridge-smoke/v1", "status": "passed", "records": records,
              "model_calls": 0, "real_upstream_used": True, "native_worker_started": False,
              "native_registry_admission_tested": False, "scored_benchmark_tasks": 0}
    write_new(target / "report.json", report)
    return report
