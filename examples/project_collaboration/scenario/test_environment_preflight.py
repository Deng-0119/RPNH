"""Metadata-fixture checks only; never native or provider execution."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from build_project import ROOT
from preflight_environment import preflight


def inventory():
    rows = {}
    for name, version, dependencies in [
        ('rpnh-harness', '0.1.0rc2', ['websockets>=12,<16']),
        ('pandas', '3.0.3', []), ('pyarrow', '24.0.0', []), ('websockets', '15.0.1', []),
    ]:
        rows[name] = {'name': name, 'version': version, 'requires_dist': dependencies,
                      'requires_python': '>=3.11', 'artifact_digest': None}
    return {'python': {'implementation': 'cpython', 'version': '3.12.1', 'abi': 'cpython-312', 'platform': 'linux-x86_64'},
            'distributions': rows, 'platform': {'os_family': 'linux', 'architecture': 'x86_64'},
            'markers': {'python_version': '3.12'}, 'supported_tags': []}


class EnvironmentMetadata(unittest.TestCase):
    def run_fixture(self, value):
        with patch('cpn.rpnh.collaboration.environment_check.probe_python', return_value=value):
            return preflight((ROOT / 'scenario/generated/seed-project.zip').read_bytes())

    def test_installed_metadata_pass_does_not_claim_native_or_profile_ready(self):
        result = self.run_fixture(inventory())
        self.assertTrue(result['metadata_scope_passed'])
        self.assertFalse(result['execution_permitted_by_this_check'])
        self.assertEqual(len(result['deferred_to_original_native_startup']), 3)
        self.assertNotEqual(result['original_environment_check']['aggregate'], 'passed_for_checked_scope')

    def test_non_linux_workspace_is_blocked_without_execution(self):
        from preflight_environment import workspace_platform_preflight
        with patch('preflight_environment.platform.system', return_value='Darwin'):
            result = workspace_platform_preflight()
        self.assertFalse(result['necessary_conditions_passed'])
        self.assertFalse(result['kernel_isolation_verified'])

    def test_same_id_with_other_contract_is_not_deferred(self):
        from cpn.rpnh.collaboration.environment_requirements import ScopedEnvironmentRequirements
        original = ScopedEnvironmentRequirements.document.fget
        def changed(instance):
            value = deepcopy(original(instance))
            value['services'][0]['service_contract_id'] = 'example/other-service/v1'
            value['system_requirements'][0]['capability_contract_id'] = 'example/other-native/v1'
            return value
        with patch.object(ScopedEnvironmentRequirements, 'document', property(changed)):
            result = self.run_fixture(inventory())
        self.assertFalse(result['metadata_scope_passed'])
        ids = {r['check_id'] for r in result['metadata_blockers']}
        self.assertTrue({'services:approvedModel', 'system_requirements:nativeOwner'} <= ids)

    def test_missing_parquet_dependency_blocks(self):
        value = inventory(); del value['distributions']['pyarrow']
        result = self.run_fixture(value)
        self.assertFalse(result['metadata_scope_passed'])
        self.assertIn('distributions:parquet', [r['check_id'] for r in result['metadata_blockers']])

    def test_upstream_library_versions_block_local_compatibility_variant(self):
        for name, version, check_id in [('pandas', '2.2.3', 'distributions:pandas'),
                                        ('pyarrow', '17.0.0', 'distributions:parquet')]:
            with self.subTest(distribution=name):
                value = inventory()
                value['distributions'][name]['version'] = version
                result = self.run_fixture(value)
                self.assertFalse(result['metadata_scope_passed'])
                self.assertIn(check_id, [r['check_id'] for r in result['metadata_blockers']])

    def test_later_harness_metadata_is_not_blocked_by_old_exact_pin(self):
        value = inventory()
        value['distributions']['rpnh-harness']['version'] = '0.1.0'
        result = self.run_fixture(value)
        self.assertTrue(result['metadata_scope_passed'])
        self.assertFalse(result['execution_permitted_by_this_check'])

    def test_installed_transitive_version_conflict_blocks(self):
        value = inventory(); value['distributions']['websockets']['version'] = '16.0'
        result = self.run_fixture(value)
        self.assertFalse(result['metadata_scope_passed'])
        self.assertIn('installed_dependency_closure', [r['check_id'] for r in result['metadata_blockers']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
