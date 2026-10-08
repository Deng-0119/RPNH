from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
root = Path("<WORKSPACE>").resolve()
probes = root / "task-tool-pipeline-validation-20261008/probes"
base = root / ".p26/y"
assert base.resolve().is_relative_to(root) and not base.exists()
assert probes.resolve().is_relative_to(root) and not probes.is_symlink()
for suffix in ("stdout.log", "stderr.log", "json", "junit.xml"):
    assert not (probes / ("mirror-native-y." + suffix)).exists()
env = dict(os.environ, PYTHONPATH=str(root / "RPNH-main"), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(root / ".p26/tmp"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONUNBUFFERED="1")
command = [str(root / ".p26/v/bin/python"), "-m", "pytest", "-vv", "--tb=long", str(probes / "test_native_boundaries.py") + "::test_missing_usage_join_native", "--basetemp", str(base), "--junitxml", str(probes / "mirror-native-y.junit.xml"), "-p", "no:cacheprovider", "-x"]
loaded_probe_sha256 = hashlib.sha256((probes / "test_native_boundaries.py").read_bytes()).hexdigest()
started, tick = datetime.now(timezone.utc).isoformat(), time.monotonic()
with (probes / "mirror-native-y.stdout.log").open("xb") as out, (probes / "mirror-native-y.stderr.log").open("xb") as err:
    process = subprocess.Popen(command, cwd=root / "RPNH-main", env=env, stdout=out, stderr=err)
    print(json.dumps({"pid": process.pid, "command": command, "started_at": started}), flush=True)
    code = process.wait()
record = {"command": command, "cwd": str(root / "RPNH-main"), "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.monotonic()-tick, "exit_code": code, "pid": process.pid, "status": "PASS" if code == 0 else "FAIL", "probe_sha256": loaded_probe_sha256, "environment_overrides": {k: env[k] for k in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "TMPDIR", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONUNBUFFERED")}}
if "BLOCKED: native AF_UNIX" in (probes / "mirror-native-y.stdout.log").read_text(): record["status"] = "BLOCKED"
(probes / "mirror-native-y.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record), flush=True)
raise SystemExit(code)
