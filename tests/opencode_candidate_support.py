"""Test-only native smoke support. Importing this module executes no process.

The upstream stock binary is executed only by an explicitly selected fixture.
No provider, install, login, user Registry path or production candidate switch.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


OUTPUT_LIMIT = 131072
BOOTSTRAP = {"/config/providers", "/provider", "/agent", "/config", "/path",
             "/project/current", "/command"}
UNIMPLEMENTED = ["picker selection/effort matrix", "forced SSE reconnect/retry",
                 "failure/abort presentation", "simultaneous session isolation",
                 "long-history pagination", "active-worker recovery", "live providers",
                 "upstream plugins/tools", "OS-enforced outbound-network isolation"]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def validate_binary(path):
    """Pure filesystem preflight; no PATH fallback, shebang, or execution."""
    supplied = Path(path)
    if not supplied.is_absolute():
        raise RuntimeError("BLOCKED: --opencode-certify-binary must be absolute")
    try:
        resolved = supplied.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeError("BLOCKED: requested stock binary is missing or unreadable") from None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RuntimeError("BLOCKED: stock binary must be a readable executable file")
    with resolved.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise RuntimeError("BLOCKED: provide the actual stock Linux ELF binary, not a wrapper")
    return resolved


def terminal_text(raw):
    """Bounded diagnostic text, not a terminal emulator or screenshot."""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[ -/]*[@-~]", "", text)
    return "".join(c for c in text if c in "\n\r\t" or ord(c) >= 32)


def select_profile(version):
    from cpn.frontend.opencode_protocol import DEFAULT_PROFILE, get_opencode_profile
    if type(version) is not str:
        raise ValueError("Gate version must be an exact registered string")
    return (DEFAULT_PROFILE if type(version) is str and version == DEFAULT_PROFILE.version
            else get_opencode_profile(certification_version=version))


def valid_probe_stdout(raw, version):
    return (len(raw) <= 4096 and re.fullmatch(
        r"(?:opencode[ \t]+)?" + re.escape(version),
        raw.decode("utf-8", "replace").strip()) is not None)


class CandidateRun:
    def __init__(self, root, version, binary, lane):
        self.root = root
        self.version, self.binary, self.lane = version, binary, lane
        self.evidence_path = root / ("opencode-" + lane + "-evidence.json")
        self.secrets = [str(root)]
        self.evidence = {
            "schema": "rpnh/opencode-native-smoke/v1", "status": "not-run",
            "requested_version": version, "lane": lane,
            "backend": "ApplicationDouble" if lane == "contract" else "RegistryFrontendApplication",
            "scope": "limited smoke only; not the complete G2/G3 design",
            "unimplemented": UNIMPLEMENTED,
            "network_isolation": "isolated client config/environment; no OS network guarantee",
            "stock_provenance": "caller-provisioned ELF; official release digest not independently verified",
            "scenarios": [], "terminal": [], "routes": [],
        }

    def sanitize(self, value):
        text = json.dumps(value, ensure_ascii=True, default=str)
        # Both the password and HTTP Basic token are registered before any attach.
        for secret in sorted(set(self.secrets), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "<REDACTED>")
        return json.loads(text)

    def prepare(self):
        self.evidence["status"] = "blocked"
        if not sys.platform.startswith("linux"):
            raise RuntimeError("BLOCKED: native smoke supports Linux/WSL2 only")
        for name in ("pty", "fcntl", "termios", "jsonschema", "numpy", "scipy"):
            try:
                importlib.import_module(name)
            except ImportError:
                raise RuntimeError("BLOCKED: required preinstalled dependency missing: " + name) from None
        from cpn.frontend.opencode_launcher import check_version, isolated_environment
        from cpn.frontend.opencode_protocol import DEFAULT_PROFILE
        self.profile = select_profile(self.version)
        self.binary = validate_binary(self.binary)
        self.evidence["binary_sha256"] = sha256_file(self.binary)
        self.evidence["binary_file"] = self.binary.name
        self.evidence["profile"] = {
            "version": self.profile.version, "commit": self.profile.commit,
            "certification_only": self.profile.certification_only,
            "production_default": DEFAULT_PROFILE.version,
        }
        self.evidence["platform"] = {
            "system": platform.system(), "release": platform.release(),
            "architecture": platform.machine(), "libc": platform.libc_ver(),
            "distribution": platform.freedesktop_os_release().get("PRETTY_NAME", "unknown"),
            "wsl": "microsoft" in platform.release().lower(),
            "term": "xterm-256color", "pty_rows": 36, "pty_columns": 120,
        }
        source = Path(__file__).resolve().parents[1]
        # Require an actual checkout rather than claiming a nearby ancestor's SHA.
        top = subprocess.run(["git", "-C", str(source), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5, check=False)
        if top.returncode or Path(top.stdout.strip()).resolve() != source:
            raise RuntimeError("BLOCKED: native evidence requires an exact RPNH Git checkout")
        revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, timeout=5, check=False)
        if revision.returncode or not re.fullmatch(r"[0-9a-f]{40}\n?", revision.stdout):
            raise RuntimeError("BLOCKED: cannot establish exact RPNH revision")
        dirty = subprocess.run(["git", "-C", str(source), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5, check=False)
        if dirty.returncode:
            raise RuntimeError("BLOCKED: cannot establish RPNH worktree state")
        self.evidence["rpnh_revision"] = revision.stdout.strip()
        self.evidence["rpnh_worktree_dirty"] = bool(dirty.stdout)
        sources = {str(path.relative_to(source)): sha256_file(path)
            for directory in (source / "cpn", source / "tests")
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".json"}}
        self.evidence["source_tree_sha256"] = json_digest(sources)
        self.evidence["source_file_count"] = len(sources)
        self.evidence["gate_source_sha256"] = {name: value for name, value in sources.items()
            if "opencode" in name or name == "tests/conftest.py"}
        (self.root / "client").mkdir(mode=0o700)
        self.env = isolated_environment(self.root / "client", {"PATH": os.defpath, "TERM": "xterm-256color"})
        self.display = self.root / "client" / "display"
        self.evidence["probe"] = {"status": "not-run"}
        # Original strict version probe; never monkeypatch or bypass it.
        check_version(str(self.binary), self.env, self.display, profile=self.profile)
        self.evidence["probe"]["status"] = "passed"
        # A second, independently bounded *stored* probe captures actual stdout.
        # It must agree with the original check; no fake/wrapped version string.
        capture = subprocess.run([str(self.binary), "--version"], env=self.env,
            cwd=self.display, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=10, check=False)
        self.evidence["probe"].update({"evidence_returncode": capture.returncode,
            "stdout": capture.stdout[:4096].decode("utf-8", "replace"),
            "stderr": capture.stderr[:4096].decode("utf-8", "replace"),
            "truncated": len(capture.stdout) > 4096 or len(capture.stderr) > 4096})
        if capture.returncode or not valid_probe_stdout(capture.stdout, self.profile.version):
            raise RuntimeError("BLOCKED: evidence probe disagrees with exact profile")
        self.assert_binary_unchanged()
        self.evidence["status"] = "running"

    def assert_binary_unchanged(self):
        if sha256_file(self.binary) != self.evidence["binary_sha256"]:
            raise RuntimeError("FAIL: binary changed between probe and attach")

    def fail(self, exc):
        if self.evidence["status"] == "passed-limited-smoke":
            self.evidence["status"] = "failed"
        elif self.evidence["status"] != "blocked":
            self.evidence["status"] = "failed"
        # Avoid arbitrary exception messages that may contain private paths/data.
        self.evidence.setdefault("first_failure", {"type": type(exc).__name__,
            "stage": self.evidence.get("stage", "preflight")})

    def write_evidence(self):
        self.evidence_path.write_text(json.dumps(self.sanitize(self.evidence),
            ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        self.evidence_path.chmod(0o600)

    def passed(self):
        self.assert_binary_unchanged()
        self.evidence["status"] = "passed-limited-smoke"


class NativePTY:
    """Bounded real PTY, with all lifecycle failures propagated to the lane."""
    def __init__(self, run, server, session_id, *, continue_last=False):
        self.run, self.server = run, server
        self.output = bytearray()
        self.master = self.slave = -1
        self.process = None
        self.command = [str(run.binary), "attach", server.url, "--dir", str(run.display),
                        "--username", "rpnh"]
        self.command += ["--continue"] if continue_last else ["--session", session_id]
        self.mode = "--continue" if continue_last else "--session"

    def __enter__(self):
        import base64
        import fcntl
        import pty
        import struct
        import termios
        self.run.assert_binary_unchanged()
        password = self.server.password
        self.run.secrets.extend([password, base64.b64encode(("rpnh:" + password).encode()).decode()])
        env = {**self.run.env, "OPENCODE_SERVER_USERNAME": "rpnh",
               "OPENCODE_SERVER_PASSWORD": password}
        try:
            self.master, self.slave = pty.openpty()
            fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
            self.process = subprocess.Popen(self.command, cwd=self.run.display, env=env,
                stdin=self.slave, stdout=self.slave, stderr=self.slave, start_new_session=True)
            os.close(self.slave)
            self.slave = -1
            return self
        except BaseException:
            self.close()
            raise

    def poll(self, duration=0.1):
        import select
        ready, _, _ = select.select([self.master], [], [], duration)
        if ready:
            try:
                data = os.read(self.master, 16384)
            except OSError:
                data = b""
            if len(self.output) < OUTPUT_LIMIT:
                self.output.extend(data[:OUTPUT_LIMIT - len(self.output)])
        if self.process.poll() is not None:
            raise AssertionError("Stock TUI exited before the smoke scenario completed")

    def wait(self, condition, *, timeout=25, check=lambda: None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.poll()
            check()
            if condition():
                return
        raise AssertionError("Stock TUI smoke condition timed out")

    def settle(self, duration=0.75, *, check=lambda: None):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.poll(min(0.1, max(0, deadline - time.monotonic())))
            check()

    def send_line(self, text, *, slash=False, check=lambda: None):
        self.settle(check=check)
        os.write(self.master, text.encode("utf-8"))
        self.settle(0.35, check=check)
        before = sum(d["method"] == "POST" and d["route"] == "/session/:session/command"
                     for d in self.server.diagnostics())
        os.write(self.master, b"\r")
        if slash:
            self.settle(1.0, check=check)
            after = sum(d["method"] == "POST" and d["route"] == "/session/:session/command"
                        for d in self.server.diagnostics())
            if after == before:
                os.write(self.master, b"\r")

    def contains(self, text):
        return text in terminal_text(bytes(self.output))

    def close(self):
        forced = False
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()  # Only the owned stock TUI, never a Registry worker.
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    forced = True
                    self.process.kill()
                    self.process.wait(timeout=3)
        finally:
            for descriptor in (self.slave, self.master):
                if descriptor >= 0:
                    os.close(descriptor)
            self.slave = self.master = -1
            self.run.evidence["terminal"].append({"mode": self.mode,
                "diagnostic_text": terminal_text(bytes(self.output)),
                "captured_bytes": len(self.output), "capture_limit": OUTPUT_LIMIT,
                "returncode": self.process.poll() if self.process else None,
                "exit_method": "test-owned SIGTERM", "forced_kill": forced})
        if forced:
            raise AssertionError("Stock TUI required SIGKILL during bounded cleanup")

    def __exit__(self, _kind, _value, _traceback):
        self.close()


def observed_bootstrap(server):
    return BOOTSTRAP <= {d["route"] for d in server.diagnostics() if d["status"] == 200}


def assert_routes(server, *, registry_read=False):
    for row in server.diagnostics():
        if row["status"] >= 400:
            raise AssertionError("Unexpected stock request: " + json.dumps(row, sort_keys=True))
        if registry_read and row["method"] not in {"GET", "HEAD"} and row["route"] != "/session/:session/command":
            raise AssertionError("Unexpected write request in read lane: " + json.dumps(row, sort_keys=True))


def close_server(run, server):
    run.evidence["routes"].extend(server.diagnostics())
    run.evidence["routes"] = run.evidence["routes"][-128:]
    server.close()
    assert server._server.socket.fileno() == -1
    assert server._thread is None or not server._thread.is_alive()


class EffectGuard:
    def __init__(self):
        self.calls = Counter()
        self.observations = Counter()

    def deny(self, name):
        def blocked(*_args, **_kwargs):
            self.calls[name] += 1
            raise AssertionError("Forbidden measured effect: " + name)
        return blocked

    def assert_zero(self):
        assert not self.calls, "Measured effect boundary was called: " + repr(dict(self.calls))
