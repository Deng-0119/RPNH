"""Small, lease-owned UX preference for one canonical MainSession root."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .session_access import MainSessionOwnerLease, inspect_main_session_root


SESSION_FRONTEND_FILE = "frontend.json"
SESSION_FRONTEND_SCHEMA = "rpnh/session_frontend/v1"
SESSION_FRONTENDS = ("basic", "codex", "opencode", "dsh")


def _sidecar(root: Path) -> Path:
    path = inspect_main_session_root(root).root / SESSION_FRONTEND_FILE
    if path.is_symlink():
        raise ValueError("session frontend sidecar must not be a symlink")
    return path


def read_session_frontend(root: Path) -> str | None:
    """Read a concrete presentation choice without creating or adopting anything.

    Missing, malformed and unsupported UX metadata is unknown. Registry and
    persisted execution-profile validation still belongs to session access.
    """
    path = _sidecar(root)
    try:
        with path.open("r", encoding="utf-8") as stream:
            content = stream.read(1025)
        if len(content) > 1024:
            return None
        value = json.loads(content)
    except (FileNotFoundError, UnicodeError, ValueError):
        return None
    if (not isinstance(value, dict)
            or value.get("schema_version") != SESSION_FRONTEND_SCHEMA):
        return None
    frontend = value.get("frontend")
    return frontend if isinstance(frontend, str) and frontend in SESSION_FRONTENDS else None


def remember_session_frontend(
        root: Path, frontend: str, *, lease: MainSessionOwnerLease,
) -> None:
    """Atomically remember an adopted frontend under its existing owner lease."""
    if not isinstance(frontend, str) or frontend not in SESSION_FRONTENDS:
        raise ValueError("session frontend must be basic, codex, opencode or dsh")
    lease.assert_held()
    path = _sidecar(root)
    if lease.root != path.parent:
        raise ValueError("session frontend root does not match the owner lease")
    if read_session_frontend(root) == frontend:
        return
    value = json.dumps({"schema_version": SESSION_FRONTEND_SCHEMA,
                        "frontend": frontend}) + "\n"
    lease.assert_held()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".frontend-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            lease.assert_held()
            stream.write(value)
        lease.assert_held()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = ("read_session_frontend", "remember_session_frontend")
