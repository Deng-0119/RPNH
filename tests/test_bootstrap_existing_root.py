"""Bootstrap must not turn a new installation into implicit reconfiguration."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from cpn.rpnh import bootstrap_entry


class ExistingRootTests(unittest.TestCase):
    def test_default_existing_root_denied_before_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "data"
            root = data / "rpnh"
            root.mkdir(parents=True)
            sentinel = root / "keep.txt"
            sentinel.write_text("preserve")
            update = Mock(return_value=0)
            stderr = io.StringIO()
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}), \
                    contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                bootstrap_entry.main(
                    ["--candidate-wheel", "core.whl", "--materials", "materials.json", "--select", "basic"],
                    update_main=update)
            self.assertEqual(error.exception.code, 2)
            self.assertIn("installation root already exists", stderr.getvalue())
            update.assert_not_called()
            self.assertEqual(sentinel.read_text(), "preserve")

    def test_explicit_file_and_dangling_alias_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            file_root = base / "file"
            file_root.write_text("preserve")
            alias_root = base / "alias"
            alias_root.symlink_to(base / "missing")
            for root in (file_root, alias_root):
                with self.subTest(root=root):
                    update = Mock(return_value=0)
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        bootstrap_entry.main(
                            ["--root", str(root), "--candidate-wheel", "core.whl", "--materials", "materials.json"],
                            update_main=update)
                    self.assertEqual(error.exception.code, 2)
                    update.assert_not_called()
            self.assertEqual(file_root.read_text(), "preserve")
            self.assertTrue(alias_root.is_symlink())

    def test_existing_root_inspection_remains_read_only_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            update = Mock(return_value=0)
            self.assertEqual(bootstrap_entry.main(
                ["--root", temporary, "--candidate-wheel", "core.whl",
                 "--materials", "materials.json", "--inspect-only"],
                update_main=update), 0)
            self.assertEqual(update.call_args.args[0][0], "plan")


if __name__ == "__main__":
    unittest.main()
