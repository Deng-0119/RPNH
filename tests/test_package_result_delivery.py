"""CLI file-delivery checks with synthetic authorized returns, never a real run.

These deterministic tests do not start an owner, socket, model or subprocess;
their fixture references are not business-execution evidence.
"""
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from cpn.rpnh.collaboration import environment_cli as cli
from cpn.rpnh.collaboration import environment_output as delivery
from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError
from cpn.rpnh.collaboration.share_packages import canonical_bytes


SAMPLE = Path(__file__).resolve().parents[1] / "examples/package_reuse"


@pytest.fixture
def run_cli(tmp_path, monkeypatch):
    def ref(kind, name):
        return {"entity_type": kind, "logical_id": "synthetic:" + name,
                "version_id": "synthetic-version:" + name}
    value = {"schema_version": "rpnh/package_run_result/v1", "target": {"entry_id": "main"},
        "run_ref": ref("native_run_identity/v1", "run"), "task_ref": ref("task/v1", "task"),
        "net_ref": ref("net_instance/v1", "net"), "stop_reason": "terminal",
        "terminal_evidence_ref": ref("run_terminal_evidence/v1", "terminal"),
        "actual_model_call_counts": [0, 0],
        "host_python": {"executable_realpath": "/private-host-python", "prefix_realpath": "/private-prefix"},
        "terminal_result": {"status": "available", "output": {"private": "body-sentinel"}}}
    state = SimpleNamespace(launches=0, approvals=0, waits=0, approved=True,
        before_approval=lambda: None, on_wait=lambda: None, launch_error=None, wait_error=None,
        output=tmp_path / "result.json", run_dir=tmp_path / "absent-run", value=value)
    request = tmp_path / "owner-request.json"
    request.write_text('{"task_input":"synthetic request"}')
    monkeypatch.setattr(cli, "_read", lambda path, cls: SimpleNamespace(digest="synthetic-binding-digest"))

    def confirm(description, exact):
        state.approvals += 1
        state.before_approval()
        return state.approved
    monkeypatch.setattr(cli, "_confirm", confirm)

    def launch(*args, execution_context, include_terminal_result, **kwargs):
        state.launches += 1
        assert execution_context.authorize_run is not None
        if not execution_context.authorize_run("synthetic-binding-digest", {}, state.run_dir):
            raise EnvironmentContractError("RUN_AUTHORIZATION_REQUIRED", "not authorized")
        if state.launch_error:
            raise state.launch_error
        def wait():
            state.waits += 1
            state.on_wait()
            if state.wait_error:
                raise state.wait_error
            result = deepcopy(state.value)
            if not include_terminal_result:
                result.pop("terminal_result")
            return result
        return SimpleNamespace(wait=wait)
    monkeypatch.setattr(cli, "launch_package", launch)

    def invoke(*, output=True, include=True):
        args = ["run", "--lock", str(SAMPLE / "native-add-v2.lock.json"),
            "--archive", str(SAMPLE / "native-add-v2.zip"), "--binding", "synthetic-binding",
            "--resolved-selections", "synthetic-resolution", "--receipt", "synthetic-receipt",
            "--owner-request", str(request), "--run-dir", str(state.run_dir)]
        if output:
            args += ["--output", str(state.output)]
        if include:
            args += ["--include-terminal-result"]
        return cli.main(args)
    state.invoke = invoke
    state.public = {key: value for key, value in value.items()
                    if key not in {"host_python", "terminal_result"}}
    return state


def assert_public_only(capsys, state, *, reason=None):
    captured = capsys.readouterr()
    assert json.loads(captured.out) == state.public
    if reason:
        assert json.loads(captured.err) == {"error": {"reason_code": reason}}
    else:
        assert captured.err == ""
    for private in ("body-sentinel", "/private-host-python", "/private-prefix", str(state.output)):
        assert private not in captured.out + captured.err


def test_private_output_reserved_before_approval_and_written_through_same_inode(run_cli, capsys):
    state = run_cli
    identities = []
    def inspect_reservation():
        info = state.output.stat()
        identities.append((info.st_dev, info.st_ino))
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert state.output.read_bytes() == b""
        assert not state.run_dir.exists()
    state.before_approval = inspect_reservation
    assert state.invoke() == 0
    info = state.output.stat()
    assert identities == [(info.st_dev, info.st_ino)]
    assert state.output.read_bytes() == canonical_bytes(state.value)
    assert state.launches == state.approvals == state.waits == 1
    assert_public_only(capsys, state)


def test_default_no_output_keeps_original_public_projection(run_cli, capsys):
    assert run_cli.invoke(output=False, include=False) == 0
    assert not run_cli.output.exists()
    assert run_cli.launches == run_cli.approvals == run_cli.waits == 1
    assert_public_only(capsys, run_cli)


def test_explicit_output_without_terminal_opt_in_stays_body_free(run_cli, capsys):
    assert run_cli.invoke(include=False) == 0
    saved = json.loads(run_cli.output.read_bytes())
    assert "terminal_result" not in saved
    assert saved["host_python"] == run_cli.value["host_python"]
    assert_public_only(capsys, run_cli)


@pytest.mark.parametrize("stop_reason", ["stopped_by_owner", "quiescent_marking"])
@pytest.mark.parametrize("delivery_fails", [False, True])
def test_delivery_preserves_nonterminal_stop_state(run_cli, capsys, monkeypatch, stop_reason, delivery_fails):
    run_cli.value.update(stop_reason=stop_reason, terminal_evidence_ref=None,
                         terminal_result={"status": "not_terminal"})
    run_cli.public.update(stop_reason=stop_reason, terminal_evidence_ref=None)
    if delivery_fails:
        def fsync(fd):
            raise OSError(errno.ENOSPC, "synthetic disk failure")
        monkeypatch.setattr(delivery.os, "fsync", fsync)
    assert run_cli.invoke() == (2 if delivery_fails else 0)
    assert run_cli.launches == run_cli.waits == 1
    if not delivery_fails:
        assert json.loads(run_cli.output.read_bytes()) == run_cli.value
    assert_public_only(capsys, run_cli,
        reason="ENVIRONMENT_RESULT_DELIVERY_FAILED" if delivery_fails else None)


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling_symlink"])
def test_existing_output_is_refused_before_launch_or_approval(run_cli, capsys, kind):
    path = run_cli.output
    target = path.with_name("other")
    if kind == "file":
        path.write_text("keep me")
    elif kind == "directory":
        path.mkdir()
    else:
        if kind == "symlink":
            target.write_text("keep target")
        path.symlink_to(target)
    assert run_cli.invoke() == 2
    assert run_cli.launches == run_cli.approvals == 0
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"]["reason_code"] == "ENVIRONMENT_OUTPUT_EXISTS"
    if kind == "file":
        assert path.read_text() == "keep me"
    elif kind == "directory":
        assert path.is_dir()
    else:
        assert path.is_symlink()
        assert target.exists() == (kind == "symlink")


@pytest.mark.parametrize("kind", ["missing", "under_run", "parent_file", "denied"])
def test_unusable_destination_fails_before_launch_without_creating_parents(run_cli, capsys, monkeypatch, kind):
    if kind in {"missing", "under_run"}:
        parent = run_cli.run_dir if kind == "under_run" else run_cli.output.parent / "absent-parent"
        run_cli.output = parent / "result.json"
    elif kind == "parent_file":
        parent = run_cli.output.parent / "parent-file"
        parent.write_text("keep parent")
        run_cli.output = parent / "result.json"
    else:
        actual = delivery.os.open
        def denied(path, flags, *args, **kwargs):
            if flags & os.O_CREAT:
                raise PermissionError(errno.EACCES, "private path must not be echoed")
            return actual(path, flags, *args, **kwargs)
        monkeypatch.setattr(delivery.os, "open", denied)
    assert run_cli.invoke() == 2
    assert run_cli.launches == run_cli.approvals == 0
    assert not run_cli.output.exists()
    assert not run_cli.run_dir.exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "private path" not in captured.err


@pytest.mark.parametrize("failure", ["cancelled", "launch", "wait", "interrupt"])
def test_unsuccessful_launch_or_wait_cleans_only_empty_reservation(run_cli, capsys, failure):
    if failure == "cancelled":
        run_cli.approved = False
        expected = 4
    elif failure == "launch":
        run_cli.launch_error = EnvironmentContractError("HOST_PROFILE_UNAVAILABLE", "synthetic failure")
        expected = 3
    elif failure == "wait":
        run_cli.wait_error = EnvironmentContractError("ENVIRONMENT_HOST_PROTOCOL_ERROR", "synthetic failure")
        expected = 2
    else:
        run_cli.wait_error = KeyboardInterrupt()
    if failure == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            run_cli.invoke()
    else:
        assert run_cli.invoke() == expected
    assert not run_cli.output.exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", ["write", "short_write", "flush", "fsync", "close", "serialize"])
def test_late_delivery_failure_retains_safe_returned_refs_without_rerun(run_cli, capsys, monkeypatch, failure):
    actual_fdopen = delivery.os.fdopen
    class FaultyStream:
        def __init__(self, stream):
            self.stream = stream
        def fileno(self):
            return self.stream.fileno()
        def write(self, payload):
            if failure in {"write", "short_write"}:
                self.stream.write(payload[:10])
                self.stream.flush()
                if failure == "write":
                    raise OSError(errno.ENOSPC, "body-sentinel")
                return 10
            return self.stream.write(payload)
        def flush(self):
            if failure == "flush":
                raise OSError(errno.ENOSPC, "body-sentinel")
            return self.stream.flush()
        def close(self):
            self.stream.close()
            if failure == "close":
                raise OSError(errno.EIO, "body-sentinel")
    monkeypatch.setattr(delivery.os, "fdopen", lambda *args, **kwargs: FaultyStream(actual_fdopen(*args, **kwargs)))
    if failure == "fsync":
        def fsync(fd):
            raise OSError(errno.ENOSPC, "body-sentinel")
        monkeypatch.setattr(delivery.os, "fsync", fsync)
    if failure == "serialize":
        actual = cli.canonical_bytes
        def serialize(value):
            if isinstance(value, dict) and "terminal_result" in value:
                raise ValueError("body-sentinel")
            return actual(value)
        monkeypatch.setattr(cli, "canonical_bytes", serialize)
    assert run_cli.invoke() == 2
    assert run_cli.launches == run_cli.approvals == run_cli.waits == 1
    assert not run_cli.output.exists()
    assert_public_only(capsys, run_cli, reason="ENVIRONMENT_RESULT_DELIVERY_FAILED")


@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_late_replacement_is_not_written_or_removed(run_cli, capsys, replacement):
    target = run_cli.output.with_name("other")
    target.write_text("keep target")
    def replace():
        run_cli.output.unlink()
        if replacement == "file":
            run_cli.output.write_text("keep replacement")
        else:
            run_cli.output.symlink_to(target)
    run_cli.on_wait = replace
    assert run_cli.invoke() == 2
    assert target.read_text() == "keep target"
    if replacement == "file":
        assert run_cli.output.read_text() == "keep replacement"
    else:
        assert run_cli.output.is_symlink()
    assert run_cli.launches == run_cli.waits == 1
    assert_public_only(capsys, run_cli, reason="ENVIRONMENT_RESULT_DELIVERY_FAILED")


def test_cancelled_run_does_not_remove_replacement(run_cli, capsys):
    def replace_before_cancellation():
        run_cli.output.unlink()
        run_cli.output.write_text("keep replacement")
    run_cli.before_approval = replace_before_cancellation
    run_cli.approved = False
    assert run_cli.invoke() == 4
    assert run_cli.output.read_text() == "keep replacement"
    assert run_cli.waits == 0
    assert capsys.readouterr().out == ""


def test_parent_replacement_does_not_redirect_delivery(run_cli, capsys):
    parent = run_cli.output.parent / "chosen"
    parent.mkdir()
    run_cli.output = parent / "result.json"
    moved = parent.with_name("moved")
    def move_parent():
        parent.rename(moved)
        parent.mkdir()
        run_cli.output.write_text("keep replacement")
    run_cli.on_wait = move_parent
    assert run_cli.invoke() == 2
    assert run_cli.output.read_text() == "keep replacement"
    assert not (moved / "result.json").exists()
    assert_public_only(capsys, run_cli, reason="ENVIRONMENT_RESULT_DELIVERY_FAILED")


def test_unremovable_partial_file_still_reports_failed_delivery(run_cli, capsys, monkeypatch):
    def fsync(fd):
        raise OSError(errno.ENOSPC, "synthetic disk failure")
    def unlink(*args, **kwargs):
        raise PermissionError(errno.EACCES, "synthetic cleanup failure")
    monkeypatch.setattr(delivery.os, "fsync", fsync)
    monkeypatch.setattr(delivery.os, "unlink", unlink)
    assert run_cli.invoke() == 2
    assert run_cli.output.exists()
    assert stat.S_IMODE(run_cli.output.stat().st_mode) == 0o600
    assert_public_only(capsys, run_cli, reason="ENVIRONMENT_RESULT_DELIVERY_FAILED")
