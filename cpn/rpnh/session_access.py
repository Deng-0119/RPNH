"""Access and ownership helpers for one canonical :class:`MainSession` root.

Inspection deliberately does not interpret frontend-specific directory layouts.
Fresh-root reservation and existing-root ownership share one lease file.  The
``ses_`` identifier returned here is presentation-only; Registry authority
remains the canonical main-thread logical identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import fcntl
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .main_session import MainSession


@dataclass(frozen=True, slots=True)
class MainSessionRoot:
    """The resolved root and persisted execution configuration of one session."""

    root: Path
    execution_config_path: Path


def _reject_symlink(path: Path, *, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")


def inspect_main_session_root(candidate: Path) -> MainSessionRoot:
    """Validate and return one direct, canonical MainSession root without writing.

    Frontend containers such as ``candidate/threads`` are not session roots and
    are rejected rather than searched.
    """

    if not isinstance(candidate, Path):
        raise TypeError("main session candidate requires pathlib.Path")
    _reject_symlink(candidate, label="main session candidate")
    root = candidate.resolve()
    if not root.is_dir():
        raise ValueError(f"main session root is missing or not a directory: {root}")
    if (root / "threads").is_dir():
        raise ValueError("frontend container roots are not canonical MainSession roots")
    registry_root = root / MainSession.REGISTRY_DIR
    registry_internal = registry_root / ".registry_v1"
    registry_database = registry_internal / "registry.sqlite3"
    profile_path = root / MainSession.PROFILE_FILE
    for path, label in (
            (registry_root, "main session Registry root"),
            (registry_internal, "main session Registry storage"),
            (registry_database, "main session Registry database"),
            (profile_path, "main session execution profile")):
        _reject_symlink(path, label=label)
    if not registry_database.is_file():
        raise ValueError("main session Registry database is missing")
    try:
        profile = MainSession._persisted_execution_profile(root)
        execution_config_path = MainSession._persisted_execution_config_path(root)
    except ValueError as exc:
        raise ValueError("main session has an invalid persisted execution profile") from exc
    if (profile is None
            or profile.get("schema_version") != "rpnh/main_session_profile/v2"
            or execution_config_path is None):
        raise ValueError("main session requires a persisted v2 execution profile")
    return MainSessionRoot(root=root, execution_config_path=execution_config_path)


class MainSessionOwnerLease:
    """One nonblocking, process-scoped owner lease for a canonical session root."""

    LOCK_FILE = MainSession.OWNER_LOCK_FILE

    def __init__(self, root: MainSessionRoot | Path) -> None:
        record = root if isinstance(root, MainSessionRoot) else inspect_main_session_root(root)
        _reject_symlink(record.root, label="canonical main session root")
        if not record.root.is_dir():
            raise ValueError(f"main session root is missing: {record.root}")
        self.root = record.root
        self.path = self.root / self.LOCK_FILE
        _reject_symlink(self.path, label="main session owner lock")
        self._fd: int | None = None
        self._acquire()

    def _acquire(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(self.path, flags, 0o600)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass  # The requested mode is best effort on unusual filesystems.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    raise RuntimeError("main session already has an owner lease") from exc
                raise
        except Exception:
            raise
        self._fd = fd

    @classmethod
    def acquire(cls, root: MainSessionRoot | Path) -> "MainSessionOwnerLease":
        return cls(root)

    @classmethod
    def reserve_for_creation(cls, root: Path) -> "MainSessionOwnerLease":
        """Atomically reserve an absent root before Registry initialization."""

        if not isinstance(root, Path):
            raise TypeError("main session root requires pathlib.Path")
        requested = root.expanduser()
        _reject_symlink(requested, label="main session candidate")
        canonical = requested.resolve()
        try:
            canonical.mkdir(parents=True, mode=0o700)
        except FileExistsError as exc:
            raise ValueError(
                f"new main session requires an absent root: {canonical}") from exc
        canonical.chmod(0o700)
        lease = cls.__new__(cls)
        lease.root = canonical
        lease.path = canonical / cls.LOCK_FILE
        lease._fd = None
        try:
            lease._acquire()
        except Exception:
            lease.close()
            raise
        return lease

    def close(self) -> None:
        """Release the lock; the lock-file sidecar intentionally remains."""

        if self._fd is not None:
            fd, self._fd = self._fd, None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def __enter__(self) -> "MainSessionOwnerLease":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def stable_frontend_session_id(root: MainSessionRoot | Path) -> str:
    """Return a deterministic display-only ``ses_`` ID for one canonical root.

    Opening this Registry through its current read-only core mutates SQLite WAL
    sidecars, so this derives a UUIDv5 presentation identity from the resolved
    canonical path instead of touching Registry state.  It is never authority
    or a Registry logical ID.
    """

    record = root if isinstance(root, MainSessionRoot) else inspect_main_session_root(root)
    return "ses_" + uuid5(
        NAMESPACE_URL, "rpnh:main-session-presentation:" + str(record.root),
    ).hex


__all__ = (
    "MainSessionOwnerLease",
    "MainSessionRoot",
    "inspect_main_session_root",
    "stable_frontend_session_id",
)
