"""OS process birth identity for local task handles, without Registry authority.

Linux keeps its existing proc start-tick identity.  Darwin uses libproc's
``PROC_PIDTBSDINFO`` birth timestamp, not a PID-existence or ``ps`` heuristic.
An observation failure is distinct from an observed absent process.

Darwin ABI references:
https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h
https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c
"""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
from pathlib import Path
import sys
from typing import Mapping


LINUX_IDENTITY = "linux_proc_start_ticks/v1"
DARWIN_IDENTITY = "darwin_proc_bsd_start_time/v1"


class ProcessIdentityUnavailable(RuntimeError):
    """The OS did not establish whether the recorded process is still present."""


def _integer(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_process_identity(value: object) -> dict[str, str | int]:
    """Read the small, versioned non-business birth identity document."""
    if not isinstance(value, Mapping):
        raise ValueError("process birth identity must be a mapping")
    kind = value.get("kind")
    if kind == LINUX_IDENTITY:
        if set(value) != {"kind", "start_ticks"} or not _integer(value["start_ticks"]):
            raise ValueError("Linux process birth identity is invalid")
    elif kind == DARWIN_IDENTITY:
        if (set(value) != {"kind", "seconds", "microseconds"}
                or not _integer(value["seconds"], minimum=1)
                or not _integer(value["microseconds"])
                or value["microseconds"] >= 1_000_000):
            raise ValueError("Darwin process birth identity is invalid")
    else:
        raise ValueError("process birth identity kind is unsupported")
    return dict(value)


def _pid(pid: object) -> int:
    if not _integer(pid, minimum=1) or pid > 2**31 - 1:
        raise ValueError("process identity requires one positive PID")
    return pid


def _missing_proc_entry(pid: int, cause: OSError) -> None:
    # A missing /proc entry can also mean that procfs is unavailable/restricted.
    # PID existence is used only to reject that ambiguity, never to establish
    # identity or authorize a signal.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError as exc:
        raise ProcessIdentityUnavailable(
            f"process {pid} birth identity is unavailable") from exc
    raise ProcessIdentityUnavailable(
        f"process {pid} exists but its birth identity is unavailable") from cause


def _linux_process_identity(pid: int) -> dict[str, str | int] | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        return _missing_proc_entry(pid, exc)
    except (OSError, UnicodeError) as exc:
        raise ProcessIdentityUnavailable(
            f"process {pid} birth identity is unavailable") from exc
    try:
        prefix, separator, _rest = value.partition(" (")
        closing = value.rfind(")")
        if not separator or int(prefix) != pid or closing < len(prefix) + 2:
            raise ValueError("malformed proc stat header")
        tail = value[closing + 2:].split()
        ticks = int(tail[19])
        identity = {"kind": LINUX_IDENTITY, "start_ticks": ticks}
        return validate_process_identity(identity)
    except (TypeError, ValueError, IndexError) as exc:
        raise ProcessIdentityUnavailable(
            f"process {pid} birth identity is malformed") from exc


class _ProcBsdInfo(ctypes.Structure):
    # Darwin's public proc_bsdinfo ABI: MAXCOMLEN=16; uid_t/gid_t are uint32.
    _fields_ = [
        (name, ctypes.c_uint32) for name in (
            "pbi_flags", "pbi_status", "pbi_xstatus", "pbi_pid", "pbi_ppid",
            "pbi_uid", "pbi_gid", "pbi_ruid", "pbi_rgid", "pbi_svuid",
            "pbi_svgid", "rfu_1")
    ] + [
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
    ] + [
        (name, ctypes.c_uint32) for name in (
            "pbi_nfiles", "pbi_pgid", "pbi_pjobc", "e_tdev", "e_tpgid")
    ] + [
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


@lru_cache(maxsize=1)
def _proc_pidinfo():
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pidinfo
        function.argtypes = (
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int)
        function.restype = ctypes.c_int
        return function
    except (OSError, AttributeError) as exc:
        raise ProcessIdentityUnavailable("Darwin libproc identity API is unavailable") from exc


def _darwin_process_identity(pid: int) -> dict[str, str | int] | None:
    info = _ProcBsdInfo()
    function = _proc_pidinfo()
    ctypes.set_errno(0)
    count = function(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    error = ctypes.get_errno()
    if count == 0 and error == errno.ESRCH:
        return None
    if count != ctypes.sizeof(info):
        raise ProcessIdentityUnavailable(
            f"process {pid} Darwin birth identity is unavailable (errno {error})")
    try:
        if info.pbi_pid != pid:
            raise ValueError("libproc returned a different PID")
        return validate_process_identity({
            "kind": DARWIN_IDENTITY,
            "seconds": int(info.pbi_start_tvsec),
            "microseconds": int(info.pbi_start_tvusec),
        })
    except ValueError as exc:
        raise ProcessIdentityUnavailable(
            f"process {pid} Darwin birth identity is malformed") from exc


def read_process_identity(pid: int) -> dict[str, str | int] | None:
    """Return an exact observed birth identity; None means observed absent."""
    pid = _pid(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    raise ProcessIdentityUnavailable("process birth identity is unsupported on this platform")


def linux_start_ticks(pid: int) -> int | None:
    """Compatibility reader for v1/v2 Linux task-handle manifests."""
    if not sys.platform.startswith("linux"):
        raise ProcessIdentityUnavailable(
            "a Linux task process identity cannot be established on this platform")
    identity = _linux_process_identity(_pid(pid))
    return None if identity is None else int(identity["start_ticks"])


def process_identity_matches(pid: int, expected: object) -> bool:
    try:
        expected = validate_process_identity(expected)
    except (TypeError, ValueError) as exc:
        raise ProcessIdentityUnavailable("recorded process birth identity is unavailable") from exc
    kind = expected["kind"]
    if ((kind == LINUX_IDENTITY and not sys.platform.startswith("linux"))
            or (kind == DARWIN_IDENTITY and sys.platform != "darwin")):
        raise ProcessIdentityUnavailable(
            "recorded process birth identity belongs to another platform")
    actual = read_process_identity(pid)
    return actual is not None and actual == expected


__all__ = (
    "DARWIN_IDENTITY", "LINUX_IDENTITY", "ProcessIdentityUnavailable",
    "linux_start_ticks", "process_identity_matches", "read_process_identity",
    "validate_process_identity",
)
