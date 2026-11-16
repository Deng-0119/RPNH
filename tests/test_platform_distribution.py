"""Offline installation control flow and build-content boundary checks.

The archive test builds a minimal fixture with the production build rules. It
does not certify the full repository payload or any macOS native execution.
No downloads, provider calls or package installation are performed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"


class SourceInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh installer ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "unexpected-command"
        self.script("git", "printf 'git was executed\\n' > "
                    + shlex.quote(str(self.log)) + "\nexit 97\n")
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"])

    def script(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_installer(self, *args):
        return subprocess.run(
            ["bash", str(INSTALLER), *map(str, args)], env=self.env,
            cwd=self.root, text=True, capture_output=True, timeout=30,
        )

    def source(self):
        source = self.root / "local source"
        (source / "cpn/rpnh").mkdir(parents=True)
        (source / "examples/native_plugin").mkdir(parents=True)
        (source / "pyproject.toml").write_text('[project]\nname="rpnh-harness"\n')
        (source / "examples/native_plugin/pyproject.toml").write_text('[project]\nname="demo"\n')
        return source

    def test_check_is_read_only_and_uses_selected_interpreter(self):
        invocation = self.root / "python invocation"
        python = self.script(
            "chosen python", "printf '%s\\n' \"$*\" > " + shlex.quote(str(invocation))
            + "\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
        )
        prefix = self.root / "absent prefix"
        result = self.run_installer("--check", "--python", python, "--prefix", prefix)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("manual/source", result.stdout)
        self.assertIn("no installation was performed", result.stdout)
        self.assertTrue(invocation.is_file())
        self.assertFalse(prefix.exists())
        self.assertFalse(self.log.exists())

    def test_local_source_uses_its_runtime_preflight(self):
        source = self.source()
        (source / "cpn/rpnh/platform_support.py").write_text(
            "import sys\nprint('selected source preflight')\n"
            "assert sys.argv[1:] == ['--json']\nsys.exit(2)\n",
        )
        prefix = self.root / "absent prefix"
        result = self.run_installer("--check", "--source", source, "--prefix", prefix)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("selected source preflight", result.stdout)
        self.assertFalse(prefix.exists())
        self.assertFalse(self.log.exists())

    def test_unsupported_platform_is_rejected_before_installation(self):
        self.script("uname", "printf 'MSYS_NT\\n'\n")
        prefix = self.root / "absent prefix"
        result = self.run_installer("--prefix", prefix)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported platform", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse(self.log.exists())

    def test_existing_prefix_is_preserved(self):
        prefix = self.root / "existing prefix"
        prefix.mkdir()
        marker = prefix / "keep.txt"
        marker.write_text("existing installation")
        result = self.run_installer("--prefix", prefix)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory already exists", result.stderr)
        self.assertEqual(marker.read_text(), "existing installation")
        self.assertFalse(self.log.exists())

    def test_ref_and_local_source_are_unambiguous(self):
        result = self.run_installer("--source", self.source(), "--ref", "main", "--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be used together", result.stderr)
        self.assertFalse(self.log.exists())

    def test_missing_python_is_rejected_without_creating_prefix(self):
        prefix = self.root / "absent prefix"
        result = self.run_installer("--python", self.root / "missing", "--prefix", prefix)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python executable not found", result.stderr)
        self.assertFalse(prefix.exists())
        self.assertFalse(self.log.exists())

    def test_managed_check_forwards_fixed_materials_to_selected_manager_without_writes(self):
        invocation = self.root / "manager arguments"
        manager = self.script("manager python", "printf '%s\\0' \"$@\" > "
                              + shlex.quote(str(invocation)) + "\nexit 0\n")
        prefix = self.root / "dedicated candidate"
        wheel = self.root / "selected wheel.whl"
        materials = self.root / "selected materials.json"
        components = self.root / "complete components.json"
        result = self.run_installer("--candidate-wheel", wheel, "--materials", materials,
                                    "--prefix", prefix, "--components", components,
                                    "--python", manager, "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = invocation.read_bytes().decode().rstrip("\0").split("\0")
        self.assertEqual(arguments, ["-I", "-m", "cpn.rpnh.update_cli", "plan", "--root", str(prefix),
                                    "--candidate-wheel", str(wheel), "--materials", str(materials),
                                    "--components", str(components)])
        self.assertFalse(prefix.exists())
        self.assertFalse(self.log.exists())

    def test_managed_mode_requires_explicit_complete_selection_before_any_command(self):
        prefix = self.root / "dedicated candidate"
        cases = [
            ["--candidate-wheel", "candidate.whl", "--prefix", prefix],
            ["--candidate-wheel", "candidate.whl", "--materials", "materials.json"],
            ["--candidate-wheel", "candidate.whl", "--materials", "materials.json",
             "--prefix", prefix, "--source", self.root],
            ["--candidate-wheel", "candidate.whl", "--materials", "materials.json",
             "--prefix", prefix, "--check", "--select", "auto"],
            ["--components", "components.json", "--prefix", prefix],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.run_installer(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(prefix.exists())
                self.assertFalse(self.log.exists())


class DistributionBoundaryTests(unittest.TestCase):
    def test_production_build_rules_exclude_private_material_and_include_installer(self):
        with tempfile.TemporaryDirectory(prefix="rpnh-distribution-fixture-") as temp:
            root = Path(temp)
            for name in ("setup.py", "MANIFEST.in", "pyproject.toml"):
                shutil.copyfile(ROOT / name, root / name)
            (root / "packaging").mkdir()
            shutil.copyfile(ROOT / "packaging/sdist_export.py", root / "packaging/sdist_export.py")
            files = {
                "LICENSE": "MIT fixture license\n",
                "THIRD_PARTY_NOTICES.md": "Fixture notices\n",
                "README.md": "> Private checkout [entry](development/current.md)\n\n# RPNH Harness\n\nFixture package\n",
                "README_ZH.md": "> 私有导航\n\n# RPNH Harness\n\n发行夹具\n",
                "CONTRIBUTING.md": "Read [AGENTS.md](AGENTS.md).\n",
                "cpn/__init__.py": '"""Build fixture."""\n',
                "cpn/examples/catalog.json": json.dumps({"examples": [
                    {"exportable": True, "paths": ["examples/native_plugin"]},
                ]}),
                "examples/native_plugin/pyproject.toml": '[project]\nname="demo"\n',
                "examples/native_plugin/input.json": '{"x":1}\n',
                "examples/native_plugin/rpnh_demo.py": "value = 1\n",
                "cpn/schemas/probe.json": '{}\n',
                "cpn/frontend/static/viewer.html": "<main>fixture</main>\n",
                "integrations/dsh/UPSTREAM_LICENSE": "MIT fixture license\n",
                "docs/guide.md": "Fixture guide\n",
                "docs/technical-report.md": "PRIVATE_RESEARCH_BODY\n",
                "docs/technical-report_ZH.md": "PRIVATE_RESEARCH_BODY_ZH\n",
                "docs/technical-report.sources.json": '{"private": true}\n',
                "packaging/public-docs/technical-report.md":
                    "Public report fixture: historical edition\n[Historical releases](guides/releases.md)\n",
                "packaging/public-docs/technical-report_ZH.md": "公开报告夹具：历史版本\n",
                "docs/reference/frontend-compatibility.md":
                    "2026-11-10: default 0.163.0; [private evidence](../../development/batch/evidence.md).\n",
                "tests/support_fixture.py": "VALUE = 1\n",
                "tests/fixtures/codex/Example.json": '{"type": "object"}\n',
                "tests/fixtures/codex/Example.ts": "export type Example = string;\n",
                "tests/fixtures/codex/README.md": "Exact schema fixture origin\n",
                "tests/fixtures/codex/UPSTREAM_LICENSE": "Original fixture license\n",
                "tests/fixtures/codex/UPSTREAM_NOTICE": "Original fixture notice\n",
                "frontend/net-viewer/tests/comparison-fixture.json": '{}\n',
                "frontend/net-viewer/tests/checkpoint-test-harness.mjs": "export const fixture = true;\n",
                "frontend/net-viewer/node_modules/unpublished-fixture/package.json": '{"private": true}\n',
                "tests/test_installed_business_runner.py": "PRIVATE_DEVELOPMENT_TEST = True\n",
                "docs/AGENTS.md": "PRIVATE_NESTED_SENTINEL\n",
                "AGENTS.md": "PRIVATE_ROOT_SENTINEL\n",
                "development/private.txt": "PRIVATE_DEVELOPMENT_SENTINEL\n",
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (root / "scripts").mkdir()
            shutil.copyfile(INSTALLER, root / "scripts/install.sh")
            original_readmes = {name: (root / name).read_bytes() for name in ("README.md", "README_ZH.md")}
            result = subprocess.run(
                [sys.executable, "setup.py", "sdist", "bdist_wheel"],
                cwd=root, text=True, capture_output=True, timeout=120,
                env=dict(os.environ, PIP_NO_INDEX="1"),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name, original in original_readmes.items():
                self.assertEqual((root / name).read_bytes(), original)
            self.assertEqual((root / "docs/technical-report.md").read_text(), "PRIVATE_RESEARCH_BODY\n")
            extracted = root / "unpacked"
            with tarfile.open(next((root / "dist").glob("*.tar.gz"))) as archive:
                names = {"/".join(name.split("/")[1:]) for name in archive.getnames()}
                self.assertIn("scripts/install.sh", names)
                self.assertIn("examples/native_plugin/input.json", names)
                self.assertIn("docs/guide.md", names)
                self.assertIn("packaging/sdist_export.py", names)
                self.assertIn("packaging/public-docs/technical-report.md", names)
                self.assertIn("tests/support_fixture.py", names)
                self.assertIn("tests/fixtures/codex/Example.json", names)
                self.assertIn("tests/fixtures/codex/Example.ts", names)
                self.assertIn("tests/fixtures/codex/README.md", names)
                self.assertIn("tests/fixtures/codex/UPSTREAM_LICENSE", names)
                self.assertIn("tests/fixtures/codex/UPSTREAM_NOTICE", names)
                self.assertIn("frontend/net-viewer/tests/checkpoint-test-harness.mjs", names)
                self.assertFalse(any(name.startswith("frontend/net-viewer/node_modules/") for name in names))
                self.assertNotIn("docs/technical-report.sources.json", names)
                self.assertNotIn("tests/test_installed_business_runner.py", names)
                self.assertFalse(any(name.endswith("AGENTS.md") for name in names))
                self.assertFalse(any(name.startswith("development/") for name in names))
                archive.extractall(extracted, filter="data")
            source = next(extracted.iterdir())
            self.assertEqual((source / "README.md").read_text(), "# RPNH Harness\n\nFixture package\n")
            self.assertEqual((source / "README_ZH.md").read_text(), "# RPNH Harness\n\n发行夹具\n")
            report_snapshot = (root / "packaging/public-docs/technical-report.md").read_text()
            exported_report = (source / "docs/technical-report.md").read_text()
            self.assertEqual(exported_report, report_snapshot.replace(
                "](guides/releases.md)",
                "](https://github.com/Deng-0119/RPNH/blob/99642da00523b3c60bc2e974a4ceafb2bbe1b1a2/docs/guides/releases.md)"))
            self.assertEqual((source / "packaging/public-docs/technical-report.md").read_text(), report_snapshot)
            compatibility = (source / "docs/reference/frontend-compatibility.md").read_text()
            self.assertIn("2026-11-10: default 0.163.0; private evidence", compatibility)
            self.assertNotIn("development/", compatibility)
            self.assertIn("Source distribution note", compatibility)
            self.assertIn("github.com/Deng-0119/RPNH/blob/", (source / "CONTRIBUTING.md").read_text())
            rebuilt = subprocess.run(
                [sys.executable, "setup.py", "sdist", "bdist_wheel"], cwd=source,
                text=True, capture_output=True, timeout=120,
                env=dict(os.environ, PIP_NO_INDEX="1"),
            )
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
            with tarfile.open(next((source / "dist").glob("*.tar.gz"))) as archive:
                member = next(item for item in archive.getmembers()
                              if item.name.endswith("/docs/reference/frontend-compatibility.md"))
                self.assertEqual(archive.extractfile(member).read().decode(), compatibility)
            with zipfile.ZipFile(next((root / "dist").glob("*.whl"))) as archive:
                names = set(archive.namelist())
                self.assertIn("cpn/examples/gallery/examples/native_plugin/input.json", names)
                self.assertIn("cpn/frontend/static/viewer.html", names)
                self.assertIn("cpn/schemas/probe.json", names)
                self.assertFalse(any(name.endswith("AGENTS.md") for name in names))
                self.assertFalse(any(name.startswith("development/") for name in names))
            with zipfile.ZipFile(next((source / "dist").glob("*.whl"))) as rebuilt_wheel, \
                    zipfile.ZipFile(next((root / "dist").glob("*.whl"))) as direct_wheel:
                for name in direct_wheel.namelist():
                    if name.startswith(("cpn/", "integrations/")):
                        self.assertEqual(direct_wheel.read(name), rebuilt_wheel.read(name), name)

    def test_export_replaces_a_hardlink_without_editing_repository_content(self):
        export = runpy.run_path(str(ROOT / "packaging/sdist_export.py"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original, staged = root / "original.md", root / "staged.md"
            original.write_text("repository navigation and body\n")
            os.link(original, staged)
            export["replace_staged"](staged, b"public body\n")
            self.assertEqual(original.read_text(), "repository navigation and body\n")
            self.assertEqual(staged.read_text(), "public body\n")

    def test_product_heading_must_be_unique_and_unknown_private_image_is_rejected(self):
        export = runpy.run_path(str(ROOT / "packaging/sdist_export.py"))
        for text in ("missing product heading\n", "# RPNH Harness\n# RPNH Harness\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "exactly one"):
                export["product_readme"](text, "README.md")
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(ValueError, "unreviewed private image"):
            export["exported_reference_document"](
                "![unknown](../../development/unreviewed.png)", "docs/guide.md", Path(temp))


if __name__ == "__main__":
    unittest.main()
