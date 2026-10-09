"""Separate native WebSocket RPC probe. This is not a stock TUI request trace.

Requires separate native-environment authorization. Never runs Codex, a model,
TaskControl.start or a child. Operates only on the prepared synthetic cold root.
"""
from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((Path(__file__).resolve().parent.parent / "file-manifest.json").read_text())
    for row in manifest["source_files"]:
        if hashlib.sha256((args.repo / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit("Frozen source differs: " + row["path"])
    fixture = json.loads((args.fixture / "gate.json").read_text())
    if fixture["source_fingerprint"] != manifest["source_fingerprint"] or fixture["pending_start"]:
        raise SystemExit("Matching synthetic cold fixture required")
    sys.path.insert(0, str(args.repo.resolve()))
    import websockets
    from cpn.frontend.codex_app_server import CodexAppServer
    from cpn.frontend.codex_history import HistoryCursor, public_id
    from cpn.rpnh.task_control import TaskControl
    def forbidden(*a, **kw):
        raise RuntimeError("Object RPC gate forbids child/model launch")
    old_start = TaskControl.start
    TaskControl.start = forbidden
    class ReadOnlyServer(CodexAppServer):
        async def _handle_request(self, ws, ident, method, params):
            if method not in {"initialize", "thread/resume", "thread/items/list"}:
                raise ValueError("Object RPC probe is read-only")
            return await super()._handle_request(ws, ident, method, params)
    async def run():
        evidence = []
        token, expected_thread = None, None
        for selector in ("candidate-0.161.0", "pinned-0.155.0"):
            server = ReadOnlyServer(args.fixture / "session", args.fixture / "profiles/execution/local-test.json",
                                    compatibility_profile=selector)
            try:
                state = next(iter(server._threads.values()))
                assert expected_thread in (None, state.thread_id)
                expected_thread = state.thread_id
                with tempfile.TemporaryDirectory(prefix="rpnh-object-probe-") as raw:
                    path = Path(raw) / "rpc.sock"
                    async with websockets.unix_serve(server.handle, str(path)):
                        path.chmod(0o600)
                        async with websockets.unix_connect(str(path)) as ws:
                            counter = 0
                            async def rpc(method, params):
                                nonlocal counter
                                counter += 1
                                await ws.send(json.dumps({"id": counter, "method": method, "params": params}))
                                result = json.loads(await ws.recv())
                                assert result.get("id") == counter
                                return result
                            await rpc("initialize", {"clientInfo": {"name": "codex-tui", "version": server.compatibility_profile.version}})
                            await ws.send(json.dumps({"method": "initialized"}))
                            thread, turn = state.thread_id, public_id(state.thread_id, 3, "turn")
                            anchor = {"type": "item", "itemId": public_id(thread, 3, "user")}
                            result = await rpc("thread/items/list", {"threadId": thread, "turnId": turn, "cursor": anchor, "sortDirection": "asc", "limit": 1})
                            if selector.startswith("candidate"):
                                page = result["result"]
                                assert len(page["data"]) == 1 and page["nextCursor"] is None
                                assert page["data"][0]["item"]["id"] == public_id(thread, 3, "agent")
                                assert page["data"][0]["startedAtMs"] is None and page["data"][0]["completedAtMs"] is None
                                token = page["backwardsCursor"]
                                assert HistoryCursor.decode(token).anchor.inclusive
                                bad = await rpc("thread/items/list", {"threadId": thread, "cursor": anchor})
                                assert bad["error"]["code"] == -32602
                            else:
                                assert result["error"]["code"] == -32602
                            reverse = (await rpc("thread/items/list", {"threadId": thread, "turnId": turn,
                                "cursor": token, "sortDirection": "desc"}))["result"]
                            assert [e["item"]["id"] for e in reverse["data"]] == [public_id(thread, 3, k) for k in ("agent", "user")]
                            evidence.append({"profile": selector, "thread_id": thread, "safe_slot_count": 2,
                                             "token_cut_ordinal": HistoryCursor.decode(token).cut.view.through_ordinal,
                                             "checks": "object profile gate/exclusive and same-root v1 string revalidation"})
            finally:
                server.close()
        return evidence
    try:
        result = asyncio.run(run())
        with args.output.open("x") as output:
            json.dump({"scope": "native WebSocket RPC only, not stock TUI", "source_fingerprint": manifest["source_fingerprint"],
                       "result": "PROBE_PASSED", "lanes": result, "stock_native_certification": "NOT_ESTABLISHED"}, output, indent=2)
    finally:
        TaskControl.start = old_start


if __name__ == "__main__":
    main()
