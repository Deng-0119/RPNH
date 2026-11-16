"""Fixed installation-child context; never a general managed-runtime bypass.

Only the parent's inherited exclusive lifecycle lease and the selected
installation's fixed data can authorize the two local prepare/probe handlers.
Remote metadata never chooses a module, callable or shell command here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
from typing import Callable, Iterator, Mapping

import cpn


OPERATIONS = frozenset({"prepare", "probe"})


class CandidateContextError(ValueError):
    """The installation child cannot establish its bounded parent context."""


@dataclass(frozen=True)
class CandidateContext:
    root: Path
    directory: Path
    installation_id: str
    rpnh_version: str
    operation: str
    lease_fd: int
    record: Mapping[str, object]


def inherited_exclusive_lease(root: Path, descriptor: int) -> int:
    """Validate a delivered EX lease and return one close-only duplicate.

    A separate SH attempt must first be blocked, so an unlocked or SH lease
    is never upgraded to create the missing parent authority. An idempotent
    EX attempt on the delivered description then rejects somebody else's EX.
    This verifies delivered-descriptor/current-lock conditions; portable
    flock is not a sandbox against a malicious concurrent same-user process.
    The caller must already have validated the managed root's ownership.
    """
    if (isinstance(descriptor, bool) or not isinstance(descriptor, int)
            or descriptor < 3 or descriptor > 2**31 - 1):
        raise CandidateContextError("candidate requires an inherited lifecycle descriptor above stdio")
    lock_path = root / "lifecycle.lock"
    try:
        info = os.fstat(descriptor)
        path_info = lock_path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(path_info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
                or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
            raise CandidateContextError("candidate lifecycle descriptor does not identify this root's private lock")
        probe = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            probe_info = os.fstat(probe)
            if (probe_info.st_dev, probe_info.st_ino) != (info.st_dev, info.st_ino):
                raise CandidateContextError("candidate lifecycle lock changed during validation")
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise CandidateContextError("candidate requires an already held exclusive lifecycle lease")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CandidateContextError("candidate descriptor does not own the exclusive lifecycle lease") from exc
            return fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
        finally:
            os.close(probe)  # Never LOCK_UN a description inherited from the parent.
    except CandidateContextError:
        raise
    except OSError as exc:
        raise CandidateContextError("candidate lifecycle descriptor is unavailable") from exc


@contextmanager
def candidate_context(operation: str) -> Iterator[CandidateContext]:
    """Validate the fixed candidate context while preserving the parent's EX."""
    if operation not in OPERATIONS:
        raise CandidateContextError("candidate entry permits only prepare or probe")
    from .install_contract import (
        installation_target, read_frontend_view, read_installed, read_plan,
    )
    from .install_v2 import check_root, marker

    try:
        installation_id = os.environ["RPNH_CANDIDATE_ID"]
        raw_fd = os.environ["RPNH_CANDIDATE_FD"]
        raw_root = os.environ["RPNH_MANAGED_ROOT"]
        raw_config = os.environ["RPNH_FRONTEND_CONFIG"]
        if not raw_fd.isascii() or not raw_fd.isdecimal() or not raw_root or not raw_config:
            raise ValueError("incomplete candidate context")
        target = installation_target(installation_id)
        root = check_root(Path(raw_root))
        if marker(root)["pending_activation"] is not None:
            raise ValueError("recover pending activation before candidate preparation or probing")
        directory = root / target
        if (directory != directory.resolve() or not directory.is_dir()
                or directory.stat().st_uid != os.getuid()
                or stat.S_IMODE(directory.stat().st_mode) & 0o077):
            raise ValueError("candidate directory is not a private installation in this root")
        config = directory / "frontend.json"
        venv = directory / "venv"
        if (not venv.is_dir() or venv != venv.resolve()
                or Path(raw_config).absolute() != config or config.is_symlink()
                or Path(sys.prefix).resolve() != (directory / "venv").resolve()
                or not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
                or not Path(cpn.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())):
            raise ValueError("candidate interpreter, package and frontend config do not share one installation")
        current = os.readlink(root / "current") if os.path.lexists(root / "current") else None
        if current == target:
            raise ValueError("current installation cannot run as a preparation candidate")
        lease_fd = inherited_exclusive_lease(root, int(raw_fd))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, CandidateContextError):
            raise
        raise CandidateContextError(f"invalid candidate context: {exc}") from exc
    try:
        if os.path.lexists(directory / "installed.json"):
            if operation != "probe":
                raise CandidateContextError("a completed installation cannot be prepared again")
            record = read_installed(directory)
        else:
            record = read_plan(directory)
            if record["conflicts"]:
                raise CandidateContextError("candidate plan has unresolved installation conflicts")
            if current != record["expected_from"]:
                raise CandidateContextError("current changed after the candidate plan was prepared")
        if (record["installation_id"] != installation_id
                or record["rpnh_version"] != cpn.__version__):
            raise CandidateContextError("candidate plan and running package version do not match")
        read_frontend_view(directory, expected_id=installation_id,
                           selected_entries=set(record["components"]))
        yield CandidateContext(root, directory, installation_id, record["rpnh_version"],
                               operation, lease_fd, record)
    finally:
        os.close(lease_fd)


def run_candidate(operation: str, *, prepare: Callable[[CandidateContext], object],
                  probe: Callable[[CandidateContext], object]) -> object:
    """Invoke one locally supplied fixed handler, after all context checks."""
    with candidate_context(operation) as context:
        return (prepare if operation == "prepare" else probe)(context)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    options = parser.parse_args(argv)
    # Fixed local entrypoints are supplied by the installation executor. Their
    # names cannot be selected by plan data or an environment variable.
    from .install_v2 import prepare_candidate, probe_candidate
    try:
        result = run_candidate(options.operation, prepare=prepare_candidate, probe=probe_candidate)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"rpnh candidate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
