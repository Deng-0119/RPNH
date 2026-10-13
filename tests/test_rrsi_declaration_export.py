"""Socket-free contracts for the bundled real RRSI role declarations.

Run with unittest discovery or pytest. No Viewer server or role runner is used.
"""
from copy import deepcopy
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft7Validator
from cpn.frontend.dashboard import public_net
from cpn.frontend.server import handle_request
from examples._support.declaration_identity import export_declaration_identity
from examples.rrsi_v06.declarations import (
    FIGURES, ROOT, ROLES, build_role_frame, compile_role_declaration,
    generated_files, json_bytes,
)
from examples.rrsi_v06.src.rpnh_rrsi.formal_role import ROLE_LIMITS, ROLE_TOOLS
from examples.view_declaration import DeclarationFixture, _DECLARATIONS, main


class RRSIDeclarationTests(unittest.TestCase):
    def test_rebuild_bytes_provenance_and_historical_identity(self):
        outputs = generated_files()
        for path, data in outputs.items():
            with self.subTest(path=path):
                self.assertEqual((FIGURES / path).read_bytes(), data)
        provenance = json.loads(outputs['role-declaration-provenance.json'])
        self.assertFalse(provenance['execution_claim'])
        self.assertFalse(provenance['runtime_evidence'])
        self.assertIsNone(provenance['observation_head'])
        self.assertEqual(len({d['compiled_sha256'] for d in provenance['declarations']}), 4)
        self.assertEqual(len({d['frame_sha256'] for d in provenance['declarations']}), 4)

    def test_real_compiled_schema_role_tools_and_budgets(self):
        schema = json.loads((ROOT / 'cpn/schemas/rpnh/executable_net.v1.schema.json').read_text())
        Draft7Validator.check_schema(schema)
        validator = Draft7Validator(schema)
        documents = []
        for role in ROLES:
            with self.subTest(role=role):
                document = compile_role_declaration(role).to_dict()
                validator.validate(document)
                documents.append(json_bytes(document))
                source = document['source']
                operations = {op['name']: op for op in source['components'][0]['operations']}
                self.assertEqual(operations['model']['config']['role'], role)
                self.assertEqual(operations['model']['config']['limits'], ROLE_LIMITS[role])
                expected = [f"application/rrsi_v06/formal_role/tool/{tool['function']['name']}/v1"
                            for tool in ROLE_TOOLS[role]]
                self.assertEqual(operations['model']['tools'], expected)
                self.assertEqual(operations['init']['tools'], [])
                self.assertEqual(operations['act']['tools'], [])
                self.assertEqual(source['budget_buckets'][0]['max_attempts'],
                                 2 * ROLE_LIMITS[role]['max_generations'] + 2)
                self.assertEqual(operations['model']['config']['resource_bounds'],
                                 {'max_llm_attempts': 1, 'max_tool_turns': 0})
        self.assertEqual(len(set(documents)), 4)

    def test_identity_boundaries_and_no_runtime_facts(self):
        for role in ROLES:
            with self.subTest(role=role):
                frame = build_role_frame(role)
                self.assertEqual(frame, DeclarationFixture(f'rrsi-{role}').dashboard())
                self.assertEqual(frame['net']['summary'], {
                    'node_count': 7, 'transition_count': 3, 'place_count': 4,
                    'resource_place_count': 0, 'edge_count': 7, 'resource_edge_count': 0})
                self.assertEqual(frame['agent_nodes'], [{
                    'transition_id': 'role.model', 'source': 'declared_llm_executor',
                    'semantic_group': None, 'agent_ref': None, 'executable_binding_ref': None}])
                self.assertEqual(frame['source'], frame['net']['source'])
                self.assertEqual(frame['source']['mode'], 'initial_configured')
                self.assertIsNone(frame['source']['verified_head_ordinal'])
                self.assertEqual(frame['transition_bindings'], [])
                self.assertEqual(frame['coverage'], {
                    'history': 'unsupported', 'firings': 'not_provided',
                    'agent_nodes': 'declared_llm_executor_only', 'terminal_evidence': 'not_provided'})
                self.assertEqual(frame['position'], {'mode': 'live', 'cursor': None, 'latest_head': None})
                for node in frame['net']['nodes']:
                    for key in ('runtime', 'tokens', 'active_token_count'):
                        self.assertNotIn(key, node)
                    if node['kind'] == 'transition':
                        self.assertEqual(node['config'], {})
                        self.assertEqual(node['executor_declaration'], {'contracts': {
                            'transport': 'llm' if node['id'] == 'role.model' else 'deterministic'}})
                compiled = compile_role_declaration(role)
                before = deepcopy(frame)
                exported = export_declaration_identity(frame, compiled)
                self.assertEqual(frame, before)
                self.assertEqual(exported, frame)
                self.assertEqual(public_net(exported['net']), public_net(before['net']))

    def test_identity_rejects_mismatches_without_mutating_input(self):
        for role in ROLES:
            compiled = compile_role_declaration(role)
            original = build_role_frame(role)
            for case in ('source', 'net-source', 'runtime', 'bindings', 'arc', 'boundary'):
                with self.subTest(role=role, case=case):
                    bad = deepcopy(original)
                    if case == 'source': bad['source']['mode'] = 'registry_current'
                    elif case == 'net-source': bad['net']['source']['mode'] = 'registry_current'
                    elif case == 'runtime': bad['net']['nodes'][0]['runtime'] = {'firings': [{'id': 'fake'}]}
                    elif case == 'bindings': bad['transition_bindings'] = [{'transition_id': 'role.model'}]
                    elif case == 'arc': bad['net']['edges'][0]['weight'] = 2
                    else: bad['boundaries']['terminal_rules'][0]['outcome'] = 'other'
                    before = deepcopy(bad)
                    with self.assertRaises(ValueError): export_declaration_identity(bad, compiled)
                    self.assertEqual(bad, before)

    def test_fixture_and_cli_dispatch_preserve_all_choices(self):
        legacy = {'rsi-four-round', 'rrsi-policy', 'collaboration-baseline',
                  'collaboration-adopted', 'tool-pipeline', 'parallel', 'automationbench',
                  'erp-bench', 'slopcodebench', 'harnessaudit-office', 'iteration-profile', 'compose-serial'}
        self.assertEqual(set(_DECLARATIONS), legacy | {f'rrsi-{role}' for role in ROLES})
        for name, path in _DECLARATIONS.items():
            with self.subTest(name=name):
                fixture = DeclarationFixture(name)
                expected = json.loads((ROOT / 'examples' / path).read_text())
                self.assertEqual(fixture.dashboard(), expected)
                self.assertEqual(fixture(), expected['net'])
                with self.assertRaises(ValueError): fixture.dashboard(cursor=0)
                # Test argument selection only; never invoke the HTTP transport.
                with patch('cpn.frontend.server.serve_projection') as serve:
                    self.assertEqual(main([name, '--no-open']), 0)
                    args, kwargs = serve.call_args
                    self.assertEqual(args[0].dashboard(), expected)
                    self.assertEqual(kwargs, {'host': '127.0.0.1', 'port': 0, 'open_browser': False})
        with self.assertRaises(ValueError): DeclarationFixture('rrsi-unknown')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(['rrsi-unknown'])
        self.assertEqual(raised.exception.code, 2)
        with self.assertRaises(ValueError): build_role_frame('policy')

    def test_fixture_import_does_not_require_rrsi_or_rpnh_import(self):
        program = """
import sys
from examples.view_declaration import DeclarationFixture
for role in ('analyst', 'proposer', 'critic', 'digester'):
    assert DeclarationFixture('rrsi-' + role).dashboard()['source']['mode'] == 'initial_configured'
assert not any(name == 'cpn' or name.startswith('cpn.') or 'rpnh_rrsi' in name for name in sys.modules)
"""
        result = subprocess.run([sys.executable, '-B', '-c', program], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pure_request_contract_without_http_transport(self):
        for role in ROLES:
            with self.subTest(role=role):
                fixture = DeclarationFixture(f'rrsi-{role}')
                before = deepcopy(fixture.dashboard())
                for route, expected in (('/api/v1/net', fixture()),
                                        ('/api/v1/dashboard', fixture.dashboard())):
                    response = handle_request(fixture, 'GET', route)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.body), expected)
                    self.assertEqual(handle_request(fixture, 'HEAD', route).body, b'')
                    self.assertEqual(handle_request(fixture, 'POST', route).status, 405)
                self.assertEqual(handle_request(fixture, 'GET', '/api/v1/history').status, 501)
                self.assertEqual(handle_request(fixture, 'GET', '/api/v1/dashboard?cursor=0').status, 400)
                self.assertEqual(fixture.dashboard(), before)


if __name__ == '__main__':
    unittest.main()
