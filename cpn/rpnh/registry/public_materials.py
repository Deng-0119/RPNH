"""Original Registry producer and same-cut public-material verifier.

No new execution authority: successful registration means only exact finite
installer-declared public facts. All PN/Start and native gates still apply.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from .. import public_material_contracts as w
from .event_store import RegistryConflict
from .resources import PrivateSystemOrigin, PublishResource, ResourceVersionRef
from .publication import _resource_from_payload, _ref_payload, _version_from_payload
from .resource_service import _publish_private_system
from .schema_catalog import canonical_json, PROTECTED_SCHEMA_REFS
from ._event_store.source_identity import read_source_binding
from ._event_store.collaboration_descriptors import exact_prepared
from ._candidate_read_context import _CandidateReadContext
from ._candidate_plan_reads import PlanReadClosure, _validate_resource_instance
from .bootstrap_materials import read_bootstrap_material


class _MaterialReadClosure(PlanReadClosure):
    """Memoize already verified immutable descriptors in this one SQLite cut."""
    def __init__(self,db,context):
        self._descriptor_cache={}
        super().__init__(db,context)
    def descriptor(self,ref,*,static=False):
        key=(canonical_json(ref),static)
        if key not in self._descriptor_cache:
            self._descriptor_cache[key]=super().descriptor(ref,static=static)
        return self._descriptor_cache[key]


def pair(ref): return {'resource_id':str(ref.resource_id),'resource_version_id':str(ref.resource_version_id)}
def native(pair): return _ref_payload(_resource_from_payload(pair).as_version_ref())
def unique_refs(refs):
    return tuple(_resource_from_payload(json.loads(k)) for k in sorted({canonical_json(v) for v in refs}))


def validate_documents(contract, inventory, roots, payloads, bindings=()):
    """Pure bounded semantic closure; no source discovery, factory or resolver."""
    contract=w.validate_contract(contract)
    from jsonschema import Draft7Validator
    from .schema_catalog import SchemaCatalog
    schema_document = json.loads(SchemaCatalog._mechanical_schema_path(w.INVENTORY).read_text())
    for field,value in (('content_inventory',inventory),('roots',roots),('opaque_bindings',list(bindings))):
        Draft7Validator(schema_document['properties'][field]).validate(w.strict_copy(value))
    if len(inventory)>4096: raise ValueError('node cap')
    w.ordered(inventory,lambda n:n['node_id'])
    nodes={n['node_id']:n for n in inventory}
    # Reuse the protected closed root's definitions through its full validator.
    if set(payloads)!=set(nodes): raise ValueError('exact public body set differs')
    if sum(len(v) for v in payloads.values())+len(w.canonical(contract))>w.AGGREGATE_CAP: raise ValueError('aggregate public byte cap')
    if sum(len(n['depends_on']) for n in nodes.values())>16384: raise ValueError('edge cap')
    for n in nodes.values():
        if type(n['size']) is not int or n['size']<0:raise ValueError('node size must be exact nonnegative integer')
        raw=payloads[n['node_id']]
        if type(raw) is not bytes or len(raw)!=n['size'] or w.sha(raw)!=n['sha256']: raise ValueError('public leaf exact bytes differ')
        w.ordered(n['depends_on'])
        if n['node_id'] in n['depends_on']: raise ValueError('self dependency')
        if n['encoding']==w.OPAQUE_ENCODING:
            if n['role']!='implementation_asset' or n['body_schema_id'] is not None or len(raw)>w.INLINE_CAP: raise ValueError('invalid opaque asset')
        else:
            value=w.decode(raw,canonical_required=n['encoding']==w.C_ENCODING)
            if n['encoding'] not in (w.C_ENCODING,w.ORIGINAL_ENCODING): raise ValueError('unknown public encoding')
    single=('payload','normalized_request','declaration','lowered_net','registration','selection')
    arrays=('schemas','implementation','public_host','execution_policy','inputs','budgets')
    if set(roots)!=set(single+arrays): raise ValueError('closed roots required')
    role_for={'schemas':'schema','implementation':'implementation_observation','inputs':'input','budgets':'budget'}
    selected_roots=[]
    for key in single+arrays:
        values=[roots[key]] if key in single else roots[key]
        if key in arrays:
            w.ordered(values)
            if key!='execution_policy' and not values: raise ValueError('required Module root absent')
        for value in values:
            if value not in nodes or nodes[value]['role']!=role_for.get(key,key): raise ValueError('root role mismatch')
        selected_roots+=values
    child_kind = w.decode(payloads[roots['selection']])['child_kind']
    if child_kind not in ('module','agent_task') or child_kind not in contract['supported_payloads']: raise ValueError('unsupported public payload kind')
    if child_kind == 'module' and (roots['execution_policy'] or bindings): raise ValueError('Module cannot carry provider policy/bindings')
    if child_kind == 'agent_task' and len(roots['execution_policy']) != 1: raise ValueError('AgentTask requires one public policy')
    for key in single+arrays:
        actual = {n for n,v in nodes.items() if v['role']==role_for.get(key,key)}
        wanted = {roots[key]} if key in single else set(roots[key])
        if actual != wanted: raise ValueError('role nodes differ from exact selected roots')
    if w._dag(nodes,lambda n:nodes[n]['depends_on'],selected_roots)!=set(nodes): raise ValueError('selected closure has unreachable extra nodes')
    docs={name:w.decode(raw,canonical_required=nodes[name]['encoding']==w.C_ENCODING) for name,raw in payloads.items() if nodes[name]['encoding']!=w.OPAQUE_ENCODING}
    schemas={docs[n]['$id']:(n,docs[n]) for n in roots['schemas']}
    if len(schemas)!=len(roots['schemas']): raise ValueError('duplicate selected schema')
    for name,node in nodes.items():
        schema=node['body_schema_id']
        if schema is not None:
            if node['role']=='schema':
                if schema!='registry_v1/registry_type_catalog/v1': raise ValueError('schema meta authority mismatch')
                from .content_schemas import _validate_schema_bytes
                _validate_schema_bytes(payloads[name])
            else:
                if schema not in schemas: raise ValueError('selected body schema missing')
                _validate_resource_instance(schemas[schema][1],docs[name])
                if schema in w.SCHEMAS: w.validate(schema,docs[name])
        elif node['encoding']!=w.OPAQUE_ENCODING and node['role'] not in ('registration','normalized_request','budget','declaration') and not (child_kind=='agent_task' and node['role']=='public_host'):
            raise ValueError('untyped JSON role is not an ABI exception')
    snapshot=docs[roots['registration']]
    if set(snapshot)!={'declarations'} or type(snapshot['declarations']) is not list: raise ValueError('complete Registration snapshot shape')
    declarations=snapshot['declarations'];w.ordered(declarations,lambda d:(d['kind'],d['key']))
    if len(declarations)>4096: raise ValueError('Registration cap')
    actual={(d['kind'],d['key']) for d in declarations}
    if actual!={(d['kind'],d['key']) for d in contract['registrations']}: raise ValueError('complete Registration key mismatch')
    for d in declarations:
        if set(d)!=({'kind','key','schema'} if d['kind']=='schema' else {'kind','key','identity','contracts'}): raise ValueError('Registration body mismatch')
    from ..executable_net import _load_compiled_net_offline
    from ..bound_child_lowering import with_bound_origin
    from ..module import ModuleDeclaration
    compiled=_load_compiled_net_offline(docs[roots['lowered_net']])
    normalized=docs[roots['normalized_request']]
    if set(normalized)!={'request','target'}: raise ValueError('normalized request shape')
    if child_kind == 'module':
        source=ModuleDeclaration.from_dict(docs[roots['payload']])
        spec=None
    else:
        from ..public_agent_materials import public_agent_payload
        spec,source=public_agent_payload(docs[roots['payload']],normalized['target'])
        if contract['supported_shape']['agent_task_mode']!='single_stage_plugin_free': raise ValueError('AgentTask installed shape unsupported')
    lowered=with_bound_origin(source).to_dict()
    if canonical_json(lowered)!=canonical_json(docs[roots['declaration']]) or canonical_json(compiled.source.to_dict())!=canonical_json(lowered):
        raise ValueError('original bound Module/lowered wire mismatch')
    pairs={(kind,key) for kind,entries in compiled.registrations.items() for key in entries}
    lookup={(d['kind'],d['key']):d for d in declarations}
    for kind, entries in compiled.registrations.items():
        for key,d in entries.items():
            expected=lookup.get((kind,key))
            if expected is None and kind=='schema' and key in PROTECTED_SCHEMA_REFS: expected={'kind':'schema','key':key,'schema':schemas[key][1]}
            if canonical_json(d)!=canonical_json(expected): raise ValueError('compiler consumed undeclared or changed Registration')
    selected=w.validate(w.SELECTION,docs[roots['selection']])
    w.ordered(selected['selected_registrations'],lambda d:(d['kind'],d['key']))
    if selected['child_kind']!=child_kind or child_kind=='module' and selected['execution_profiles'] or pairs!={(d['kind'],d['key']) for d in selected['selected_registrations']}:
        raise ValueError('selected Registration differs from actual compiled declarations')
    slots=w.selected_slots(contract,pairs,child_kind)
    w.ordered(selected['selected_public_slots'],lambda d:d['slot_id'])
    selected_slots={s['slot_id']:s['node_id'] for s in selected['selected_public_slots']}
    if set(selected_slots)!={s['slot_id'] for s in slots}: raise ValueError('selected public slots not exact')
    normalized=docs[roots['normalized_request']]
    if set(normalized)!={'request','target'}: raise ValueError('normalized request shape')
    from .parent_child import REQUEST_SCHEMA
    from .schema_catalog import SchemaCatalog
    SchemaCatalog().validate_schema_ref(REQUEST_SCHEMA,normalized['request'])
    target_schema=json.loads(SchemaCatalog._mechanical_schema_path('registry_v1/parent_child_intent/v2').read_text())['properties']['target']
    _validate_resource_instance(target_schema,normalized['target'])
    if normalized['request']['child_kind']!=child_kind or canonical_json(normalized['request']['definition'])!=canonical_json(docs[roots['payload']]): raise ValueError('payload differs from exact request')
    config=normalized['request']['public_configuration']
    if child_kind=='module':
        operation_names=w.module_operation_shapes(source,declarations)
        if not operation_names or not set(operation_names)<=set(contract['supported_shape']['module_operations']):
            raise ValueError('compiled operation outside declared finite Module shape')
        if set(config)!={'configuration','inputs'}: raise ValueError('public configuration shape')
        for slot in slots:
            name=selected_slots[slot['slot_id']]
            if name not in nodes or nodes[name]['role']!=slot['role']: raise ValueError('selected public slot role mismatch')
            expected={'public_host':config['configuration'],'input':config['inputs'],'budget':{'budgets':lowered['budgets'],'budget_buckets':lowered['budget_buckets']}}[slot['role']]
            if canonical_json(docs[name])!=canonical_json(expected): raise ValueError('public slot body differs from request/lowering')
            if nodes[name]['body_schema_id']!=(None if slot['role']=='budget' else slot['body_schema_id']): raise ValueError('public slot schema mismatch')
    else:
        if set(config)!={'policy','opaque_bindings'} or w.canonical(config['opaque_bindings'])!=w.canonical(list(bindings)):
            raise ValueError('AgentTask exact public configuration/bindings mismatch')
    units=w.selected_units(contract,pairs);obs={}
    for name in roots['implementation']:
        value=w.validate(w.OBSERVATION,docs[name]);unit_id=value['unit_id']
        if unit_id in obs: raise ValueError('duplicate unit observation')
        obs[unit_id]=(name,value)
    if set(obs)!=units: raise ValueError('selected implementation closure differs')
    for unit in contract['implementation_units']:
        if unit['unit_id'] not in obs: continue
        name,value=obs[unit['unit_id']];w.ordered(value['assets'],lambda a:a['asset_id'])
        if (value['contract_id']!=contract['contract_id'] or value['contract_revision']!=contract['revision'] or value['installed_contract_sha256']!=w.sha(w.canonical(contract))
            or value['distribution']!=unit['distribution'] or value['mode']!=unit['mode'] or value['runtime']['abi_tag']!=unit['runtime_abi']
            or [a['asset_id'] for a in value['assets']]!=unit['asset_ids']): raise ValueError('unit observation contract join mismatch')
        dependencies={obs[d][0] for d in unit['dependency_unit_ids']}
        dependencies.add(schemas[w.OBSERVATION][0])
        for asset in value['assets']:
            aid=asset['inline_node_id']
            if unit['mode']=='inline_public_bytes':
                if aid not in nodes or nodes[aid]['role']!='implementation_asset' or nodes[aid]['sha256']!=asset['sha256'] or nodes[aid]['size']!=asset['size']: raise ValueError('inline implementation asset mismatch')
                dependencies.add(aid)
            elif aid is not None: raise ValueError('installed observation cannot imply registered implementation bytes')
        if set(nodes[name]['depends_on'])!=dependencies: raise ValueError('observation dependency edges mismatch')
    if child_kind=='agent_task':
        from ..public_agent_materials import agent_slot_values,agent_slot_kind,SPEC
        from cpn.llm_adapters.config import RegisteredLLMExecutionSelection
        policy=w.validate_policy(docs[roots['execution_policy'][0]])
        if w.canonical(policy)!=w.canonical(config['policy']): raise ValueError('public policy differs from exact request')
        selection=RegisteredLLMExecutionSelection(contract=contract,policy=policy,bindings=list(bindings),observations={uid:row[1] for uid,row in obs.items()})
        values=agent_slot_values(spec,selection,with_bound_origin(source))
        kinds=[agent_slot_kind(slot) for slot in slots]
        if sorted(kinds)!=sorted(values): raise ValueError('AgentTask public slot set differs')
        names={kind:selected_slots[slot['slot_id']] for slot,kind in zip(slots,kinds)}
        for slot,kind in zip(slots,kinds):
            name=names[kind];schema,value=values[kind]
            if name not in nodes or nodes[name]['role']!=slot['role'] or nodes[name]['body_schema_id']!=schema or w.canonical(docs[name])!=w.canonical(value):
                raise ValueError('AgentTask public slot body/schema/role differs')
        policy_id=dict(spec.registered_execution_sources)['default']
        profile={'profile_id':'default','policy_node_id':policy_id,'target_node_id':names['target'],'backend_node_id':names['backend'],'transport_node_id':names['transport']}
        if policy_id!=names['execution_policy'] or roots['execution_policy']!=[policy_id] or selected['execution_profiles']!=[profile]:
            raise ValueError('AgentTask Spec/default policy exact join differs')
        if roots['public_host']!=sorted(names[k] for k in ('target','backend','transport')) or roots['inputs']!=[names['input']] or roots['budgets']!=[names['budget']]:
            raise ValueError('AgentTask exact public roots differ')
    # The fixed producer ABI determines designated edges; arbitrary ref-shaped
    # business data never contributes graph edges.
    required = {
        roots['payload']:{roots['selection'],schemas['rpnh/module_declaration/v1' if child_kind=='module' else 'rpnh/agent_task_spec/v13'][0],*roots['execution_policy']},
        roots['declaration']:{roots['payload'],schemas['rpnh/module_declaration/v1'][0]},
        roots['registration']:set(roots['schemas'])|set(roots['implementation']),
        roots['lowered_net']:{roots['declaration'],roots['registration'],schemas['rpnh/executable_net/v1'][0]},
        roots['normalized_request']:{roots['payload']},
        roots['selection']:{schemas[w.SELECTION][0],*selected_slots.values()},
    }
    for name in roots['schemas']: required[name]=set()
    for name in (*roots['public_host'],*roots['inputs'],*roots['budgets']):
        sid=nodes[name]['body_schema_id']; required[name]=set() if sid is None else {schemas[sid][0]}
    if child_kind=='agent_task':
        for kind in ('target','backend','transport'):
            required[names[kind]].add(policy_id)
        credential_units={binding[role+'_unit_id'] for binding in bindings for role in ('resolver','renderer')}
        required[policy_id]={schemas[w.POLICY][0],*[obs[uid][0] for uid in {policy['implementation_unit_id'],*credential_units}]}
    used_inline={a['inline_node_id'] for _,v in obs.values() for a in v['assets'] if a['inline_node_id'] is not None}
    if used_inline!={n for n,v in nodes.items() if v['role']=='implementation_asset'}:raise ValueError('unused implementation bytes')
    for name in used_inline:required[name]=set()
    for name,edges in required.items():
        if set(nodes[name]['depends_on'])!=edges: raise ValueError('designated material dependencies differ')
    return docs,schemas,declarations


def validate_draft(draft):
    from ..public_module_materials import PublicMaterialDraft
    if type(draft) is not PublicMaterialDraft: raise TypeError('registered producer needs original public draft')
    docs,schemas,declarations=validate_documents(draft.contract,draft.inventory,draft.roots,dict(draft.payloads),draft.bindings)
    if docs[draft.roots['selection']]['child_kind']!=draft.child_kind: raise ValueError('draft payload kind mismatch')
    if canonical_json({'declarations':sorted(list(draft.registration.declarations()),key=lambda d:(d['kind'],d['key']))})!=canonical_json({'declarations':declarations}):
        raise ValueError('actual complete Registration changed after draft')
    return docs,schemas,declarations


@dataclass(frozen=True, slots=True)
class RegisteredPublicMaterialInventory:
    resource_ref: ResourceVersionRef
    root_bytes: bytes

    @property
    def root(self): return w.decode(self.root_bytes,canonical_required=True)


def publish_inventory(gateway,draft,*,command_id):
    if type(command_id) is not str or not command_id: raise ValueError('explicit owner command required')
    docs,schemas,declarations=validate_draft(draft)
    # Reconstruct the selected installation observation through the original
    # entry-point selector. A hand-created draft/self-reported hash is not an
    # observation merely because its Python type has the expected name.
    import importlib.metadata as metadata
    from ..public_module_materials import read_installed_contract
    selected=docs[draft.roots['selection']]
    pairs={(v['kind'],v['key']) for v in selected['selected_registrations']}
    entries=[ep for ep in metadata.entry_points(group='rpnh.environment_hosts') if ep.name==draft.contract['profile_id']]
    if len(entries)!=1:raise RegistryConflict('public material original installed selection unavailable')
    observed=read_installed_contract(entries[0],draft.child_kind,pairs)
    if observed.contract_bytes!=draft.contract_bytes:raise RegistryConflict('draft differs from selected installed contract')
    observed_units=dict(observed.observations)
    for name in draft.roots['implementation']:
        doc=docs[name]
        if observed_units.get(doc['unit_id'])!=dict(draft.payloads)[name]:raise RegistryConflict('draft contains unobserved implementation identity')
    for name,raw in observed.inline_assets:
        if dict(draft.payloads).get(name)!=raw:raise RegistryConflict('draft contains unobserved implementation bytes')
    core=gateway._core
    with core.event_store.connect() as db:
        db.execute('BEGIN');binding=_binding(db,core)
    if _ref_payload(gateway._task_ref)!=binding['task_ref'] or _ref_payload(gateway._bootstrap_ref)!=binding['bootstrap_command_ref']:
        raise RegistryConflict('material gateway differs from bound source')
    draft.registration.bind_schema_catalog(core.catalog);draft.registration.bind_gateway(gateway)
    used_schema_ids={n['body_schema_id'] for n in draft.inventory if n['body_schema_id']} | {d['key'] for d in declarations if d['kind']=='schema'} | set(w.MODULE_SCHEMAS)
    used_schema_ids.update(schemas)
    for sid in sorted(used_schema_ids):
        if sid in PROTECTED_SCHEMA_REFS: gateway.bind_builtin_schema(sid)
        elif sid not in gateway.schema_refs: raise ValueError('actual Gateway schema source missing')
    def publish(raw,schema,media,key,deps=()):
        return _publish_private_system(core,gateway._task_ref,PublishResource(origin=PrivateSystemOrigin(gateway._bootstrap_ref),payload=raw,
            media_type=media,content_schema_ref=schema,content_schema_authority_ref=None if schema is None or schema in PROTECTED_SCHEMA_REFS else gateway.schema_refs[schema],
            summary='Declared public material',lifetime_ref=gateway._bootstrap_ref,descriptors={'public_material_producer':w.PRODUCER},
            derived_from=deps,idempotency_key=key))
    contract=draft.contract;D=draft.public_material_digest;sha=w.sha(draft.contract_bytes)
    contract_key='public-material:v1:contract:'+w.sha(w.canonical({'contract_id':contract['contract_id'],'revision':contract['revision']}))
    contract_ref=publish(draft.contract_bytes,w.CONTRACT,'application/json',contract_key)
    node_refs={};nodes={n['node_id']:n for n in draft.inventory};payloads=dict(draft.payloads)
    def emit(name):
        if name in node_refs:return node_refs[name]
        n=nodes[name];deps=unique_refs([pair(emit(v)) for v in n['depends_on']])
        if n['role']=='schema':
            ref=gateway.schema_refs[docs[name]['$id']]
            if deps:raise ValueError('schema leaf cannot have arbitrary material dependencies')
        else:
            ref=publish(payloads[name],n['body_schema_id'],n['media_type'],'public-material:v1:leaf:'+sha+':'+D+':'+name,deps)
        node_refs[name]=ref;return ref
    for name in nodes:emit(name)
    root={'schema_version':w.INVENTORY,'producer_contract':w.PRODUCER,'source':_source(binding),
        'installed_contract_ref':pair(contract_ref),'installed_contract_sha256':sha,'child_kind':draft.child_kind,'content_inventory':draft.inventory,
        'node_refs':[{'node_id':n,'resource_ref':pair(node_refs[n])} for n in sorted(node_refs)],
        'declaration_refs':[{'kind':d['kind'],'key':d['key'],'resource_ref':pair(gateway.declaration_refs[d['kind'],d['key']])} for d in declarations],
        'schema_refs':[{'schema_id':sid,'resource_ref':pair(gateway.schema_refs[sid])} for sid in sorted(used_schema_ids)],
        'roots':draft.roots,'opaque_bindings':draft.bindings,'public_material_digest':D,'initial_declaration_digest':nodes[draft.roots['lowered_net']]['sha256']}
    w.validate(w.INVENTORY,root)
    dependencies=unique_refs([root['installed_contract_ref'],*[r['resource_ref'] for key in ('node_refs','declaration_refs','schema_refs') for r in root[key]]])
    ref=publish(w.canonical(root),w.INVENTORY,'application/json','public-material:v1:root:'+D,dependencies)
    return read_inventory(core,ref)


def _binding(db,context):
    row=read_source_binding(db,context.catalog,context.task_id)
    if row is None: raise RegistryConflict('public materials need original source binding')
    return json.loads(row['binding_metadata_json'])


def _source(binding):return {'source_id':binding['source_id'],'binding_ref':binding['binding_ref'],'task_ref':binding['task_ref'],'bootstrap_ref':binding['bootstrap_command_ref']}


def _exact_key(context,ref,binding,key,*,prospective=False):
    from .identities import fresh_bootstrap_resource_id
    from .publication import _stable_id
    bootstrap=_version_from_payload(binding['bootstrap_command_ref'])
    expected=ResourceVersionRef(fresh_bootstrap_resource_id(context.task_id,bootstrap.version_id,key),
        _stable_id('resource_version',context.task_id,key,'fresh_bootstrap_reference',bootstrap.version_id,bootstrap.version_id))
    if ref!=expected: raise RegistryConflict('material is not the exact original producer key')
    if not prospective:
        row=context.db.execute('SELECT t.idempotency_key FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id WHERE o.version_id=?',(str(ref.resource_version_id),)).fetchone()
        if row is None or row[0]!=key:raise RegistryConflict('material transaction key differs')


def read_inventory_at(context,ref,*,proposal=None,relations=(),events=(),transaction_id=None,command_key=None,_return_materials=False):
    db=context.db;binding=_binding(db,context)
    root_prepared=proposal if proposal is not None else exact_prepared(db,context.object_store,context.task_id,_ref_payload(ref.as_version_ref()))
    raw=context.object_store.read_registered(root_prepared)
    root=w.validate(w.INVENTORY,w.decode(raw,canonical_required=True))
    if (root['source']!=_source(binding) or root['child_kind'] not in ('module','agent_task') or root_prepared.metadata['content_schema_ref']!=w.INVENTORY
        or root_prepared.metadata['descriptors']!={'public_material_producer':w.PRODUCER}): raise RegistryConflict('registered inventory source/producer mismatch')
    dependencies=unique_refs([root['installed_contract_ref'],*[r['resource_ref'] for key in ('node_refs','declaration_refs','schema_refs') for r in root[key]]])
    _exact_key(context,ref,binding,'public-material:v1:root:'+root['public_material_digest'],prospective=proposal is not None)
    read_bootstrap_material(db,context,ref,binding,media_type='application/json',derived_from=dependencies,proposal=proposal,relations=relations,events=events,strict_events=True,transaction_id=transaction_id,command_key=command_key)
    closure=_MaterialReadClosure(db,context)
    total=len(raw);read_cache={}
    def leaf(p,media,deps=()):
        nonlocal total
        r=_resource_from_payload(p);key=canonical_json(p)
        value,meta=read_bootstrap_material(db,context,r,binding,media_type=media,derived_from=deps,strict_events=True)
        schema_validated=closure.resource(native(p))
        if value!=schema_validated:raise RegistryConflict('material bytes differ across same-cut readers')
        if key not in read_cache:total+=len(value);read_cache[key]=(value,meta)
        if total>w.AGGREGATE_CAP:raise RegistryConflict('registered closure aggregate cap')
        return value,meta
    contract_raw,contract_meta=leaf(root['installed_contract_ref'],'application/json')
    contract=w.validate_contract(w.decode(contract_raw,canonical_required=True))
    if contract_meta['content_schema_ref']!=w.CONTRACT or w.sha(contract_raw)!=root['installed_contract_sha256']:raise RegistryConflict('registered contract bytes mismatch')
    _exact_key(context,_resource_from_payload(root['installed_contract_ref']),binding,'public-material:v1:contract:'+w.sha(w.canonical({'contract_id':contract['contract_id'],'revision':contract['revision']})))
    w.ordered(root['node_refs'],lambda n:n['node_id']);w.ordered(root['declaration_refs'],lambda n:(n['kind'],n['key']));w.ordered(root['schema_refs'],lambda n:n['schema_id'])
    refs={r['node_id']:r['resource_ref'] for r in root['node_refs']};nodes={n['node_id']:n for n in root['content_inventory']}
    if set(refs)!=set(nodes) or len({canonical_json(r) for r in refs.values()})!=len(refs):raise RegistryConflict('node references are not exact one-to-one')
    payloads={};metas={}
    for name,n in nodes.items():
        if any(dep not in refs for dep in n['depends_on']):raise RegistryConflict('missing dependency ref')
        deps=unique_refs([refs[dep] for dep in n['depends_on']])
        value,meta=leaf(refs[name],n['media_type'],deps)
        if meta['content_schema_ref']!=n['body_schema_id']:raise RegistryConflict('node schema metadata differs')
        payloads[name]=value;metas[name]=meta
        if n['role']!='schema':
            _exact_key(context,_resource_from_payload(refs[name]),binding,'public-material:v1:leaf:'+root['installed_contract_sha256']+':'+root['public_material_digest']+':'+name)
            if meta['descriptors']!={'public_material_producer':w.PRODUCER}:raise RegistryConflict('leaf producer descriptor differs')
    docs,schemas,declarations=validate_documents(contract,root['content_inventory'],root['roots'],payloads,root['opaque_bindings'])
    if docs[root['roots']['selection']]['child_kind']!=root['child_kind']: raise RegistryConflict('root/selection payload kind mismatch')
    declared={(d['kind'],d['key']):d for d in declarations}
    if {(d['kind'],d['key']) for d in root['declaration_refs']}!=set(declared):raise RegistryConflict('full declaration references differ')
    schema_refs={r['schema_id']:r['resource_ref'] for r in root['schema_refs']}
    expected_schema_ids={n['body_schema_id'] for n in nodes.values() if n['body_schema_id']}|set(schemas)|set(w.MODULE_SCHEMAS)|{d['key'] for d in declarations if d['kind']=='schema'}
    if set(schema_refs)!=expected_schema_ids:raise RegistryConflict('full schema authority references differ')
    for d in root['declaration_refs']:
        original=declared[d['kind'],d['key']];is_schema=d['kind']=='schema'
        _exact_key(context,_resource_from_payload(d['resource_ref']),binding,'host-registration:'+d['kind']+':'+d['key'])
        value,meta=leaf(d['resource_ref'],'application/schema+json' if is_schema else 'application/json')
        if value!=canonical_json(original['schema'] if is_schema else original) or meta['descriptors'].get('host_registration_kind')!=d['kind'] or meta['descriptors'].get('registered_key')!=d['key']:
            raise RegistryConflict('actual Gateway declaration differs')
        if is_schema and d['resource_ref']!=schema_refs[d['key']]:raise RegistryConflict('schema and declaration ref identity mismatch')
    for sid,p in schema_refs.items():
        _exact_key(context,_resource_from_payload(p),binding,('builtin-schema-source:' if sid in PROTECTED_SCHEMA_REFS else 'host-registration:schema:')+sid)
        value,meta=leaf(p,'application/schema+json')
        if w.decode(value).get('$id')!=sid or meta['descriptors'].get('registered_key')!=sid:raise RegistryConflict('schema resource identity mismatch')
        if sid in PROTECTED_SCHEMA_REFS:
            expected = json.loads(context.catalog.schema_path(sid).read_text())
            if value != canonical_json(expected) or meta['descriptors'].get('mechanical_schema') is not True:
                raise RegistryConflict('protected schema source differs from original builtin bytes')
        if sid in schemas and (p!=refs[schemas[sid][0]] or value!=payloads[schemas[sid][0]]):raise RegistryConflict('selected schema exact reference mismatch')
    for name,meta in metas.items():
        sid=nodes[name]['body_schema_id']; authority=meta['content_schema_authority_ref']
        if sid is not None and sid not in PROTECTED_SCHEMA_REFS and authority!=schema_refs[sid]:
            raise RegistryConflict('leaf application schema is not the exact selected authority')
    D=w.public_digest(root['child_kind'],root['installed_contract_sha256'],root['content_inventory'],root['roots'],root['opaque_bindings'])
    if root['public_material_digest']!=D or root['initial_declaration_digest']!=nodes[root['roots']['lowered_net']]['sha256']:raise RegistryConflict('material digest differs')
    # Proposal schema authority is the same existing catalog terminal; no recursion.
    authority=root_prepared.metadata['content_schema_authority_ref']
    catalog=closure.descriptor(authority)
    if json.loads(catalog['schemas'][w.INVENTORY]['source'])!=json.loads(context.catalog.schema_path(w.INVENTORY).read_text()):raise RegistryConflict('root catalog authority differs')
    result=RegisteredPublicMaterialInventory(ref,raw)
    if _return_materials:
        return VerifiedMaterialRead(result,contract_raw,tuple(sorted(payloads.items())))
    return result


def read_inventory(core,ref):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return read_inventory_at(_CandidateReadContext.from_core(core,db),ref)


def validate_public_material_commit(store,db,*,task_id,branch_id,objects,events,relations,existing,idempotency_key,transaction_id,task_round_id,net_instance_id,extra_commands):
    def signal(item):return item.object_type=='resource_version/v1' and (item.metadata.get('content_schema_ref') in w.SCHEMAS or item.metadata.get('descriptors',{}).get('public_material_producer')==w.PRODUCER)
    previous=[] if existing is None else list(db.execute('SELECT object_type,metadata_json,logical_id,version_id FROM objects WHERE transaction_id=?',(existing['transaction_id'],)))
    prior_signal=any(r['object_type']=='resource_version/v1' and (json.loads(r['metadata_json']).get('content_schema_ref') in w.SCHEMAS or json.loads(r['metadata_json']).get('descriptors',{}).get('public_material_producer')==w.PRODUCER) for r in previous)
    selected=[item for item in objects if signal(item)]
    if not selected and not prior_signal and not idempotency_key.startswith('public-material:v1:'):return
    if len(objects)!=1 or len(selected)!=1 or task_round_id is not None or net_instance_id is not None or extra_commands:raise RegistryConflict('material producer requires one exact private-system publication')
    context=_CandidateReadContext.from_event_store(store,db,task_id=task_id,branch_id=branch_id);item=selected[0]
    binding=_binding(db,context);ref=ResourceVersionRef(item.logical_id,item.version_id)
    if item.metadata.get('descriptors')!={'public_material_producer':w.PRODUCER}:raise RegistryConflict('reserved material schema requires original material producer')
    if item.metadata['content_schema_ref']==w.INVENTORY:
        read_inventory_at(context,ref,proposal=item,relations=relations,events=events,transaction_id=transaction_id,command_key=idempotency_key)
        if existing is not None:read_inventory_at(context,ref)
        root=w.decode(context.object_store.read_registered(item),canonical_required=True)
        if idempotency_key!='public-material:v1:root:'+root['public_material_digest']:raise RegistryConflict('root idempotency identity mismatch')
    else:
        deps=tuple(_resource_from_payload({'resource_id':v['logical_id'],'resource_version_id':v['version_id']}) for v in item.metadata['reference_provenance']['derived_from_refs'])
        raw,_=read_bootstrap_material(db,context,ref,binding,media_type=item.media_type,derived_from=deps,proposal=item,relations=relations,events=events,strict_events=True,transaction_id=transaction_id,command_key=idempotency_key)
        if item.metadata['content_schema_ref'] in w.SCHEMAS:w.validate(item.metadata['content_schema_ref'],w.decode(raw,canonical_required=True))
        if item.metadata['content_schema_ref']==w.CONTRACT:
            contract=w.validate_contract(w.decode(raw,canonical_required=True))
            expected_key='public-material:v1:contract:'+w.sha(w.canonical({'contract_id':contract['contract_id'],'revision':contract['revision']}))
            if deps or idempotency_key!=expected_key:raise RegistryConflict('contract immutable command identity differs')
            _exact_key(context,ref,binding,expected_key,prospective=True)
        else:
            import re
            if re.fullmatch(r'public-material:v1:leaf:[a-f0-9]{64}:[a-f0-9]{64}:[a-z][a-z0-9_.:-]{0,127}',idempotency_key) is None:
                raise RegistryConflict('material leaf command identity differs')
            _exact_key(context,ref,binding,idempotency_key,prospective=True)
        if existing is not None:read_bootstrap_material(db,context,ref,binding,media_type=item.media_type,derived_from=deps,strict_events=True)


def validate_resource_replay(core,ref):
    """Called before the original helper's early replay return."""
    prepared=core.get_version(ref.resource_version_id)
    if prepared.metadata.get('content_schema_ref')==w.INVENTORY:return read_inventory(core,ref)
    if prepared.metadata.get('descriptors',{}).get('public_material_producer')==w.PRODUCER:
        with core.event_store.connect() as db:
            db.execute('BEGIN');context=_CandidateReadContext.from_core(core,db);binding=_binding(db,context)
            deps=tuple(_resource_from_payload({'resource_id':v['logical_id'],'resource_version_id':v['version_id']}) for v in prepared.metadata['reference_provenance']['derived_from_refs'])
            read_bootstrap_material(db,context,ref,binding,media_type=prepared.media_type,derived_from=deps,strict_events=True)


@dataclass(frozen=True,slots=True)
class VerifiedMaterialRead:
    inventory: RegisteredPublicMaterialInventory
    contract_bytes: bytes
    payloads: tuple[tuple[str,bytes],...]

    @property
    def documents(self):
        nodes={n['node_id']:n for n in self.inventory.root['content_inventory']}
        return {name:w.decode(raw,canonical_required=nodes[name]['encoding']==w.C_ENCODING)
                for name,raw in self.payloads if nodes[name]['encoding']!=w.OPAQUE_ENCODING}


def read_verified_materials(core,ref):
    with core.event_store.connect() as db:
        db.execute('BEGIN')
        return read_inventory_at(_CandidateReadContext.from_core(core,db),ref,_return_materials=True)
