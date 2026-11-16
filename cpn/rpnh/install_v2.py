"""Managed-v2 software layout and activation transactions.

This module never opens a Registry, starts a MainSession, or resumes a task.
An installation record describes software; current is the only activation
commit point. Runtime health probes are deliberately separate from recovery.
The explicit offline executor consumes fixed materials before activation.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
from typing import Callable
from uuid import uuid4

from .install_contract import (
    V2_PROTOCOL, installation_target, validate_installed, validate_managed,
    validate_pending_activation, read_software_record, validate_plan,
)


TARGET_RE = re.compile(r"versions/i-[0-9a-f]{32}")


@dataclass(frozen=True)
class ActivationResult:
    current: str | None
    previous: str | None
    committed: bool
    recovered: bool = False
    operation: str | None = None

    def to_document(self) -> dict:
        return asdict(self)


class ActivationError(RuntimeError):
    """Report the observed commit fact instead of assuming rollback occurred."""

    def __init__(self, *, current: str | None, committed: bool | None):
        self.current = current
        self.committed = committed
        detail = ("activation committed; transaction recovery is required" if committed else
                  "activation did not commit; transaction recovery may be required" if committed is False else
                  "activation state is indeterminate; retain the installation for explicit recovery")
        super().__init__(detail)


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or path != path.resolve()
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError(f"managed-v2 directory must be private, owned, and real: {path}")


def _read_json(path: Path, *, limit: int = 256 * 1024) -> dict:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_size > limit or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError(f"invalid managed-v2 record: {path.name}")
    value = read_software_record(path, limit=limit)
    if not isinstance(value, dict):
        raise ValueError(f"managed-v2 record must be an object: {path.name}")
    return value


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, value: dict) -> None:
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                prefix="." + path.name + "-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def marker(root: Path) -> dict:
    document = _read_json(root / "managed.json", limit=16384)
    validate_managed(document)
    return document


def check_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    _private_directory(root)
    marker(root)
    for name in ("versions", "bin"):
        _private_directory(root / name)
    return root


def _pointer(root: Path, name: str, *, allow_missing: bool = False) -> str | None:
    if name not in ("current", "previous"):
        raise ValueError("unknown managed pointer")
    path = root / name
    try:
        info = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise ValueError(f"managed-v2 {name} is absent") from None
    if not stat.S_ISLNK(info.st_mode):
        raise ValueError(f"managed-v2 {name} is not a symlink")
    target = os.readlink(path)
    if TARGET_RE.fullmatch(target) is None:
        raise ValueError(f"invalid managed-v2 {name} target")
    return target


def read_installation(root: Path, target: str) -> tuple[str, Path, dict]:
    if not isinstance(target, str) or TARGET_RE.fullmatch(target) is None:
        raise ValueError("invalid managed-v2 installation target")
    directory = root / target
    _private_directory(directory)
    record = _read_json(directory / "installed.json")
    validate_installed(record, expected_id=directory.name)
    return target, directory, record


def read_target(root: Path, name: str = "current", *, allow_missing: bool = False):
    """Validate target identity and complete metadata, not external health."""
    root = check_root(root)
    target = _pointer(root, name, allow_missing=allow_missing)
    return None if target is None else read_installation(root, target)


def _set_pointer(root: Path, name: str, target: str | None) -> None:
    if name not in ("current", "previous"):
        raise ValueError("unknown managed pointer")
    if target is None:
        if name != "previous":
            raise ValueError("current cannot be removed by an activation transaction")
        (root / name).unlink(missing_ok=True)
        _sync_directory(root)
        return
    if TARGET_RE.fullmatch(target) is None:
        raise ValueError("invalid managed-v2 target")
    temporary = root / ("." + name + "-" + uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, root / name)
        _sync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)


def _launcher(root: Path) -> str:
    return ("#!/bin/sh\nset -eu\nROOT=" + shlex.quote(str(root)) + "\n"
            "TARGET=$(/usr/bin/readlink \"$ROOT/current\")\n"
            "case \"$TARGET\" in versions/i-*) NAME=${TARGET#versions/};; *) exit 78;; esac\n"
            "[ \"${#NAME}\" -eq 34 ] || exit 78\n"
            "case \"${NAME#i-}\" in *[!0-9a-f]*) exit 78;; esac\n"
            "[ ! -L \"$ROOT/versions\" ] || exit 78\n"
            "[ ! -L \"$ROOT/$TARGET\" ] || exit 78\n"
            "export RPNH_MANAGED_ROOT=\"$ROOT\"\n"
            "export RPNH_FRONTEND_CONFIG=\"$ROOT/$TARGET/frontend.json\"\n"
            "exec \"$ROOT/$TARGET/venv/bin/rpnh\" \"$@\"\n")


def create_root(root: Path) -> Path:
    """Explicit bootstrap only; never adopt or migrate a pre-existing path."""
    root = root.expanduser().absolute()
    if root != root.resolve() or root.exists() or root.is_symlink():
        raise ValueError("managed-v2 bootstrap needs a new dedicated real directory")
    root.mkdir(parents=True, mode=0o700)
    for name in ("versions", "bin"):
        (root / name).mkdir(mode=0o700)
    _write_json(root / "managed.json", {
        "schema_version": "rpnh/managed/v2", "protocol": V2_PROTOCOL,
        "pending_activation": None,
    })
    launcher = root / "bin/rpnh"
    with launcher.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o700)
        stream.write(_launcher(root))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(root / "bin")
    _sync_directory(root.parent)
    return check_root(root)


@dataclass(frozen=True)
class _ManagementSource:
    root: Path
    prefix: Path | None = None
    target: str | None = None
    rpnh_version: str | None = None
    package_root: Path | None = None


def _source_prefix(prefix: Path):
    """Keep reserved interpreter layout visible even if its marker is broken."""
    from cpn.update_context import MANAGED_TARGET_RE
    if prefix.name != "venv" or prefix.parent.parent.name != "versions":
        return None
    target = "versions/" + prefix.parent.name
    if TARGET_RE.fullmatch(target):
        return prefix.parents[2], target, True
    if prefix.parent.name.startswith("i-"):
        raise ValueError("invalid managed-v2 source installation identity")
    if MANAGED_TARGET_RE.fullmatch(target):
        return prefix.parents[2], target, False
    return None


def _management_package_root() -> Path:
    """Require the manager and its authority readers to share one cpn tree."""
    import cpn
    from cpn import update_context
    from . import install_contract, update_install
    try:
        package = Path(cpn.__file__).resolve().parent
        origins = (
            (update_context.__file__, "update_context.py"),
            (install_contract.__file__, "rpnh/install_contract.py"),
            (update_install.__file__, "rpnh/update_install.py"),
            (__file__, "rpnh/install_v2.py"),
        )
        if any(Path(origin).resolve() != package / relative
               for origin, relative in origins):
            raise ValueError("management source contains mixed cpn package files")
    except TypeError as exc:
        raise ValueError("management source package location is unavailable") from exc
    return package


def _management_source() -> _ManagementSource | None:
    """Validate executing software without requiring source-current health.

    A retained complete I may run management under this protocol. This does
    not authorize ordinary runtime entry or business writes from that I.
    """
    import cpn
    from .update_install import _runtime_check_root, _v2_root
    prefix = Path(sys.prefix).absolute()
    actual = _source_prefix(prefix)
    resolved = _source_prefix(prefix.resolve())
    if (actual is not None or resolved is not None) and prefix != prefix.resolve():
        raise ValueError("managed source interpreter must use its own real venv")
    package = _management_package_root()
    for ancestor in package.parents:
        if _source_prefix(ancestor) is not None and ancestor != prefix:
            raise ValueError("management package and interpreter identify different sources")
    explicit = os.environ.get("RPNH_MANAGED_ROOT")
    hint = None if not explicit else Path(explicit).expanduser().absolute()
    if actual is None:
        if hint is None:
            return None
        # Preserve legacy root-level management semantics. A v2 hint alone
        # cannot label an unrelated Python process as a managed source.
        legacy = _runtime_check_root(hint)
        if _v2_root(legacy):
            raise ValueError("managed-v2 source hint does not match the actual interpreter")
        return _ManagementSource(legacy)
    source, target, v2 = actual
    if hint is not None and hint != source:
        raise ValueError("managed source hint conflicts with the actual interpreter")
    source = _runtime_check_root(source)
    if not prefix.is_dir() or not package.is_relative_to(prefix):
        raise ValueError("management package is outside its source environment")
    if not v2:
        if _v2_root(source):
            raise ValueError("managed-v2 source requires an installation identity")
        return _ManagementSource(source, prefix=prefix, target=target, package_root=package)
    _, directory, record = read_installation(check_root(source), target)
    if prefix != directory / "venv" or record["rpnh_version"] != cpn.__version__:
        raise ValueError("management source package version differs from its installation")
    return _ManagementSource(source, prefix, target, record["rpnh_version"], package)


@contextmanager
def management_lease(root: Path):
    """Acquire source-SH plus target-EX in canonical path order, nonblocking."""
    from cpn.update_context import reject_candidate_runtime
    from .update_install import _open_lease
    reject_candidate_runtime()
    root = check_root(root)
    source = _management_source()
    requested = {root: True}
    if source is not None and source.root != root:
        requested[source.root] = False
    with ExitStack() as stack:
        leases = {}
        for path, exclusive in sorted(requested.items(), key=lambda pair: str(pair[0])):
            # close-only preserves any inherited open-file-description lease.
            leases[path] = stack.enter_context(_open_lease(path, exclusive))
        if _management_source() != source:
            raise ValueError("management source changed before its leases were acquired")
        yield leases[root]


def _state(root: Path, *, recovered: bool = False,
           committed: bool = False, operation: str | None = None) -> ActivationResult:
    current = read_target(root, allow_missing=True)
    previous = read_target(root, "previous", allow_missing=True)
    if current is None and previous is not None:
        raise ValueError("previous exists without a current installation")
    return ActivationResult(None if current is None else current[0],
                            None if previous is None else previous[0],
                            committed, recovered, operation)


def recover_locked(root: Path) -> ActivationResult:
    """Finish/abort an intent under target EX, using only committed metadata."""
    root = check_root(root)
    document = marker(root)
    pending = document["pending_activation"]
    if pending is None:
        return _state(root)
    validate_pending_activation(pending)
    current = _pointer(root, "current", allow_missing=True)
    if current == pending["from"]:
        if current is not None:
            read_installation(root, current)
        if pending["old_previous"] is not None:
            read_installation(root, pending["old_previous"])
        _set_pointer(root, "previous", pending["old_previous"])
        committed = False
    elif current == pending["to"]:
        read_installation(root, current)
        if pending["previous_after"] is not None:
            read_installation(root, pending["previous_after"])
        _set_pointer(root, "previous", pending["previous_after"])
        committed = True
    else:
        raise ValueError("current conflicts with pending activation; retain records for explicit repair")
    document["pending_activation"] = None
    _write_json(root / "managed.json", document)
    previous = pending["previous_after"] if committed else pending["old_previous"]
    return ActivationResult(current, previous, committed, True, pending["operation"])


def recover(root: Path) -> ActivationResult:
    with management_lease(root):
        return recover_locked(root)


def activate_locked(root: Path, target: str, *, operation: str,
                    expected_from: str | None, probe: Callable[[Path, dict], None],
                    event: Callable[[str], None] | None = None) -> ActivationResult:
    """Commit one already-complete candidate under the parent's EX lease.

    Only the parent preparation executor may produce installed.json. The
    required local probe is fixed application code, never a remote command.
    This primitive does not award release, dependency, or data compatibility.
    """
    root = check_root(root)
    if marker(root)["pending_activation"] is not None:
        raise ValueError("recover the pending activation before selecting another target")
    state = _state(root)
    if state.current != expected_from:
        raise ValueError("current changed after the installation plan was selected")
    if state.current == target:
        if operation == "rollback" and state.previous == target:
            return ActivationResult(target, target, True, False, operation)
        raise ValueError("activation target is already current")
    _, directory, record = read_installation(root, target)
    pending = {
        "operation": operation, "from": state.current, "to": target,
        "old_previous": state.previous,
        "previous_after": target if operation == "rollback" else state.current,
    }
    validate_pending_activation(pending)
    if operation == "rollback" and target != state.previous:
        raise ValueError("rollback may select only the retained previous installation")
    # Exiting current need not have a healthy Node/client; only the destination
    # is probed. A completed commit is recovered without this health check.
    probe(directory, record)
    emit = event if event is not None else lambda _stage: None
    try:
        document = marker(root)
        document["pending_activation"] = pending
        _write_json(root / "managed.json", document)
        emit("intent")
        _set_pointer(root, "current", target)
        emit("current")
        _set_pointer(root, "previous", pending["previous_after"])
        emit("previous")
        document["pending_activation"] = None
        _write_json(root / "managed.json", document)
        emit("complete")
    except BaseException as exc:
        try:
            observed = _pointer(root, "current", allow_missing=True)
            committed = observed == target
        except (OSError, ValueError):
            observed, committed = None, None
        # Never write FAILED to an I that may already be current.
        raise ActivationError(current=observed, committed=committed) from exc
    return ActivationResult(target, pending["previous_after"], True, False, operation)


def rollback(root: Path, *, probe: Callable[[Path, dict], None],
             compatible: Callable[[dict, dict], bool]) -> ActivationResult:
    """Internal v2 rollback; qualification must come from a release-pair check."""
    with management_lease(root):
        recover_locked(root)
        current = read_target(root)
        previous = read_target(root, "previous", allow_missing=True)
        if previous is None:
            raise ValueError("this v2 root has no retained previous installation")
        if current[0] == previous[0]:
            return ActivationResult(current[0], previous[0], True, False, "rollback")
        if compatible(current[2], previous[2]) is not True:
            raise ValueError("this release pair has no verified software/data rollback scope")
        return activate_locked(root, previous[0], operation="rollback",
                               expected_from=current[0], probe=probe)


def create_stage_locked(root: Path, plan) -> Path:
    """Create a new final-path stage; failed or orphaned stages are never reused."""
    root = check_root(root)
    if marker(root)["pending_activation"] is not None:
        raise ValueError("recover pending activation before creating a new installation")
    document = validate_plan(plan.to_document())
    if document.get("conflicts"):
        raise ValueError("installation plan contains unresolved conflicts")
    current = _state(root).current
    if current != document["expected_from"]:
        raise ValueError("current changed after planning")
    target = installation_target(document["installation_id"])
    directory = root / target
    directory.mkdir(mode=0o700)  # no exist_ok: an I is never reused
    _write_json(directory / "plan.json", document)
    _sync_directory(root / "versions")
    return directory


def prepare_candidate(context) -> dict:
    from .install_executor import prepare_candidate_materials
    return prepare_candidate_materials(context)


def probe_candidate(context) -> dict:
    from .install_executor import probe_candidate_materials
    return probe_candidate_materials(context)
