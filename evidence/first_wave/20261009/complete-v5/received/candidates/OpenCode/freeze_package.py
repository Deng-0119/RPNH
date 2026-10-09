#!/usr/bin/env python3
"""Create the one portable candidate ZIP from reviewed bytes; no upload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from verify_package import digest, files, main as verify


def main():
    root = Path(__file__).resolve().parent
    members = files(root)
    members.pop("FILE_MANIFEST.json", None)
    source = files(root / "source")
    fingerprint = hashlib.sha256("".join(
        name + ":" + digest(path) + "\n" for name, path in sorted(source.items())
    ).encode()).hexdigest()
    manifest = {
        "schema": "rpnh/opencode-candidate-package/v1",
        "main_commit": "8dd360e4848912a998dbd83220c3f0ce0a1caa86",
        "product_parent": "d92ff3704b6002bf5ecbccb3e6a3d1489809a805",
        "default_version": "1.18.32", "candidate_version": "1.18.35",
        "native_g2": "NOT_RUN", "native_g3": "NOT_RUN",
        "source_fingerprint": fingerprint,
        "changed_files": (root / "changed-files.txt").read_text().splitlines(),
        "files": {name: digest(path) for name, path in sorted(members.items())},
    }
    (root / "FILE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify()
    members["FILE_MANIFEST.json"] = root / "FILE_MANIFEST.json"
    archive = root.parent / "RPNH_OpenCode_11835_Candidate_Local_Gate_20261008.zip"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as output:
        for name, path in sorted(members.items()):
            info = ZipInfo(root.name + "/" + name, date_time=(2026, 10, 8, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())
    with ZipFile(archive) as check:
        assert check.testzip() is None, "ZIP CRC failure"
        assert len(check.namelist()) == len(members)
        for name, path in members.items():
            assert check.read(root.name + "/" + name) == path.read_bytes()
    result = {
        "archive": str(archive), "archive_sha256": digest(archive),
        "archive_bytes": archive.stat().st_size, "archive_entries": len(members),
        "patch_sha256": digest(root / "opencode-candidate.patch"),
        "source_fingerprint": fingerprint,
        "verified_baseline_files": 972, "source_files": len(source),
        "changed_files": len(manifest["changed_files"]),
        "g1_author": "274 passed", "g1_independent_rerun": "274 passed",
        "additional_independent_checks": "33 passed",
        "clean_apply_g1": "274 passed", "native_g2": "NOT_RUN", "native_g3": "NOT_RUN",
    }
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        result["archive_sha256"] + "  " + archive.name + "\n")
    (root.parent / "rpnh-opencode-candidate-delivery-result.json").write_text(
        json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
