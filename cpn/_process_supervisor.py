"""Linux owner of one invocation process tree, without changing its process group.

Executed as a standalone, isolated Python script. Only this process becomes a
subreaper; the RPNH process and unrelated children keep their existing ownership.
The original command, standard streams, explicit environment and authority FDs are preserved.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

_stop_signal = 0


def _request_stop(signum, _frame):
    global _stop_signal
    _stop_signal = signum


def _children():
    # This supervisor is single-threaded and never reaps in a signal handler.
    # Each listed PID remains our waitable child until the next explicit reap.
    return [int(value) for value in Path(
        f"/proc/self/task/{os.getpid()}/children").read_text().split()]


def _exit_like(status):
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    signum = os.WTERMSIG(status)
    if signum != signal.SIGKILL:
        signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)
    return 128 + signum


def main():
    ready = int(sys.argv[1])
    environment_fd = int(sys.argv[2])
    command = sys.argv[3:]
    if not command:
        os.close(environment_fd)
        os.close(ready)
        return 126
    try:
        # Python startup may coerce its own locale. Restore the original map
        # from an anonymous private FD instead of inheriting helper os.environ.
        with os.fdopen(environment_fd, "r", encoding="utf-8") as source:
            launch = json.load(source)
        environment = launch["environment"]
        target_fds = tuple(launch["pass_fds"])
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "subreaper unavailable")
        _children()  # Verify the required ownership view before any child exists.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)
        if _stop_signal:
            return 128 + _stop_signal
        child = subprocess.Popen(command, executable=launch["executable"], env=environment,
                                 pass_fds=target_fds, close_fds=True, start_new_session=False)
        # These are target capabilities, not supervisor capabilities. Close our
        # copies without unlocking or otherwise changing their shared authority.
        for descriptor in target_fds:
            if descriptor > 2:
                os.close(descriptor)
    except (OSError, ValueError):
        return 126
    finally:
        # Successful spawn is signalled below; this branch only closes on error.
        if "child" not in locals():
            os.close(ready)
    try:
        os.write(ready, b"R")
    except OSError:
        _request_stop(signal.SIGTERM, None)
    main_status = None
    had_live_descendants = False
    while True:
        no_children = False
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                no_children = True
                break
            if pid == 0:
                break
            if pid == child.pid:
                main_status = status
                child.returncode = os.waitstatus_to_exitcode(status)
        if no_children:
            break
        if _stop_signal or main_status is not None:
            if not _stop_signal and main_status is not None:
                had_live_descendants = True
            # Killing a direct child reparents its surviving descendants here.
            # Repeat until all adopted descendants have been killed and reaped.
            for pid in _children():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(0.01)
    try:
        os.write(ready, b"C1" if had_live_descendants else b"C0")
    except OSError:
        pass
    finally:
        os.close(ready)
    if main_status is not None:
        return _exit_like(main_status)
    return 128 + _stop_signal if _stop_signal else 126


if __name__ == "__main__":
    raise SystemExit(main())
