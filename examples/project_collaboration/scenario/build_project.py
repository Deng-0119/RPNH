"""Build an inert, portable stock-agent project; never call a model or install."""
from __future__ import annotations
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

from cpn.rpnh.agent_tasks import AgentStage, build_agent_task_module, agent_task_registration, TEXT_SCHEMA
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package
from cpn.rpnh.collaboration.share_packages import canonical_bytes

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / 'materials/upstream'
COMMIT = '2fd12b88aafdd04a52c298e3940bcb189f9766d6'
TOOLS = ['complete_interaction', 'query_environment_resources', 'query_registry_resources',
         'read_action_output', 'read_file', 'request_resource', 'search_text', 'workspace', 'write_file']

COMMON = '''This is the fixed public Terminal-Bench 2 multi-source-data-merger task, not a newly invented benchmark. Your responsibility is an agent task in a plain closed business PN. Domain transformations belong in code you write and execute with the workspace tool. Do not replace yourself with a precomputed domain pipeline or claim scripted work is model analysis. The subordinate execution PN remains the harness's existing materialization/finalization mechanism.
Use only the declared tools. Read your exact registered input using read_file. The located registered input file is also materialized in your firing-private workspace: load its bytes in a workspace action rather than copying base64 through the response. TEXT input bytes are a JSON string containing a JSON document, so decode the outer string and then that document. Preserve failed actions and record actual tool results. Do not install packages, access networks, or choose new provider credentials. Report missing environment capabilities accurately.
Workspace files created in this firing become registered resources and are projected to the next agent after settlement. Finish by registering a JSON-text handoff/report on semantic result with write_file (outcome complete), then call complete_interaction. Native task terminal alone is not the original verifier score.'''

IMPLEMENTER = '''You are the implementation agent. Read the delivered rpnh_multi_source_task/v1 resource and its exact upstream instruction. Decode source_files into safe relative paths under task/ and verify each declared byte length and SHA-256 before use. Never execute upstream tests/test.sh: it installs software. The supplied JSON/CSV/Parquet are original bytes. Translate /data/... only to task/environment/data/... and /app/... only to outputs/... within this bounded workspace, recording this path adaptation; do not weaken task semantics.
Understand all original requirements, write your own reusable merger program, execute it on all three original sources, and produce outputs/merged_users.parquet and outputs/conflicts.json. Do not substitute benchmark expected rows or hardcode answers. Retain your source code, diagnostic results and exact data/output hashes. Record conflicts and source priority according to the original instruction. Produce a handoff with instruction digest, source paths/digests, implementation path, both output paths/digests, commands, exit statuses and any unresolved issues. The next independent checking agent must be able to find the exact original verifier and outputs in the settled workspace.''' + '\n' + COMMON

REVIEWER = '''You are the independent checking agent. Read the registered implementation handoff and inspect the settled source code, original instruction, source bytes, and output files. Independently check implementation correctness against the full original instruction. Execute all original test_outputs.py test functions against the actual output bytes, preserving the original verifier source and using only the disclosed /app path relocation to outputs. Do not change assertions, fabricate expected output, silently repair the implementation, or claim blind evaluation: the grader is visible. Use the supplied verifier_adapter.py if available; it verifies the source hash and records each original test outcome. Report original verifier pass/fail separately from your broader instruction review and separately from the collaboration integration checks. Missing dependencies, failures or unreviewed requirements must remain explicit. Register the final report with exact input/output/verifier digests and tool evidence references; preserve both output files and the implementation.''' + '\n' + COMMON


def module():
    pieces = [build_agent_task_module((AgentStage(role, instruction),), max_attempts_per_stage=12).to_dict()
              for role, instruction in [('implementer', IMPLEMENTER), ('reviewer', REVIEWER)]]
    for piece in pieces:
        role = piece['components'][0]['name']
        operation = piece['components'][0]['operations'][0]
        operation['tools'] = TOOLS
        operation['budget_binding']['budget_scope'] = role
        piece['budget_buckets'][0]['budget_scope'] = role
    result = deepcopy(pieces[0])
    result['name'] = 'CollaborativeMultiSourceAgentProject'
    result['components'] = [p['components'][0] for p in pieces]
    result['budget_buckets'] = [p['budget_buckets'][0] for p in pieces]
    result['links'] = [{'source': {'component': 'implementer', 'port': 'result'},
                        'target': {'component': 'reviewer', 'port': 'request'}}]
    result['exit'] = pieces[1]['exit']
    result['terminal'] = pieces[1]['terminal']
    # These are the Module's actual application data/config schema needs.
    # Protected AgentLoop mechanical schemas remain in the trusted HOST catalog;
    # portable packages must neither supply nor override them.
    result['required_schemas'] = sorted({component['config_schema'] for component in result['components']} |
                                         {port['schema'] for component in result['components'] for port in component['ports']})
    return ModuleDeclaration.from_dict(result)


def task_input():
    source = json.loads((ROOT / 'materials/SOURCES.json').read_text())
    rows = []
    for item in source['files']:
        body = (UPSTREAM / item['path']).read_bytes()
        assert hashlib.sha256(body).hexdigest() == item['sha256']
        if item['path'].startswith('multi-source-data-merger/'):
            rows.append({'path': item['path'].split('/', 1)[1], 'bytes': len(body),
                         'sha256': item['sha256'], 'encoding': 'base64',
                         'content': base64.b64encode(body).decode()})
    adapter = (ROOT / 'scenario/verifier_adapter.py').read_bytes()
    rows.append({'path': 'verifier_adapter.py', 'bytes': len(adapter),
                 'sha256': hashlib.sha256(adapter).hexdigest(), 'encoding': 'base64',
                 'content': base64.b64encode(adapter).decode(), 'origin': 'RPNH path-only verifier adapter'})
    return {'schema_version': 'rpnh_multi_source_task/v1',
            'task': 'terminal-bench/multi-source-data-merger',
            'upstream_commit': COMMIT, 'source_files': rows,
            'source_license': 'Apache-2.0; complete text in package licenses/terminal-bench.txt',
            'grader_visibility': 'visible; not a blind evaluation',
            'path_adaptation': {'/data/': 'task/environment/data/', '/app/': 'outputs/'},
            'expected_outputs': ['outputs/merged_users.parquet', 'outputs/conflicts.json'],
            'install_policy': 'No package installation or network access from agent tools.',
            'scoring': 'Run the unmodified original verifier in its official layout when available; otherwise disclose the supplied path-only adapter result separately.'}


def build():
    output = ROOT / 'scenario/generated'
    output.mkdir(exist_ok=True)
    selected = module()
    reg = agent_task_registration()
    compiled = compile_module(selected, reg)
    del compiled
    document = selected.to_dict()
    inputs = {'request': {'schema_id': TEXT_SCHEMA, 'value': canonical_bytes(task_input()).decode()}}
    (output / 'module.json').write_bytes(canonical_bytes(document))
    (output / 'inputs.json').write_bytes(canonical_bytes(inputs))
    for name, extra in [('contributor-a', 'Require the handoff to include source-by-source SHA-256 verification evidence.'),
                        ('contributor-b', 'Require the handoff to include exact pandas and Parquet engine versions plus input hashes.')]:
        variant = deepcopy(document)
        variant['components'][0]['operations'][0]['config']['node_synopsis'] += '\n' + extra
        if name == 'contributor-a':
            variant['components'][1]['operations'][0]['config']['node_synopsis'] += '\nIndependently re-hash all three input files and report agreement with the original source manifest before judging outputs.'
        else:
            # The merge model treats each operation body as one atom. This
            # component-level capacity contribution is a distinct real PN atom.
            # The linked ports fuse into one place and must agree on capacity.
            variant['components'][0]['config']['capacities'] = {'result': 1}
            variant['components'][1]['config']['capacities'] = {'request': 1, 'result': 1}
        (output / (name + '.json')).write_bytes(canonical_bytes(variant))
    reconciled = json.loads((output / 'contributor-a.json').read_bytes())
    incoming = json.loads((output / 'contributor-b.json').read_bytes())
    for index in (0, 1):
        reconciled['components'][index]['config'] = deepcopy(incoming['components'][index]['config'])
    (output / 'contributor-a-resolved.json').write_bytes(canonical_bytes(reconciled))
    files, roles, origins = {}, {}, {}
    def add(path, body, role='document', media='application/json', license_id='rpnh', copied=False):
        files[path] = body
        roles[path] = (role, media, license_id)
        origins[path] = {'artifact_path': path, 'relation': 'copied' if copied else 'authored',
                         'origin_ref': None, 'origin_digest': hashlib.sha256(body).hexdigest() if copied else None}
    add('declarations/main.json', canonical_bytes(document), 'declaration')
    add('fixtures/inputs.json', canonical_bytes(inputs), 'fixture', license_id='combined')
    add('sources/SOURCES.json', (ROOT / 'materials/SOURCES.json').read_bytes())
    add('licenses/terminal-bench.txt', (UPSTREAM / 'LICENSE').read_bytes(), 'license', 'text/plain', 'terminal-bench', True)
    # Public RPNH license is supplied by the selected immutable base checkout.
    import cpn
    license_bytes = (Path(cpn.__file__).resolve().parents[1] / 'LICENSE').read_bytes()
    add('licenses/RPNH.txt', license_bytes, 'license', 'text/plain', 'rpnh', True)
    for item in json.loads((ROOT / 'materials/SOURCES.json').read_text())['files']:
        if item['path'] == 'LICENSE':
            continue
        raw = (UPSTREAM / item['path']).read_bytes()
        if item['path'].endswith('.parquet'):
            # Existing portable artifact contract is UTF-8. Lossless base64 is explicit.
            encoded = canonical_bytes({'encoding': 'base64', 'original_path': item['path'],
                                       'bytes': len(raw), 'sha256': item['sha256'],
                                       'content': base64.b64encode(raw).decode()})
            add('upstream/' + item['path'] + '.base64.json', encoded, 'fixture', license_id='terminal-bench')
            origins['upstream/' + item['path'] + '.base64.json'].update(relation='derived', origin_digest=item['sha256'])
        else:
            add('upstream/' + item['path'], raw, 'document', 'text/plain', 'terminal-bench', True)
    add('tools/verifier_adapter.py', (ROOT / 'scenario/verifier_adapter.py').read_bytes(), 'document', 'text/plain')
    for i, schema_id in enumerate(document['required_schemas']):
        schema = reg.declaration('schema', schema_id)['schema']
        add(f'schemas/{i:03d}.json', canonical_bytes(schema), 'schema', 'application/schema+json')
    requirements = []
    needed = {('terminal', document['terminal']['key'])}
    for component in document['components']:
        needed.add(('component', component['key']))
        for operation in component['operations']:
            needed.add(('executor', operation['executor']))
            needed.update(('tool', tool) for tool in operation['tools'])
    for i, (kind, contract) in enumerate(sorted(needed)):
        requirements.append({'requirement_id': f'req{i:03d}', 'kind': kind, 'contract_id': contract,
                             'required': True, 'effects': [], 'data_classes': []})
    env = {'schema_version': 'rpnh/environment_requirements/v1', 'entry_id': 'main',
           'python': {'requirement_id': 'python', 'implementation': 'cpython', 'version_specifier': '>=3.11,<3.14'},
           'distributions': [{'requirement_id': key, 'name': name, 'version_specifier': version,
                              'satisfies_host_requirement_ids': []} for key, name, version in
                            [('harness', 'rpnh-harness', '>=0.1.0rc2'), ('pandas', 'pandas', '==3.0.3'),
                             ('parquet', 'pyarrow', '==24.0.0')]],
           'system_requirements': [{'requirement_id': 'nativeOwner', 'kind': 'capability',
              'capability_contract_id': 'rpnh/native-owner-af-unix/v1', 'constraint': {'operator': 'present', 'value': True},
              'satisfies_host_requirement_ids': []}],
           'tools': [], 'plugins': [], 'services': [{'requirement_id': 'approvedModel',
              'service_contract_id': 'rpnh/existing-approved-provider-profile/v1', 'required_capabilities': [],
              'model_constraint': None, 'authentication_required': True, 'satisfies_host_requirement_ids': []}]}
    add('environment/task.json', canonical_bytes(env))
    artifacts = [{'path': path, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                  'role': roles[path][0], 'media_type': roles[path][1], 'license_id': roles[path][2],
                  'disclosure': 'public'} for path, body in sorted(files.items())]
    manifest = {'schema_version': 'rpnh/share_package/v2', 'package_id': 'rpnh/collaborative-multi-source', 'version': '0.1.1',
        'entries': [{'entry_id': 'main', 'kind': 'closed_module', 'declaration_path': 'declarations/main.json',
                     'declaration_schema': 'rpnh/module_declaration/v1', 'input_schema_ids': [TEXT_SCHEMA],
                     'output_schema_ids': [TEXT_SCHEMA], 'environment_requirements_path': 'environment/task.json',
                     'completion_contract': {'success_exit': 'result', 'failure_exits': [], 'acceptor_requirement_ids': [], 'open_obligations': []}}],
        'artifacts': artifacts, 'origin': {'repository_url': 'https://github.com/Deng-0119/RPNH',
        'commit': '518101cb3db8726cb3da52e507aa23895840774f', 'publisher_claim': 'Unpublished candidate project materials', 'source_refs': []},
        'provenance': list(origins.values()), 'dependencies': [],
        'compatibility': {'declaration_schemas': ['rpnh/module_declaration/v1'], 'runtime_contracts': [], 'host_contracts': sorted({contract for _, contract in needed})},
        'requirements': requirements, 'policy_surface': [],
        'licenses': [{'license_id': 'rpnh', 'expression': 'MIT', 'text_paths': ['licenses/RPNH.txt'], 'notice_paths': []},
                     {'license_id': 'terminal-bench', 'expression': 'Apache-2.0', 'text_paths': ['licenses/terminal-bench.txt'], 'notice_paths': []},
                     {'license_id': 'combined', 'expression': 'MIT AND Apache-2.0', 'text_paths': ['licenses/RPNH.txt', 'licenses/terminal-bench.txt'], 'notice_paths': []}],
        'disclosure': {'classification': 'public', 'intended_audience': 'Readers of the public example after review; no publication occurs here',
                       'excluded_categories': ['credentials', 'provider-profiles', 'Registry-databases', 'sessions', 'grants']}}
    files['manifest.json'] = canonical_bytes(manifest)
    archive = output / 'seed-project.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_STORED) as z:
        for path, raw in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 13, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            z.writestr(info, raw)
    preview = preview_package(archive.read_bytes())
    lock = resolve_package(preview)
    (output / 'package-preview.json').write_bytes(preview.report_bytes)
    (output / 'package-lock.json').write_bytes(lock.payload)
    (output / 'manifest.json').write_bytes(canonical_bytes(manifest))
    print(json.dumps({'module_components': [c['name'] for c in document['components']],
                      'artifacts': len(artifacts), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                      'manifest_sha256': preview.manifest_digest, 'lock_sha256': hashlib.sha256(lock.payload).hexdigest(),
                      'task_executed': False, 'model_calls': 0}, indent=2))


if __name__ == '__main__':
    build()
