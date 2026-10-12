"""Pin public reused bytes; report, rather than invent, executed source identity."""
import hashlib
import json
from pathlib import Path

import cpn
import examples.tool_pipeline as billing


def source_identity():
    manifest = json.loads(Path(__file__).with_name("SOURCES.json").read_text())
    billing_root = Path(billing.__file__).parent
    for item in manifest["files"]:
        path = billing_root / item["path"].removeprefix("examples/tool_pipeline/")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("reused public billing source differs: " + item["path"])

    def tree_digest(root, suffixes):
        digest = hashlib.sha256()
        count = 0
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                digest.update(path.relative_to(root).as_posix().encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
                count += 1
        return {"sha256": digest.hexdigest(), "file_count": count,
                "method": "sorted-relative-path-NUL-and-binary-SHA256"}

    return {"reused_public_source_commit": manifest["source_commit"],
        "reused_files": manifest["files"],
        "executed_cpn_source_tree": tree_digest(Path(cpn.__file__).parent, {".py", ".json", ".js", ".css", ".html"}),
        "executed_example_source_tree": tree_digest(Path(__file__).parent, {".py", ".json"}),
        "product_commit": "not_inferred_from_fixture_pin"}
