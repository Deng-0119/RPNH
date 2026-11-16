"""Actual Linux adapter trees share the caller PGID and remain invocation-owned."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from cpn.llm_adapters.local_process import _run_bounded

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux subreaper contract")

_PROGRAM = r"""
import json,os,signal,sys,time
from pathlib import Path
path,mode=Path(sys.argv[1]),sys.argv[2]
def record(label):
    with path.open("a") as stream:
        stream.write(json.dumps({"label":label,"pid":os.getpid(),"pgid":os.getpgrp()})+"\n")
sys.stdin.buffer.read()
record("adapter")
first=os.fork()
if first==0:
    record("child")
    second=os.fork()
    if second==0:
        record("grandchild")
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        while True: time.sleep(0.05)
    os._exit(0)
deadline=time.monotonic()+3
while len(path.read_text().splitlines())<3:
    if time.monotonic()>deadline: raise RuntimeError("fixture child did not start")
    time.sleep(0.01)
record("ready")
if mode=="success":
    print("complete",flush=True)
    print("diagnostic",file=sys.stderr,flush=True)
elif mode=="failure":
    sys.exit(7)
elif mode=="signal":
    os.kill(os.getpid(),signal.SIGUSR1)
elif mode=="overflow":
    sys.stdout.write("x"*1048576)
    sys.stdout.flush()
else:
    while True: time.sleep(0.05)
"""


@pytest.mark.parametrize("mode,expected_failure", [
    ("success", None), ("failure", "local_process_error"),
    ("signal", "local_process_error"), ("timeout", "timeout"),
    ("interrupt", "owner_interrupted"), ("overflow", "response_too_large"),
])
def test_owned_multigeneration_tree_cleanup_preserves_caller_group(tmp_path, mode, expected_failure):
    marker = tmp_path / "pids.jsonl"
    script = tmp_path / "provider.py"
    script.write_text(_PROGRAM)
    unrelated = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                                 start_new_session=False)
    def interrupted():
        return mode == "interrupt" and marker.exists() and len(marker.read_text().splitlines()) == 4
    try:
        assert os.getpgid(unrelated.pid) == os.getpgrp()
        code, body, failure, stderr, submitted = _run_bounded(
            (sys.executable, "-B", str(script), str(marker), mode), {}, b"request",
            cwd=tmp_path, timeout_seconds=1 if mode == "timeout" else 5,
            max_stdout_bytes=64, interruption_requested=interrupted)
        assert failure == expected_failure
        assert submitted is True
        rows = [json.loads(line) for line in marker.read_text().splitlines()]
        assert {row["label"] for row in rows} == {"adapter", "child", "grandchild", "ready"}
        assert {row["pgid"] for row in rows} == {os.getpgrp()}
        for pid in {row["pid"] for row in rows}:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        assert unrelated.poll() is None
        if mode == "success":
            assert (code, body, stderr) == (0, b"complete\n", b"diagnostic\n")
        else:
            assert body is None
        if mode == "failure":
            assert code == 7
        elif mode == "signal":
            assert code == -signal.SIGUSR1
        elif mode in {"timeout", "interrupt"}:
            assert code == -signal.SIGKILL
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_missing_target_is_unavailable_before_any_request_submission(tmp_path):
    result = _run_bounded((str(tmp_path / "absent-executable"),), {}, b"request",
                          cwd=tmp_path, timeout_seconds=5, max_stdout_bytes=64)
    assert result == (None, None, "local_process_unavailable", b"", False)


def test_interrupt_during_supervisor_start_does_not_submit(tmp_path):
    calls = 0
    marker = tmp_path / "submitted"
    def interrupted():
        nonlocal calls
        calls += 1
        return calls >= 2
    code, body, failure, stderr, submitted = _run_bounded(
        (sys.executable, "-B", "-c",
         "import pathlib,sys; sys.stdin.buffer.read(); pathlib.Path(sys.argv[1]).write_text(\x27unexpected\x27)",
         str(marker)), {}, b"request", cwd=tmp_path, timeout_seconds=5,
        max_stdout_bytes=64, interruption_requested=interrupted)
    assert (code, body, failure, stderr, submitted) == (None, None, "owner_interrupted", b"", False)
    assert not marker.exists()


@pytest.mark.parametrize("environment", [{}, {"LANG": "C", "EXPLICIT_VALUE": "synthetic"}])
def test_non_python_target_receives_exact_environment_without_locale_coercion(tmp_path, environment):
    import shutil
    executable = shutil.which("env")
    assert executable is not None
    code, body, failure, stderr, _submitted = _run_bounded(
        (executable, "-0"), environment, b"", cwd=tmp_path,
        timeout_seconds=5, max_stdout_bytes=1024)
    assert (code, failure, stderr) == (0, None, b"")
    observed = dict(entry.decode().split("=", 1) for entry in body.split(b"\0") if entry)
    assert observed == environment
