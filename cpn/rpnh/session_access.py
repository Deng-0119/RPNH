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
import stat
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
            or profile.get("schema_version") not in {
                "rpnh/main_session_profile/v2",
                "rpnh/main_session_profile/v3",
            }
            or execution_config_path is None):
        raise ValueError(
            "main session requires a persisted v2 or v3 execution profile")
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
        try:
            self._root_identity = _path_identity(self.root, directory=True)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def assert_held(self) -> None:
        """Recheck this existing lease without acquiring or changing a lock."""
        try:
            if self._fd is None:
                raise ValueError("closed")
            actual = os.fstat(self._fd)
            if (not stat.S_ISREG(actual.st_mode)
                    or _path_identity(self.path, directory=False)
                    != (actual.st_dev, actual.st_ino)
                    or _path_identity(self.root, directory=True) != self._root_identity):
                raise ValueError("changed")
        except (OSError, ValueError) as exc:
            raise ValueError("main session owner binding is unavailable") from exc

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


def _path_identity(path: Path, *, directory: bool) -> tuple[int, int]:
    value = path.lstat()
    if (stat.S_ISLNK(value.st_mode)
            or not (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))):
        raise ValueError("main session source path is unavailable")
    return value.st_dev, value.st_ino


@dataclass(frozen=True, slots=True)
class MainSessionSourceBinding:
    """Fingerprint of an already admitted owner session, never a new grant.

    Pre/post checks reject persistent path replacement. They do not claim to
    defend against malicious same-OS-owner ABA races in an immutable store.
    Profiles may legitimately change and are deliberately not fingerprinted.
    """

    session: MainSession
    core: object
    reader: object
    root: Path
    paths: tuple[tuple[Path, bool, tuple[int, int]], ...]
    task_id: str
    branch_id: str
    thread_id: object

    @classmethod
    def capture(cls, session: MainSession) -> "MainSessionSourceBinding":
        core = session._registry_core
        root = session.root
        registry = root / MainSession.REGISTRY_DIR
        internal = registry / ".registry_v1"
        paths = ((root, True), (registry, True), (internal, True),
                 (internal / "objects", True), (internal / "registry.sqlite3", False))
        reader = session.history_registry
        result = cls(session, core, reader, root,
                     tuple((path, directory, _path_identity(path, directory=directory))
                           for path, directory in paths),
                     str(core.task_id), core.branch_id, session._thread_ref.entity_id)
        result.assert_matches(session)
        return result

    def assert_matches(self, session: MainSession) -> None:
        try:
            core = self.core
            read_core = self.reader.core
            if (session is not self.session or session._registry_core is not core
                    or session._main_thread.core is not core or session.root != self.root
                    or session.history_registry is not self.reader or not read_core.read_only
                    or not read_core.event_store.read_only or not read_core.object_store.read_only
                    or core.run_dir != self.root / MainSession.REGISTRY_DIR
                    or core.event_store.path != self.root / MainSession.REGISTRY_DIR / ".registry_v1" / "registry.sqlite3"
                    or core.object_store.root != self.root / MainSession.REGISTRY_DIR / ".registry_v1" / "objects"
                    or read_core.run_dir != core.run_dir
                    or read_core.event_store.path != core.event_store.path
                    or read_core.object_store.root != core.object_store.root
                    or session._thread_ref.entity_id != self.thread_id
                    or str(core.task_id) != self.task_id or core.branch_id != self.branch_id
                    or str(read_core.task_id) != self.task_id or read_core.branch_id != self.branch_id
                    or read_core.event_store.get_meta("task_id") != self.task_id
                    or read_core.event_store.get_meta("branch_id") != self.branch_id
                    or any(_path_identity(path, directory=directory) != identity
                           for path, directory, identity in self.paths)):
                raise ValueError("changed")
        except Exception as exc:
            raise ValueError("main session source binding is unavailable") from exc


__all__ = (
    "MainSessionOwnerLease",
    "MainSessionRoot",
    "MainSessionSourceBinding",
    "inspect_main_session_root",
    "stable_frontend_session_id",
)
