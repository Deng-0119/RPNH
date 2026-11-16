"""Explicit published Python extras, with synthetic official transport only.

The executable fixture is a reduced RPNH package and tiny optional wheels, not
the real MCP/Skill SDK closure or native/provider acceptance. The original
material checks, venv/pip, prepare/probe and activation remain real.
"""
from io import StringIO
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from cpn.rpnh import install_v2, published_install as bootstrap, update_cli, updates
from tests.test_install_materials import fixture_wheel
from tests.test_published_bootstrap import BootstrapTestCase
from tests import test_install_executor as executor_fixtures
from tests.test_published_bootstrap import SOURCE


class ExplicitExtrasTests(BootstrapTestCase):
    def test_exact_single_and_combined_choices_are_normalized_and_bound(self):
        for extras in (['mcp'], ['skills'], ['skills', 'mcp']):
            fixture = self.new_fixture(extras=extras)
            with self.subTest(extras=extras), self.transports(fixture):
                document = fixture.inspect(root_extras=extras).to_document()
            self.assertEqual(document['materials']['root_extras'], sorted(extras))
            self.assertEqual(document['support']['combinations'][0]['materials'], document['materials'])
            self.assertEqual(document['validation']['results'][0]['materials'], document['materials'])
            self.assertEqual(len(document['materials']['python_wheels']), 2 + len(extras))
            self.assertFalse(document['qualification_gaps']['qualification_granted'])
            self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_an_explicit_combination_id_does_not_silently_enable_extras(self):
        fixture = self.new_fixture(extras=('mcp',))
        for arguments in ({}, {'combination': fixture.support['combinations'][0]['id']}):
            with self.subTest(arguments=arguments), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect(**arguments)
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_invalid_duplicate_or_unsupported_selection_fails_before_metadata(self):
        for extras in (None, 'mcp', ['mcp', 'mcp'], ['MCP'], ['test'], ['other'], [1]):
            fixture = self.new_fixture()
            with self.subTest(extras=extras), self.transports(fixture), self.assertRaises(ValueError):
                fixture.inspect(root_extras=extras)
            self.assertEqual(fixture.json_calls, [])
            self.assertEqual(fixture.wheel_calls, [])

    def test_requested_subset_or_superset_cannot_replace_the_backed_selection(self):
        for available, requested in ((('mcp', 'skills'), ['mcp']), (('mcp',), ['mcp', 'skills'])):
            fixture = self.new_fixture(extras=available)
            with self.subTest(available=available, requested=requested), self.transports(fixture), \
                    self.assertRaises(bootstrap.PublishedBootstrapError):
                fixture.inspect(root_extras=requested, combination=fixture.support['combinations'][0]['id'])
            self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_nonmatching_validation_extras_cannot_back_the_requested_choice(self):
        fixture = self.new_fixture(extras=('mcp',))
        fixture.validation['results'][0]['materials']['root_extras'] = []
        with self.transports(fixture), self.assertRaises(bootstrap.PublishedBootstrapError):
            fixture.inspect(root_extras=['mcp'])
        self.assertEqual(fixture.wheel_calls, [])

    def test_optional_wheel_must_be_an_exact_official_asset(self):
        fixture = self.new_fixture(extras=('mcp',))
        optional = next(row for row in fixture.release['assets'] if row['name'].startswith('fixture_optional_'))
        optional['size'] += 1
        with self.transports(fixture), self.assertRaises(bootstrap.PublishedBootstrapError):
            fixture.inspect(root_extras=['mcp'])
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_changed_extra_selection_after_review_is_rejected_before_acquisition(self):
        original = self.new_fixture(extras=('mcp',))
        replacement = self.new_fixture(extras=('skills',))
        with self.transports(original):
            request = original.inspect(root_extras=['mcp'])
        with self.transports(replacement), self.assertRaises(bootstrap.PublishedBootstrapError):
            bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(original.wheel_calls + replacement.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_declared_extras_do_not_bypass_the_acquired_wheel_metadata(self):
        fixture = self.new_fixture(extras=('mcp',))
        members = {name: (SOURCE / name).read_bytes() for name in executor_fixtures.CATALOG_FILES}
        # Official-looking metadata claims the extra, but actual RPNH does not.
        wheel = fixture_wheel(fixture.inputs, 'rpnh-harness', fixture.version,
            ['fixture-dependency>=1,<3'], provides_extras=(), extra_members=members)
        payload = Path(wheel['path']).read_bytes()
        fixture.payloads[701] = payload
        fixture.release['assets'][0]['size'] = len(payload)
        for row in (fixture.support['combinations'][0], fixture.validation['results'][0]):
            selected = next(w for w in row['materials']['python_wheels'] if w['name'] == 'rpnh-harness')
            selected['asset']['size'] = len(payload)
        with self.transports(fixture):
            request = fixture.inspect(root_extras=['mcp'])
            with self.assertRaisesRegex(ValueError, 'extra not declared'):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 3)
        self.assertFalse(self.root.exists())

    def test_missing_actual_optional_transitive_dependency_is_rejected_before_root(self):
        fixture = self.new_fixture(extras=('mcp',))
        wheel = fixture_wheel(fixture.inputs, 'fixture-optional-mcp', '1.0.0',
                              ['absent-transitive-dependency==1.0.0'])
        payload = Path(wheel['path']).read_bytes()
        fixture.payloads[703] = payload
        next(a for a in fixture.release['assets'] if a['id'] == 703)['size'] = len(payload)
        for row in (fixture.support['combinations'][0], fixture.validation['results'][0]):
            next(w for w in row['materials']['python_wheels'] if w['asset']['id'] == 703)['asset']['size'] = len(payload)
        with self.transports(fixture):
            request = fixture.inspect(root_extras=['mcp'])
            with self.assertRaisesRegex(ValueError, 'closure'):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(len(fixture.wheel_calls), 3)
        self.assertFalse(self.root.exists())

    def test_changed_extra_selection_under_ex_refuses_candidate_creation(self):
        fixture = self.new_fixture(extras=('mcp',))
        def changed_metadata(url):
            if url == updates.RELEASES_URL and fixture.json_calls.count(url) == 3:
                for row in (fixture.support['combinations'][0], fixture.validation['results'][0]):
                    row['materials']['root_extras'] = []
            return fixture.get_json(url)
        with self.transports(fixture, get_json=changed_metadata):
            request = fixture.inspect(root_extras=['mcp'])
            with self.assertRaises(bootstrap.PublishedBootstrapError):
                bootstrap.bootstrap_published(self.root, request)
        self.assertEqual(fixture.json_calls.count(updates.RELEASES_URL), 4)
        self.assertTrue(self.root.is_dir())
        self.assertEqual(list((self.root / 'versions').iterdir()), [])
        self.assertFalse((self.root / 'current').exists())

    def test_cli_review_and_confirmation_show_the_requested_extras(self):
        fixture = self.new_fixture(extras=('skills', 'mcp'))
        platform, runtimes = self.base / 'platform.json', self.base / 'runtimes.json'
        platform.write_text(json.dumps(fixture.platform))
        runtimes.write_text(json.dumps(fixture.runtimes))
        arguments = ['bootstrap', '--root', str(self.root), '--release', fixture.tag,
            '--platform', str(platform), '--runtime-bindings', str(runtimes),
            '--extra', 'skills', '--extra', 'mcp']
        output = StringIO()
        with self.transports(fixture), mock.patch('sys.stdout', output), \
                mock.patch.object(update_cli, '_confirm', side_effect=AssertionError('inspection must not confirm')):
            self.assertEqual(update_cli.main(arguments + ['--inspect-only']), 0)
        self.assertEqual(json.loads(output.getvalue())['materials']['root_extras'], ['mcp', 'skills'])
        # Stop exactly at review, without substituting the production installer.
        class ConfirmationObserved(Exception):
            pass
        with self.transports(fixture), mock.patch.object(update_cli, '_confirm',
                side_effect=ConfirmationObserved) as confirm, self.assertRaises(ConfirmationObserved):
            update_cli.main(arguments)
        message = confirm.call_args.args[0]
        self.assertIn('Python optional dependencies: mcp, skills', message)
        self.assertIn('fixture_optional_mcp-1.0.0-py3-none-any.whl', message)
        self.assertIn('fixture_optional_skills-1.0.0-py3-none-any.whl', message)
        self.assertEqual(fixture.wheel_calls, [])
        self.assertFalse(self.root.exists())

    def test_real_reduced_install_and_retained_probe_preserve_explicit_extras(self):
        fixture = self.new_fixture(runnable=True, extras=('mcp', 'skills'))
        with self.transports(fixture):
            request = fixture.inspect(root_extras=['skills', 'mcp'])
            result = bootstrap.bootstrap_published(self.root, request)
        target, directory, record = install_v2.read_target(self.root)
        material = json.loads((directory / 'materials.json').read_text())
        probe = json.loads((directory / 'probe.json').read_text())
        self.assertEqual(material['root_extras'], ['mcp', 'skills'])
        self.assertEqual(material['published_selection'], request.to_document()['materials'])
        self.assertEqual(probe['python']['root_extras'], ['mcp', 'skills'])
        self.assertEqual(record['source'], fixture.source)
        self.assertTrue(result.activation.committed)
        self.assertEqual(result.to_document()['native_qualification'], 'not_awarded')
        self.assertFalse(result.to_document()['tasks_resumed'])
        self.assertEqual(len(fixture.wheel_calls), 4)
        self.assertTrue(all(not Path(row['path']).exists() for row in material['python_wheels']))
        script = '''
import json, os, sys
from pathlib import Path
from cpn.rpnh.install_candidate import inherited_exclusive_lease
from cpn.rpnh.install_contract import read_installed, read_plan
from cpn.rpnh.install_materials import load_materials, probe_installation
from cpn.rpnh.install_v2 import check_root
directory = Path(sys.argv[1])
root = check_root(directory.parent.parent)
lease = inherited_exclusive_lease(root, int(sys.argv[2]))
try:
    materials = load_materials(directory / 'materials.json', read_plan(directory), installed=True)
    print(json.dumps(probe_installation(directory, read_installed(directory), materials, retained=True)))
finally:
    os.close(lease)
'''
        with install_v2.management_lease(self.root) as lease:
            process = subprocess.run([str(directory / 'venv/bin/python'), '-I', '-B', '-c', script,
                str(directory), str(lease.fileno())], cwd=self.base, pass_fds=(lease.fileno(),),
                env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['python']['root_extras'], ['mcp', 'skills'])
        self.assertEqual(install_v2.read_target(self.root)[0], target)
        self.assertEqual(len(fixture.wheel_calls), 4)


if __name__ == '__main__':
    unittest.main()
