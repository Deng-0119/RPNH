"""Same-release payload equality must include additional importable code."""
from importlib import machinery, util
from pathlib import Path
import py_compile
import tempfile
import unittest

from cpn.rpnh.install_executor import same_release_payload


class InstalledPayloadIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.products = [self.root / name for name in ("old", "new")]
        self.sites = []
        for product in self.products:
            site = product / "venv/lib/python3.12/site-packages"
            package = site / "cpn"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("")
            (package / "product.py").write_text("value = 1\n")
            metadata = site / "rpnh_harness-1.0.dist-info"
            metadata.mkdir()
            (metadata / "METADATA").write_text("Name: rpnh-harness\nVersion: 1.0\n")
            self.sites.append(site)

    def test_matching_source_cache_is_ignored_but_source_bytes_still_compare(self):
        self.assertTrue(same_release_payload(*self.products))
        source = self.sites[1] / "cpn/product.py"
        py_compile.compile(str(source), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        self.assertTrue(same_release_payload(*self.products))
        source.write_text("value = 2\n")
        self.assertFalse(same_release_payload(*self.products))

    def test_added_importable_sourceless_module_changes_payload(self):
        source = self.root / "extra.py"
        source.write_text("additional_behavior = 42\n")
        target = self.sites[1] / "cpn/extra.pyc"
        py_compile.compile(str(source), cfile=str(target), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        source.unlink()
        loader = util.spec_from_file_location("unexecuted_extra", target).loader
        self.assertIsInstance(loader, machinery.SourcelessFileLoader)
        self.assertFalse(same_release_payload(*self.products))

    def test_orphan_noncanonical_cache_and_other_members_are_compared(self):
        for relative in ("cpn/__pycache__/absent.cpython-312.pyc",
                         "cpn/__pycache__/not-a-cache.pyc",
                         "cpn/extra.pyo", "cpn/__pycache__/extra.txt"):
            with self.subTest(relative=relative):
                path = self.sites[1] / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture extra payload")
                self.assertFalse(same_release_payload(*self.products))
                path.unlink()

    def test_cache_shaped_symlink_is_rejected_before_any_exemption(self):
        source = self.sites[1] / "cpn/product.py"
        cache = Path(util.cache_from_source(str(source)))
        cache.parent.mkdir()
        cache.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "aliased member"):
            same_release_payload(*self.products)

    def test_optional_package_root_symlink_is_not_treated_as_missing(self):
        external = self.root / "external-integrations"
        external.mkdir()
        (external / "__init__.py").write_text("additional_behavior = 42\n")
        (self.sites[1] / "integrations").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "aliased package"):
            same_release_payload(*self.products)


if __name__ == "__main__":
    unittest.main()
