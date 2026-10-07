"""Read and copy installed reusable examples without importing example code."""
from __future__ import annotations

import hashlib
import importlib.resources
import json
from pathlib import Path, PurePosixPath
from typing import Any


def catalog() -> dict[str, Any]:
    return json.loads(importlib.resources.files("cpn.examples").joinpath(
        "catalog.json").read_text(encoding="utf-8"))


def _assets():
    # This is populated at build time from authoritative examples/. An installed
    # export must never discover, import, or fall back to a source checkout.
    return importlib.resources.files("cpn.examples").joinpath("gallery")


def _files(source, prefix: str):
    if source.is_file():
        yield prefix, source
    elif source.is_dir():
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            yield from _files(child, f"{prefix}/{child.name}")
    else:
        raise ValueError(
            f"installed example asset is missing: {prefix}; install a built rpnh-harness wheel")


def _readme(example: dict[str, Any], *, chinese: bool) -> str:
    title = example["title_zh" if chinese else "title"]
    if chinese:
        lines = [f"# {title}", "", "[English](README.md) | 中文", "",
                 "本目录是从已安装发行包导出的用户自有副本，不需要源码检出。", "",
                 "## 前提与执行边界", "", *[f"- {item}" for item in example["prerequisites_zh"]],
                 "", f"执行边界：{example['execution_boundary']}", "",
                 "列举与导出只读取/复制资源；不会导入案例、安装插件、启动任务或调用 provider。", "",
                 "## 修改与运行", "", "先进入本导出根目录；可修改：", ""]
    else:
        lines = [f"# {title}", "", "English | [中文](README_ZH.md)", "",
                 "This is your editable copy from the installed distribution. No source checkout is required.", "",
                 "## Prerequisites and execution boundary", "", *[f"- {item}" for item in example["prerequisites"]],
                 "", f"Execution boundary: {example['execution_boundary']}", "",
                 "Listing and exporting only read/copy assets. They do not import examples, install plugins, start tasks or call a provider.", "",
                 "## Modify and run", "", "Change into this exported root first. Modification points:", ""]
    lines.extend(f"- `{path}`" for path in example["modify"])
    commands = example["commands"]
    if commands:
        lines.extend(["", "```bash", *commands, "```"])
    if example["guide"]:
        guide = example["guide"]
        if chinese:
            guide = guide.replace("README.md", "README_ZH.md")
        lines.extend(["", f"[{('完整案例指南' if chinese else 'Complete example guide')}]({guide})"])
    if example["id"] == "hybrid_summary":
        lines.extend(["", (
            "脚本化 fixture 只识别随附任务协议；任意 prompt/graph 改动可能需同步修改 _support/scripted_model.py，或单独授权真实 execution profile。保留同级依赖目录。"
            if chinese else
            "The scripted fixture recognizes the included task protocol. Arbitrary prompt/graph changes may require editing _support/scripted_model.py or separately authorizing a live execution profile. Keep sibling dependency directories together.")])
    lines.extend(["", (
        "每次运行使用未存在的 run 目录。导出拒绝已存在的目标目录，绝不覆盖用户修改；升级时请导出到新目录后自行比较。"
        if chinese else
        "Use an absent run directory for every new run. Export refuses an existing destination and never overwrites your edits; export upgrades to a new directory and compare them yourself."), "", (
        "manifest.json 列出完整复制闭包和原始文件 SHA-256；这些哈希只标识导出时的原始内容，不阻止修改。LICENSE 保留原许可。"
        if chinese else
        "manifest.json records the complete copied closure and original file SHA-256 hashes. These identify original exported contents and do not prevent edits. LICENSE preserves the source license."), "",
        f"[{('上游源码' if chinese else 'Upstream source')}]({example['source_url']})", ""])
    return "\n".join(lines)


def export_files(example_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    example = next((item for item in catalog()["examples"] if item["id"] == example_id), None)
    if example is None:
        raise ValueError(f"unknown example: {example_id}; see rpnh examples list")
    if not example["exportable"]:
        raise ValueError(f"{example_id} is source-only; see {example['source_url']}")
    if example_id == "adapter_task":
        raise ValueError("adapter_task uses the backwards-compatible bundle exporter")
    resources = _assets()
    copied: dict[str, bytes] = {}
    for path in ["LICENSE", *example["paths"]]:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid packaged example resource path")
        for name, resource in _files(resources.joinpath(*relative.parts), path):
            copied[name] = resource.read_bytes()
    if example["guide"]:
        for guide in (example["guide"], example["guide"].replace("README.md", "README_ZH.md")):
            if guide not in copied:
                raise ValueError(f"installed example guide is missing: {guide}")
    copied["README.md"] = _readme(example, chinese=False).encode("utf-8")
    copied["README_ZH.md"] = _readme(example, chinese=True).encode("utf-8")
    manifest = {
        "schema_version": "rpnh/reusable_example_export/v1",
        "example_id": example_id,
        "source_url": example["source_url"],
        "execution_boundary": example["execution_boundary"],
        "prerequisites": example["prerequisites"],
        "dependency_closure": example["paths"],
        "modify": example["modify"],
        "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in sorted(copied.items())],
    }
    copied["manifest.json"] = (json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return manifest, copied


def write_export(destination: Path, copied: dict[str, bytes]) -> None:
    # Exclusive creation prevents an overwrite even if another exporter races.
    destination.mkdir(mode=0o700)
    for name, content in copied.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
