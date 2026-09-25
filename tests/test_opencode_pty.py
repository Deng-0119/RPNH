"""Optional real pinned TUI attach, only against an in-process application double."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import time

import pytest

from cpn.frontend.opencode_http import OpenCodeHTTPServer
from cpn.frontend.opencode_launcher import check_version, isolated_environment
from cpn.frontend.opencode_protocol import OpenCodeProtocol
from test_opencode_frontend import ApplicationDouble, LocalGateway, SID


@pytest.mark.skipif(shutil.which("opencode") is None, reason="Pinned OpenCode executable is not installed")
def test_real_pinned_tui_bootstrap_command_and_prompt_submission(tmp_path):
    binary = str(Path(shutil.which("opencode")).absolute())
    env = isolated_environment(tmp_path, {**os.environ, "TERM": "xterm-256color"})
    display = tmp_path / "display"
    check_version(binary, env, display)
    app = ApplicationDouble()
    app.create_session()
    protocol = OpenCodeProtocol(LocalGateway(app), str(display))
    server = OpenCodeHTTPServer(protocol)
    master, slave = pty.openpty()
    process = None
    output = bytearray()
    success = False
    try:
        import fcntl, struct, termios
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
        server.start()
        env.update(OPENCODE_SERVER_USERNAME="rpnh", OPENCODE_SERVER_PASSWORD=server.password)
        process = subprocess.Popen([binary, "attach", server.url, "--dir", str(display), "--session", SID, "--username", "rpnh"],
                                   cwd=display, env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 25
        ready_at = None
        typed_at = None
        entered_at = None
        entered_twice = False
        command_seen_at = None
        prompt_typed_at = None
        prompt_entered = False
        required = {"/config/providers", "/provider", "/agent", "/config", "/path", "/project/current", "/command"}
        while time.monotonic() < deadline and process.poll() is None:
            ready, _, _ = select.select([master], [], [], min(0.1, max(0, deadline - time.monotonic())))
            if ready:
                try:
                    data = os.read(master, 16384)
                except OSError:
                    break
                if not data:
                    break
                if len(output) < 262144:
                    output.extend(data[:262144 - len(output)])
            diagnostics = server.diagnostics()
            observed = {d["route"] for d in diagnostics if d["status"] == 200}
            now = time.monotonic()
            if required <= observed and ready_at is None:
                # Route bootstrap can complete just before the TUI prompt has
                # entered raw input mode. Wait for the rendered prompt, then
                # separate text entry from Enter so slash completion cannot
                # consume the whole command as one startup-time key burst.
                ready_at = now
            if ready_at is not None and typed_at is None and now - ready_at >= 0.75:
                os.write(master, b"/rpnh-help")
                typed_at = now
            if typed_at is not None and entered_at is None and now - typed_at >= 0.35:
                os.write(master, b"\r")
                entered_at = now
            if entered_at is not None and not entered_twice and now - entered_at >= 1.0:
                os.write(master, b"\r")
                entered_twice = True
            command_seen = any(
                d["method"] == "POST"
                and d["route"] == "/session/:session/command"
                and d["status"] == 200
                for d in diagnostics)
            if command_seen and command_seen_at is None:
                command_seen_at = now
            if command_seen_at is not None and prompt_typed_at is None and now - command_seen_at >= 0.75:
                os.write(master, b"offline frontend prompt")
                prompt_typed_at = now
            if prompt_typed_at is not None and not prompt_entered and now - prompt_typed_at >= 0.35:
                os.write(master, b"\r")
                prompt_entered = True
            if any(
                    d["method"] == "POST"
                    and d["route"] == "/session/:session/message"
                    and d["status"] == 200
                    for d in diagnostics):
                success = True
                break
        assert success, "Pinned TUI did not complete bootstrap, command, and prompt submission"
        assert app.physical_calls == 0 and app.calls.count("submit") == 1
    finally:
        if not success:
            # First failure only, private temporary test directory, not Registry.
            evidence = tmp_path / "opencode-pty-first-failure.json"
            if not evidence.exists():
                text = output.decode("utf-8", "replace").replace(str(tmp_path), "<TEST_ROOT>").replace(server.password, "<AUTH>")
                evidence.write_text(json.dumps({"terminal": text, "routes": server.diagnostics(),
                                                "returncode": process.poll() if process else None}, indent=2))
        if process is not None and process.poll() is None:
            process.terminate()  # Test TUI only, never a Registry worker.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if slave >= 0:
            os.close(slave)
        os.close(master)
        server.close()
