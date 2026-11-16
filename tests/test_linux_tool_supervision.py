"""Actual Linux tool/admission trees; no provider, installer or network access."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from cpn.components import tool_executors as workers
from cpn.process_supervision import ProcessCleanupError, ProcessStartError
from cpn.rpnh import tool_environment_admission as admission
from test_workspace_call_context import _runtime

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux supervision")


def _capture(monkeypatch, module):
    calls = []
    launch = module.start_process

    def start(argv, **kwargs):
        process = launch(argv, **kwargs)
        assert os.getpgid(process.pid) == os.getpgrp()
        calls.append((process, argv, kwargs))
        return process

    monkeypatch.setattr(module, "start_process", start)
    return calls


def _reaped(calls):
    assert calls
    for process, _, _ in calls:
        assert process.poll() is not None
        assert process.cleanup_complete
        assert not Path(f"/proc/{process.pid}").exists()


def _tree_program(marker, *, finish=False, children=1):
    return (
        "import json,os,time\n"
        "pids=[]\n"
        f"for _ in range({children}):\n"
        " pid=os.fork()\n"
        " if pid == 0: time.sleep(30); os._exit(0)\n"
        " pids.append(pid)\n"
        f"with open({str(marker)!r}, 'w') as f: "
        "json.dump({'pids':[os.getpid(),*pids], 'group':os.getpgrp()}, f)\n"
        + ("" if finish else "time.sleep(30)\n")
    )


def _tree_reaped(marker):
    observed = json.loads(marker.read_text())
    assert observed["group"] == os.getpgrp()
    assert all(not Path(f"/proc/{pid}").exists() for pid in observed["pids"])


@pytest.mark.parametrize("ending", ["timeout", "interrupted", "process_limit", "normal"])
def test_workspace_owned_tree_is_reaped_without_group_escape(tmp_path, monkeypatch, ending):
    calls = _capture(monkeypatch, workers)
    environment, profile = _runtime()
    marker = tmp_path / "tree.json"
    if ending == "process_limit":
        profile = replace(profile, process_limit=2)
    script = "exec python -c " + shlex.quote(_tree_program(
        marker, finish=ending == "normal", children=4 if ending == "process_limit" else 1))
    started = time.monotonic()
    result = workers.execute_bounded_workspace_tool(
        script=script, cwd=str(tmp_path), timeout_seconds=1 if ending == "timeout" else 5,
        max_output_bytes=4096, environment=environment, profile=profile,
        interruption_requested=marker.exists if ending == "interrupted" else None,
        _use_user_namespace=False)
    assert time.monotonic() - started < 4
    assert result["command_started"] is True
    assert result["status"] == {"timeout": "timed_out", "interrupted": "interrupted"}.get(ending, "completed")
    if ending == "process_limit":
        assert "process_limited" in result["stderr"]
    if ending == "normal":
        assert result["exit_code"] == 0
        assert calls[0][0].had_live_descendants
    _reaped(calls)
    _tree_reaped(marker)


def test_workspace_keeps_network_memory_write_and_output_bounds(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, workers)
    environment, profile = _runtime()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    program = (
        "import os,resource,socket\n"
        f"assert resource.getrlimit(resource.RLIMIT_AS) == ({profile.memory_bytes},)*2\n"
        "assert os.environ['TMPDIR'] == os.getcwd()\n"
        "try: socket.socket()\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('network enabled')\n"
        f"try: open({str(outside)!r}, 'w')\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('outside write enabled')\n"
        "print('x'*2048)\n")
    result = workers.execute_bounded_workspace_tool(
        script="exec python -c " + shlex.quote(program), cwd=str(workspace),
        timeout_seconds=5, max_output_bytes=128, environment=environment,
        profile=profile, _use_user_namespace=False)
    assert result["exit_code"] == 0
    assert result["command_started"] is True
    assert result["output_truncated"] is True
    assert result["stdout"] == "x" * 128
    assert not outside.exists()
    _reaped(calls)


@pytest.mark.parametrize("ending", ["normal", "timeout", "interrupt"])
def test_inventory_preserves_identity_results_and_reaps_tree(tmp_path, monkeypatch, ending):
    calls = _capture(monkeypatch, workers)
    environment, profile = _runtime()
    profile = replace(profile, timeout_seconds=1)
    marker = tmp_path / "tree.json"
    original = workers._NUMERICAL_WORKER
    monkeypatch.setattr(workers, "_NUMERICAL_WORKER",
                        _tree_program(marker, finish=ending == "normal") + original)
    if ending == "interrupt":
        launch = workers.start_process

        def interrupting_start(*args, **kwargs):
            process = launch(*args, **kwargs)
            def communicate(**kwargs):
                until = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < until:
                    time.sleep(0.01)
                raise KeyboardInterrupt("caller interrupted")
            process.communicate = communicate
            return process
        monkeypatch.setattr(workers, "start_process", interrupting_start)
    started = time.monotonic()
    if ending == "normal":
        result = workers.capture_execution_environment_inventory(environment=environment, profile=profile)
        assert result["inventory_complete"] is True
        assert result["environment_name"] == environment.name
        assert calls[0][0].had_live_descendants
    elif ending == "timeout":
        with pytest.raises(workers.BoundedNumericalExecutionError) as caught:
            workers.capture_execution_environment_inventory(environment=environment, profile=profile)
        assert caught.value.code == "numerical_timeout"
    else:
        with pytest.raises(KeyboardInterrupt, match="caller interrupted"):
            workers.capture_execution_environment_inventory(environment=environment, profile=profile)
    assert time.monotonic() - started < 4
    _reaped(calls)
    _tree_reaped(marker)
    _, argv, kwargs = calls[0]
    assert argv[0] == environment.python_launcher
    assert argv[1:3] == ["-I", "-B"]
    assert kwargs["env"] == {}
    assert kwargs["cwd"] == "/"
    assert kwargs["deadline"] is not None


def test_registered_python_refuses_cleaned_leftovers_and_preserves_identity(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, admission)
    environment, _ = _runtime()
    marker = tmp_path / "tree.json"
    monkeypatch.setattr(admission, "_IDENTITY_PROGRAM",
                        _tree_program(marker, finish=True) + admission._IDENTITY_PROGRAM)
    with pytest.raises(admission.ToolEnvironmentAdmissionError, match="unfinished descendants"):
        admission._observe_registered_python(environment.environment_ref, {
            "name": environment.name, "python_executable": environment.python_executable,
            "python_prefix": environment.python_prefix})
    _reaped(calls)
    _tree_reaped(marker)
    assert calls[0][0].had_live_descendants
    assert calls[0][1][0] == environment.python_launcher
    assert calls[0][2]["env"] == {}


@pytest.mark.parametrize("lane", ["inventory", "workspace", "admission"])
def test_missing_cleanup_confirmation_never_returns_success(tmp_path, monkeypatch, lane):
    module = admission if lane == "admission" else workers
    launch = module.start_process
    calls = []

    def start(*args, **kwargs):
        process = launch(*args, **kwargs)
        calls.append(process)
        method = {"inventory": "communicate", "workspace": "poll", "admission": "wait"}[lane]
        original = getattr(process, method)
        def incomplete(*args, **kwargs):
            value = original(*args, **kwargs)
            if process.returncode is not None:
                process.cleanup_complete = False
            return value
        setattr(process, method, incomplete)
        return process

    monkeypatch.setattr(module, "start_process", start)
    environment, profile = _runtime()
    expected = admission.ToolEnvironmentAdmissionError if lane == "admission" else ProcessCleanupError
    with pytest.raises(expected, match="cleanup was not confirmed"):
        if lane == "inventory":
            workers.capture_execution_environment_inventory(environment=environment, profile=profile)
        elif lane == "workspace":
            workers.execute_bounded_workspace_tool(script="true", cwd=str(tmp_path), timeout_seconds=5,
                max_output_bytes=4096, environment=environment, profile=profile, _use_user_namespace=False)
        else:
            admission._observe_registered_python(environment.environment_ref, {
                "name": environment.name, "python_executable": environment.python_executable,
                "python_prefix": environment.python_prefix})
    assert calls and all(process.returncode is not None for process in calls)


@pytest.mark.parametrize("code", ["owner_interrupted", "timeout"])
@pytest.mark.parametrize("handshake", [b"", b"R", b"RF"])
def test_startup_stop_preserves_workspace_handshake(tmp_path, monkeypatch, code, handshake):
    environment, profile = _runtime()
    def interrupt(*args, **kwargs):
        assert kwargs["deadline"] is not None
        assert kwargs["interrupted"]()
        os.write(kwargs["pass_fds"][0], handshake)
        raise ProcessStartError(code)
    monkeypatch.setattr(workers, "start_process", interrupt)
    arguments = dict(script="true", cwd=str(tmp_path), timeout_seconds=5,
        max_output_bytes=4096, environment=environment, profile=profile, interruption_requested=lambda: True)
    if code == "timeout":
        with pytest.raises(workers.RuntimeEnvironmentFrameworkFault) as caught:
            workers.execute_bounded_workspace_tool(**arguments)
        assert caught.value.code == ("workspace_worker_unavailable" if handshake == b"R"
                                     else "workspace_command_not_started")
        result = caught.value.mechanical_result
    else:
        result = workers.execute_bounded_workspace_tool(**arguments)
    assert result["status"] == ("interrupted" if code == "owner_interrupted" else "timed_out")
    assert result["command_started"] is (handshake == b"R")


def test_process_limit_excludes_only_supervisor(tmp_path, monkeypatch):
    calls = _capture(monkeypatch, workers)
    environment, profile = _runtime()
    profile = replace(profile, process_limit=1)
    result = workers.execute_bounded_workspace_tool(script="exec sleep 0.25", cwd=str(tmp_path),
        timeout_seconds=5, max_output_bytes=4096, environment=environment,
        profile=profile, _use_user_namespace=False)
    assert result["exit_code"] == 0
    assert result["stderr"] == ""
    _reaped(calls)


@pytest.mark.parametrize("lane", ["inventory", "workspace", "admission"])
def test_stop_failure_remains_cleanup_unknown(tmp_path, monkeypatch, lane):
    module = admission if lane == "admission" else workers
    calls = _capture(monkeypatch, module)
    stop = module.stop_process
    failure = ProcessCleanupError("test cleanup confirmation unavailable")
    def unconfirmed(process):
        # Reap the actual fixture first; simulate only the missing confirmation.
        stop(process)
        raise failure
    monkeypatch.setattr(module, "stop_process", unconfirmed)
    environment, profile = _runtime()
    profile = replace(profile, timeout_seconds=1)
    if lane == "inventory":
        monkeypatch.setattr(workers, "_NUMERICAL_WORKER", "import time; time.sleep(30)")
        with pytest.raises(ProcessCleanupError) as caught:
            workers.capture_execution_environment_inventory(environment=environment, profile=profile)
        assert caught.value is failure
        assert isinstance(caught.value.__context__, workers.BoundedNumericalExecutionError)
    elif lane == "workspace":
        with pytest.raises(ProcessCleanupError) as caught:
            workers.execute_bounded_workspace_tool(script="exec sleep 30", cwd=str(tmp_path),
                timeout_seconds=1, max_output_bytes=4096, environment=environment,
                profile=profile, _use_user_namespace=False)
        assert caught.value is failure
    else:
        monkeypatch.setattr(admission, "_PROBE_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(admission, "_IDENTITY_PROGRAM", "import time; time.sleep(30)")
        with pytest.raises(admission.ToolEnvironmentAdmissionError) as caught:
            admission._observe_registered_python(environment.environment_ref, {
                "name": environment.name, "python_executable": environment.python_executable,
                "python_prefix": environment.python_prefix})
        assert caught.value.cleanup_error is failure
        assert "timed out" in str(caught.value.__cause__)
        assert "cleanup is unconfirmed" in str(caught.value)
    _reaped(calls)
