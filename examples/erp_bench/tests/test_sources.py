"""Synthetic integrity fixtures; no task regeneration or business execution."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rpnh_erp_bench import source


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.task_id = source.TASK_IDS[0]
        self.prefix = f"tasks/{self.task_id}/"
        self.instruction = b"Synthetic instruction\r\nRetain exact bytes.\n"
        self.toml = b'[metadata]\nseed=17\n[agent]\ntimeout_sec=3600\n[verifier]\ntimeout_sec=300\n[environment]\nbuild_timeout_sec=600\n'
        self.files = {self.prefix + "instruction.md": self.instruction,
                      self.prefix + "task.toml": self.toml,
                      self.prefix + "environment/setup.py": b"# synthetic setup\n",
                      "LICENSE": b"synthetic license\n"}
        for name, data in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.metadata = {"revision": source.UPSTREAM_COMMIT, "tasks": {self.task_id: {
            "files": {name: {"sha256": source.sha256(data), "bytes": len(data), "git_blob_sha1": "a" * 40}
                      for name, data in self.files.items()},
            "task_toml": source.tomllib.loads(self.toml.decode())}}}
        self.dirty = b""
        self.pin = source.UPSTREAM_COMMIT
        self.untracked = b""
        self.addCleanup(patch.stopall)
        patch.object(source, "_metadata", return_value=self.metadata).start()
        patch.object(source, "git", side_effect=self.git).start()

    def git(self, root, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(self.root).encode()
        if args == ("rev-parse", "HEAD"):
            return self.pin.encode()
        if args[:2] == ("remote", "get-url"):
            return source.UPSTREAM_REPOSITORY.encode()
        if args[0] == "diff":
            return self.dirty
        if args[:2] == ("ls-files", "--others"):
            return self.untracked
        if args[0] == "ls-files":
            return "\0".join(n for n in self.files if n.startswith(self.prefix)).encode()
        if args[0] == "rev-parse":
            return b"a" * 40
        raise AssertionError(args)

    def test_exact_instruction_and_json_identity(self):
        task = source.validate_task(self.root, self.task_id)
        self.assertEqual(task.instruction_bytes, self.instruction)
        self.assertEqual(task.seed, 17)
        self.assertEqual(task.official_limits["agent_timeout_sec"], 3600)
        self.assertNotIn(str(self.root), source.json_bytes(task.public_identity()).decode())
        self.assertEqual(task.input_sha256, source.sha256(source.json_bytes(task.public_identity())))

    def test_task_allowlist_and_traversal(self):
        for tid in ("../" + self.task_id, "/tmp/task", "unknown", self.task_id + "/"):
            with self.subTest(tid=tid), self.assertRaises(ValueError):
                source.validate_task(self.root, tid)

    def test_pin_mismatch(self):
        self.pin = "0" * 40
        with self.assertRaisesRegex(ValueError, "commit mismatch"):
            source.validate_task(self.root, self.task_id)

    def test_tracked_source_must_be_clean(self):
        self.dirty = b"environment/setup.py"
        with self.assertRaisesRegex(ValueError, "dirty"):
            source.validate_task(self.root, self.task_id)

    def test_untracked_selected_task_rejected(self):
        self.untracked = b"new-environment-input"
        with self.assertRaisesRegex(ValueError, "untracked"):
            source.validate_task(self.root, self.task_id)

    def test_digest_mismatch_even_if_git_claims_clean(self):
        (self.root / self.prefix / "instruction.md").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            source.validate_task(self.root, self.task_id)

    def test_missing_file(self):
        (self.root / "LICENSE").unlink()
        with self.assertRaises(ValueError):
            source.validate_task(self.root, self.task_id)

    def test_symlink_and_path_aliases(self):
        target = self.root / self.prefix / "instruction.md"
        target.unlink()
        target.symlink_to(self.root / "LICENSE")
        with self.assertRaisesRegex(ValueError, "symlink"):
            source.validate_task(self.root, self.task_id)
        for name in ("../LICENSE", "./LICENSE", "//LICENSE", "a//b", "a\\b", "C:/x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                source.safe_file(self.root, name)

    def test_nonfinite_duplicate_json(self):
        for data in (b'{"x":NaN}', b'{"x":1e999}', b'{"x":1,"x":2}'):
            with self.assertRaises(ValueError):
                source.load_json(data)


@unittest.skipUnless(os.environ.get("ERP_TEST_UPSTREAM"), "explicit read-only upstream checkout not configured")
class PinnedCheckoutTests(unittest.TestCase):
    def test_both_exact_pinned_tasks(self):
        for tid in source.TASK_IDS:
            with self.subTest(task=tid):
                task = source.validate_task(os.environ["ERP_TEST_UPSTREAM"], tid)
                self.assertEqual(task.instruction_bytes, task.instruction_path.read_bytes())
                self.assertEqual(task.official_limits["agent_timeout_sec"], 3600)
                self.assertEqual(task.source_pin, source.UPSTREAM_COMMIT)


if __name__ == "__main__":
    unittest.main()
