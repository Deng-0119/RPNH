"""Positive-allowlist publication helpers; no Registry DB or raw run export."""
from __future__ import annotations

import difflib
from pathlib import Path
import re

from .source import ERP_PREFIX, RPNH_BASE, git, json_bytes, safe_file, safe_relative, sha256

_FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules",
                    "raw", "private", "registry", "credentials", "secrets", "cache"}

# These are the independently assigned first-wave integration lanes. Their
# committed presence does not change ERP ownership or relabel earlier scores.
_INTEGRATED_PREFIXES = (ERP_PREFIX, "examples/slopcodebench/",
                        "examples/example_validation/", "evidence/first_wave/")


def publication_path(name: str):
    path = safe_relative(name)
    if (any(p.lower() in _FORBIDDEN_PARTS or p.startswith(".env") for p in path.parts)
            or path.suffix.lower() in (".db", ".sqlite", ".sqlite3", ".dump", ".pyc", ".pem", ".key")
            or path.name.lower() in ("config.json", "provider.json", "api_key")):
        raise ValueError("private/runtime artifact cannot be published")
    return path


def new_path(root, name: str) -> Path:
    """Resolve before writes; refuse links and existing targets (append only)."""
    root = Path(root)
    # A caller may choose any portable output root, but never traverse links.
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise ValueError("output root traverses symlink")
    root = root.resolve()
    path = root
    for part in safe_relative(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("output traverses symlink")
    if path.exists():
        raise FileExistsError("append-only output already exists")
    if not path.resolve().is_relative_to(root):
        raise ValueError("output escapes root")
    return path


def write_new(root, name: str, data: bytes) -> Path:
    path = new_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return path


def source_identity(source_root) -> dict:
    root = Path(source_root).resolve(strict=True)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    if git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root):
        raise ValueError("source root must be checkout root")
    if head != RPNH_BASE:
        try:
            git(root, "merge-base", "--is-ancestor", RPNH_BASE, head)
        except ValueError as exc:
            raise ValueError("source HEAD must descend from the assigned base") from exc
        # Disable rename detection so both sides of cross-lane moves are checked.
        integrated = git(root, "diff", "--no-renames", "--name-only", "-z",
                         RPNH_BASE, head, "--").decode().split("\0")
        if any(name and not name.startswith(_INTEGRATED_PREFIXES) for name in integrated):
            raise ValueError("committed changes outside assigned first-wave integration since assigned base")
    names = set(git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                    "--", ERP_PREFIX).decode().split("\0")) - {""}
    changed = []
    owned = []
    for name in sorted(names):
        publication_path(name)
        path = safe_file(root, name)
        data = path.read_bytes()
        final = sha256(data)
        in_base = bool(git(root, "ls-tree", RPNH_BASE, "--", name))
        base = git(root, "show", f"{RPNH_BASE}:{name}") if in_base else None
        base_hash = sha256(base) if base is not None else None
        owned.append({"path": name, "sha256": final, "bytes": len(data),
                      "mode": "100755" if path.stat().st_mode & 0o111 else "100644"})
        if final != base_hash:
            changed.append({"path": name, "base_sha256": base_hash, "final_sha256": final})
    # HEAD tree plus exact changed bytes is the tested source, not a clean HEAD claim.
    tracked_dirty = git(root, "diff", "--no-renames", "HEAD", "--name-only", "-z").decode().split("\0")
    if any(name and not name.startswith(ERP_PREFIX) for name in tracked_dirty):
        raise ValueError("tracked changes outside ERP ownership")
    return {"base_commit": RPNH_BASE, "tested_commit": head,
            "head_tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            "tracked_clean": not any(tracked_dirty), "owned_files": owned,
            "owned_tree_sha256": sha256(json_bytes(owned)), "changed_files": changed,
            "identity_scope": "Git HEAD tree plus complete ERP-owned working-file hashes and modes; dirty additions are explicit. Committed SCB/shared-validation/evidence first-wave lanes are allowed integration context, not ERP-owned source or a basis to relabel older scores."}


def _patch(name, before, after, mode):
    """Git-compatible text patch, including new/empty/no-final-newline files."""
    header = [f"diff --git a/{name} b/{name}\n"]
    if before is None:
        header.append(f"new file mode {mode}\n")
    old = (before or b"").decode("utf-8")
    new = after.decode("utf-8")
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 fromfile=f"a/{name}" if before is not None else "/dev/null",
                                 tofile=f"b/{name}")
    for line in lines:
        header.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(header)


def validate_patch_paths(patch: str) -> None:
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            if not match or match[1] != match[2]:
                raise ValueError("unsupported patch path/rename")
            for name in (match[1], match[2]):
                publication_path(name)
                if not name.startswith(ERP_PREFIX):
                    raise ValueError("patch outside ERP ownership")
        elif line.startswith(("--- ", "+++ ")):
            name = line[4:]
            if name == "/dev/null":
                continue
            if not name.startswith(("a/", "b/")):
                raise ValueError("unsafe patch header")
            publication_path(name[2:])
            if not name[2:].startswith(ERP_PREFIX):
                raise ValueError("patch outside ERP ownership")


def export_source_overlay(source_root, destination, *, paths) -> dict:
    """Export explicitly selected UTF-8 source files plus a reviewable patch.

    No automatic run-directory traversal. A new destination is required, and all
    inputs are checked before anything is written. Apply with git apply --check.
    """
    root = Path(source_root).resolve(strict=True)
    identity = source_identity(root)
    names = list(paths)
    if not names or len(set(names)) != len(names):
        raise ValueError("source allowlist must be nonempty and unique")
    dest = Path(destination)
    if dest.exists():
        raise FileExistsError("export destination already exists")
    rows, files, patches = [], {}, []
    identity_files = {r["path"]: r for r in identity["owned_files"]}
    for name in sorted(names):
        publication_path(name)
        if not name.startswith(ERP_PREFIX) or name not in identity_files:
            raise ValueError("source allowlist outside owned tree")
        if any(c.isspace() for c in name):
            raise ValueError("patch export requires source paths without whitespace")
        path = safe_file(root, name)
        data = path.read_bytes()
        data.decode("utf-8")
        if sha256(data) != identity_files[name]["sha256"]:
            raise ValueError("source changed during export")
        in_base = bool(git(root, "ls-tree", RPNH_BASE, "--", name))
        base = git(root, "show", f"{RPNH_BASE}:{name}") if in_base else None
        mode = identity_files[name]["mode"]
        files["source-overlay/" + name] = (data, mode)
        if base != data:
            rows.append({"path": name, "base_sha256": sha256(base) if base is not None else None,
                         "final_sha256": sha256(data), "change_kind": "modified" if in_base else "added"})
            patches.append(_patch(name, base, data, mode))
    patch = "".join(patches)
    validate_patch_paths(patch)
    files["implementation.patch"] = (patch.encode(), "100644")
    files["changed-files.json"] = (json_bytes(rows), "100644")
    files["source-identity.json"] = (json_bytes(identity), "100644")
    # Precheck every destination, including parents, before creating the output.
    for name in files:
        new_path(dest, name)
    for name, (data, mode) in files.items():
        path = write_new(dest, name, data)
        path.chmod(0o755 if mode == "100755" else 0o644)
    checksums = "".join(f"{sha256(data)}  {name}\n" for name, (data, _) in sorted(files.items()))
    write_new(dest, "checksums.sha256", checksums.encode())
    return {"source": identity, "changed_files": rows,
            "files": [{"path": name, "sha256": sha256(data), "bytes": len(data)}
                      for name, (data, _) in sorted(files.items())]}
