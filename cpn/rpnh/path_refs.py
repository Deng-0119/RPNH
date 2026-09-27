"""Canonical parent-relative references for the Registry ownership tree."""
from __future__ import annotations

from pathlib import Path, PurePosixPath


def canonical_child_ref(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value):
        raise ValueError("Registry child path must be a relative POSIX path")
    pure = PurePosixPath(value)
    if (pure.is_absolute() or pure.as_posix() != value
            or pure.as_posix() in {"", "."}
            or any(part in {"", ".", ".."} for part in pure.parts)):
        raise ValueError("Registry child path is not canonical and relative")
    return pure.as_posix()


def resolve_child_registry(parent_registry_root: Path, value: str) -> Path:
    """Resolve one direct child reference against its parent Registry root."""
    parent = Path(parent_registry_root).resolve(strict=True)
    relative = canonical_child_ref(value)
    current = parent
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Registry child path contains a symlink")
    resolved = current.resolve(strict=False)
    try:
        resolved.relative_to(parent)
    except ValueError as exc:
        raise ValueError("Registry child path escapes its parent Registry") from exc
    return resolved


def relative_child_registry(
        parent_registry_root: Path, child_registry_root: Path,
) -> str:
    """Return one canonical edge from a parent Registry to its child."""
    parent = Path(parent_registry_root).resolve(strict=True)
    child = Path(child_registry_root).resolve(strict=False)
    try:
        relative = child.relative_to(parent)
    except ValueError as exc:
        raise ValueError("child Registry is outside its parent Registry") from exc
    return canonical_child_ref(relative.as_posix())


__all__ = (
    "canonical_child_ref", "relative_child_registry",
    "resolve_child_registry",
)
