"""Exercise the pinned, real terminal client with zero model requests.

No credentials or real user profile are passed to the terminal. A server guard
rejects turn/start before it can launch work. This checks presentation, not a
live subscription or live model response.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import pty
import signal
import struct
import subprocess
import tempfile
import termios
import time

import websockets
from cpn.frontend.codex_app_server import CodexAppServer, codex_frontend_argv, resolve_codex_binary


def profile(root: Path) -> Path:
    adapter = root / "adapter.json"
    adapter.write_text(json.dumps({"schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process", "model_condition": "offline-placeholder",
        "argv": ["/usr/bin/false"], "probe_argv": ["/usr/bin/false"], "env": {}, "inherit_env": []}))
    path = root / "profile.json"
    path.write_text(json.dumps({"schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": "offline-placeholder", "adapter_config_path": "adapter.json",
        "timeout_seconds": 1, "max_output_tokens": 128, "max_response_bytes": 2048}))
    return path


async def exercise(binary: str, output: Path) -> None:
    binary = resolve_codex_binary(binary)
    output.mkdir(parents=True, exist_ok=True)
    calls = []
    model_requests = []
    terminal = bytearray()
    with tempfile.TemporaryDirectory(prefix="rpnh-tui-audit-") as directory:
        root = Path(directory)
        class GuardedServer(CodexAppServer):
            async def _handle_request(self, socket, request_id, method, params):
                calls.append(method)
                if method == "turn/start":
                    model_requests.append(method)
                    await self._error(socket, request_id, "Model execution forbidden in presentation audit")
                    return
                await super()._handle_request(socket, request_id, method, params)
        server = GuardedServer(root / "session", profile(root))
        socket_path = root / "frontend.sock"
        (root / "home").mkdir()
        (root / "codex-home").mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "TERM": "xterm-256color", "LANG": "C.UTF-8",
               "HOME": str(root / "home"), "CODEX_HOME": str(root / "codex-home")}
        required = {"initialize", "model/list", "config/read"}
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        os.set_blocking(master, False)
        process = None
        try:
            async with websockets.unix_serve(server.handle, str(socket_path)):
                process = subprocess.Popen(codex_frontend_argv(binary, socket_path, server.frontend_model_id),
                    stdin=slave, stdout=slave, stderr=slave, env=env, cwd=root, start_new_session=True)
                os.close(slave); slave = -1
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        chunk = os.read(master, 65536)
                        terminal.extend(chunk)
                        # Answer terminal device/cursor queries; this is not task text.
                        if b"\x1b[6n" in chunk:
                            os.write(master, b"\x1b[1;1R")
                    except (BlockingIOError, OSError):
                        pass
                    if required <= set(calls):
                        await asyncio.sleep(0.5)
                        break
                    await asyncio.sleep(0.05)
                assert not model_requests, "Terminal unexpectedly requested model execution"
                assert required <= set(calls), {"missing": sorted(required - set(calls)), "exit": process.poll()}
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)
            if slave >= 0:
                os.close(slave)
            os.close(master)
            (output / "terminal.txt").write_bytes(terminal)
            (output / "observations.json").write_text(json.dumps({"methods": calls,
                "required": sorted(required), "model_requests": model_requests,
                "passed": required <= set(calls) and not model_requests}, indent=2))
    print("Pinned terminal handshake completed; zero model requests.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(exercise(args.binary, args.output))
