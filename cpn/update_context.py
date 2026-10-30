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


def managed_context_hint() -> Path | None:
    explicit = os.environ.get("RPNH_MANAGED_ROOT")
    if explicit:
        return Path(explicit)
    prefix = Path(sys.prefix).absolute()
    if (prefix.name != "venv" or prefix.parent.parent.name != "versions"
            or MANAGED_TARGET_RE.fullmatch("versions/" + prefix.parent.name) is None):
        return None
    root = prefix.parents[2]
    marker = root / "managed.json"
    return root if marker.exists() or marker.is_symlink() else None

