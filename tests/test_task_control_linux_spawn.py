"""Offline Linux task launch boundary: real child, no task/provider execution."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from cpn.rpnh import task_control as module
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
from cpn.rpnh.task_control import TaskControl


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("mode", ["fresh", "resume", "reopen"])
def test_spawn_preserves_platform_session_policy_and_arguments(
        tmp_path, monkeypatch, platform, mode):
    monkeypatch.setattr(module, "sys", SimpleNamespace(
        platform=platform, executable=sys.executable))
    monkeypatch.setattr("cpn.rpnh.update_install.worker_lease", lambda: None)
    calls = []
    process = object()

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    control = TaskControl(tmp_path, popen_factory=spawn)
    spec = tmp_path / "spec.json"
    extra = {} if mode != "reopen" else dict(
        checkpoint_version_id="checkpoint", reopen_command_id="command",
        reopen_reason="reason")
    assert control._spawn("task-test", spec, tmp_path / "worker.log",
                          resume=mode == "resume", **extra) is process
    argv, kwargs = calls[0]
    expected = [sys.executable, "-m", "cpn.rpnh.task_worker"]
    if mode == "resume":
        expected += ["--resume"]
    elif mode == "reopen":
        expected += ["--reopen", "checkpoint", "command", "reason"]
    assert argv == [*expected, str(spec)]
    assert kwargs["start_new_session"] is (platform != "linux")
    assert kwargs["pass_fds"] == ()
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["stdout"].closed


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs identity")
def test_linux_real_child_inherits_group_identity_and_lease_fds(tmp_path, monkeypatch):
    import fcntl

    lease_path = tmp_path / "update.lease"
    lease = lease_path.open("a+b")
    fcntl.flock(lease, fcntl.LOCK_SH)
    lease_fd = lease.fileno()
    lease_stat = os.fstat(lease_fd)
    monkeypatch.setattr("cpn.rpnh.update_install.worker_lease", lambda: lease)
    captured = {}
    children = []
    # Replace only the task execution boundary. The child uses the real launch
    # claim and inherited descriptors, without entering Registry/provider work.
    child = r'''
import json, os, signal, sys
from pathlib import Path
from cpn.rpnh.agent_tasks import AgentTaskSpec
from cpn.rpnh.task_control import claim_task_worker_launch
from cpn.rpnh.process_identity import linux_start_ticks
lease_fd, lock_fd = int(sys.argv[1]), int(sys.argv[2])
spec_path = Path(sys.argv[3])
spec = AgentTaskSpec.from_worker_document(
    json.loads(spec_path.read_text()), document_root=spec_path.parent)
claim = claim_task_worker_launch(spec_path, spec, resume=False, inherited_lock_fd=lock_fd)
assert claim is not None
signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
info = os.fstat(lease_fd)
print(json.dumps(dict(pid=os.getpid(), pgid=os.getpgrp(), sid=os.getsid(0),
    ticks=linux_start_ticks(os.getpid()), lease_fd=lease_fd, lock_fd=claim.fileno(),
    lease_device=info.st_dev, lease_inode=info.st_ino)), flush=True)
signal.pause()
'''

    def spawn(argv, **kwargs):
        assert argv[:3] == [sys.executable, "-m", "cpn.rpnh.task_worker"]
        assert argv[3] == "--launch-lock-fd"
        lock_fd = int(argv[4])
        assert kwargs["pass_fds"] == (lock_fd, lease_fd)
        captured.update(kwargs)
        captured["lock_fd"] = lock_fd
        process = subprocess.Popen(
            [sys.executable, "-c", child, str(lease_fd), str(lock_fd), argv[5]],
            **kwargs)
        children.append(process)
        return process

    control = TaskControl(tmp_path, popen_factory=spawn, channel_ready=lambda _p: True)
    sibling = subprocess.Popen(
        [sys.executable, "-c", "import signal; print('ready', flush=True); signal.pause()"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    children.append(sibling)
    try:
        assert sibling.stdout.readline() == "ready\n"
        assert os.getpgid(sibling.pid) == os.getpgrp()
        handle = control.start(AgentTaskSpec(
            run_dir=tmp_path / "run", prompt="Offline launch boundary.",
            stages=(AgentStage("worker", "No provider execution."),),
            execution_config_path=tmp_path / "unused.json"))
        assert handle.process is children[1]
        deadline = time.monotonic() + 10
        while True:
            output = handle.log_path.read_text()
            if output.endswith("\n"):
                break
            assert handle.process.poll() is None, output
            assert time.monotonic() < deadline, output
            time.sleep(0.02)
        info = json.loads(output)
        assert info["pgid"] == os.getpgrp()
        assert info["sid"] == os.getsid(0)
        assert info["pid"] == handle.process.pid
        assert info["ticks"] == module._process_start_ticks(handle.process.pid)
        assert info["lease_fd"] == lease_fd
        assert info["lock_fd"] == captured["lock_fd"]
        assert (info["lease_device"], info["lease_inode"]) == (lease_stat.st_dev, lease_stat.st_ino)
        assert lease.closed and captured["stdout"].closed
        with pytest.raises(OSError):
            os.fstat(captured["lock_fd"])
        manifest = json.loads((tmp_path / "manifests" / f"{handle.task_id}.json").read_text())
        assert manifest["pid"] == info["pid"]
        assert manifest["process_start_ticks"] == info["ticks"]
        assert manifest["launch_state"] == "active"
        assert module._acquire_task_launch_lock(tmp_path, handle.task_id) is None
        with lease_path.open("a+b") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert control.stop(handle.task_id)["status"] == "STOP_REQUESTED"
        assert handle.process.wait(timeout=10) == 0
        assert sibling.poll() is None
        assert os.getpgid(sibling.pid) == os.getpgrp()
        with lease_path.open("a+b") as contender:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        released = module._acquire_task_launch_lock(tmp_path, handle.task_id)
        assert released is not None
        released.close()
    finally:
        lease.close()
        sibling.stdout.close()
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
