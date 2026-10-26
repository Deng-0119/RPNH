"""Offline export enumeration regressions; no install, example import or owner run."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


# Load only the exporter. The regression has no runtime/provider dependencies.
_SPEC = importlib.util.spec_from_file_location(
    "gallery_under_test", Path(__file__).resolve().parents[1] / "cpn/examples/gallery.py")
gallery = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gallery)


class GalleryBytecodeExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "resources"
        self.root.mkdir()
        self.kept = {
            "LICENSE": b"fixture license\n",
            "examples/demo/__init__.py": b"raise AssertionError('must not import')\n",
            "examples/demo/module.py": b"raise AssertionError('must not execute')\n",
            "examples/demo/README.md": b"English\n",
            "examples/demo/README_ZH.md": "中文\n".encode(),
            "examples/demo/data/surveys.csv": b"date,value\n1977-07-16,3\n",
            "examples/demo/data/input.json": b'{"value": 3}\n',
            "examples/demo/assets/view.png": b"\x89PNG\r\n\x1a\nfixture",
            "examples/demo/static/app.js": b"const value = 3;\n",
            "examples/demo/static/index.html": b"<p>fixture</p>\n",
            "examples/demo/static/style.css": b"p { color: blue; }\n",
            "examples/demo/__pycache__.json": b'{"keep": true}',
            "examples/demo/data/__pycache__": b"a file, not a cache directory",
            "examples/demo/__pycache__-data/keep.txt": b"similar directory name",
            "examples/demo/module.pyc.txt": b"bytecode documentation",
            "examples/demo/module.pyo.json": b"{}",
            "examples/demo/.pyc": b"a hidden data filename with no suffix",
            "examples/demo/reports.pyc/keep.csv": b"a directory, not bytecode",
            "examples/demo/reports.pyo/keep.csv": b"a directory, not bytecode",
            "examples/demo/数据 目录/保留.txt": "原始内容".encode(),
        }
        self.excluded = {
            "examples/demo/__pycache__/module.cpython-313.pyc": b"cache",
            "examples/demo/__pycache__/module.cpython-313.opt-1.pyc": b"optimized",
            "examples/demo/__pycache__/module.cpython-312.pyc": b"other version",
            "examples/demo/nested/__pycache__/nested.cpython-313.pyc": b"nested cache",
            "examples/demo/module.pyc": b"legacy bytecode",
            "examples/demo/module.pyo": b"legacy optimized bytecode",
        }
        self.example = {
            "id": "demo", "exportable": True, "paths": ["examples/demo"],
            "title": "Demo", "title_zh": "示例", "prerequisites": [],
            "prerequisites_zh": [], "execution_boundary": "copy-only",
            "modify": ["examples/demo/module.py"], "commands": [],
            "guide": "examples/demo/README.md", "source_url": "https://example.test/demo",
        }
        # Reverse creation order to ensure enumeration explicitly sorts children.
        self.write_members(dict(reversed(list(self.kept.items()))))

    def write_members(self, members):
        for name, content in members.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def export(self, assets=None):
        with patch.object(gallery, "_assets", return_value=assets or self.root), \
                patch.object(gallery, "catalog", return_value={"examples": [self.example]}):
            return gallery.export_files("demo")

    def test_directory_export_ignores_runtime_caches_and_keeps_exact_assets(self):
        self.write_members(self.excluded)
        manifest, copied = self.export()
        self.assertEqual(set(copied), set(self.kept) | {"README.md", "README_ZH.md", "manifest.json"})
        for name, content in self.kept.items():
            self.assertEqual(copied[name], content, name)
        self.assertEqual(manifest["dependency_closure"], ["examples/demo"])

    def test_manifest_and_copy_use_the_same_filtered_enumeration(self):
        self.write_members(self.excluded)
        manifest, copied = self.export()
        source = {name: resource.read_bytes() for name, resource in gallery._files(self.root, "")}
        self.assertEqual({name.lstrip("/"): data for name, data in source.items()}, self.kept)
        self.assertEqual(json.loads(copied["manifest.json"]), manifest)
        self.assertEqual(manifest["files"], [
            {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sorted(copied.items()) if name != "manifest.json"])

    def test_zip_traversable_matches_directory_export(self):
        self.write_members(self.excluded)
        archive = Path(self.temporary.name) / "resources.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in sorted(self.root.rglob("*")):
                if path.is_file():
                    output.write(path, path.relative_to(self.root).as_posix())
        with zipfile.ZipFile(archive) as source:
            self.assertEqual(self.export(zipfile.Path(source)), self.export())

    def test_cache_injection_does_not_change_export_or_manifest(self):
        clean = self.export()
        self.write_members(self.excluded)
        self.assertEqual(self.export(), clean)

    def test_names_are_filtered_by_node_kind_and_exact_suffix(self):
        self.write_members(self.excluded)
        for name in ("module.pyc", "module.pyo", "__pycache__"):
            with self.subTest(name=name):
                self.assertEqual(list(gallery._files(self.root / "examples/demo" / name, name)), [])
        for name in ("__pycache__.json", "module.pyc.txt", ".pyc", "reports.pyc", "reports.pyo", "data/__pycache__"):
            with self.subTest(name=name):
                self.assertTrue(list(gallery._files(self.root / "examples/demo" / name, name)))

    def test_missing_assets_still_raise_even_with_cache_like_names(self):
        for name in ("missing.py", "missing.pyc", "missing.pyo", "__pycache__"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "asset is missing"):
                list(gallery._files(self.root / name, name))

    def test_written_tree_matches_manifest_and_never_overwrites(self):
        self.write_members(self.excluded)
        manifest, copied = self.export()
        destination = Path(self.temporary.name) / "my export"
        gallery.write_export(destination, copied)
        actual = {path.relative_to(destination).as_posix(): path.read_bytes()
                  for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(actual, copied)
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256(actual[item["path"]]).hexdigest(), item["sha256"])
        with self.assertRaises(FileExistsError):
            gallery.write_export(destination, copied)

    def test_cache_injection_does_not_mutate_source_or_import_example(self):
        self.write_members(self.excluded)
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        self.export()
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
