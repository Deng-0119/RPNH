"""Producer-persisted PN projections and grouped author correspondence.

Publishing is an explicit owner operation. Reading verifies stored inventories
and exact dependency bytes, never calls a component lowerer or launches a HOST.
Legacy revisions with no stored projection remain explicitly unavailable.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from ..registry.schema_catalog import canonical_json, canonical_text
from .registry_typed_readers import TypedReadError, wire_ref, prepared_at, payload_at, descriptor_at

SCHEMA = 'rpnh/public_pn_projection/v1'
STORED_SCHEMA = 'rpnh/stored_public_pn_projection/v1'
PRODUCER_CONTRACTS = ('rpnh/closed_author_public_pn/v1', 'rpnh/plain_transform_public_pn/v1', 'rpnh/assembly_public_pn/v9')
NODE_FIELDS = frozenset({'id','label','kind','category','hidden_by_default','operation','operation_id','executor',
    'inputs','outputs','token_kind','capacity','schema'})
EDGE_FIELDS = frozenset({'id','source','target','kind','mode','weight','outcome','hidden_by_default','resource',
    'direction','emit','forward_source'})


def _key(reference):
    return 'public-pn:' + canonical_text(reference.to_dict() if hasattr(reference, 'to_dict') else reference)


def projection_resource_ref(core, reference, *, binding):
    from .assembly_v2 import _material_ref
    return _material_ref(core, binding, _key(reference))


def _safe_graph(compiled):
    from ..inspection import project_compiled_net, project_compiled_boundaries
    graph = project_compiled_net(compiled, source={'mode':'initial_configured'})
    return ([{k:v for k,v in n.items() if k in NODE_FIELDS} for n in graph['nodes']],
            [{k:v for k,v in e.items() if k in EDGE_FIELDS} for e in graph['edges']],
            project_compiled_boundaries(compiled))


def _lowering(compiled, elements, nodes, edges):
    from ._assembly_lowering import _declaration_targets, resolve_pointer
    pointers, _ = _declaration_targets(compiled.source, compiled)
    inventory = compiled.to_dict()
    result = []
    for item in elements['elements']:
        selected = set()
        for pointer in pointers[item['locator']]:
            if pointer.startswith(('/symbolic/places/', '/symbolic/transitions/')):
                selected.add(resolve_pointer(inventory, pointer)['name'])
        # Root owns the declared net as a group; it does not assert pairwise
        # identity among its component nodes.
        if item['locator'] == '/':
            selected.update(n['id'] for n in nodes)
        transitions = {n['id'] for n in nodes if n['kind'] == 'transition' and n['id'] in selected}
        selected_edges = {e['id'] for e in edges if e['source'] in transitions or e['target'] in transitions}
        result.append({'element_id':item['element_id'],'kind':item['kind'],'locator':item['locator'],
            'nodes':sorted(selected),'edges':sorted(selected_edges),'occurrence_path':[]})
    return result



def _edge_roles(nodes, edges, lowering):
    """Exact declaration ownership and arc role, never name-similarity matching."""
    kinds={node['id']:node['kind'] for node in nodes}
    result=[]
    for edge in edges:
        transition=edge['source'] if kinds[edge['source']]=='transition' else edge['target']
        place=edge['target'] if edge['source']==transition else edge['source']
        operations=[row['element_id'] for row in lowering if row['kind']=='operation' and transition in row['nodes']]
        ports=sorted({row['element_id'] for row in lowering if row['kind']=='port' and place in row['nodes']})
        if len(operations)!=1 or not ports:continue
        role={key:edge.get(key) for key in ('kind','mode','outcome','emit','forward_source')}
        role['direction']='output' if edge['source']==transition else 'input'
        result.append({'edge_id':edge['id'],'operation_element_id':operations[0],
            'place_element_ids':ports,'role':role})
    return result


def _groups(revision, elements, parent=None, transform=None, transform_ref=None):
    if parent is None: return [], None
    source = parent.revision.revision_ref.to_dict()
    target = revision.revision_ref.to_dict()
    old = {r['element_id'] for r in parent.element_map['elements']}
    current = {r['element_id'] for r in elements['elements']}
    evidence = [revision.element_mapping_ref.to_dict(), parent.revision.element_mapping_ref.to_dict()]
    groups = []
    def add(kind, left, right, refs=evidence):
        groups.append({'source_revision_ref':source,'target_revision_ref':target,'relation_kind':kind,
            'source_element_ids':sorted(left),'target_element_ids':sorted(right),
            'semantic_claim':'author_correspondence','evidence_refs':deepcopy(refs)})
    for identity in sorted(old & current): add('retained_author_element',[identity],[identity])
    for item in elements['elements']:
        if item['copied_from'] is not None:
            add('copied_from',[item['copied_from']['element_id']],[item['element_id']])
    if transform is not None:
        for group in transform['transform_groups']:
            add(group['kind'],group['source_element_ids'],group['target_element_ids'],[*evidence,transform_ref.to_dict()])
        created, removed = transform['created_element_ids'], transform['removed_element_ids']
    else:
        copied = {i['element_id'] for i in elements['elements'] if i['copied_from'] is not None}
        created, removed = sorted(current-old-copied), sorted(old-current)
    closure = {'source_revision_ref':source,'target_revision_ref':target,'created_element_ids':created,
        'removed_element_ids':removed,'coverage':'complete_author_element_partition','evidence_refs':evidence}
    return groups, closure


def _native_capability_roles(compiled, elements):
    """Recognize the recorded native v1 recipe, without calling its lowerer.

    Local names only address exact declaration/fragment bindings. They never
    establish correspondence across revisions. Semantics stay inside this
    reader; capability/config bodies are not added to the public projection.
    """
    from dataclasses import asdict
    from ..petri_contracts import (PNFragment, PlaceDeclaration, InitialTokenDeclaration,
        TransitionDeclaration, ArcDeclaration, PortDeclaration, PortBinding)
    ids = {row['locator']: row['element_id'] for row in elements['elements']}
    _, edges, _ = _safe_graph(compiled)
    roles = {}
    for component in compiled.source.components:
        if component.key != 'rpnh/native-plugin-operation/v1' or len(component.operations) != 1:
            continue
        registered = compiled.registrations['component'][component.key]
        if registered['identity'] != {'implementation_id': 'rpnh.native_plugin_component', 'revision': 'v1'}:
            continue
        operation = component.operations[0]
        binding = component.config.get('native_plugin', {})
        capability = binding.get('capability_port')
        ports = {port.name: port for port in component.ports}
        if (set(ports) != {'request', 'result'} or not capability
                or canonical_json(operation.config) != canonical_json(component.config)
                or operation.inputs != ('request', capability) or operation.outputs != ('result',)
                or operation.request_port is not None or [o.name for o in operation.outcomes] != ['complete']):
            continue
        expected = PNFragment(
            places=(PlaceDeclaration('request', ports['request'].schema),
                PlaceDeclaration('result', ports['result'].schema),
                PlaceDeclaration(capability, binding['capability_schema'], capacity=1,
                    initial_tokens=(InitialTokenDeclaration(schema=binding['capability_schema'],
                        value=binding['capability']),))),
            transitions=(TransitionDeclaration(operation.name, operation.name),),
            arcs=(ArcDeclaration('request', operation.name, 'input'),
                ArcDeclaration(capability, operation.name, 'input', mode='read'),
                ArcDeclaration('result', operation.name, 'output', mode='produce', outcome='complete')),
            ports=(PortBinding('request', 'request'), PortBinding('result', 'result')),
            operations=component.operations,
            internal_ports=(PortDeclaration(capability, 'input', binding['capability_schema']),),
            internal_bindings=(PortBinding(capability, capability),))
        if canonical_json(asdict(compiled.fragments[component.name])) != canonical_json(asdict(expected)):
            continue
        port = next((p for p in compiled.ports if p.name == f'{component.name}.{capability}' and not p.public), None)
        if port is None:
            continue
        # Shared/fused carriers are outside this single generated-place recipe.
        if sum(p.place == port.place for p in compiled.ports) != 1:
            continue
        transition = f'{component.name}.{operation.name}'
        reads = [edge for edge in edges if edge['source'] == port.place
            and edge['target'] == transition and edge['kind'] == 'arc' and edge['mode'] == 'read']
        if len(reads) != 1:
            continue
        operation_doc = asdict(operation)
        operation_doc.pop('name')
        schemas = {p.schema for p in component.ports} | {component.config_schema, binding['capability_schema']}
        semantics = {'component_registration': registered, 'config': component.config,
            'operation': operation_doc, 'ports': [asdict(p) for p in component.ports],
            'executor_registration': compiled.registrations['executor'][operation.executor],
            'schemas': {key: compiled.registrations['schema'][key] for key in sorted(schemas)},
            'tools': {key: compiled.registrations['tool'][key] for key in operation.tools}}
        roles[ids[f'/components/{component.name}/operations/{operation.name}']] = {
            'node': port.place, 'edge': reads[0]['id'], 'semantics': semantics}
    return roles


def _generated_correspondences(core, result, compiled, elements, parent, snapshot):
    """Direct verified author derivation plus exact native lowering semantics.

    Only two generated subjects are covered. This is neither author element
    identity nor runtime/token continuity, and does not compose parent history.
    """
    if parent is None or result['producer_contract'] == PRODUCER_CONTRACTS[2]:
        return []
    current = _native_capability_roles(compiled, elements)
    groups = [g for g in result['mapping_groups'] if g['relation_kind'] in
        {'copied_from', 'retained_author_element'} and len(g['source_element_ids']) == 1
        and len(g['target_element_ids']) == 1 and g['target_element_ids'][0] in current]
    if not groups:
        return []
    source_ref = parent.revision.revision_ref.to_dict()
    try:
        # Verify the parent's own exact definition/boundary/HOST/fragment closure;
        # no recursion through its ancestry or invocation of compiler/HOST code.
        previous = _read_topology_only(core, source_ref, snapshot=snapshot)
    except Exception as exc:
        if getattr(exc, 'code', None) in {'PROJECTION_UNAVAILABLE', 'UNSUPPORTED_READER_VERSION',
                'MATERIAL_ACCESS_NOT_GRANTED', 'NOT_DISCLOSED'}:
            return []
        raise
    if previous['producer_contract'] != PRODUCER_CONTRACTS[0]:
        return []
    from ..executable_net import _load_compiled_net_offline
    raw, _ = payload_at(core, previous['projection_ref'], snapshot, material=True,
        max_bytes=getattr(snapshot, 'max_material_bytes', 4*1024*1024))
    stored = json.loads(raw)
    old = _native_capability_roles(_load_compiled_net_offline(stored['compiled']), stored['elements'])
    rows = []
    for group in groups:
        if group['source_revision_ref'] != source_ref or group['target_revision_ref'] != result['author_revision_ref']:
            continue
        left = old.get(group['source_element_ids'][0])
        right = current[group['target_element_ids'][0]]
        if left is None or canonical_json(left['semantics']) != canonical_json(right['semantics']):
            continue
        for kind in ('node', 'edge'):
            rows.append({'source_revision_ref': source_ref, 'target_revision_ref': result['author_revision_ref'],
                'relation_kind': group['relation_kind'], 'semantic_claim': 'author_correspondence',
                'subject_kind': kind, 'source_subject_id': left[kind], 'target_subject_id': right[kind],
                'source_occurrence_path': [], 'target_occurrence_path': [],
                'source_operation_element_id': group['source_element_ids'][0],
                'target_operation_element_id': group['target_element_ids'][0],
                'verification_contract': 'rpnh/native_capability_derivation/v1',
                'evidence_refs': [*deepcopy(group['evidence_refs']), previous['projection_ref'], result['projection_ref']]})
    return rows


def make_public_projection(revision, compiled, elements, *, producer_contract, parent=None,
                           transform=None, transform_ref=None, assembly_plan=None, assembly_mapping=None,
                           generated_revision_ref=None, mapping_ref=None, configuration_refs=None):
    if producer_contract not in PRODUCER_CONTRACTS:
        raise ValueError('unsupported public projection producer')
    nodes, edges, boundaries = _safe_graph(compiled)
    lowering = _lowering(compiled, elements, nodes, edges)
    groups, closure = ([], None) if assembly_plan is not None else _groups(revision,elements,parent,transform,transform_ref)
    root = {'kind':'module','selector':{'declaration_ref':revision.revision_ref.to_dict(),'occurrence_path':[],
        'element_id':next(row['element_id'] for row in elements['elements'] if row['locator']=='/')}}
    hierarchy = [{'scope':root,'parent_scope':None,'label':compiled.source.name,
        'node_ids':[n['id'] for n in nodes],'edge_ids':[e['id'] for e in edges],'evidence_refs':[]}]
    if assembly_plan is not None:
        ids={row['locator']:row['element_id'] for row in lowering}
        for row in lowering:
            members={(origin['member_id'],canonical_text(origin['revision_ref'])) for origin in assembly_mapping['origins']
                if row['locator']!='/' and row['locator'] in origin['declaration_locators']}
            if len(members)==1:
                member,reference=next(iter(members))
                row['occurrence_path']=[{'declaration_ref':json.loads(reference),'member_id':member}]
        for origin in assembly_mapping['origins']:
            groups.append({'source_revision_ref':origin['revision_ref'],'target_revision_ref':(generated_revision_ref or revision.revision_ref).to_dict(),
                'relation_kind':'copied_from','source_element_ids':[origin['element_id']],
                'target_element_ids':sorted({ids[loc] for loc in origin['declaration_locators']}),
                'semantic_claim':'author_correspondence','evidence_refs':[mapping_ref.to_dict()]})
        # Exact actual-final-context lowering is retained. This producer does
        # not assert member-alone PN identity: context-sensitive lowerers may
        # genuinely change the fragment when composed.
        for member in assembly_plan['members']:
            locators = {loc for row in assembly_mapping['origins'] if row['member_id']==member['member_id']
                and row['kind'] != 'module' for loc in row['declaration_locators']}
            member_nodes = sorted({n for row in lowering if row['locator'] in locators for n in row['nodes']})
            member_edges = sorted({e for row in lowering if row['locator'] in locators for e in row['edges']})
            hierarchy.append({'scope':{'kind':'subnet','selector':{'declaration_ref':revision.revision_ref.to_dict(),
                'occurrence_path':[{'declaration_ref':member['revision_ref'],'member_id':member['member_id']}],
                'subnet_id':member['member_id']}},'parent_scope':root,
                'label':member['display_name'],'node_ids':member_nodes,'edge_ids':member_edges,
                'evidence_refs':[mapping_ref.to_dict()]})
    return {'schema_version':SCHEMA,'target_ref':revision.revision_ref.to_dict(),
        'producer_contract':producer_contract,'nodes':nodes,'edges':edges,'boundaries':boundaries,
        'configuration':(deepcopy(configuration_refs) if configuration_refs is not None else {
            'host_requirements_ref':revision.host_requirements_ref.to_dict(),
            'declared_configuration_ref':revision.definition_ref.to_dict()}),'hierarchy':hierarchy,
        'author_revision_ref':(generated_revision_ref or revision.revision_ref).to_dict(),
        'lowering':lowering,'edge_roles':_edge_roles(nodes,edges,lowering),'mapping_groups':groups,'mapping_closure':closure,
        'assembly_origins':({key:deepcopy(assembly_mapping[key]) for key in ('schema_version','origins','introduced',
            'declarations','place_aliases','origin_contract','carrier_origins') if key in assembly_mapping}
            if assembly_mapping is not None else None),
        'runtime':{'coverage':'not_provided'}}


def publish_public_projection(author, value, *, parent=None, producer_contract=PRODUCER_CONTRACTS[0]):
    """Called only after the producer validated the exact publication inputs."""
    from ..registry.resource_service import _publish_private_system
    from ..registry.resources import PrivateSystemOrigin, PublishResource
    from .references import SourceQualifiedResourceRef
    revision = value.revision
    assembly = producer_contract == PRODUCER_CONTRACTS[2]
    source_value = value.generated if assembly else value
    projection = make_public_projection(revision, value.compiled, source_value.element_map,
        producer_contract=producer_contract, parent=parent,
        transform=getattr(value,'transformation',None),transform_ref=getattr(value,'transform_map_ref',None),
        assembly_plan=value.plan if assembly else None,assembly_mapping=value.lowering_map if assembly else None,
        generated_revision_ref=source_value.revision.revision_ref if assembly else None,
        mapping_ref=revision.lowering_mapping_ref if assembly else None,
        configuration_refs={'host_requirements_ref':source_value.revision.host_requirements_ref.to_dict(),
            'declared_configuration_ref':source_value.revision.definition_ref.to_dict()})
    references = [getattr(source_value.revision, name) for name in ('definition_ref','element_mapping_ref',
        'boundary_mapping_ref','host_requirements_ref')]
    references.extend(SourceQualifiedResourceRef.from_dict(row['resource_ref'],catalog=author.core.catalog)
        for row in source_value.host_requirements['declaration_refs'])
    if parent is not None:
        references.extend([parent.revision.element_mapping_ref,parent.revision.definition_ref])
    if hasattr(value,'transform_map_ref'): references.extend([value.transform_map_ref,value.command_ref])
    if assembly:
        references.extend([revision.plan_ref,revision.compiled_inventory_ref,revision.lowering_mapping_ref])
        for member in value.plan['members']:
            references.extend(SourceQualifiedResourceRef.from_dict(item['resource_ref'],catalog=author.core.catalog)
                for item in member['resolution']['original_materials'])
            references.append(projection_resource_ref(author.core,member['revision_ref'],binding=author.binding))
    dependencies = []
    for ref in sorted(set(references), key=lambda r:canonical_text(r.to_dict())):
        prepared = author.core.get_version(ref.ref.resource_version_id)
        raw = author.core.object_store.read_registered(prepared)
        dependencies.append({'ref':ref.to_dict(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    document = {'schema_version':STORED_SCHEMA,'projection':projection,'compiled':value.compiled.to_dict(),
        'elements':source_value.element_map,'dependencies':dependencies}
    payload = canonical_json(document)
    resource = _publish_private_system(author.core,author.gateway._task_ref,PublishResource(
        origin=PrivateSystemOrigin(author.gateway._bootstrap_ref),payload=payload,media_type='application/json',
        content_schema_ref=None,summary='Stored public PN projection',lifetime_ref=author.gateway._bootstrap_ref,
        descriptors={'public_pn_revision_ref':canonical_text(revision.revision_ref.to_dict()),
            'public_pn_contract':producer_contract,'content_sha256':hashlib.sha256(payload).hexdigest()},
        idempotency_key=_key(revision.revision_ref)))
    return SourceQualifiedResourceRef(author.binding['source_id'],resource)


def _projection_row(core, entry_ref, snapshot):
    target = entry_ref.to_dict() if hasattr(entry_ref,'to_dict') else entry_ref
    candidates = []
    for row in snapshot.objects.values():
        if row['object_type'] != 'resource_version/v1': continue
        metadata = json.loads(row['metadata_json']) if 'metadata_json' in row else row['metadata']
        if metadata.get('descriptors',{}).get('public_pn_revision_ref') == canonical_text(target):
            candidates.append(row)
    if not candidates: raise TypedReadError('PROJECTION_UNAVAILABLE')
    if len(candidates) != 1: raise TypedReadError('INTEGRITY_FAILED')
    row = candidates[0]
    from .registry_typed_readers import qualify
    return qualify(target['source_id'],{'resource_id':row['logical_id'],'resource_version_id':row['version_id']})


def _read_public_projection_complete(core, entry_ref, *, snapshot):
    from ..executable_net import _load_compiled_net_offline
    target = entry_ref.to_dict() if hasattr(entry_ref,'to_dict') else entry_ref
    _, local = wire_ref(target)
    if local['entity_type'] in {'net_instance/v1','marking_checkpoint/v1'}:
        return _read_runtime_projection(core,target,snapshot)
    if local['entity_type'] not in {'collaboration_net_revision/v1','collaboration_assembly_revision/v9'}:
        raise TypedReadError('UNSUPPORTED_READER_VERSION')
    record = descriptor_at(core,target,snapshot)
    projection_ref = _projection_row(core,target,snapshot)
    raw, prepared = payload_at(core,projection_ref,snapshot,material=True,
        max_bytes=getattr(snapshot,'max_material_bytes',4*1024*1024))
    metadata = prepared.metadata
    if metadata.get('origin_kind') != 'private_system' or not metadata.get('descriptors',{}).get('content_sha256'):
        raise TypedReadError('INTEGRITY_FAILED')
    from ..registry.publication import _version_from_payload,_fresh_bootstrap_reference_resource_metadata
    from ..registry.resources import ResourceVersionRef
    from .registry_typed_readers import qualify
    bootstrap=metadata['origin']['primary_ref']
    expected_ref=projection_resource_ref(core,target,binding={'source_id':target['source_id'],'bootstrap_command_ref':bootstrap})
    if expected_ref.to_dict()!=projection_ref:raise TypedReadError('INTEGRITY_FAILED')
    descriptor_at(core,qualify(target['source_id'],bootstrap),snapshot)
    expected_metadata=_fresh_bootstrap_reference_resource_metadata(core,ref=expected_ref.ref,
        task_ref=_version_from_payload(metadata['task_ref']),bootstrap_ref=_version_from_payload(bootstrap),
        lifetime_ref=_version_from_payload(bootstrap),payload_size=len(raw),media_type='application/json',
        content_schema_ref=None,content_schema_authority_ref=None,summary='Stored public PN projection',
        descriptors=metadata['descriptors'],extensions={},derived_from=())
    if canonical_json(metadata)!=canonical_json(expected_metadata) or prepared.producer_invocation_id is not None:
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        stored = json.loads(raw)
        if set(stored) != {'schema_version','projection','compiled','elements','dependencies'} or stored['schema_version'] != STORED_SCHEMA:
            raise ValueError('unknown projection')
        result = stored['projection']
        if result['schema_version'] != SCHEMA or result['target_ref'] != target or result['producer_contract'] not in PRODUCER_CONTRACTS:
            raise ValueError('wrong exact projection')
        if metadata['descriptors']['public_pn_contract'] != result['producer_contract']:
            raise ValueError('wrong producer')
        total=len(raw);seen=set()
        for dep in stored['dependencies']:
            key=canonical_json(dep['ref'])
            if key in seen:raise ValueError('duplicate projection dependency')
            seen.add(key)
            total+=dep['bytes']
            if total>getattr(snapshot,'max_material_bytes',4*1024*1024):raise TypedReadError('LIMIT_EXCEEDED')
            body,_ = payload_at(core,dep['ref'],snapshot,material=True,
                max_bytes=getattr(snapshot,'max_material_bytes',4*1024*1024))
            if len(body)!=dep['bytes'] or hashlib.sha256(body).hexdigest()!=dep['sha256']:
                raise ValueError('dependency integrity')
        compiled = _load_compiled_net_offline(stored['compiled'])
        nodes,edges,boundaries = _safe_graph(compiled)
        if (result['nodes'] != nodes or result['edges'] != edges or result['boundaries'] != boundaries
            or (result['producer_contract']!=PRODUCER_CONTRACTS[2] and result['lowering'] != _lowering(compiled,stored['elements'],nodes,edges))):
            raise ValueError('projection differs from stored compiler inventory')
        # The stored source/elements must be the exact revision's materials.
        snapshot.check_authorized(result['author_revision_ref'],'record')
        source_record = descriptor_at(core,result['author_revision_ref'],snapshot)
        if source_record['definition_kind']!='closed_module' or source_record.get('open_region_contract_ref') is not None:
            raise TypedReadError('UNSUPPORTED_READER_VERSION')
        for field, expected in [('definition_ref',compiled.source.to_dict()),('element_mapping_ref',stored['elements'])]:
            payload,_ = payload_at(core,source_record[field],snapshot,material=True)
            if canonical_json(json.loads(payload)) != canonical_json(expected):
                raise ValueError('source binding differs')
        from types import SimpleNamespace
        from .references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
        from .authoring import NetRevision
        parent = None
        for parent_ref in source_record.get('parent_revision_refs', []):
            snapshot.check_authorized(parent_ref,'record')
            parent_record = descriptor_at(core,parent_ref,snapshot)
            parent_raw,_ = payload_at(core,parent_record['element_mapping_ref'],snapshot,material=True)
            parent = SimpleNamespace(revision=NetRevision.from_dict(parent_record,catalog=core.catalog),
                element_map=json.loads(parent_raw))
        from .materials import _element_map,_boundaries,_command_material
        ids={row['locator']:row['element_id'] for row in stored['elements']['elements']}
        copies={row['element_id']:row['copied_from']['element_id'] for row in stored['elements']['elements'] if row['copied_from'] is not None}
        if _element_map(compiled.source,ids,parent,copies)!=stored['elements']:
            raise ValueError('author element declarations differ')
        boundary=json.loads(payload_at(core,source_record['boundary_mapping_ref'],snapshot,material=True)[0])
        host=json.loads(payload_at(core,source_record['host_requirements_ref'],snapshot,material=True)[0])
        if boundary!=_boundaries(compiled.source,stored['elements']) or host['registrations']!=stored['compiled']['registrations']:
            raise ValueError('author boundary/HOST differs from compiler inventory')
        for declaration in host['declaration_refs']:
            value=json.loads(payload_at(core,declaration['resource_ref'],snapshot,material=True)[0])
            expected_declaration=host['registrations'][declaration['kind']][declaration['key']]
            if declaration['kind']=='schema':expected_declaration=expected_declaration['schema']
            if canonical_json(value)!=canonical_json(expected_declaration):raise ValueError('HOST declaration bytes differ')
        definition_meta=prepared_at(core,source_record['definition_ref'],snapshot).metadata
        if result['producer_contract']!=PRODUCER_CONTRACTS[1]:
            source_typed=NetRevision.from_dict(source_record,catalog=core.catalog)
            expected_command=_command_material(source_id=target['source_id'],owner=source_typed.owner_task_ref,
                producer=source_typed.producer_principal_ref,command_id=source_typed.command_id,
                parents=source_typed.parent_revision_refs,documents=(compiled.source.to_dict(),stored['elements'],boundary,host))
            if definition_meta['descriptors']!={'closed_author_command_v1':expected_command}:
                raise ValueError('ordinary author command differs')
        transform = transform_ref = None
        if result['producer_contract'] == PRODUCER_CONTRACTS[1]:
            for dep in stored['dependencies']:
                dep_prepared = prepared_at(core,dep['ref'],snapshot)
                if dep_prepared.metadata.get('content_schema_ref') == 'rpnh/collaboration/plain_author_transform_map/v1':
                    body,_ = payload_at(core,dep['ref'],snapshot,material=True)
                    transform,transform_ref = json.loads(body),SourceQualifiedResourceRef.from_dict(dep['ref'],catalog=core.catalog)
            if transform is None or parent is None: raise ValueError('missing transform proof')
            from .plain_transform import _mapping,MARKER
            if set(definition_meta['descriptors'])!={MARKER}:raise ValueError('missing exact transform command')
            command_ref=json.loads(definition_meta['descriptors'][MARKER])
            command=json.loads(payload_at(core,command_ref,snapshot,material=True)[0])
            if command['result_revision_ref']!=target or command['parent_revision_refs']!=source_record['parent_revision_refs']:
                raise ValueError('transform exact command target differs')
            for spec in command['prepared_materials']:
                body,metadata=payload_at(core,spec['resource_ref'],snapshot,material=True)
                if (hashlib.sha256(body).hexdigest()!=spec['sha256'] or canonical_json(json.loads(body))!=canonical_json(spec['document'])
                        or canonical_json(metadata.metadata)!=canonical_json(spec['metadata'])):
                    raise ValueError('transform prepared command material differs')
            from .plain_transform import _mapping
            ids = {row['locator']:row['element_id'] for row in stored['elements']['elements']}
            rebuilt_map, rebuilt_elements = _mapping(compiled.source,ids,parent,
                {row['element_id'] for row in parent.element_map['elements']},transform)
            if rebuilt_map != transform or rebuilt_elements != stored['elements']: raise ValueError('invalid transform partition')
        assembly_plan = assembly_mapping = generated_ref = mapping_ref = None
        if result['producer_contract'] == PRODUCER_CONTRACTS[2]:
            from .assembly_v9 import AssemblyRevisionV9
            typed = AssemblyRevisionV9.from_dict(record,catalog=core.catalog)
            assembly_plan = json.loads(payload_at(core,record['plan_ref'],snapshot,material=True)[0])
            assembly_mapping = json.loads(payload_at(core,record['lowering_mapping_ref'],snapshot,material=True)[0])
            persisted_compiled = json.loads(payload_at(core,record['compiled_inventory_ref'],snapshot,material=True)[0])
            if persisted_compiled != stored['compiled']:
                raise ValueError('assembly material differs')
            generated_ref,mapping_ref = typed.generated_revision_ref,typed.lowering_mapping_ref
            from .materials import ValidatedClosedRevision
            from ._assembly_v9_lowering import lowering_map_v9,verify_final_projection,compose_declarations
            members={}
            for member in assembly_plan['members']:
                member_ref=member['revision_ref']
                snapshot.check_authorized(member_ref,'record')
                read_public_projection(core,member_ref,snapshot=snapshot)
                member_raw,_=payload_at(core,_projection_row(core,member_ref,snapshot),snapshot,material=True)
                member_stored=json.loads(member_raw)
                member_compiled=_load_compiled_net_offline(member_stored['compiled'])
                member_record=NetRevision.from_dict(descriptor_at(core,member_ref,snapshot),catalog=core.catalog)
                boundary=json.loads(payload_at(core,member_record.boundary_mapping_ref,snapshot,material=True)[0])
                host=json.loads(payload_at(core,member_record.host_requirements_ref,snapshot,material=True)[0])
                members[member['member_id']]=ValidatedClosedRevision(member_record,member_compiled.source,member_compiled,
                    member_stored['elements'],boundary,host)
                for original in member['resolution']['original_materials']:
                    body,meta=payload_at(core,original['resource_ref'],snapshot,material=True)
                    if (len(body)!=original['bytes'] or hashlib.sha256(body).hexdigest()!=original['sha256']
                            or canonical_json(meta.metadata)!=canonical_json(original['metadata'])):
                        raise ValueError('assembly member exact locked bytes differ')
            if compose_declarations(assembly_plan,members).to_dict()!=compiled.source.to_dict():
                # Connected assemblies contract consumed public carriers. Their
                # exact original fragment proof is independently checked below.
                from ._assembly_lowering import _contract_carriers
                baseline=compose_declarations(assembly_plan,members,connections=False)
                candidate,_=_contract_carriers(assembly_plan,members,compose_declarations(assembly_plan,members),compiled,
                    baseline_module=baseline,_offline_schema_validation=True)
                if candidate.to_dict()!=compiled.source.to_dict():raise ValueError('assembly composition differs')
            verify_final_projection(assembly_plan,members,compiled.source,compiled)
            rebuilt,ids=lowering_map_v9(assembly_plan,members,compiled.source,compiled,str(typed.revision_ref.ref.entity_id))
            if canonical_json(rebuilt)!=canonical_json(assembly_mapping) or ids!={row['locator']:row['element_id'] for row in stored['elements']['elements']}:
                raise ValueError('assembly actual-final mapping differs')
        else:
            typed = NetRevision.from_dict(record,catalog=core.catalog)
        expected = make_public_projection(typed,compiled,stored['elements'],producer_contract=result['producer_contract'],
            parent=parent,transform=transform,transform_ref=transform_ref,assembly_plan=assembly_plan,
            assembly_mapping=assembly_mapping,generated_revision_ref=generated_ref,mapping_ref=mapping_ref,
            configuration_refs={'host_requirements_ref':source_record['host_requirements_ref'],
                'declared_configuration_ref':source_record['definition_ref']})
        if result != expected: raise ValueError('public correspondence or hierarchy differs from exact producer inputs')
        result = deepcopy(result)
        result['mapping_coverage']='verified'
        result['projection_ref'] = projection_ref
        result['integrity'] = {'status':'verified_recorded_sha256','sha256':hashlib.sha256(raw).hexdigest()}
        result['generated_correspondences'] = _generated_correspondences(
            core, result, compiled, stored['elements'], parent, snapshot)
        return result
    except TypedReadError: raise
    except (ValueError,KeyError,TypeError) as exc:
        raise TypedReadError('INTEGRITY_FAILED') from exc



def read_public_projection(core,entry_ref,*,snapshot):
    try:
        return _read_public_projection_complete(core,entry_ref,snapshot=snapshot)
    except Exception as exc:
        # Initial missing optional proof access is a useful unknown. Current
        # access changes, corruption and unsupported contracts are never hidden.
        if (getattr(exc,'code',None) not in {'MATERIAL_ACCESS_NOT_GRANTED','NOT_DISCLOSED'}
                or wire_ref(entry_ref)[1]['entity_type'] not in {'collaboration_net_revision/v1','collaboration_assembly_revision/v9'}):
            raise
        return _read_topology_only(core,entry_ref,snapshot=snapshot)


def _read_topology_only(core,entry_ref,*,snapshot):
    from ..executable_net import _load_compiled_net_offline
    from .authoring import NetRevision
    from .materials import _elements,_boundaries,_command_material
    from ..registry.publication import _version_from_payload,_fresh_bootstrap_reference_resource_metadata
    from .registry_typed_readers import qualify
    target=entry_ref.to_dict() if hasattr(entry_ref,'to_dict') else entry_ref
    record=descriptor_at(core,target,snapshot)
    ref=_projection_row(core,target,snapshot)
    raw,prepared=payload_at(core,ref,snapshot,material=True,max_bytes=snapshot.max_material_bytes)
    meta=prepared.metadata
    if not meta.get('descriptors',{}).get('content_sha256') or meta.get('origin_kind')!='private_system':
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        stored=json.loads(raw);public=stored['projection']
        if (stored['schema_version']!=STORED_SCHEMA or public['schema_version']!=SCHEMA or public['target_ref']!=target
                or public['producer_contract'] not in PRODUCER_CONTRACTS):raise ValueError('wrong public projection')
        bootstrap=meta['origin']['primary_ref']
        allocated=projection_resource_ref(core,target,binding={'source_id':target['source_id'],'bootstrap_command_ref':bootstrap})
        if allocated.to_dict()!=ref:raise ValueError('wrong projection allocation')
        descriptor_at(core,qualify(target['source_id'],bootstrap),snapshot)
        expected=_fresh_bootstrap_reference_resource_metadata(core,ref=allocated.ref,
            task_ref=_version_from_payload(meta['task_ref']),bootstrap_ref=_version_from_payload(bootstrap),
            lifetime_ref=_version_from_payload(bootstrap),payload_size=len(raw),media_type='application/json',
            content_schema_ref=None,content_schema_authority_ref=None,summary='Stored public PN projection',
            descriptors=meta['descriptors'],extensions={},derived_from=())
        if canonical_json(expected)!=canonical_json(meta):raise ValueError('wrong projection provenance')
        source_ref=record['generated_revision_ref'] if 'generated_revision_ref' in record else target
        if public['author_revision_ref']!=source_ref:raise ValueError('wrong public author')
        snapshot.check_authorized(source_ref,'record')
        source=descriptor_at(core,source_ref,snapshot)
        if source['definition_kind']!='closed_module' or source.get('open_region_contract_ref') is not None:
            raise TypedReadError('UNSUPPORTED_READER_VERSION')
        pins={canonical_json(row['ref']):row for row in stored['dependencies']}
        if len(pins)!=len(stored['dependencies']):raise ValueError('duplicate public pin')
        total=len(raw)
        def material(reference):
            nonlocal total
            pin=pins.get(canonical_json(reference))
            if pin is None:raise ValueError('missing own public dependency')
            body,metadata=payload_at(core,reference,snapshot,material=True,max_bytes=snapshot.max_material_bytes)
            total+=len(body)
            if total>snapshot.max_material_bytes:raise TypedReadError('LIMIT_EXCEEDED')
            if len(body)!=pin['bytes'] or hashlib.sha256(body).hexdigest()!=pin['sha256']:raise ValueError('own public digest differs')
            return json.loads(body),metadata
        definition,definition_meta=material(source['definition_ref'])
        elements,_=material(source['element_mapping_ref'])
        boundary,_=material(source['boundary_mapping_ref'])
        host,_=material(source['host_requirements_ref'])
        compiled=_load_compiled_net_offline(stored['compiled'])
        if (compiled.source.to_dict()!=definition or elements!=stored['elements']
                or host['registrations']!=stored['compiled']['registrations']):raise ValueError('own public materials differ')
        declarations=_elements(compiled.source)
        if ({row['locator']:row['kind'] for row in elements['elements']}!=declarations
                or len(elements['elements'])!=len(declarations)
                or len({row['element_id'] for row in elements['elements']})!=len(declarations)
                or boundary!=_boundaries(compiled.source,elements)):raise ValueError('own declarations differ')
        for declaration in host['declaration_refs']:
            body,_=material(declaration['resource_ref'])
            expected=host['registrations'][declaration['kind']][declaration['key']]
            if declaration['kind']=='schema':expected=expected['schema']
            if canonical_json(body)!=canonical_json(expected):raise ValueError('own HOST declaration differs')
        if public['producer_contract']!=PRODUCER_CONTRACTS[1]:
            typed=NetRevision.from_dict(source,catalog=core.catalog)
            command=_command_material(source_id=target['source_id'],owner=typed.owner_task_ref,
                producer=typed.producer_principal_ref,command_id=typed.command_id,parents=typed.parent_revision_refs,
                documents=(definition,elements,boundary,host))
            if definition_meta.metadata['descriptors']!={'closed_author_command_v1':command}:raise ValueError('own command differs')
        nodes,edges,boundaries=_safe_graph(compiled)
        if public['nodes']!=nodes or public['edges']!=edges or public['boundaries']!=boundaries:
            raise ValueError('own graph differs')
        lowering=_lowering(compiled,elements,nodes,edges)
        root_element=next(row['element_id'] for row in elements['elements'] if row['locator']=='/')
        return {'schema_version':SCHEMA,'target_ref':target,'producer_contract':public['producer_contract'],
            'nodes':nodes,'edges':edges,'boundaries':boundaries,'configuration':{'host_requirements_ref':source['host_requirements_ref'],
                'declared_configuration_ref':source['definition_ref']},'hierarchy':[{'scope':{'kind':'module','selector':{
                    'declaration_ref':target,'occurrence_path':[],'element_id':root_element}},'parent_scope':None,
                    'label':compiled.source.name,'node_ids':[n['id'] for n in nodes],'edge_ids':[e['id'] for e in edges],'evidence_refs':[]}],
            'author_revision_ref':source_ref,'lowering':lowering,'edge_roles':_edge_roles(nodes,edges,lowering),
            'mapping_groups':[],'mapping_closure':None,'assembly_origins':None,'mapping_coverage':'not_provided',
            'runtime':{'coverage':'not_provided'},'projection_ref':ref,
            'integrity':{'status':'verified_recorded_sha256','sha256':hashlib.sha256(raw).hexdigest()}}
    except (KeyError,ValueError,TypeError) as exc:
        if hasattr(exc,'code'):raise
        raise TypedReadError('INTEGRITY_FAILED') from exc


def read_public_mapping(core, left_ref, right_ref, *, snapshot):
    left = read_public_projection(core,left_ref,snapshot=snapshot)
    right = read_public_projection(core,right_ref,snapshot=snapshot)
    a,b = left['target_ref'],right['target_ref']
    groups = [group for value in (left,right) for group in value['mapping_groups']
        if (group['source_revision_ref'],group['target_revision_ref']) in ((a,b),(b,a))]
    generated = [row for value in (left,right) for row in value.get('generated_correspondences', [])
        if (row['source_revision_ref'],row['target_revision_ref']) in ((a,b),(b,a))]
    return {'schema_version':'rpnh/public_author_mapping/v1','groups':groups,
        'generated_correspondences': generated,
        'left_projection_ref':left['projection_ref'],'right_projection_ref':right['projection_ref'],
        'runtime_identity':'not_established','general_many_to_many':'unsupported'}


class _SnapshotNetReads:
    """Adapter for the existing pure exact-net closure validator.

    The legacy validator's live _BoundedReads constructor is never called.
    Every fact comes from the session's bounded canonical snapshot; every
    resource body additionally checks its independent material permission.
    """
    def __init__(self,core,snapshot,source):
        self.core,self.snapshot,self.source=core,snapshot,source

    def qualified(self,ref):
        from .registry_typed_readers import qualify
        if ref.get('entity_type')=='resource_version/v1':
            ref={'resource_id':ref['logical_id'],'resource_version_id':ref['version_id']}
        return qualify(self.source,ref)

    def metadata(self,ref,kind):
        if ref.get('entity_type')!=kind:raise TypedReadError('INTEGRITY_FAILED')
        qualified=self.qualified(ref)
        if kind=='resource_version/v1':return dict(prepared_at(self.core,qualified,self.snapshot).metadata)
        return descriptor_at(self.core,qualified,self.snapshot)

    def declaration(self,ref,task_ref):
        from ..executable_net import _load_compiled_net_offline
        qualified=self.qualified(ref)
        raw,prepared=payload_at(self.core,qualified,self.snapshot,material=True,max_bytes=self.snapshot.max_material_bytes)
        if prepared.metadata['task_ref']!=task_ref or prepared.metadata['content_schema_ref']!='rpnh/executable_net/v1':
            raise TypedReadError('INTEGRITY_FAILED')
        return _load_compiled_net_offline(json.loads(raw))

    def port_schema(self,ref,root,schema_id,expected):
        qualified=self.qualified(ref)
        raw,prepared=payload_at(self.core,qualified,self.snapshot,material=True,max_bytes=self.snapshot.max_material_bytes)
        if (prepared.metadata['task_ref']!=root['task_ref'] or prepared.media_type!='application/schema+json'
                or canonical_json(json.loads(raw))!=canonical_json(expected) or expected.get('$id')!=schema_id):
            raise TypedReadError('INTEGRITY_FAILED')


def _read_runtime_projection(core,target,snapshot):
    from cpn.frontend.checkpoint_view import _inventory,_tokens
    from .registry_typed_readers import qualify,_checkpoint_commit_ordinal
    source,local=wire_ref(target)
    record=descriptor_at(core,target,snapshot)
    checkpoint=record if local['entity_type']=='marking_checkpoint/v1' else None
    net_ref=checkpoint['net_instance_ref'] if checkpoint else local
    qualified_net=qualify(source,net_ref)
    snapshot.check_authorized(qualified_net,'record')
    reads=_SnapshotNetReads(core,snapshot,source)
    net=reads.metadata(net_ref,'net_instance/v1')
    # A net selection has no checkpoint state. The closure validator uses only
    # these two identity fields; no synthetic checkpoint is published/returned.
    identity_fields=checkpoint or {'net_instance_ref':net_ref,'team_design_root_ref':net['team_design_root_ref']}
    compiled,bindings,root=_inventory(reads,{'net_ref':net_ref},identity_fields,None)
    nodes,edges,boundaries=_safe_graph(compiled)
    runtime={'coverage':'not_provided'};commit=None
    if checkpoint:
        commit=_checkpoint_commit_ordinal(checkpoint,snapshot)
        for ref in checkpoint['token_refs']:snapshot.check_authorized(qualify(source,ref),'record')
        tokens=_tokens(reads,checkpoint,net_ref,{p.name:p.capacity for p in compiled.symbolic.places},root['task_ref'])
        for row in tokens:
            row['token_ref']=qualify(source,row['token_ref'])
            if row['resource_ref'] is not None:row['resource_ref']=qualify(source,row['resource_ref'])
        runtime={'coverage':'selected_checkpoint_marking','marking':{'epoch':checkpoint['epoch'],'token_count':len(tokens),
            'active_token_count':sum(row['active_in_checkpoint'] for row in tokens)},'tokens':tokens}
    return {'schema_version':SCHEMA,'target_ref':target,'producer_contract':'rpnh/stored_runtime_public_pn/v1',
        'nodes':nodes,'edges':edges,'boundaries':boundaries,
        'configuration':{'declared_configuration_ref':reads.qualified(net['team_net_declaration_resource_ref'])},'hierarchy':None,
        'author_revision_ref':None,'lowering':[],'edge_roles':[],'mapping_groups':[],'mapping_closure':None,'assembly_origins':None,
        'runtime':runtime,'net_ref':qualified_net,'checkpoint_commit_ordinal':commit,
        'projection_ref':reads.qualified(net['team_net_declaration_resource_ref']),
        'integrity':{'status':'canonical_descriptor_and_compiled_closure','sha256':None}}
