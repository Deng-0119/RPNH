"""Offline assembly contract tests; no installer or provider calls."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

SOURCE = Path(__file__).resolve().parent


class PackageAssemblyTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wheel = self.root / "rpnh_harness-0.1.0rc9-py3-none-any.whl"
        with zipfile.ZipFile(self.wheel, "w") as wheel:
            wheel.writestr("rpnh_harness-0.1.0rc9.dist-info/METADATA",
                           "Name: rpnh-harness\nVersion: 0.1.0rc9\n")
            wheel.writestr("rpnh_harness-0.1.0rc9.dist-info/WHEEL",
                           "Wheel-Version: 1.0\nTag: py3-none-any\n")
            wheel.writestr("cpn/rpnh/bootstrap_entry.py",
                           'def default_root(): pass\ndef first_run_setup(): pass\nFLAG="--first-run"\n')
        self.license = self.root / "LICENSE"
        self.license.write_text("MIT fixture\n")
        self.output = self.root / "package"

    def assemble(self, *options, version="0.1.0-rc.9"):
        return subprocess.run([
            sys.executable, str(SOURCE / "prepare_first_run_candidate.py"),
            "--convenience-source", str(SOURCE), "--launcher-source", str(SOURCE),
            "--wheel", str(self.wheel), "--npm-version", version,
            "--output", str(self.output), "--license", str(self.license), *options,
        ], capture_output=True, text=True)

    def package(self):
        return json.loads((self.output / "package.json").read_text())

    def test_public_cli_identity_and_internal_transport(self):
        result = self.assemble("--public-cli")
        self.assertEqual(result.returncode, 0, result.stderr)
        package = self.package()
        self.assertEqual(package["name"], "rpnh")
        self.assertEqual(package["version"], "0.1.0-rc.9")
        self.assertEqual(package["bin"], {"rpnh": "first-run.mjs"})
        self.assertEqual(package["publishConfig"], {"access": "public", "tag": "latest"})
        self.assertEqual(package["engines"], {"node": ">=20"})
        for key in ("private", "dependencies", "devDependencies", "scripts"):
            self.assertNotIn(key, package)
        self.assertIn("entry.mjs", package["files"])
        self.assertIn("controller/", package["files"])
        first = (self.output / "first-run.mjs").read_text()
        self.assertIn("new URL('./entry.mjs',import.meta.url)", first)
        self.assertNotIn("./release-bootstrap.mjs", first)
        entry = (self.output / "entry.mjs").read_text()
        self.assertIn("./controller/" + self.wheel.name, entry)
        self.assertEqual([p.name for p in (self.output / "controller").iterdir()],
                         [self.wheel.name])
        with zipfile.ZipFile(self.output / "controller" / self.wheel.name) as wheel:
            metadata = wheel.read("rpnh_harness-0.1.0rc9.dist-info/METADATA").decode()
            self.assertIn("Version: 0.1.0rc9", metadata)
        readme = (self.output / "README.md").read_text()
        for expected in ("npm install rpnh", "npm exec -- rpnh", "npm install -g rpnh",
                         "CPython 3.12.13", "WSL2 Linux x86_64", "Basic"):
            self.assertIn(expected, readme)
        help_result = subprocess.run(["node", str(self.output / "first-run.mjs"), "--help"],
                                     capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage: rpnh", help_result.stdout)
        transport = subprocess.run([
            "node", "--input-type=module", "-e",
            "import {bundledArguments} from " + json.dumps((self.output / "entry.mjs").as_uri())
            + "; console.log(JSON.stringify(bundledArguments(['--','--first-run'])));"
            + " try {bundledArguments(['--controller-wheel','other.whl']); process.exit(1); }"
            + " catch (e) {if (!e.message.includes('exact bundled controller')) throw e;}",
        ], capture_output=True, text=True)
        self.assertEqual(transport.returncode, 0, transport.stderr)
        self.assertEqual(json.loads(transport.stdout), [
            "--controller-wheel", str(self.output / "controller" / self.wheel.name),
            "--", "--first-run"])

    def test_private_and_public_preview_keep_existing_identity(self):
        for options in ((), ("--public-preview",)):
            with self.subTest(options=options):
                self.output = self.root / ("preview" if options else "private")
                result = self.assemble(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                package = self.package()
                self.assertEqual(package["name"], "rpnh-bootstrap")
                self.assertEqual(package["bin"], {
                    "rpnh-bootstrap": "entry.mjs", "rpnh": "first-run.mjs"})
                if options:
                    self.assertNotIn("private", package)
                    self.assertEqual(package["publishConfig"], {"access": "public", "tag": "next"})
                else:
                    self.assertTrue(package["private"])
                    self.assertNotIn("publishConfig", package)

    def test_public_modes_are_mutually_exclusive(self):
        result = self.assemble("--public-cli", "--public-preview")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not allowed with argument", result.stderr)
        self.assertFalse(self.output.exists())

    def test_official_cli_rejects_mismatched_wheel_version(self):
        result = self.assemble("--public-cli", version="0.1.0-rc.10")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wheel and npm version must agree", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    main()
