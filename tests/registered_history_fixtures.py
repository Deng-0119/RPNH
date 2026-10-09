"""Static public installation layout for the original deterministic PN fixtures.

This is a trusted, finite test HOST declaration, not an installed production
profile or a proof of Python purity. The actual test-created Registration and
its original callables are retained. No Registration is reconstructed from a
compiled snapshot, and no business executor or native transport is invoked.
"""
from contextlib import contextmanager
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.collaboration.environment_host import HostProfile
from cpn.rpnh.public_module_materials import prepare_module_material_draft
from cpn.rpnh.registry import parent_child as h7
from cpn.rpnh.registry.schema_catalog import canonical_json
from registered_material_fixtures import LayoutDistribution, _paths


ROOT = Path(__file__).parents[1]
PROFILE_ID = 'rpnh-static-history-fixture/v1'
HOST_SCHEMA = 'application/history_public_host/v1'
INPUT_SCHEMA = 'application/history_public_inputs/v1'


def _input(value):
    return {'schema_id': value.schema_id, 'payload_text': value.payload.decode('utf-8'),
            'summary': value.summary}


def public_inputs(task, entries, resources):
    return {'task': _input(task),
            'entries': {key: [_input(v) for v in ((value,) if hasattr(value, 'payload') else value)]
                        for key, value in sorted(entries.items())},
            'resources': {key: _input(value) for key, value in sorted(resources.items())}}


def _schema(registration, identity, document):
    registration.register_schema(identity, {'$id': identity,
        '$schema': 'http://json-schema.org/draft-07/schema#', **document})


@contextmanager
def installed_history_layout(path, registration, module, *, task, entries, resources,
                             request_transform=None):
    """Declare public assets, then expose only metadata discovery for this HOST."""
    import pytest
    path.mkdir(parents=True)
    _schema(registration, HOST_SCHEMA, {'type': 'object'})
    value = {'type': 'object', 'additionalProperties': False,
        'properties': {'schema_id': {'type': 'string'}, 'payload_text': {'type': 'string'},
                       'summary': {'type': 'string'}},
        'required': ['schema_id', 'payload_text', 'summary']}
    _schema(registration, INPUT_SCHEMA, {'type': 'object', 'additionalProperties': False,
        'properties': {'task': value,
            'entries': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': value}},
            'resources': {'type': 'object', 'additionalProperties': value}},
        'required': ['task', 'entries', 'resources']})
    request = {'schema_version': h7.REQUEST_SCHEMA, 'slot_id': 'child', 'child_kind': 'module',
        'definition': module.to_dict(), 'public_configuration': {'configuration': {},
            'inputs': public_inputs(task, entries, resources)}}
    if request_transform is not None:
        request = request_transform(request)
    manifest = path / 'rpnh_public_materials.json'
    source_paths = {p.relative_to(ROOT).as_posix(): p for base in ('cpn', 'tests')
        for p in (ROOT / base).rglob('*') if p.is_file() and '__pycache__' not in p.parts
        and p.suffix in ('.py', '.json')}
    source_paths['rpnh_public_materials.json'] = manifest
    source = LayoutDistribution('rpnh-static-history-layout', '20261009', source_paths)
    # Explicit public interpreter/stdlib/native members. This fixture makes no
    # assertion about loader choice or ambient environment independence.
    stdlib = Path(__import__('sysconfig').get_path('stdlib')).resolve()
    runtime_paths = {'python': Path(sys.executable).resolve()}
    runtime_paths.update({'stdlib/' + p.relative_to(stdlib).as_posix(): p
        for p in stdlib.rglob('*') if p.is_file() and 'site-packages' not in p.parts
        and '__pycache__' not in p.parts and p.suffix in ('.py', '.so')})
    for name in ('libc.so.6', 'libm.so.6', 'libstdc++.so.6', 'libgcc_s.so.1', 'libz.so.1',
                 'libpthread.so.0', 'ld-linux-x86-64.so.2', 'libdl.so.2', 'libutil.so.1', 'librt.so.1'):
        runtime_paths['native/' + name] = Path('/lib/x86_64-linux-gnu') / name
    runtime = LayoutDistribution('rpnh-history-public-runtime-layout',
        'python-' + '.'.join(map(str, sys.version_info[:3])), runtime_paths)
    distributions = {d.metadata['Name']: d for d in (source, runtime)}
    for name in ('jsonschema', 'referencing', 'rpds-py', 'attrs', 'jsonschema-specifications',
                 'pytest', 'iniconfig', 'packaging', 'pluggy', 'pygments'):
        dist = metadata.distribution(name)
        distributions[dist.metadata['Name']] = dist
    units, assets, ids = [], [], {}
    for index, (name, distribution) in enumerate(sorted(distributions.items())):
        unit_id = 'u.' + str(index); ids[name] = unit_id
        paths = distribution.paths if isinstance(distribution, LayoutDistribution) else _paths(distribution)
        asset_ids = []
        for number, (member, member_path) in enumerate(sorted(paths.items())):
            asset_id = 'a.' + str(index) + '.' + str(number); asset_ids.append(asset_id)
            raw = None if member_path == manifest else member_path.read_bytes()
            assets.append({'asset_id': asset_id, 'member': member,
                'size': None if raw is None else len(raw), 'sha256': None if raw is None else w.sha(raw)})
        units.append({'unit_id': unit_id, 'distribution': {'name': name, 'version': distribution.version},
            'runtime_abi': sys.implementation.cache_tag, 'mode': 'installed_digest_identity',
            'asset_ids': sorted(asset_ids), 'dependency_unit_ids': []})
    for unit in units:
        if unit['unit_id'] != ids[runtime.metadata['Name']]:
            unit['dependency_unit_ids'] = [ids[runtime.metadata['Name']]]
    all_units = sorted(ids.values())
    declarations = sorted(registration.declarations(), key=lambda d: (d['kind'], d['key']))
    registrations = [{'kind': d['kind'], 'key': d['key'],
        'implementation_unit_ids': [] if d['kind'] == 'schema' else all_units,
        'public_slot_ids': []} for d in declarations]
    def slot(identity, role, schema):
        return {'slot_id': identity, 'role': role, 'body_schema_id': schema, 'cardinality': 'one',
            'selection_rule': {'kind': 'always', 'payload_kind': None, 'registration_key': None}}
    operation_names = sorted({o['name'] for c in module.to_dict()['components'] for o in c['operations']})
    contract = {'schema_version': w.CONTRACT, 'contract_id': 'static-history-fixture',
        'revision': 'v1', 'profile_id': PROFILE_ID,
        'preparation_abi': 'rpnh/public_material_preparation/v1',
        'coverage_basis': 'installer_declared_public_closure/v1', 'supported_payloads': ['module'],
        'implementation_units': sorted(units, key=lambda v: v['unit_id']),
        'preparation_roots': {key: all_units for key in
            ('profile_factory', 'registration_factory', 'lowerer', 'serializer', 'compiler', 'catalog_factory')},
        'execution_roots': {key: [] for key in
            ('executors', 'tools', 'services', 'host_bindings', 'configuration_sources', 'before_dispatch', 'adapter')},
        'registrations': registrations,
        'public_slots': [slot('budget', 'budget', 'rpnh/module_declaration/v1'),
            slot('configuration', 'public_host', HOST_SCHEMA), slot('input', 'input', INPUT_SCHEMA)],
        'binding_slots': [], 'supported_shape': {'module_operations': operation_names,
            'agent_task_mode': None, 'local_process': False, 'workflow': False, 'managed_plugins': False},
        'assumptions': ['binding_identity', 'dependency_completeness', 'preparation_side_effect_contract']}
    manifest.write_bytes(w.canonical({'schema_version': 'rpnh/public_material_installation/v1',
        'profiles': [{'contract': contract, 'installation_id': 'offline-history-layout',
            'runtime': {'implementation': sys.implementation.name,
                'version': '.'.join(map(str, sys.version_info[:3])),
                'abi_tag': sys.implementation.cache_tag, 'platform_tag': sys.platform},
            'assets': sorted(assets, key=lambda a: a['asset_id'])}]}))
    def profile(selection):
        return HostProfile(PROFILE_ID, lambda: registration,
            public_material_contract=selection.contract_bytes)
    entrypoint = SimpleNamespace(name=PROFILE_ID, dist=source, load=lambda: profile)
    original_distribution = metadata.distribution
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(metadata, 'distribution', lambda name:
            distributions[name] if name in distributions else original_distribution(name))
        patch.setattr(metadata, 'entry_points', lambda **kwargs:
            [entrypoint] if kwargs.get('group') == 'rpnh.environment_hosts' else [])
        yield SimpleNamespace(request=request, registration=registration, module=module)


def prepare_registered_history(owner, request_ref, request_bytes, control_root):
    draft = prepare_module_material_draft(profile_id=PROFILE_ID,
        parent=h7.parent_identity(owner._core), request_bytes=request_bytes,
        root_binding='test-root-binding', control_root=str(control_root))
    inventory = owner.schema_gateway.publish_public_material_inventory(draft, command_id='history:materials')
    return h7.prepare_registered_child_materials(owner._core, request_ref=request_ref,
        request_bytes=request_bytes, registered_inventory_ref=inventory.resource_ref,
        root_binding='test-root-binding', control_root=str(control_root))
