"""Linux invocation-owned process trees that retain the caller process group.

The supervisor is only an OS lifetime owner. It does not interpret application
results, grant authority, retry commands, or change the target environment.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time


class ProcessStartError(OSError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ProcessCleanupError(RuntimeError):
    """The invocation tree has not been confirmed reaped; never retry it."""


def _environment_descriptor(document):
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        create = libc.memfd_create
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "anonymous launch descriptor unavailable") from exc
    create.argtypes = (ctypes.c_char_p, ctypes.c_uint)
    create.restype = ctypes.c_int
    descriptor = create(b"rpnh-process-launch", 1)  # MFD_CLOEXEC
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "anonymous launch descriptor unavailable")
    try:
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(json.dumps(document, ensure_ascii=True).encode("utf-8"))
            stream.seek(0)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


class SupervisedProcess:
    """Popen-shaped handle for an owned tree; pid identifies its supervisor.

    The original target exit status is preserved. Cleanup is confirmed only
    after the owned descendants have exited and been reaped.
    had_live_descendants records whether target exit required descendant cleanup;
    strict installation callers can refuse such a completion without weakening
    the existing LLM/worker result policy.
    """
    def __init__(self, process, report_fd):
        self._process = process
        self._report_fd = report_fd
        self.started = False
        self.cleanup_complete = False
        self.had_live_descendants = False

    @property
    def pid(self):
        return self._process.pid

    @property
    def returncode(self):
        return self._process.returncode

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        return self._process.stderr

    def _collect_report(self):
        if self._report_fd is None:
            return
        try:
            value = os.read(self._report_fd, 8)
            # A startup interruption can leave the ready marker unread.
            if value.startswith(b"R"):
                value = value[1:]
            self.cleanup_complete = value in (b"C0", b"C1")
            self.had_live_descendants = value == b"C1"
        finally:
            os.close(self._report_fd)
            self._report_fd = None

    def poll(self):
        result = self._process.poll()
        if result is not None:
            self._collect_report()
        return result

    def wait(self, timeout=None):
        result = self._process.wait(timeout=timeout)
        self._collect_report()
        return result

    def communicate(self, input=None, timeout=None):
        result = self._process.communicate(input=input, timeout=timeout)
        self._collect_report()
        return result

    def terminate(self):
        self._process.terminate()

    def kill(self):
        # Never SIGKILL the reaper before it has collected its owned descendants.
        stop_process(self)


def stop_process(process: SupervisedProcess, timeout=2):
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessCleanupError("owned process cleanup was not confirmed") from exc
    if process.started and not process.cleanup_complete:
        raise ProcessCleanupError("owned process cleanup was not confirmed")


def _wait_started(process, *, deadline, interrupted):
    with selectors.DefaultSelector() as ready:
        ready.register(process._report_fd, selectors.EVENT_READ)
        while True:
            if interrupted is not None and interrupted():
                raise ProcessStartError("owner_interrupted")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ProcessStartError("timeout")
            if ready.select(0.05 if remaining is None else min(remaining, 0.05)):
                if os.read(process._report_fd, 1) != b"R":
                    raise ProcessStartError("local_process_unavailable")
                process.started = True
                return


def start_process(argv, *, env, cwd=None, stdin=None, stdout=None, stderr=None,
                  pass_fds=(), executable=None, deadline=None, interrupted=None,
                  bufsize=-1):
    """Start the original target and confirm exec before exposing its input pipe.

    Uses Linux subreaper/procfs and anonymous descriptors. Non-Linux callers keep
    their existing platform implementation rather than silently losing cleanup.
    """
    if sys.platform != "linux":
        raise OSError(errno.ENOSYS, "owned inherited-group processes require Linux")
    target_fds = tuple(sorted(set(pass_fds)))
    for descriptor in target_fds:
        os.fstat(descriptor)  # Reject closed caller FDs before allocating ours.
    read_fd = write_fd = launch_fd = None
    process = None
    try:
        read_fd, write_fd = os.pipe()
        launch_fd = _environment_descriptor({
            "environment": dict(env), "pass_fds": target_fds,
            "executable": None if executable is None else os.fsdecode(executable),
        })
        command = (sys.executable, "-I", "-B", str(Path(__file__).with_name(
            "_process_supervisor.py")), str(write_fd), str(launch_fd), *argv)
        process = SupervisedProcess(subprocess.Popen(
            command, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd,
            env=dict(env), close_fds=True, bufsize=bufsize,
            pass_fds=(write_fd, launch_fd, *target_fds), start_new_session=False), read_fd)
    except BaseException:
        if read_fd is not None:
            os.close(read_fd)
        raise
    finally:
        for descriptor in (write_fd, launch_fd):
            if descriptor is not None:
                os.close(descriptor)
    try:
        _wait_started(process, deadline=deadline, interrupted=interrupted)
    except BaseException:
        try:
            stop_process(process)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        raise
    return process
