"""Verify a materialized delivery package without running product/model code."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
for line in (root / "SHA256SUMS").read_text().splitlines():
    expected, name = line.split("  ", 1)
    path = Path(name)
    assert not path.is_absolute() and ".." not in path.parts
    assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected, name
manifest = json.loads((root / "file-manifest.json").read_text())
assert hashlib.sha256((root / manifest["patch"]).read_bytes()).hexdigest() == manifest["patch_sha256"]
assert len(manifest["files"]) == 21
for row in manifest["files"]:
    assert hashlib.sha256((root / "source" / row["path"]).read_bytes()).hexdigest() == row["sha256"]
for row in json.loads((root / "upstream/PROVENANCE.json").read_text()):
    data = (root / "upstream" / row["version"] / row.get("filename", row["name"] + ".json")).read_bytes()
    assert hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() == row["sha"]
print("PASS: all package checksums, 21 final source files, patch and exact official upstream blobs")
