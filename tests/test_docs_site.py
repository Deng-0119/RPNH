"""Documentation-only regressions. No RPNH imports and no example execution."""
from pathlib import Path
import importlib.util
import re
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
        pages = {path.resolve(): docs.parse_page(path) for path in docs.pages_in(ROOT)}
        support, downloads = docs.supporting_documents(ROOT, pages)
        for path in set(support) | downloads:
            dest = self.root / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        for path in docs.auxiliary_documents(ROOT):
            dest = self.root / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        for path in docs.reviewed_assets(ROOT):
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
            re.sub(
                r'^  revision: "[^"]+"$', '  revision: "older"',
                path.read_text(), count=1, flags=re.MULTILINE),
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

    def test_unreviewed_image_location_rejected(self):
        source = next(path for path in docs.reviewed_assets(self.root)
                      if path.suffix == '.png')
        shutil.copyfile(source, self.root / 'unreviewed.png')
        self.append('![Unreviewed](unreviewed.png)')
        with self.assertRaisesRegex(ValueError, 'outside reviewed asset policy'):
            docs.check(self.root)

    def test_malformed_reviewed_png_rejected(self):
        path = next(path for path in docs.reviewed_assets(self.root)
                      if path.suffix == '.png')
        path.write_bytes(b'not a png')
        with self.assertRaisesRegex(ValueError, 'invalid PNG'):
            docs.check(self.root)

    def test_truncated_reviewed_png_rejected_after_valid_header(self):
        path = next(path for path in docs.reviewed_assets(self.root)
                      if path.suffix == '.png')
        path.write_bytes(path.read_bytes()[:24])
        with self.assertRaisesRegex(ValueError, 'invalid PNG'):
            docs.check(self.root)

    def test_reviewed_report_svgs_have_static_xml_and_exact_content(self):
        self.assertEqual(len(docs.REVIEWED_REPORT_SVGS), 16)
        for name in docs.REVIEWED_REPORT_SVGS:
            path = self.root / name
            self.assertIn(path, docs.reviewed_assets(self.root))
            self.assertEqual(
                docs.reviewed_image_target(self.root / 'README.md', name, self.root),
                path)

    def test_unknown_svg_rejected_even_in_report_assets(self):
        source = self.root / next(iter(docs.REVIEWED_REPORT_SVGS))
        target = source.with_name('unreviewed.svg')
        shutil.copyfile(source, target)
        with self.assertRaisesRegex(ValueError, 'outside reviewed asset policy'):
            docs.reviewed_image_target(
                self.root / 'README.md', str(target.relative_to(self.root)), self.root)

    def test_modified_reviewed_svg_rejected(self):
        name = next(iter(docs.REVIEWED_REPORT_SVGS))
        path = self.root / name
        path.write_bytes(path.read_bytes().replace(b'<title id="title">',
                                                   b'<title id="title">Changed '))
        with self.assertRaisesRegex(ValueError, 'unreviewed SVG content'):
            docs.reviewed_image_target(self.root / 'README.md', name, self.root)

    def test_svg_xml_safety_is_independent_of_content_hash(self):
        opening = '<svg xmlns="http://www.w3.org/2000/svg"'
        fixtures = (
            opening + '><script>alert(1)</script></svg>',
            opening + '><foreignObject/></svg>',
            opening + ' onload="alert(1)"></svg>',
            opening + '><path style="fill:red"/></svg>',
            opening + '><path href="https://example.invalid/a"/></svg>',
            opening + '><path fill="url(https://example.invalid/a)"/></svg>',
            opening + '><path marker-end="url(#missing)"/></svg>',
            opening + '><g id="same"/><g id="same"/></svg>',
            '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            + opening + '><text>&x;</text></svg>',
            '<?xml-stylesheet href="https://example.invalid/a"?>'
            + opening + '></svg>',
            '<svg><text>wrong namespace</text></svg>',
            opening + '><unknown/></svg>',
            opening + '><path data-extra="new"/></svg>',
        )
        for payload in fixtures:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    docs.validate_report_svg(payload.encode('utf-8'))

    def test_report_svg_path_and_source_boundaries_remain_strict(self):
        name = next(iter(docs.REVIEWED_REPORT_SVGS))
        for source in (name + '#title', 'https://example.invalid/report.svg'):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    docs.reviewed_image_target(self.root / 'README.md', source, self.root)
        target = self.root / name
        outside = Path(self.tmp.name) / 'outside.svg'
        shutil.copyfile(target, outside)
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes documentation root'):
            docs.reviewed_image_target(self.root / 'README.md', name, self.root)

    def test_report_svgs_are_copied_unchanged_into_bilingual_site(self):
        output = Path(self.tmp.name) / 'report-site'
        docs.build(self.root, output)
        for language, document in (('en', 'technical-report'), ('zh', 'technical-report_ZH')):
            html = (output / 'docs' / (document + '.html')).read_text()
            for name in docs.REVIEWED_REPORT_SVGS:
                self.assertEqual((output / name).read_bytes(), (self.root / name).read_bytes())
                if name.endswith('-' + language + '.svg'):
                    self.assertIn('src="' + name.removeprefix('docs/') + '"', html)

    def test_build_language_links_and_no_runtime_import(self):
        output = Path(self.tmp.name) / 'site'
        before = {name for name in sys.modules if name == 'cpn' or name.startswith('cpn.')}
        result = docs.build(self.root, output)
        self.assertEqual(
            result['built_html_pages'],
            len(docs.pages_in(ROOT)) + result['support_documents'])
        home = (output / 'index.html').read_text()
        self.assertIn('index_ZH.html', home)
        self.assertIn('docs/guides/installation.html', home)
        self.assertIn('class="nav-group"', home)
        self.assertIn('Source examples', home)
        self.assertTrue((output / 'examples/README.html').is_file())
        self.assertTrue(
            (output / 'examples/workflow_patterns/README_ZH.html').is_file())
        self.assertTrue((
            output / 'examples/workflow_patterns/assets/parallel-overview.png'
        ).is_file())
        self.assertIn(
            'examples/workflow_patterns/assets/parallel-overview.png', home)
        zh = (output / 'docs/guides/installation_ZH.html').read_text()
        self.assertIn('lang="zh-CN"', zh)
        self.assertIn('installation.html', zh)
        self.assertNotIn('<script', home)
        self.assertTrue((output / 'LICENSE').is_file())
        self.assertTrue((output / 'THIRD_PARTY_NOTICES.md').is_file())
        self.assertTrue((output / 'CHANGELOG.html').is_file())
        self.assertTrue((output / 'CHANGELOG_ZH.html').is_file())
        self.assertTrue((output / 'CONTRIBUTING.html').is_file())
        self.assertTrue((output / 'SECURITY_ZH.html').is_file())
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

    def test_every_runnable_example_page_has_a_reviewed_dashboard_image(self):
        pages = (
            'README.md', 'README_ZH.md',
            'examples/native_plugin/README.md',
            'examples/native_plugin/README_ZH.md',
            'examples/hybrid_summary/README.md',
            'examples/hybrid_summary/README_ZH.md',
            'examples/workflow_patterns/README.md',
            'examples/workflow_patterns/README_ZH.md',
            'examples/task_workspace/README.md',
            'examples/task_workspace/README_ZH.md',
            'examples/jb_steering_packet/README.md',
            'examples/jb_steering_packet/README_ZH.md',
            'examples/three_dof_powered_descent/README.md',
            'examples/three_dof_powered_descent/README_ZH.md',
            'examples/net_operations/README.md',
            'examples/net_operations/README_ZH.md',
            *(f'cpn/examples/adapter_task/{host}/README{suffix}.md'
              for host in ('basic', 'codex', 'dsh', 'opencode')
              for suffix in ('', '_ZH')),
        )
        for relative in pages:
            tokens = docs.PARSER.parse(
                (self.root / relative).read_text(encoding='utf-8'))
            images = [
                token for token in docs.children(tokens)
                if token.type == 'image'
            ]
            self.assertTrue(images, relative)
            for token in images:
                docs.reviewed_image_target(
                    self.root / relative, token.attrGet('src'), self.root)

    def test_existing_output_not_overwritten(self):
        output = Path(self.tmp.name) / 'site'
        output.mkdir()
        with self.assertRaisesRegex(ValueError, 'new output directory'):
            docs.build(self.root, output)


@unittest.skipIf(docs is None, 'Install docs/requirements.txt to run documentation checks')
class SupportingDocumentTests(unittest.TestCase):
    """Small source tree isolates support policy from unrelated product docs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        self.root.mkdir()
        self.output = Path(self.tmp.name) / 'site'
        for name in docs.ROOT_PROJECT_DOCUMENTS:
            zh = name.endswith('_ZH.md')
            topic = name.removesuffix('_ZH.md').removesuffix('.md')
            counterpart = topic + ('.md' if zh else '_ZH.md')
            self.write(name, (
                f'---\nname: {topic}\ndescription: Project policy\nmetadata:\n'
                f'  document-kind: policy\n  language: {"zh-CN" if zh else "en"}\n'
                f'  counterpart: {counterpart}\n  revision: "1"\n  status: current\n'
                f'---\n# {topic}\n'))
        self.write('README.md', '# Home\n[中文](README_ZH.md)\n' + '\n'.join(
            f'[{name}]({name})' for name in docs.ROOT_PROJECT_DOCUMENTS)
            + '\n[Example](examples/demo/README.md)\n')
        self.write('README_ZH.md', '# 首页\n[English](README.md)\n')
        self.write('examples/demo/README.md',
                   '# Demo\n[中文](README_ZH.md)\n[Results](RESULTS.md#scores)\n')
        self.write('examples/demo/README_ZH.md', '# 示例\n[English](README.md)\n')
        self.write('examples/demo/RESULTS.md',
                   '# Results\n## Scores\n[Data](results/scores.json)\n'
                   '[More](details/METHOD.md#method)\n[Back](README.md)\n'
                   '[Legal](THIRD_PARTY_NOTICES.md)\n'
                   '[Scores](results/scores.csv)\n[Comparison](comparison/groups.csv)\n'
                   '[Source](src/rpnh_demo/workflow.py)\n')
        self.write('examples/demo/details/METHOD.md',
                   '# Method\n[Results](../RESULTS.md#scores)\n'
                   '[Home](../../../README.md)\n'
                   '[Matrix](../../../docs/reference/matrix.json)\n'
                   '[Schema](../../../cpn/schemas/rpnh/example.schema.json)\n')
        self.write('examples/demo/results/scores.json', '{"passed": 1}\n')
        self.write('examples/demo/results/scores.csv', 'task,score\nexample,1\n')
        self.write('examples/demo/comparison/groups.csv', 'group,count\npublic,1\n')
        self.write('examples/demo/src/rpnh_demo/workflow.py',
                   'raise RuntimeError("source must never execute")\n')
        self.write('docs/reference/matrix.json', '{"status": "NOT_RUN"}\n')
        self.write('cpn/schemas/rpnh/example.schema.json', '{"type": "object"}\n')
        self.write('examples/demo/THIRD_PARTY_NOTICES.md', '# Demo license\n')

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def link(self, href):
        path = self.root / 'examples/demo/RESULTS.md'
        path.write_text(path.read_text() + f'\n[Target]({href})\n', encoding='utf-8')

    def test_linked_support_is_validated_and_built_without_becoming_topics(self):
        self.write('examples/demo/UNLINKED.md', '# Unlinked\n[Bad](missing.md)\n')
        self.write('examples/demo/results/unlinked.json', 'invalid JSON')
        self.write('examples/demo/runner.py', 'raise RuntimeError("do not run")\n')
        self.write('examples/demo/results/unlinked.csv', '"unterminated')
        self.write('examples/demo/comparison/unlinked.csv', '"unterminated')
        self.write('examples/demo/src/rpnh_demo/unlinked.py', 'def broken(')
        pages, stats = docs.check(self.root)
        self.assertEqual(len(pages), len(docs.pages_in(self.root)))
        self.assertEqual(stats['reachable_pages'], len(pages))
        self.assertEqual(stats['language_pairs'] * 2, len(pages))
        self.assertEqual(stats['support_documents'], 2)
        self.assertEqual(stats['json_documents_syntax_only'], 3)
        self.assertEqual(stats['csv_documents_syntax_only'], 2)
        self.assertEqual(stats['python_documents_syntax_only'], 1)
        result = docs.build(self.root, self.output)
        self.assertEqual(result['built_html_pages'], len(pages) + 2)
        example = (self.output / 'examples/demo/README.html').read_text()
        self.assertIn('href="RESULTS.html#scores"', example)
        results = (self.output / 'examples/demo/RESULTS.html').read_text()
        self.assertIn('id="scores"', results)
        self.assertIn('href="results/scores.json"', results)
        self.assertIn('href="results/scores.csv"', results)
        self.assertIn('href="comparison/groups.csv"', results)
        self.assertIn('href="src/rpnh_demo/workflow.py"', results)
        self.assertIn('href="details/METHOD.html#method"', results)
        self.assertIn('href="README.html"', results)
        self.assertIn('href="THIRD_PARTY_NOTICES.md"', results)
        method = (self.output / 'examples/demo/details/METHOD.html').read_text()
        self.assertIn('href="../../../index.html"', method)
        self.assertIn('href="../RESULTS.html#scores"', method)
        for relative in ('examples/demo/results/scores.json',
                         'docs/reference/matrix.json',
                         'cpn/schemas/rpnh/example.schema.json',
                         'examples/demo/THIRD_PARTY_NOTICES.md',
                         'examples/demo/results/scores.csv',
                         'examples/demo/comparison/groups.csv',
                         'examples/demo/src/rpnh_demo/workflow.py'):
            self.assertEqual((self.output / relative).read_bytes(),
                             (self.root / relative).read_bytes())
        for relative in ('examples/demo/UNLINKED.html',
                         'examples/demo/results/unlinked.json', 'examples/demo/runner.py',
                         'examples/demo/results/unlinked.csv',
                         'examples/demo/comparison/unlinked.csv',
                         'examples/demo/src/rpnh_demo/unlinked.py'):
            self.assertFalse((self.output / relative).exists())

    def test_missing_support_documents_and_downloads_rejected(self):
        for relative in ('examples/demo/RESULTS.md',
                         'examples/demo/details/METHOD.md',
                         'examples/demo/results/scores.json',
                         'examples/demo/THIRD_PARTY_NOTICES.md',
                         'examples/demo/results/scores.csv',
                         'examples/demo/comparison/groups.csv',
                         'examples/demo/src/rpnh_demo/workflow.py'):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_text()
                path.unlink()
                try:
                    with self.assertRaisesRegex(ValueError, 'missing link'):
                        docs.check(self.root)
                finally:
                    self.write(relative, original)

    def test_invalid_support_json_rejected_before_build(self):
        for payload in ('{"broken":', '{"score": NaN}', '{"score": Infinity}'):
            with self.subTest(payload=payload):
                self.write('examples/demo/results/scores.json', payload)
                with self.assertRaisesRegex(ValueError, 'scores.json: invalid JSON document'):
                    docs.build(self.root, self.output)
                self.assertFalse(self.output.exists())

    def test_invalid_linked_csv_and_python_rejected_before_build(self):
        for relative, payload, error in (
                ('examples/demo/results/scores.csv', '"unterminated', ValueError),
                ('examples/demo/comparison/groups.csv', '"unterminated', ValueError),
                ('examples/demo/src/rpnh_demo/workflow.py', 'def broken(', SyntaxError)):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_text()
                self.write(relative, payload)
                try:
                    with self.assertRaises(error) as caught:
                        docs.build(self.root, self.output)
                    self.assertIn(path.name, str(caught.exception))
                    self.assertFalse(self.output.exists())
                finally:
                    self.write(relative, original)

    def test_support_links_keep_anchor_escape_and_type_checks(self):
        path = self.root / 'examples/demo/RESULTS.md'
        original = path.read_text()
        for href, error in (
                ('details/METHOD.md#missing', 'missing anchor'),
                ('results/scores.json#missing', 'missing anchor'),
                ('../../../outside.md', 'escapes'),
                ('missing.md', 'missing link'),
                ('directory.md', 'missing link'),
                ('runner.py', 'undocumented'),
                ('private.csv', 'undocumented'),
                ('results/scores.csv#missing', 'missing anchor'),
                ('src/rpnh_demo/workflow.py#missing', 'missing anchor'),
                ('private.json', 'undocumented'),
                ('../../..', 'escapes'),
                ('../..', 'undocumented')):
            with self.subTest(href=href):
                (self.root / 'examples/demo/directory.md').mkdir(exist_ok=True)
                self.write('examples/demo/runner.py', '# No code bundling\n')
                self.write('examples/demo/private.csv', 'score\n1\n')
                self.write('examples/demo/private.json', '{}')
                self.write('examples/demo/RESULTS.md', original)
                self.link(href)
                with self.assertRaisesRegex(ValueError, error):
                    docs.check(self.root)

    def test_support_code_fences_are_syntax_checked(self):
        self.write('examples/demo/details/METHOD.md', '# Method\n```json\n{"bad": NaN}\n```\n')
        with self.assertRaisesRegex(ValueError, 'nonfinite JSON'):
            docs.check(self.root)

    def test_support_does_not_relax_maintained_metadata_or_counterparts(self):
        path = self.root / 'CONTRIBUTING.md'
        original = path.read_text()
        for text, error in (
                ('# Contributing\n', 'front matter required'),
                (original.replace('counterpart: CONTRIBUTING_ZH.md',
                                  'counterpart: examples/demo/RESULTS.md'), 'counterpart missing')):
            with self.subTest(error=error):
                path.write_text(text, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, error):
                    docs.check(self.root)

    def test_support_links_do_not_satisfy_maintained_reachability(self):
        path = self.root / 'README.md'
        path.write_text(path.read_text().replace('[SECURITY.md](SECURITY.md)', ''),
                        encoding='utf-8')
        self.link('../../SECURITY.md')
        with self.assertRaisesRegex(ValueError, 'Pages not reachable from README'):
            docs.check(self.root)


if __name__ == '__main__':
    unittest.main()
