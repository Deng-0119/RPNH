"""Manual stock-0.155 TUI gate with body-free RPC evidence and execution denial.

Run only on a separately authorized local/native environment with the pinned
Codex already installed. This script does not install, log in, or call models.
It preserves the real Unix socket transport; no transport workaround exists.
"""
from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True, help="Existing codex-cli 0.155.0 binary")
    parser.add_argument("--log", type=Path, required=True, help="New body-free JSONL evidence file")
    args = parser.parse_args()
    repo = args.repo.resolve()
    package = Path(__file__).resolve().parent.parent
    manifest = json.loads((package / "file-manifest.json").read_text())
    for row in manifest["files"]:
        if hashlib.sha256((repo / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit(f"Frozen product input differs: {row['path']}")
    fixture = args.fixture.resolve()
    fixture_info = json.loads((fixture / "gate.json").read_text())
    if fixture_info["patch_sha256"] != manifest["patch_sha256"]:
        raise SystemExit("Fixture belongs to another code snapshot")
    sys.path.insert(0, str(repo))
    from cpn.frontend.codex_app_server import CodexAppServer, run_codex_frontend
    from cpn.frontend.codex_history import HistoryCursor
    from cpn.rpnh.task_control import TaskControl
    allowed = {"initialize", "account/read", "model/list", "configRequirements/read", "config/read",
               "collaborationMode/list", "hooks/list", "skills/list", "plugin/list", "apps/list",
               "permissionProfile/list", "thread/resume", "thread/loaded/list", "thread/list",
               "thread/read", "thread/turns/list", "thread/items/list"}
    output = args.log.open("x", encoding="utf-8")
    requests = {}
    def emit(value):
        output.write(json.dumps({"time": time.time(), **value}, ensure_ascii=False) + "\n")
        output.flush()
    def edge(value):
        if value is None:
            return None
        decoded = HistoryCursor.decode(value)
        return {"cut_ordinal": decoded.cut.view.through_ordinal,
                "boundary_event": str(decoded.cut.boundary_event_id), "query": decoded.query,
                "order": decoded.order, "view": decoded.items_view,
                "position": None if decoded.anchor is None else
                    [decoded.anchor.turn_ordinal, decoded.anchor.item_index, decoded.anchor.inclusive]}
    original_handle, original_send = CodexAppServer._handle_request, CodexAppServer._send
    async def observed_handle(self, websocket, request_id, method, params):
        requests[(id(websocket), request_id)] = method
        emit({"phase": "request", "id": request_id, "method": method,
              "limit": params.get("limit"), "order": params.get("sortDirection"),
              "view": params.get("itemsView"), "has_cursor": params.get("cursor") is not None,
              "has_turn_filter": params.get("turnId") is not None})
        if method not in allowed:
            emit({"phase": "execution_denied", "method": method})
            raise ValueError("Native history gate is read-only; execution and settings are disabled")
        return await original_handle(self, websocket, request_id, method, params)
    async def observed_send(self, websocket, value, **kwargs):
        # Let the real checked send complete before recording a result.
        result = await original_send(self, websocket, value, **kwargs)
        method = requests.pop((id(websocket), value.get("id")), None)
        if method is not None:
            record = {"phase": "response", "id": value.get("id"), "method": method,
                      "error": value.get("error", {}).get("code")}
            payload = value.get("result", {})
            if isinstance(payload, dict):
                if isinstance(payload.get("thread"), dict):
                    record["thread_id"] = payload["thread"].get("id")
                    record["thread_turn_count"] = len(payload["thread"].get("turns", []))
                    record["thread_status"] = payload["thread"].get("status")
                if method in {"thread/turns/list", "thread/items/list"}:
                    entries = payload.get("data", [])
                    record["count"] = len(entries)
                    record["ids"] = [(entry.get("item") or entry).get("id") for entry in entries]
                for key in ("turnsBackwardsCursor", "itemsBackwardsCursor", "nextCursor", "backwardsCursor"):
                    if key in payload and method in {"thread/resume", "thread/turns/list", "thread/items/list"}:
                        record[key] = edge(payload[key])
            emit(record)
        return result
    def forbid_launch(*args, **kwargs):
        emit({"phase": "child_launch_denied"})
        raise RuntimeError("Native history gate forbids every child/model launch")
    CodexAppServer._handle_request = observed_handle
    CodexAppServer._send = observed_send
    TaskControl.start = forbid_launch
    emit({"phase": "start", "patch_sha256": manifest["patch_sha256"],
          "fixture": fixture_info, "runtime_claim": "not established until human-reviewed run completes"})
    try:
        status = run_codex_frontend(fixture / "session", fixture / "profiles/execution/local-test.json",
                                   codex_binary=str(args.codex.resolve()), resume=True)
        emit({"phase": "exit", "exit_code": status, "provider_calls": 0,
              "note": "Execution RPCs and TaskControl.start were denied; review RPC/UI evidence separately"})
        return status
    finally:
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
