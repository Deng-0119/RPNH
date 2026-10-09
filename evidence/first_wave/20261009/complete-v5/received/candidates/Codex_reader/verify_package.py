"""Offline packaging QA only; imports no project code and runs no project test."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


for line in (root / "SHA256SUMS").read_text().splitlines():
    expected, name = line.split("  ", 1)
    path = root / name
    assert path.resolve().is_relative_to(root.resolve()), name
    assert not path.is_symlink(), name
    assert digest(path.read_bytes()) == expected, name

manifest = json.loads((root / "file-manifest.json").read_text())
assert len(manifest["files"]) == 7
assert manifest["patch_sha256"] == "f4726d7c1a230c8935b22310bb1d29f7c8e68342ef9b660cabb6430b8a3d4654"
patch = root / manifest["patch"]
assert digest(patch.read_bytes()) == manifest["patch_sha256"]
assert {line[6:] for line in patch.read_text().splitlines() if line.startswith("+++ b/")} == {
    row["path"] for row in manifest["files"]}
for row in manifest["files"]:
    data = (root / "source" / row["path"]).read_bytes()
    assert digest(data) == row["sha256"], row["path"]
    assert blob(data) == row["final_git_blob_sha1"], row["path"]
    assert len(data) == row["bytes"], row["path"]
    if row["baseline_git_blob_sha1"]:
        assert blob((root / "baseline" / row["path"]).read_bytes()) == row["baseline_git_blob_sha1"], row["path"]

with tempfile.TemporaryDirectory(prefix="rpnh-history-package-qa-") as directory:
    target = Path(directory)
    for row in manifest["files"]:
        if row["baseline_git_blob_sha1"]:
            path = target / row["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / "baseline" / row["path"], path)
    for options in (["--check"], []):
        subprocess.run(["git", "apply", *options, str(patch)], cwd=target, check=True,
                       capture_output=True)
    for row in manifest["files"]:
        assert (target / row["path"]).read_bytes() == (root / "source" / row["path"]).read_bytes(), row["path"]

checks = {}
for name, expected in {
    "final-tests.xml": 57,
    "latest-main-tests.xml": 57,
    "independent-review/results.xml": 32,
    "portable-gate-validation.xml": 32,
}.items():
    tree = ET.parse(root / name)
    cases = list(tree.iter("testcase"))
    assert len(cases) == expected, name
    assert all(not any(case.find(k) is not None for k in ("failure", "error", "skipped"))
               for case in cases), name
    suites = list(tree.iter("testsuite"))
    assert sum(int(s.get("tests", 0)) for s in suites) == expected, name
    checks[name] = expected

combination = json.loads((root / "combination-inputs.json").read_text())
owners = {}
for entry in combination["packages"]:
    for row in entry["files"]:
        owners.setdefault(row["path"], []).append(entry["name"])
collisions = {path: names for path, names in owners.items() if len(names) > 1}
assert len(owners) == 37
assert collisions == combination["frozen_target_path_collisions"] == {}
print(json.dumps({"packaging_qa": "PASS", "frozen_files": 7,
                  "fresh_patch_apply_bytes": "PASS", "recorded_junit_counts": checks,
                  "fixed_combination_unique_paths": len(owners),
                  "fixed_combination_collisions": collisions,
                  "project_tests_run_by_this_checker": 0}, indent=2))
