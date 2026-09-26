"""Documentation-only regressions. No RPNH imports and no example execution."""
from pathlib import Path
import importlib.util
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rpnh_docs_builder', ROOT / 'scripts/docs.py')
docs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = docs
try:
    spec.loader.exec_module(docs)
except ModuleNotFoundError as exc:
    if exc.name not in {'markdown_it', 'yaml'}:
        raise
    docs = None


@unittest.skipIf(docs is None, 'Install docs/requirements.txt to run documentation checks')
class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'source'
        self.root.mkdir()
        for path in docs.pages_in(ROOT):
            dest = self.root / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        for path in docs.auxiliary_documents(ROOT):
            dest = self.root / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        for name in docs.STATIC_DOCUMENTS:
            shutil.copyfile(ROOT / name, self.root / name)

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, text):
        with (self.root / 'README.md').open('a', encoding='utf-8') as handle:
            handle.write('\n' + text + '\n')

    def test_all_topics(self):
        pages, stats = docs.check(self.root)
        self.assertEqual(len(pages), len(docs.pages_in(ROOT)))
        self.assertEqual(stats['language_pairs'] * 2, len(pages))
        self.assertEqual(stats['reachable_pages'], len(pages))

    def test_missing_link_rejected(self):
        self.append('[Missing](missing.md)')
        with self.assertRaisesRegex(ValueError, 'missing link'):
            docs.check(self.root)

    def test_missing_example_link_rejected(self):
        path = self.root / 'examples/native_plugin/README.md'
        with path.open('a', encoding='utf-8') as handle:
            handle.write('\n[Missing](does-not-exist.md)\n')
        with self.assertRaisesRegex(ValueError, 'missing link'):
            docs.check(self.root)

    def test_missing_example_anchor_rejected(self):
        path = self.root / 'examples/native_plugin/README.md'
        with path.open('a', encoding='utf-8') as handle:
            handle.write('\n[Missing](../README.md#does-not-exist)\n')
        with self.assertRaisesRegex(ValueError, 'missing anchor'):
            docs.check(self.root)

    def test_missing_anchor_rejected(self):
        self.append('[Missing](README.md#not-present)')
        with self.assertRaisesRegex(ValueError, 'missing anchor'):
            docs.check(self.root)

    def test_counterpart_drift_rejected(self):
        path = self.root / 'docs/index_ZH.md'
        path.write_text(
            path.read_text().replace(
                'revision: "2026-09-26.3"', 'revision: "older"', 1),
            encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'counterpart mismatch'):
            docs.check(self.root)

    def test_nonfinite_json_rejected(self):
        self.append('```json\n{"bad": NaN}\n```')
        with self.assertRaisesRegex(ValueError, 'nonfinite JSON'):
            docs.check(self.root)

    def test_invalid_python_rejected(self):
        self.append('```python\ndef broken(\n```')
        with self.assertRaises(SyntaxError):
            docs.check(self.root)

    def test_invalid_bash_rejected(self):
        self.append('```bash\nif then\n```')
        with self.assertRaisesRegex(ValueError, 'Bash syntax'):
            docs.check(self.root)

    def test_path_escape_rejected(self):
        self.append('[Outside](../outside.md)')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            docs.check(self.root)

    def test_build_language_links_and_no_runtime_import(self):
        output = Path(self.tmp.name) / 'site'
        before = {name for name in sys.modules if name == 'cpn' or name.startswith('cpn.')}
        result = docs.build(self.root, output)
        self.assertEqual(result['built_html_pages'], len(docs.pages_in(ROOT)))
        home = (output / 'index.html').read_text()
        self.assertIn('index_ZH.html', home)
        self.assertIn('docs/guides/installation.html', home)
        self.assertIn('class="nav-group"', home)
        self.assertIn('Source examples', home)
        self.assertTrue((output / 'examples/README.html').is_file())
        self.assertTrue(
            (output / 'examples/workflow_patterns/README_ZH.html').is_file())
        zh = (output / 'docs/guides/installation_ZH.html').read_text()
        self.assertIn('lang="zh-CN"', zh)
        self.assertIn('installation.html', zh)
        self.assertNotIn('<script', home)
        self.assertTrue((output / 'LICENSE').is_file())
        self.assertTrue((output / 'THIRD_PARTY_NOTICES.md').is_file())
        after = {name for name in sys.modules if name == 'cpn' or name.startswith('cpn.')}
        self.assertEqual(before, after)

    def test_bundled_host_guides_are_reciprocal_language_pairs(self):
        for path in docs.auxiliary_documents(self.root):
            counterpart = (
                path.with_name('README.md')
                if path.name == 'README_ZH.md'
                else path.with_name('README_ZH.md'))
            self.assertTrue(counterpart.is_file(), str(path))
            self.assertIn(
                f']({counterpart.name})', path.read_text(encoding='utf-8'))

    def test_viewer_tutorial_covers_views_controls_and_visual_legend(self):
        required = (
            'Overview', 'Detailed flow', 'PetriNet', 'Executions',
            'Search + Locate', 'Resources', 'Locate activity', 'Refresh',
            'Line bridges', 'Fit to view', 'Reset focus', 'Minimap',
            'Live position', 'Earlier records', 'Animate changes',
            'Enter', 'Space', 'consume', 'produce', 'read arc', 'reset arc',
            'terminal-success',
        )
        for relative in (
                'docs/guides/viewer.md',
                'docs/guides/viewer_ZH.md'):
            document = (self.root / relative).read_text(encoding='utf-8').lower()
            missing = [value for value in required if value.lower() not in document]
            self.assertEqual(missing, [], relative)

    def test_existing_output_not_overwritten(self):
        output = Path(self.tmp.name) / 'site'
        output.mkdir()
        with self.assertRaisesRegex(ValueError, 'new output directory'):
            docs.build(self.root, output)


if __name__ == '__main__':
    unittest.main()
