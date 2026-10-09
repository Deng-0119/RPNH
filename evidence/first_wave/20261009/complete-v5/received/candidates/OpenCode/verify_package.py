#!/usr/bin/env python3
"""Offline byte/provenance verification; never executes product or native code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files(root):
    working = ("independent-review/clean-apply/",
               "independent-review/fake-failure-artifacts/",
               "independent-review/fake-fixed-artifacts/",
               "independent-review/fake-final-artifacts/")
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts and path.suffix != ".pyc"
            and not path.relative_to(root).as_posix().startswith(working)}


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "FILE_MANIFEST.json").read_text())
    actual = files(root)
    actual.pop("FILE_MANIFEST.json", None)
    assert set(actual) == set(manifest["files"]), "Package membership changed"
    for name, path in actual.items():
        assert digest(path) == manifest["files"][name], "File hash changed: " + name
    tree = json.loads((root / "evidence/main-tree.json").read_text())
    blobs = {row["path"]: row["sha"] for row in tree["tree"] if row["type"] == "blob"}
    baseline = files(root / "baseline")
    for name, path in baseline.items():
        content = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        assert blob == blobs[name], "Baseline differs from verified main: " + name
    source = files(root / "source")
    changed = sorted(name for name in set(source) | set(baseline)
                     if name not in source or name not in baseline
                     or source[name].read_bytes() != baseline[name].read_bytes())
    assert changed == manifest["changed_files"], "Changed-file scope differs"
    old = json.loads(baseline["cpn/frontend/opencode_compatibility.v1.json"].read_text())
    new = json.loads(source["cpn/frontend/opencode_compatibility.v1.json"].read_text())
    candidates = new.pop("certification_candidates")
    assert old == new, "Default metadata or source-extracted schema changed"
    assert old["upstream"]["package_version"] == "1.18.32"
    assert len(candidates) == 1 and candidates[0]["package_version"] == "1.18.35"
    assert candidates[0]["production_enabled"] is False
    assert candidates[0]["native_g2"] == candidates[0]["native_g3"] == "not-run"
    print(json.dumps({"status": "PASS", "package_files": len(actual),
        "verified_main_baseline_files": len(baseline), "source_files": len(source),
        "changed_files": changed, "default_version": "1.18.32",
        "candidate_version": "1.18.35", "native_g2": "NOT_RUN", "native_g3": "NOT_RUN"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
