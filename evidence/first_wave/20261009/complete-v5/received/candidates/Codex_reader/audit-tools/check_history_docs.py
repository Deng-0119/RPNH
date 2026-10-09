"""Targeted page checks using the exact main documentation parser."""
import hashlib
import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
checker = root / "audit-tools/docs.py"
raw = checker.read_bytes()
blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
assert blob == "9de34e8187c718d21b5a5f8efcac878a42139b48"
spec = importlib.util.spec_from_file_location("rpnh_docs_check", checker)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
pages = [module.parse_page(root / "source/docs/reference" / name) for name in (
    "main-thread-history.md", "main-thread-history_ZH.md",
    "extensions-observation.md", "extensions-observation_ZH.md",
)]
for page in pages:
    counterpart = module.parse_page(page.path.parent / page.metadata["counterpart"])
    assert counterpart.metadata["name"] == page.metadata["name"]
    assert counterpart.metadata["revision"] == page.metadata["revision"]
    assert counterpart.metadata["language"] != page.metadata["language"]
    assert counterpart.metadata["counterpart"] == page.path.name
for page in pages[:2]:
    for token in page.tokens:
        for child in token.children or ():
            if child.type == "link_open":
                target = module.local_target(page.path, child.attrGet("href"), root / "source")
                assert target is None or target[0].is_file()
for page, destination in zip(pages[2:], pages[:2]):
    assert destination.path.name in page.path.read_text()
print("PASS: 4 changed pages parsed, bilingual identity/revision pairs and new-page links checked.")
print("Full-site check/build not run: isolated code baseline omits existing site files; no installation.")
