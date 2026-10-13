"""No-model, no-socket material/contract checks. These are not benchmark scores."""
import ast
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import build_project
from verifier_adapter import SOURCE_SHA256, verify
from cpn.rpnh.agent_tasks import agent_task_registration
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package


class ProjectMaterials(unittest.TestCase):
    def test_upstream_bytes_match_frozen_manifest(self):
        source = json.loads((build_project.ROOT / 'materials/SOURCES.json').read_text())
        self.assertEqual(len(source['files']), 10)
        for row in source['files']:
            body = (build_project.UPSTREAM / row['path']).read_bytes()
            self.assertEqual(len(body), row['bytes'])
            self.assertEqual(hashlib.sha256(body).hexdigest(), row['sha256'])
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest(), row['git_blob_sha1'])

    def test_agent_task_definition_compiles_and_has_real_handoff(self):
        module = build_project.module()
        document = module.to_dict()
        compile_module(module, agent_task_registration())
        self.assertEqual([c['name'] for c in document['components']], ['implementer', 'reviewer'])
        self.assertFalse(document.get('designer_constraints'))
        self.assertEqual(len(document['links']), 1)
        for component in document['components']:
            operation = component['operations'][0]
            self.assertEqual(operation['executor'], 'rpnh/default-agent/v1')
            self.assertEqual(operation['config']['agent_loop_role'], 'actor')
            self.assertIn('workspace', operation['tools'])
        self.assertTrue(all(s.startswith('application/') for s in document['required_schemas']))

    def test_versions_change_same_agent_contract_without_tool_authority(self):
        root = build_project.ROOT / 'scenario/generated'
        initial = json.loads((root / 'module.json').read_text())
        for role in ('contributor-a', 'contributor-b'):
            variant = json.loads((root / (role + '.json')).read_text())
            compile_module(ModuleDeclaration.from_dict(variant), agent_task_registration())
            stripped = deepcopy(variant)
            self.assertNotEqual(stripped['components'][0]['operations'][0]['config']['node_synopsis'],
                                initial['components'][0]['operations'][0]['config']['node_synopsis'])
            stripped['components'][0]['operations'][0]['config']['node_synopsis'] = initial['components'][0]['operations'][0]['config']['node_synopsis']
            if role == 'contributor-a':
                self.assertNotEqual(stripped['components'][1]['operations'][0]['config']['node_synopsis'], initial['components'][1]['operations'][0]['config']['node_synopsis'])
                stripped['components'][1]['operations'][0]['config']['node_synopsis'] = initial['components'][1]['operations'][0]['config']['node_synopsis']
            else:
                self.assertEqual(stripped['components'][0]['config']['capacities'], {'result': 1})
                del stripped['components'][0]['config']['capacities']
                self.assertEqual(stripped['components'][1]['config']['capacities'], {'request': 1, 'result': 1})
                del stripped['components'][1]['config']['capacities']
            self.assertEqual(stripped, initial)

    def test_task_input_is_lossless_original_resources_not_solution(self):
        task = build_project.task_input()
        self.assertEqual(task['upstream_commit'], build_project.COMMIT)
        for row in task['source_files']:
            body = base64.b64decode(row['content'], validate=True)
            self.assertEqual(len(body), row['bytes'])
            self.assertEqual(hashlib.sha256(body).hexdigest(), row['sha256'])
            expected = (build_project.ROOT / 'scenario/verifier_adapter.py' if row['path'] == 'verifier_adapter.py'
                        else build_project.UPSTREAM / 'multi-source-data-merger' / row['path'])
            self.assertEqual(body, expected.read_bytes())
        self.assertNotIn('solution', {r['path'].split('/')[0] for r in task['source_files']})

    def test_package_preview_and_lock_are_inert_and_complete(self):
        raw = (build_project.ROOT / 'scenario/generated/seed-project.zip').read_bytes()
        preview = preview_package(raw)
        lock = resolve_package(preview)
        self.assertFalse(json.loads(lock.payload)['execution_permitted'])
        self.assertFalse(json.loads(preview.report_bytes)['execution_permitted'])
        paths = dict(preview.artifacts)
        self.assertIn('fixtures/inputs.json', paths)
        self.assertIn('licenses/terminal-bench.txt', paths)
        self.assertIn('environment/task.json', paths)
        self.assertIn('upstream/multi-source-data-merger/tests/test_outputs.py', paths)
        parquet = json.loads(paths['upstream/multi-source-data-merger/environment/data/source_c/users.parquet.base64.json'])
        self.assertEqual(hashlib.sha256(base64.b64decode(parquet['content'])).hexdigest(), parquet['sha256'])

    def test_verifier_original_inventory_and_relocation_count(self):
        source = build_project.UPSTREAM / 'multi-source-data-merger/tests/test_outputs.py'
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA256)
        tree = ast.parse(source.read_text())
        self.assertEqual(len([n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')]), 3)
        self.assertEqual(len([n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith('/app/')]), 4)

    def test_adapter_rejects_modified_grader_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'grader.py'
            source.write_text('raise AssertionError("must not execute")')
            with self.assertRaisesRegex(ValueError, 'original verifier bytes differ'):
                verify(source, Path(temporary))

    def test_adapter_negative_missing_outputs_never_passes(self):
        # This negative fixture tests scorer failure reporting, not agent work.
        with tempfile.TemporaryDirectory() as temporary:
            result = verify(build_project.UPSTREAM / 'multi-source-data-merger/tests/test_outputs.py', Path(temporary))
            self.assertEqual(result['denominator'], 3)
            self.assertEqual(result['passed'], 0)
            self.assertEqual(len(result['path_only_adaptation']), 4)
            self.assertFalse(result['original_test_sh_executed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
