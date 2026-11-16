"""Observed worker software provenance, separate from Registry authority.

These records describe the process that claimed a task launch.  They do not
grant data-write compatibility, choose a checkpoint, or certify a release.
The initial interoperability scope is deliberately the same RPNH version and
the explicit worker/control contract below.  Cross-release scopes need their
own evidence and are not inferred from a process being alive.
"""
from __future__ import annotations

from pathlib import Path
import platform
import sys
from typing import Any, Mapping

import cpn


WORKER_RUNTIME_SCHEMA = "rpnh/task_worker_runtime/v1"
WORKER_IMPLEMENTATION = "cpn.rpnh.task_worker/runtime-observation-v1"
OWNER_CONTROL_PROTOCOL = "rpnh/owner_channel/v1"
_TEXT_FIELDS = frozenset({
    "rpnh_version", "worker_implementation", "control_protocol",
    "package_root", "python_executable", "python_prefix", "python_version",
    "python_implementation", "process_arch", "platform",
})
_PATH_FIELDS = ("package_root", "python_executable", "python_prefix")


class WorkerCompatibilityError(ValueError):
    """A live worker or pending launch has no supported interoperability scope."""


def observe_worker_runtime() -> dict[str, Any]:
    """Observe this process without launching probes or opening a Registry."""
    from cpn.update_context import managed_context_hint, reject_candidate_runtime
    reject_candidate_runtime()
    root = None
    if managed_context_hint() is not None:
        from .update_install import managed_root
        root = managed_root()
    installation_id = None
    if root is not None:
        from .update_install import _v2_root
        if _v2_root(root):
            from .install_v2 import read_target
            target = read_target(root)
            if target is None:
                raise WorkerCompatibilityError("managed worker has no current installation")
            installation_id = target[2]["installation_id"]
    return {
        "schema_version": WORKER_RUNTIME_SCHEMA,
        "rpnh_version": cpn.__version__,
        "worker_implementation": WORKER_IMPLEMENTATION,
        "control_protocol": OWNER_CONTROL_PROTOCOL,
        "package_root": str(Path(cpn.__file__).resolve().parent),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_prefix": str(Path(sys.prefix).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": sys.implementation.name,
        "process_arch": platform.machine(),
        "platform": sys.platform,
        "managed_root": None if root is None else str(root.resolve()),
        "installation_id": installation_id,
    }


def validate_worker_runtime(value: object) -> dict[str, Any]:
    """Parse the observation; this does not inspect or trust another process."""
    expected = _TEXT_FIELDS | {"schema_version", "managed_root", "installation_id"}
    if (not isinstance(value, Mapping) or set(value) != expected
            or value.get("schema_version") != WORKER_RUNTIME_SCHEMA
            or any(not isinstance(value.get(key), str) or not value[key]
                   for key in _TEXT_FIELDS)):
        raise WorkerCompatibilityError("worker runtime observation is missing or malformed")
    if any(not Path(value[key]).is_absolute() for key in _PATH_FIELDS):
        raise WorkerCompatibilityError("worker runtime source paths must be absolute")
    root, installation = value["managed_root"], value["installation_id"]
    if root is not None and (not isinstance(root, str) or not Path(root).is_absolute()):
        raise WorkerCompatibilityError("worker managed source is malformed")
    if installation is not None:
        from cpn.update_context import MANAGED_V2_TARGET_RE
        if (root is None or not isinstance(installation, str)
                or not MANAGED_V2_TARGET_RE.fullmatch("versions/" + installation)):
            raise WorkerCompatibilityError("worker installation identity is malformed")
    return dict(value)


def require_worker_interoperability(value: object, *, label: str) -> None:
    """Require the implemented exact runtime/protocol scope for a live worker."""
    try:
        observed = validate_worker_runtime(value)
    except WorkerCompatibilityError as exc:
        raise WorkerCompatibilityError(f"{label}: {exc}") from exc
    if (observed["rpnh_version"] != cpn.__version__
            or observed["worker_implementation"] != WORKER_IMPLEMENTATION
            or observed["control_protocol"] != OWNER_CONTROL_PROTOCOL
            or observed["platform"] != sys.platform):
        raise WorkerCompatibilityError(
            f"{label}: worker runtime/control protocol has no supported interoperability scope")


def require_launch_runtime(value: object, *, label: str) -> dict[str, Any]:
    """A pending intent must still select the same actual worker software."""
    require_worker_interoperability(value, label=label)
    actual = observe_worker_runtime()
    if validate_worker_runtime(value) != actual:
        raise WorkerCompatibilityError(
            f"{label}: pending launch runtime/source changed; automatic recovery is unavailable")
    return actual


__all__ = (
    "WorkerCompatibilityError", "observe_worker_runtime", "validate_worker_runtime",
    "require_worker_interoperability", "require_launch_runtime",
)
