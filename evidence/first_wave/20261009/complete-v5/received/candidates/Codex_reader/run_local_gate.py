"""Verify applied bytes and run only the 32-case offline native history gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True,
                    help="Authorized RPNH worktree with this patch already applied")
parser.add_argument("--junitxml", type=Path, required=True)
args = parser.parse_args()
package = Path(__file__).resolve().parent
repository = args.repo.resolve(strict=True)
output = args.junitxml.resolve()
manifest = json.loads((package / "file-manifest.json").read_text())


def verify():
    for entry in manifest["files"]:
        data = (repository / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["path"]


verify()
os.chdir(repository)
sys.path.insert(0, str(repository))
sys.path.insert(0, str(repository / "tests"))


def guard(event, arguments):
    if event in {"socket.connect", "socket.bind", "subprocess.Popen", "os.system",
                 "os.posix_spawn", "os.spawn"}:
        raise RuntimeError(f"Native history gate forbids external execution: {event}")


sys.addaudithook(guard)
import pytest

status = pytest.main([
    "-q", "tests/test_main_thread_history.py", "tests/test_main_thread_registry.py",
    str(package / "independent-review/test_independent_history.py"),
    "--junitxml=" + str(output),
])
verify()
raise SystemExit(status)
