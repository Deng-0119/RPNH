"""Lightweight hints for application lifecycle validation, not authorization.

Importing this module must not initialize cpn.rpnh, package resources, provider
configuration, or a Registry. A hint only requests the full managed validator.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys

MANAGED_TARGET_RE = re.compile(r"versions/[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?-[0-9a-f]{12}")
MANAGED_V2_TARGET_RE = re.compile(r"versions/i-[0-9a-f]{32}")
CANDIDATE_CONTEXT_KEYS = ("RPNH_CANDIDATE_ID", "RPNH_CANDIDATE_FD")


def candidate_context_requested() -> bool:
    """An incomplete candidate context must also reject ordinary runtime use."""
    return any(name in os.environ for name in CANDIDATE_CONTEXT_KEYS)


def reject_candidate_runtime() -> None:
    """Only the fixed installation child may consume a candidate context."""
    if candidate_context_requested():
        raise ValueError(
            "candidate installation context permits only the fixed prepare/probe entry; "
            "ordinary runtime, task and update commands are unavailable")


def managed_context_hint() -> Path | None:
    explicit = os.environ.get("RPNH_MANAGED_ROOT")
    if explicit:
        return Path(explicit)
    prefix = Path(sys.prefix).absolute()
    target = "versions/" + prefix.parent.name
    if (prefix.name != "venv" or prefix.parent.parent.name != "versions"
            or not (MANAGED_TARGET_RE.fullmatch(target)
                    or MANAGED_V2_TARGET_RE.fullmatch(target))):
        return None
    root = prefix.parents[2]
    if MANAGED_V2_TARGET_RE.fullmatch(target):
        # A damaged v2 marker must not turn its reserved interpreter path into
        # an unmanaged runtime. The v1 hint retains its original behavior.
        return root
    marker = root / "managed.json"
    return root if marker.exists() or marker.is_symlink() else None
