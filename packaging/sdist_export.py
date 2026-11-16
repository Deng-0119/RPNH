"""Derive source-distribution documents without editing the source checkout.

Only the reviewed paths below are transformed. Product code, example payloads,
historical dates/results and source identifiers are never rewritten here.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit


PRODUCT_HEADING = "# RPNH Harness"
PUBLIC_REFERENCE = "99642da00523b3c60bc2e974a4ceafb2bbe1b1a2"
PRIVATE_SOURCE_REPOSITORY = "Deng-0119/RPNH-dev"
SCREENSHOT_SOURCE = (
    "../../development/20261114-viewer-acceptance-closure/"
    "local-results-20261114/screenshots/"
)
SCREENSHOT_DESTINATION = "../assets/registry-browser/20261114/"
SCREENSHOT_STEMS = (
    "S01-resources", "S02-exact-record", "S03-position-history",
    "S04-complete-read", "S05-draft-and-result", "S06-disable",
    "S07-carriers", "S08-cleanup", "S09-narrow-navigation",
)
SCREENSHOTS = {stem + suffix for stem in SCREENSHOT_STEMS
               for suffix in (".annotated.png", ".raw.png")}
PRIVATE_REFERENCE_DOCUMENTS = (
    "CHANGELOG.md", "CHANGELOG_ZH.md",
    "docs/reference/frontend-compatibility.md",
    "docs/reference/frontend-compatibility_ZH.md",
    "docs/guides/registry-browser.md", "docs/guides/registry-browser_ZH.md",
)
PRIVATE_ONLY_TESTS = (
    "tests/test_frontend_candidate_launcher.py",
    "tests/test_frontend_connectivity_fixture.py",
    "tests/test_multi_tool_local_preparation.py",
    "tests/test_installed_business_runner.py",
    "tests/test_installed_worker_runner.py",
    "tests/installed_worker_support.py",
    "tests/material_purge_joined_runner.py",
)
LINK = re.compile(r"(?P<image>!?)\[(?P<label>[^\]\n]*)\]\((?P<target>[^\s)]+)\)")


def product_readme(text: str, name: str) -> str:
    """Preserve the single product section verbatim, including final newlines."""
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines)
              if line.rstrip("\r\n") == PRODUCT_HEADING]
    if len(starts) != 1:
        raise ValueError(f"{name} must contain exactly one {PRODUCT_HEADING!r} product heading")
    return "".join(lines[starts[0]:])


def private_target(target: str) -> bool:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return parsed.netloc.lower() == "github.com" and parsed.path.lstrip("/").startswith(
            PRIVATE_SOURCE_REPOSITORY + "/")
    return "development" in PurePosixPath(parsed.path).parts


def exported_reference_document(text: str, name: str, root: Path) -> tuple[str, int, int]:
    """Keep the visible historical account; remove only unavailable link targets."""
    unlinked = 0
    relocated = 0

    def replace(match):
        nonlocal unlinked, relocated
        label, target = match["label"], match["target"]
        if target.startswith(SCREENSHOT_SOURCE):
            filename = target.removeprefix(SCREENSHOT_SOURCE)
            if filename not in SCREENSHOTS:
                raise ValueError(f"unreviewed screenshot reference in {name}: {filename}")
            destination = root / "docs/assets/registry-browser/20261114" / filename
            if not destination.is_file() or destination.is_symlink():
                raise FileNotFoundError(f"missing original screenshot: {destination}")
            relocated += 1
            return f"{match['image']}[{label}]({SCREENSHOT_DESTINATION}{filename})"
        if not private_target(target):
            return match[0]
        if match["image"]:
            raise ValueError(f"unreviewed private image in {name}")
        unlinked += 1
        return label

    exported = LINK.sub(replace, text)
    # The original private source identity stays a private source; no historical
    # commit is converted into a link or a claim about a public/current revision.
    exported = exported.replace("RPNH-dev", "私有源码" if name.endswith("_ZH.md") else "private source")
    if unlinked:
        note = (
            "\n> 源码发行包说明：内部证据保留原题名、日期、源码标识和结果范围；"
            "未分发的证据不提供链接。它们不代表当前成品重新通过验收。\n"
            if name.endswith("_ZH.md") else
            "\n> Source distribution note: internal evidence retains its original titles, dates, "
            "source identifiers and result scope. Evidence not distributed here is not linked; "
            "these historical records do not certify the current artifact.\n"
        )
        if note not in exported:
            exported = exported.rstrip("\n") + "\n" + note
    return exported, unlinked, relocated


def replace_staged(path: Path, data: bytes) -> None:
    """Replace a possibly hard-linked staging entry, never write through it."""
    temporary = path.with_name(path.name + ".rpnh-export-tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def export_release_tree(root: Path) -> list[dict]:
    """Apply the same offline policy to a checkout build or an unpacked sdist."""
    changes = []

    def update(name, data, *, kind, **details):
        path = root / name
        before = path.read_bytes()
        if before != data:
            replace_staged(path, data)
        changes.append({"path": name, "kind": kind, "changed": before != data,
                        "source_bytes": len(before), "exported_bytes": len(data), **details})

    for name in ("README.md", "README_ZH.md"):
        path = root / name
        if path.is_file():
            update(name, product_readme(path.read_text(encoding="utf-8"), name).encode("utf-8"),
                   kind="product_readme")
    for name in ("technical-report.md", "technical-report_ZH.md"):
        target = "docs/" + name
        if (root / target).is_file():
            snapshot = root / "packaging/public-docs" / name
            if snapshot.is_symlink() or not snapshot.is_file():
                raise FileNotFoundError(f"missing fixed public report snapshot: {snapshot}")
            text = snapshot.read_text(encoding="utf-8")
            old_guide = "releases_ZH.md" if name.endswith("_ZH.md") else "releases.md"
            # This historical report predates the current updates guide. Link
            # its historical release account to the same fixed public source,
            # without reintroducing an obsolete "latest release" guide locally.
            text = text.replace(f"](guides/{old_guide})",
                f"](https://github.com/Deng-0119/RPNH/blob/{PUBLIC_REFERENCE}/docs/guides/{old_guide})")
            update(target, text.encode("utf-8"), kind="fixed_public_report", reference=PUBLIC_REFERENCE,
                   historical_release_guide=old_guide)
    for name in PRIVATE_REFERENCE_DOCUMENTS:
        path = root / name
        if path.is_file():
            text, unlinked, relocated = exported_reference_document(path.read_text(encoding="utf-8"), name, root)
            update(name, text.encode("utf-8"), kind="available_document_references",
                   unlinked_private_targets=unlinked, relocated_images=relocated)
    for name in ("CONTRIBUTING.md", "CONTRIBUTING_ZH.md"):
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            text = text.replace("[AGENTS.md](AGENTS.md)",
                f"[AGENTS.md](https://github.com/Deng-0119/RPNH/blob/{PUBLIC_REFERENCE}/AGENTS.md)")
            update(name, text.encode("utf-8"), kind="public_contribution_policy_reference")
    forbidden = [name for name in PRIVATE_ONLY_TESTS if (root / name).exists()]
    if (root / "docs/technical-report.sources.json").exists():
        forbidden.append("docs/technical-report.sources.json")
    if forbidden:
        raise ValueError(f"private-only inputs escaped the source manifest: {forbidden}")
    return changes
