"""Adapter-side shutdown facts. No Registry writes or inferred task success.

TaskControl launches a new session. This observer verifies that ownership before
signalling and tracks descendants by pid/start-ticks, including observed session
escapees. It is a /proc observation, not a cgroup-wide containment guarantee.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import signal
from typing import Any


class NativeTerminationUnconfirmed(RuntimeError):
    """Execution may still be active; callers must not publish final snapshots."""
    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__("native shutdown is unconfirmed; no final Bank/Registry projection")


def _libc_pidfd(name: str):
    """Use glibc's pidfd API when this Python build omits its wrappers."""
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, name, None)
    if function is None:
        raise RuntimeError(
            "pidfd signalling unavailable; manual cleanup required")
    return function


def _pidfd_open(pid: int) -> int:
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    function = _libc_pidfd("pidfd_open")
    function.argtypes = (ctypes.c_int, ctypes.c_uint)
    function.restype = ctypes.c_int
    descriptor = function(pid, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def _pidfd_send_signal(descriptor: int, value: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(descriptor, value)
        return
    function = _libc_pidfd("pidfd_send_signal")
    function.argtypes = (
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(descriptor, int(value), None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent: int
    group: int
    session: int
    start_ticks: int
    state: str


def read_process(pid: int, proc_root: Path = Path('/proc')) -> ProcessIdentity | None:
    try:
        text = (proc_root / str(pid) / 'stat').read_text()
    except FileNotFoundError:
        return None
    tail = text[text.rfind(')') + 2:].split()
    if len(tail) < 20:
        raise ValueError("malformed process stat")
    return ProcessIdentity(pid, int(tail[1]), int(tail[2]), int(tail[3]),
                           int(tail[19]), tail[0])


class OwnedProcessTree:
    """Track only a verified new-session worker and observed descendants."""
    def __init__(self, process: Any, proc_root: Path = Path('/proc')):
        self.process = process
        self.proc_root = proc_root
        self.pid = getattr(process, 'pid', None)
        if type(self.pid) is not int or self.pid <= 1 or self.pid == os.getpid():
            raise ValueError("positive owned worker pid required")
        root = read_process(self.pid, proc_root)
        self.root = root
        self.identities: dict[int, int] = {}
        self.empty_observations = 0
        if root is None:
            # An already-exited leader cannot prove there are no surviving children.
            self.ownership_verified = False
        else:
            self.ownership_verified = root.session == root.pid and root.group == root.pid
            if not self.ownership_verified:
                raise ValueError("worker is not the leader of its own session")
            self.identities[root.pid] = root.start_ticks

    def sample(self) -> dict:
        try:
            table = {}
            for path in self.proc_root.iterdir():
                if path.name.isdigit():
                    item = read_process(int(path.name), self.proc_root)
                    if item is not None:
                        table[item.pid] = item
            if not self.ownership_verified:
                raise ValueError("worker identity was not observed at launch")
            root_now = table.get(self.pid)
            if (root_now is not None and self.root is not None
                    and root_now.start_ticks != self.root.start_ticks):
                raise ValueError("worker pid identity changed")
            # All same-session members belong to the verified fresh worker session.
            for item in table.values():
                if item.session == self.pid:
                    self.identities.setdefault(item.pid, item.start_ticks)
            changed = True
            while changed:
                changed = False
                for item in table.values():
                    parent = table.get(item.parent)
                    if (parent is not None
                            and self.identities.get(parent.pid) == parent.start_ticks
                            and item.pid not in self.identities):
                        self.identities[item.pid] = item.start_ticks
                        changed = True
            live = [item for item in table.values()
                    if self.identities.get(item.pid) == item.start_ticks
                    and item.state not in {'Z', 'X'}]
            root_exited = self.process.poll() is not None
            empty = root_exited and not live
            self.empty_observations = self.empty_observations + 1 if empty else 0
            return {
                'process_exit_confirmed': root_exited,
                'observed_tree_quiescent': self.empty_observations >= 2,
                'live_pids': sorted(item.pid for item in live),
                'ownership_verified': True,
                'observation_error': None,
                'scope': 'verified_session_and_observed_descendants',
                'arbitrary_detached_descendants_proven_absent': False,
            }
        except (OSError, ValueError) as exc:
            self.empty_observations = 0
            return {'process_exit_confirmed': self.process.poll() is not None,
                    'observed_tree_quiescent': False, 'live_pids': None,
                    'ownership_verified': self.ownership_verified,
                    'observation_error': type(exc).__name__ + ': ' + str(exc),
                    'scope': 'unconfirmed'}

    def send_signal(self, value: int = signal.SIGTERM) -> list[int]:
        """Recheck each observed identity; never signal an unverified process group."""
        state = self.sample()
        if state['observation_error']:
            raise ValueError(state['observation_error'])
        sent = []
        for pid in state['live_pids']:
            current = read_process(pid, self.proc_root)
            if (current is None or current.start_ticks != self.identities[pid]
                    or current.state in {'Z', 'X'}):
                continue
            try:
                # pidfd avoids signalling a reused pid after the identity check.
                fd = _pidfd_open(pid)
                try:
                    after = read_process(pid, self.proc_root)
                    if after is None or after.start_ticks != current.start_ticks:
                        continue
                    _pidfd_send_signal(fd, value)
                finally:
                    os.close(fd)
                sent.append(pid)
            except ProcessLookupError:
                continue
        return sent
