"""Build installed example resources from their single authoritative source tree."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithExamples(build_py):
    """Copy declared example dependency closures, never import their code."""

    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        catalog = json.loads((root / "cpn/examples/catalog.json").read_text(encoding="utf-8"))
        destination = Path(self.build_lib) / "cpn/examples/gallery"
        # Remove only this generated resource tree to avoid stale build contents.
        if destination.exists():
            shutil.rmtree(destination)
        paths = {"LICENSE"}
        for example in catalog["examples"]:
            if example["exportable"]:
                paths.update(example.get("paths", ()))
        allowed = {".py", ".json", ".md", ".png", ".toml", ".txt", ".zip", ".in"}
        ignored = {"__pycache__", "build", "dist", ".pytest_cache", ".venv"}
        for relative in sorted(paths):
            source = root / relative
            if source.is_symlink():
                raise ValueError(f"example assets cannot be symlinks: {relative}")
            if not source.exists():
                raise FileNotFoundError(f"missing declared example asset: {relative}")
            files = source.rglob("*") if source.is_dir() else (source,)
            for file in sorted(files):
                parts = file.relative_to(root).parts
                if any(part in ignored or part.endswith(".egg-info") for part in parts):
                    continue
                if file.is_symlink():
                    raise ValueError(f"example assets cannot be symlinks: {file}")
                if file.is_file() and (file.suffix in allowed or file.name == "LICENSE"):
                    target = destination / file.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(file, target)


setup(cmdclass={"build_py": BuildWithExamples})
