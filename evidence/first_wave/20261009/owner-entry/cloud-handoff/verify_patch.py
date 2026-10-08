"""Verify this patch against a checkout without changing that checkout."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

package = Path(__file__).resolve().parent
repository = Path(sys.argv[1]).resolve()
rows = json.loads((package / "file-manifest.json").read_text())
with tempfile.TemporaryDirectory(prefix="rpnh-owner-entry-apply-") as temporary:
    target = Path(temporary)
    for row in rows:
        original = repository / row["path"]
        if row["baseline_sha256"] is None:
            if original.exists():
                raise SystemExit("Expected a new file, but it exists: " + row["path"])
            continue
        payload = original.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row["baseline_sha256"]:
            raise SystemExit("Baseline differs; review before applying: " + row["path"])
        copied = target / row["path"]
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(payload)
    for arguments in (["git", "apply", "--check"], ["git", "apply"]):
        subprocess.run(arguments + [str(package / "owner-entry-convergence.patch")], cwd=target, check=True)
    for row in rows:
        actual = hashlib.sha256((target / row["path"]).read_bytes()).hexdigest()
        if actual != row["final_sha256"]:
            raise SystemExit("Applied bytes differ: " + row["path"])
print(json.dumps({"status": "PASS", "changed_files": len(rows), "checkout_modified": False}))
