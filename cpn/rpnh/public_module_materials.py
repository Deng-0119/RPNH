"""Installer-declared Module material draft, using the original HOST/compiler.

No installation, dependency discovery, business executor, provider or secret
resolver is run here. Distribution identities are original installed selections.
The declared finite boundary and purity are trusted installer assumptions.
"""
from __future__ import annotations
from dataclasses import dataclass
import importlib.metadata as metadata
import json
from pathlib import Path, PurePosixPath
import sys
from . import public_material_contracts as wire
from .registry.parent_child import ParentChildUnsupported, _target, _json
from .registry.schema_catalog import SchemaCatalog, canonical_json
from .registry.runtime_binding_contracts import freeze_candidate_document

MANIFEST = 'rpnh_public_materials.json'


@dataclass(frozen=True, slots=True)
class PublicHostSelection:
    """Path-less immutable public inputs, never a private local binding."""
    profile_id: str
    contract_bytes: bytes
    public_assets: tuple[tuple[str, bytes], ...]
    payload_bytes: bytes
    target_bytes: bytes

    @property
    def contract(self): return wire.decode(self.contract_bytes, canonical_required=True)

    @property
    def configuration(self): return wire.decode(dict(self.public_assets)['configuration'])


@dataclass(frozen=True, slots=True)
class VerifiedInstalledPublicContract:
    contract_bytes: bytes
    observations: tuple[tuple[str, bytes], ...]
    inline_assets: tuple[tuple[str, bytes], ...]
    _entrypoint: object
    _registration_pairs: tuple

    @property
    def contract(self): return wire.decode(self.contract_bytes, canonical_required=True)


def _member(distribution, name, members=None):
    if (type(name) is not str or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
        or '\\' in name or name not in (members if members is not None else {str(v) for v in distribution.files or ()})):
        raise ParentChildUnsupported('PUBLIC_MATERIAL_CONTRACT_UNAVAILABLE: asset is not an exact registered member')
    path = Path(distribution.locate_file(name))
    if not path.is_file(): raise ParentChildUnsupported('PUBLIC_MATERIAL_ASSET_MISSING')
    return path


def read_installed_contract(entrypoint, payload_kind, registration_pairs):
    """Validate and observe static metadata before loading any selected factory."""
    try: raw = _member(entrypoint.dist, MANIFEST).read_bytes()
    except (OSError, ValueError) as exc:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_CONTRACT_UNAVAILABLE') from exc
    doc = wire.decode(raw)
    if set(doc) != {'schema_version','profiles'} or doc['schema_version'] != 'rpnh/public_material_installation/v1' or type(doc['profiles']) is not list:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_INSTALLATION_ABI_UNSUPPORTED')
    rows = [v for v in doc['profiles'] if type(v) is dict and v.get('contract',{}).get('profile_id') == entrypoint.name]
    if len(rows) != 1: raise ParentChildUnsupported('PUBLIC_MATERIAL_PROFILE_AMBIGUOUS_OR_MISSING')
    selected = rows[0]
    if set(selected) != {'contract','installation_id','runtime','assets'}: raise ValueError('closed installation metadata required')
    contract = wire.validate_contract(selected['contract'])
    if payload_kind not in contract['supported_payloads']: raise ParentChildUnsupported('PUBLIC_MATERIAL_PAYLOAD_UNSUPPORTED')
    if payload_kind not in ('module','agent_task') or payload_kind == 'agent_task' and contract['supported_shape']['agent_task_mode'] != 'single_stage_plugin_free':
        raise ParentChildUnsupported('PUBLIC_MATERIAL_PAYLOAD_UNSUPPORTED')
    runtime = selected['runtime']
    if (set(runtime) != {'implementation','version','abi_tag','platform_tag'}
        or runtime['implementation'] != sys.implementation.name
        or runtime['version'] != '.'.join(map(str, sys.version_info[:3]))
        or runtime['abi_tag'] != sys.implementation.cache_tag or runtime['platform_tag'] != sys.platform):
        raise ParentChildUnsupported('PUBLIC_MATERIAL_RUNTIME_CHANGED')
    if type(selected['assets']) is not list: raise ValueError('asset mapping must be finite')
    wire.ordered(selected['assets'], lambda v: v['asset_id'])
    assets = {v['asset_id']:v for v in selected['assets']}
    if set(assets) != {a for u in contract['implementation_units'] for a in u['asset_ids']}:
        raise ValueError('installed asset map differs from exact declared assets')
    contract_raw = wire.canonical(contract); observations = []; inline = []
    distributions = {entrypoint.dist.metadata['Name']:entrypoint.dist}
    selected_unit_ids = wire.selected_units(contract,set(registration_pairs))
    declared_pairs={(v['kind'],v['key']) for v in contract['registrations']}
    from .registry.schema_catalog import PROTECTED_SCHEMA_REFS
    if set(registration_pairs)-declared_pairs-{('schema',sid) for sid in PROTECTED_SCHEMA_REFS}:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_UNDECLARED_SELECTION')
    metadata_units=[u['unit_id'] for u in contract['implementation_units']
        if u['distribution']['name']==entrypoint.dist.metadata['Name'] and any(assets[a]['member']==MANIFEST for a in u['asset_ids'])]
    if len(metadata_units)!=1 or metadata_units[0] not in selected_unit_ids:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_METADATA_ASSET_UNDECLARED')
    for unit in contract['implementation_units']:
        if unit['unit_id'] not in selected_unit_ids: continue
        name, version = unit['distribution']['name'], unit['distribution']['version']
        if name not in distributions:
            try: distributions[name] = metadata.distribution(name)
            except metadata.PackageNotFoundError as exc: raise ParentChildUnsupported('PUBLIC_MATERIAL_DEPENDENCY_MISSING: '+name) from exc
        distribution = distributions[name]
        if distribution.metadata['Name'] != name or distribution.version != version or unit['runtime_abi'] != runtime['abi_tag']:
            raise ParentChildUnsupported('PUBLIC_MATERIAL_DEPENDENCY_IDENTITY_CHANGED')
        observed = []
        members = frozenset(str(v) for v in distribution.files or ())
        for asset_id in unit['asset_ids']:
            locator = assets[asset_id]
            if set(locator) != {'asset_id','member','size','sha256'}: raise ValueError('closed asset locator required')
            member = locator['member']
            content = raw if distribution is entrypoint.dist and member == MANIFEST else _member(distribution, member, members).read_bytes()
            if not (distribution is entrypoint.dist and member == MANIFEST):
                if type(locator['size']) is not int or locator['size'] != len(content) or locator['sha256'] != wire.sha(content):
                    raise ParentChildUnsupported('PUBLIC_MATERIAL_CHANGED: '+asset_id)
            elif locator['size'] is not None or locator['sha256'] is not None:
                raise ValueError('installation metadata cannot require a self hash')
            node_id = 'asset.' + asset_id if unit['mode'] == 'inline_public_bytes' else None
            if node_id:
                if len(content) > wire.INLINE_CAP: raise ParentChildUnsupported('PUBLIC_MATERIAL_INLINE_CAP')
                inline.append((node_id,content))
            observed.append({'asset_id':asset_id,'size':len(content),'sha256':wire.sha(content),'inline_node_id':node_id})
        observation = {'schema_version':wire.OBSERVATION,'contract_id':contract['contract_id'],'contract_revision':contract['revision'],
            'installed_contract_sha256':wire.sha(contract_raw),'unit_id':unit['unit_id'],'distribution':unit['distribution'],
            'installation_id':selected['installation_id'],'runtime':runtime,'mode':unit['mode'],'assets':observed}
        wire.validate(wire.OBSERVATION, observation)
        observations.append((unit['unit_id'],wire.canonical(observation)))
    return VerifiedInstalledPublicContract(contract_raw,tuple(observations),tuple(sorted(inline)),entrypoint,tuple(sorted(registration_pairs)))


@dataclass(frozen=True, slots=True)
class PublicMaterialDraft:
    """Unregistered bytes plus original trusted Registration. Never Prepared."""
    contract_bytes: bytes
    inventory_bytes: bytes
    roots_bytes: bytes
    payloads: tuple[tuple[str, bytes], ...]
    registration: object
    child_kind: str = 'module'
    bindings_bytes: bytes = b'[]'

    @property
    def bindings(self): return wire.decode(self.bindings_bytes, canonical_required=True)

    @property
    def inventory(self): return wire.decode(self.inventory_bytes, canonical_required=True)
    @property
    def roots(self): return wire.decode(self.roots_bytes, canonical_required=True)
    @property
    def contract(self): return wire.decode(self.contract_bytes, canonical_required=True)
    @property
    def public_material_digest(self): return wire.public_digest(self.child_kind,wire.sha(self.contract_bytes),self.inventory,self.roots,self.bindings)


def prepare_module_material_draft(*, profile_id, parent, request_bytes, root_binding, control_root):
    """K -> final T -> original selected factory/Registration/compile -> draft."""
    request = wire.decode(request_bytes)
    SchemaCatalog().validate_schema_ref('rpnh/parent_child_request/v1',request)
    if _json(request) != request_bytes: raise ValueError('request requires its original exact canonical bytes')
    if request['child_kind'] != 'module': raise ParentChildUnsupported('M1/M2 support only Module materials')
    from .module import ModuleDeclaration
    module = ModuleDeclaration.from_dict(request['definition'])
    target = _target(parent,request['slot_id'],root_binding,control_root)
    from .collaboration.environment_host import load_host_profile
    selection = PublicHostSelection(profile_id,b'',(('configuration',wire.canonical(request['public_configuration'])),),
        canonical_json(module.to_dict()),_json(target))
    profile = load_host_profile(profile_id,None,public_selection=selection)
    installed = profile.public_material_contract
    contract = installed.contract
    from .registration import Registration
    registration = profile.registration_factory()
    if type(registration) is not Registration: raise TypeError('public profile requires original Registration')
    snapshot = {'declarations':sorted(list(registration.declarations()),key=lambda v:(v['kind'],v['key']))}
    before = freeze_candidate_document(snapshot).encode('ascii')
    expected = {(e['kind'],e['key']) for e in contract['registrations']}
    if expected != {(e['kind'],e['key']) for e in snapshot['declarations']}: raise ValueError('complete Registration differs from installed contract')
    from .bound_child_lowering import with_bound_origin
    from .compiler import compile_module
    bound = with_bound_origin(module); compiled = compile_module(bound,registration)
    if freeze_candidate_document({'declarations':sorted(list(registration.declarations()),key=lambda v:(v['kind'],v['key']))}).encode('ascii') != before:
        raise ValueError('Registration changed during public preparation')
    pairs = {(kind,key) for kind, entries in compiled.registrations.items() for key in entries}
    # Mechanical schemas need not be Registration entries.
    if pairs - expected - {('schema',key) for key in compiled.registrations.get('schema',{}) if key in __import__('cpn.rpnh.registry.schema_catalog',fromlist=['PROTECTED_SCHEMA_REFS']).PROTECTED_SCHEMA_REFS}:
        raise ValueError('compiler consumed an undeclared Registration key')
    operation_names = wire.module_operation_shapes(module,snapshot['declarations'])
    if not operation_names or not set(operation_names)<=set(contract['supported_shape']['module_operations']):
        raise ParentChildUnsupported('Module operation outside installed finite shape')
    configuration = request['public_configuration']
    if set(configuration) != {'configuration','inputs'}: raise ValueError('Module public input requires explicit configuration and inputs')
    selected = wire.selected_slots(contract,pairs)
    units = wire.selected_units(contract,pairs)
    data, descriptors = {}, {}
    def node(identity, role, value, schema=None, dependencies=(), encoding=wire.ORIGINAL_ENCODING, media='application/json'):
        raw = value if type(value) is bytes else wire.canonical(value) if encoding == wire.C_ENCODING else freeze_candidate_document(value).encode('ascii')
        data[identity]=raw
        descriptors[identity]={'node_id':identity,'role':role,'media_type':media,'body_schema_id':schema,'encoding':encoding,
            'size':len(raw),'sha256':wire.sha(raw),'depends_on':sorted(set(dependencies))}
        return identity
    schema_ids = sorted(set(compiled.registrations.get('schema',{})) | set(wire.MODULE_SCHEMAS) | {'rpnh/module_declaration/v1','rpnh/executable_net/v1'})
    schema_nodes = {sid:'schema.s'+str(i) for i,sid in enumerate(schema_ids)}
    for sid in schema_ids:
        doc = registration.declaration('schema',sid)['schema']
        node(schema_nodes[sid],'schema',canonical_json(doc),'registry_v1/registry_type_catalog/v1',media='application/schema+json')
    obsmap = dict(installed.observations); inline = dict(installed.inline_assets)
    for unit in contract['implementation_units']:
        if unit['unit_id'] not in units: continue
        obs = wire.decode(obsmap[unit['unit_id']],canonical_required=True)
        dependencies = [schema_nodes[wire.OBSERVATION], *['observation.'+v for v in unit['dependency_unit_ids']]]
        for asset in obs['assets']:
            if asset['inline_node_id'] is not None:
                aid=asset['inline_node_id']; node(aid,'implementation_asset',inline[aid],encoding=wire.OPAQUE_ENCODING,media='application/octet-stream');dependencies.append(aid)
        node('observation.'+unit['unit_id'],'implementation_observation',obsmap[unit['unit_id']],wire.OBSERVATION,dependencies,wire.C_ENCODING)
    slot_rows=[]; slot_nodes={'public_host':[],'input':[],'budget':[]}
    budgets={'budgets':bound.to_dict()['budgets'],'budget_buckets':bound.to_dict()['budget_buckets']}
    for slot in selected:
        identity='slot.'+slot['slot_id'];role=slot['role']
        if role=='public_host': value=configuration['configuration']
        elif role=='input': value=configuration['inputs']
        elif role=='budget': value=budgets
        else: raise ParentChildUnsupported('Module has no execution policy')
        schema=slot['body_schema_id']
        # Budget is the one explicitly untyped original ABI exception.
        if role=='budget': schema=None
        if schema:
            if schema not in schema_nodes:
                schema_nodes[schema]='schema.s'+str(len(schema_nodes)); doc=registration.declaration('schema',schema)['schema']
                node(schema_nodes[schema],'schema',canonical_json(doc),'registry_v1/registry_type_catalog/v1',media='application/schema+json')
            # Validation also occurs at exact Registry schema authority.
        node(identity,role,value,schema,() if schema is None else (schema_nodes[schema],))
        slot_rows.append({'slot_id':slot['slot_id'],'node_id':identity});slot_nodes[role].append(identity)
    selection_doc={'schema_version':wire.SELECTION,'child_kind':'module','selected_registrations':[{'kind':k,'key':v} for k,v in sorted(pairs)],
        'selected_public_slots':slot_rows,'execution_profiles':[]}
    node('selection','selection',selection_doc,wire.SELECTION,[schema_nodes[wire.SELECTION],*[v['node_id'] for v in slot_rows]],wire.C_ENCODING)
    observations=sorted('observation.'+v for v in units)
    node('payload','payload',canonical_json(module.to_dict()),'rpnh/module_declaration/v1',['selection',schema_nodes['rpnh/module_declaration/v1']] if 'rpnh/module_declaration/v1' in schema_nodes else ['selection'])
    node('declaration','declaration',canonical_json(bound.to_dict()),'rpnh/module_declaration/v1',['payload',schema_nodes['rpnh/module_declaration/v1']])
    node('registration','registration',before,None,[*observations,*schema_nodes.values()])
    node('lowered-net','lowered_net',canonical_json(compiled.to_dict()),'rpnh/executable_net/v1',['declaration','registration',schema_nodes['rpnh/executable_net/v1']])
    node('normalized-request','normalized_request',_json({'request':request,'target':target}),None,['payload'])
    roots={'payload':'payload','normalized_request':'normalized-request','declaration':'declaration','lowered_net':'lowered-net',
        'registration':'registration','selection':'selection','schemas':sorted(schema_nodes.values()),'implementation':observations,
        'public_host':sorted(slot_nodes['public_host']),'execution_policy':[],'inputs':sorted(slot_nodes['input']),'budgets':sorted(slot_nodes['budget'])}
    # Include schema authorities used by payload/net even when compiler omits them.
    for sid in ('rpnh/module_declaration/v1','rpnh/executable_net/v1'):
        if sid not in schema_nodes:
            identity='schema.s'+str(len(schema_nodes));schema_nodes[sid]=identity
            node(identity,'schema',canonical_json(registration.declaration('schema',sid)['schema']),'registry_v1/registry_type_catalog/v1',media='application/schema+json')
            roots['schemas'].append(identity)
    roots['schemas'].sort()
    fresh = read_installed_contract(installed._entrypoint,'module',installed._registration_pairs)
    if fresh != installed:
        raise ParentChildUnsupported('PUBLIC_MATERIAL_CHANGED_DURING_PREPARATION')
    result=PublicMaterialDraft(installed.contract_bytes,wire.canonical([descriptors[n] for n in sorted(descriptors)]),wire.canonical(roots),tuple(sorted(data.items())),registration)
    from .registry.public_materials import validate_draft
    validate_draft(result)
    return result
