"""Owned bounded subprocess recorder; every output stays in probes or .p26/x."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT = Path("<WORKSPACE>").resolve()
REPO = ROOT / "RPNH-main"
PROBES = ROOT / "task-tool-pipeline-validation-20261008/probes"
PYTHON = ROOT / ".p26/v/bin/python"
BASE = ROOT / ".p26/x"
for target in (PROBES, BASE, ROOT / ".p26/tmp"):
    assert target.resolve().is_relative_to(ROOT) and not target.is_symlink()
assert not BASE.exists(), "pytest basetemp must be fresh"
manifest = json.loads((ROOT / "task-tool-pipeline-validation-20261008/RPNH_Tool_Pipeline_Local_Validation_20261008/SOURCE_MANIFEST.json").read_text())
files = []
for row in manifest["files"]:
    path = REPO / row["path"]
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    assert digest == row["sha256"] and len(data) == row["size_bytes"], path
    files.append({"path": row["path"], "sha256": digest, "size_bytes": len(data)})
source_hash = hashlib.sha256("".join(r["path"] + "\0" + r["sha256"] + "\n" for r in sorted(files, key=lambda r: r["path"])).encode()).hexdigest()
assert source_hash == manifest["source_set_sha256"]
def git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()
identity = {"head": git("rev-parse", "HEAD"), "origin_main_local_ref": git("rev-parse", "origin/main"),
    "dirty_before": git("status", "--short"), "source_set_sha256": source_hash, "files": files,
    "python": platform.python_version(), "os": platform.platform(),
    "transport": "cpn.rpnh.control_server.OwnerEventLoop (native default)",
    "probe_sha256": hashlib.sha256((PROBES / "test_native_boundaries.py").read_bytes()).hexdigest()}
assert identity["head"] == "00f2d29c7deffed44e2ec635a24f390c6e0d9ace"
(PROBES / "source_identity.json").write_text(json.dumps(identity, indent=2) + "\n")
env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(ROOT / ".p26/tmp"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUNBUFFERED="1")
command = [str(PYTHON), "-m", "pytest", "-vv", "--tb=long", str(PROBES / "test_native_boundaries.py"), "--basetemp", str(BASE), "--junitxml", str(PROBES / "extra-native.junit.xml"), "-p", "no:cacheprovider", "-x"]
started = datetime.now(timezone.utc).isoformat()
tick = time.monotonic()
with (PROBES / "extra-native.stdout.log").open("xb") as out, (PROBES / "extra-native.stderr.log").open("xb") as err:
    process = subprocess.Popen(command, cwd=REPO, env=env, stdout=out, stderr=err)
    print(json.dumps({"pid": process.pid, "started_at": started, "command": command}), flush=True)
    code = process.wait()
record = {"command": command, "cwd": str(REPO), "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic()-tick, "exit_code": code, "pid": process.pid,
    "environment_overrides": {k: env[k] for k in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONUNBUFFERED")},
    "status": "PASS" if code == 0 else "FAIL", "dirty_after": git("status", "--short"),
    "stdout": str(PROBES / "extra-native.stdout.log"), "stderr": str(PROBES / "extra-native.stderr.log")}
if "BLOCKED: native AF_UNIX" in (PROBES / "extra-native.stdout.log").read_text(): record["status"] = "BLOCKED"
record["test_ids"] = []
if (PROBES / "extra-native.junit.xml").exists():
    for case in ET.parse(PROBES / "extra-native.junit.xml").iter("testcase"):
        record["test_ids"].append("test_native_boundaries.py::" + case.attrib["name"])
(PROBES / "extra-native.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record), flush=True)
raise SystemExit(code)
