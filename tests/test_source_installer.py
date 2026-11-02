"""Offline installer safety/flow checks; Git and Python installation are stubs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rpnh-installer-test-")
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.root),
                        PATH=str(self.bin) + ":" + os.environ["PATH"],
                        INSTALL_LOG=str(self.root / "commands"))
        self.stub("uname", '#!/bin/bash\necho "${TEST_OS:-Linux}"\n')
        self.stub("git", '''#!/bin/bash
printf 'git %s\\n' "$*" >> "$INSTALL_LOG"
if [[ "$*" == *fetch* && "${FAIL_FETCH:-}" == 1 ]]; then exit 9; fi
if [[ "$1" == init ]]; then mkdir -p "${@: -1}"; fi
if [[ "$*" == *rev-parse* ]]; then printf '%040d\\n' 1; fi
''')
        self.stub("python3", '''#!/bin/bash
printf 'python3 %s\\n' "$*" >> "$INSTALL_LOG"
if [[ "${OLD_PYTHON:-}" == 1 ]]; then exit 1; fi
if [[ "$*" == *'-m venv'* ]]; then
  dest="${@: -1}"; mkdir -p "$dest/bin"
  printf '#!/bin/bash\\nprintf "pip %%s\\\\n" "$*" >> "$INSTALL_LOG"\\n' > "$dest/bin/python"
  printf '#!/bin/bash\\nexit 0\\n' > "$dest/bin/rpnh"
  chmod +x "$dest/bin/python" "$dest/bin/rpnh"
fi
''')

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=self.env,
                              cwd=self.root, text=True, capture_output=True)

    def test_default_install(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / ".local/share/rpnh"
        self.assertTrue((target / "SOURCE_COMMIT").is_file())
        log = (self.root / "commands").read_text()
        self.assertIn("fetch --depth 1 origin main", log)
        self.assertIn("pip -I -m pip --isolated install", log)
        self.assertIn("examples/native_plugin", log)
        self.assertFalse((self.root / ".bashrc").exists())

    def test_explicit_prefix_with_spaces_and_commit(self):
        target = self.root / "custom install"
        result = self.run_script("--prefix", str(target), "--ref", "a" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / "venv/bin/rpnh").is_file())

    def test_existing_directory_retained(self):
        target = self.root / "keep"
        target.mkdir()
        (target / "data").write_text("keep")
        result = self.run_script("--prefix", str(target))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((target / "data").read_text(), "keep")
        self.assertNotIn("git ", (self.root / "commands").read_text())

    def test_dangling_symlink_rejected(self):
        target = self.root / "link"
        target.symlink_to(self.root / "absent")
        self.assertNotEqual(self.run_script("--prefix", str(target)).returncode, 0)
        self.assertTrue(target.is_symlink())

    def test_fetch_failure_keeps_created_directory(self):
        self.env["FAIL_FETCH"] = "1"
        result = self.run_script("--prefix", "failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "failed/source").is_dir())
        self.assertIn("Files are retained", result.stderr)
        self.assertFalse((self.root / "failed/venv").exists())

    def test_unsupported_platform_no_directory(self):
        self.env["TEST_OS"] = "Darwin"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.root / ".local").exists())

    def test_old_python_no_directory(self):
        self.env["OLD_PYTHON"] = "1"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertFalse((self.root / ".local").exists())

    def test_invalid_arguments_no_directory(self):
        for args in [("--prefix",), ("--ref", "-bad"), ("--unknown",)]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)
        self.assertFalse((self.root / ".local").exists())


if __name__ == "__main__":
    unittest.main()
