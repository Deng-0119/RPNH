"""Bounded ordinary closed-author exchange with exact, inert provenance.

Exports do not grant destination access. Imports require the real owner author
and create fresh local identities; they never adopt, run, install or prepare.
Open/specialized/assembly imports are rejected rather than flattened.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import io
import json
import uuid
import zipfile

from .share_packages import DEFAULT_LIMITS, PackageError, PackagePreview, canonical_bytes, sha256, strict_json, safe_path
from .package_preview import preview_package
from .package_resolution import resolve_package
from .registry_typed_readers import wire_ref

EXCHANGE_SCHEMA = 'rpnh/subnet_exchange/v1'
PROVENANCE_SCHEMA = 'rpnh/subnet_import_provenance/v1'


class SubnetExchangeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _dict(value):
    return value.to_dict() if hasattr(value,'to_dict') else deepcopy(value)


def _unwrap(value, key):
    value = _dict(value)
    if isinstance(value,dict) and value.get('status') not in (None,'ok','success','readable'):
        raise SubnetExchangeError(value.get('code', 'READ_FAILED'))
    return value.get(key,value) if isinstance(value,dict) else value


def _ref(value):
    return _dict(value)


def _typed_ref(value):
    from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
    from ..registry.identities import TypedId
    from ..registry.models import VersionRef
    from ..registry.resources import ResourceVersionRef
    if isinstance(value,(SourceQualifiedResourceRef,SourceQualifiedVersionRef)): return value
    source,ref=wire_ref(value)
    if value['schema_version']=='rpnh/collaboration/source_resource_ref/v1':
        return SourceQualifiedResourceRef(source,ResourceVersionRef(TypedId.parse(ref['logical_id']),TypedId.parse(ref['version_id'])))
    return SourceQualifiedVersionRef(source,VersionRef(ref['entity_type'],TypedId.parse(ref['logical_id']),TypedId.parse(ref['version_id'])))


def _record(session, reference, cuts):
    source,_ = wire_ref(reference)
    if source not in cuts: raise SubnetExchangeError('MISSING_SOURCE')
    return _unwrap(session.read_exact(entry_ref=_typed_ref(reference),at_cut=cuts[source]),'record')


def _material(session, reference, cuts, limits):
    source,_ = wire_ref(reference)
    if source not in cuts: raise SubnetExchangeError('MISSING_SOURCE')
    result = session.read_material(resource_ref=_typed_ref(reference),at_cut=cuts[source],max_bytes=limits.artifact_bytes)
    body = result.body if hasattr(result,'body') else result.get('body',result.get('bytes'))
    if not isinstance(body,bytes): raise SubnetExchangeError('INVALID_MATERIAL_REPRESENTATION')
    return body


def _ordinary(module, revision=None):
    if (module.get('designer_constraints') or any(c['key'].startswith('rpnh/agent-workflow-graph/') for c in module['components'])
        or (revision and (revision['definition_kind'] != 'closed_module' or revision.get('open_region_contract_ref') is not None
            or revision.get('selected_change_refs') or len(revision.get('parent_revision_refs',[]))>1
            or revision['command_id'].startswith('collaboration-assembly-')))):
        raise SubnetExchangeError('UNSUPPORTED_EXCHANGE_CONTRACT')


@dataclass(frozen=True)
class SubnetExportPlan:
    payload: bytes
    materials: tuple[tuple[bytes, bytes], ...]
    selected_cuts: tuple[tuple[str,object], ...]
    session: object
    limits: object = DEFAULT_LIMITS
    def __post_init__(self):
        if type(self.payload) is not bytes or type(self.materials) is not tuple or type(self.selected_cuts) is not tuple:
            raise TypeError('export plan requires immutable bytes and tuples')
    @property
    def plan_digest(self): return sha256(self.payload)
    def to_dict(self): return json.loads(self.payload)


@dataclass(frozen=True)
class SubnetExportResult:
    package: PackagePreview
    dependency_archives: tuple[bytes,...]
    plan_digest: str
    @property
    def archive_bytes(self): return self.package.archive_bytes
    def to_dict(self):
        return {'schema_version':'rpnh/subnet_export_result/v1','manifest_digest':self.package.manifest_digest,
            'archive_digest':self.package.archive_digest,'plan_digest':self.plan_digest,
            'dependency_archive_digests':[sha256(v) for v in self.dependency_archives],
            'definition_closure_status':'complete','boundary_status':'closed','readiness':'not_established'}


@dataclass(frozen=True)
class TargetRegistrySelection:
    source_ref: object
    def to_dict(self): return {'source_ref':_ref(self.source_ref)}


@dataclass(frozen=True)
class SubnetImportPlan:
    payload: bytes
    root_archive: bytes
    dependency_archives: tuple[bytes,...]
    limits: object = DEFAULT_LIMITS
    def __post_init__(self):
        if (type(self.payload) is not bytes or type(self.root_archive) is not bytes or type(self.dependency_archives) is not tuple
                or any(type(raw) is not bytes for raw in self.dependency_archives)):
            raise TypeError('import plan requires immutable archive bytes')
    @property
    def plan_digest(self): return sha256(self.payload)
    def to_dict(self): return json.loads(self.payload)


@dataclass(frozen=True)
class SubnetImportResult:
    revision_ref: object
    provenance_ref: object
    origin_to_local_map: tuple[dict,...]
    plan_digest: str
    def to_dict(self):
        return {'schema_version':'rpnh/subnet_import_result/v1','revision_ref':_ref(self.revision_ref),
            'provenance_ref':_ref(self.provenance_ref),'origin_to_local_map':deepcopy(list(self.origin_to_local_map)),
            'plan_digest':self.plan_digest,'adopted':False,'executed':False,'environment_prepared':False}


def plan_subnet_export(session, *, root_revision_ref, cuts, include='definition_closure', limits=DEFAULT_LIMITS):
    if include != 'definition_closure': raise SubnetExchangeError('UNSUPPORTED_EXPORT_SELECTION')
    root = _ref(root_revision_ref)
    if wire_ref(root)[1]['entity_type'] != 'collaboration_net_revision/v1':
        raise SubnetExchangeError('UNSUPPORTED_EXCHANGE_CONTRACT')
    cuts = dict(cuts)
    materials, records, visiting, seen = {}, {}, set(), set()
    total, edges = 0, 0
    def material(reference):
        nonlocal total
        key = canonical_bytes(_ref(reference))
        if key not in materials:
            raw = _material(session,reference,cuts,limits)
            total += len(raw)
            if total>limits.closure_expanded_bytes or len(materials)>=limits.members-8:
                raise SubnetExchangeError('LIMIT_EXCEEDED')
            materials[key]=raw
        return json.loads(materials[key])
    def visit(reference,depth=0):
        nonlocal edges
        key = canonical_bytes(reference)
        if key in visiting: raise SubnetExchangeError('DEPENDENCY_CYCLE')
        if key in seen: return
        if depth>=limits.dependency_depth: raise SubnetExchangeError('LIMIT_EXCEEDED')
        visiting.add(key)
        record = _record(session,reference,cuts)
        module = material(record['definition_ref'])
        _ordinary(module,record)
        # Stored producer validation establishes ordinary provenance and actual
        # public lowering without invoking a compiler at read time.
        projection = _unwrap(session.read_public_projection(entry_ref=_typed_ref(reference),at_cut=cuts[reference['source_id']]),'record')
        if projection.get('producer_contract') != 'rpnh/closed_author_public_pn/v1':
            raise SubnetExchangeError('UNSUPPORTED_EXCHANGE_CONTRACT')
        for field in ('element_mapping_ref','boundary_mapping_ref','host_requirements_ref'):
            document=material(record[field])
            if field=='host_requirements_ref':
                for dep in document['declaration_refs']: material(dep['resource_ref'])
        material(projection['projection_ref'])
        for parent in record['parent_revision_refs']:
            edges += 1
            if edges>limits.dependency_edges: raise SubnetExchangeError('LIMIT_EXCEEDED')
            visit(parent,depth+1)
        if record.get('subnet_provenance_ref'): material(record['subnet_provenance_ref'])
        records[key]=record
        visiting.remove(key);seen.add(key)
    visit(root)
    evidence=[]
    for source,cut in sorted(cuts.items()):
        value=_dict(cut)
        evidence.append({key:value[key] for key in ('source_id','head','reader_contract_version')})
    rows=[{'ref':json.loads(key),'bytes':len(raw),'sha256':sha256(raw)} for key,raw in sorted(materials.items())]
    document={'schema_version':'rpnh/subnet_export_plan/v1','root_revision_ref':root,
        'records':[records[key] for key in sorted(records)],'included_refs':[json.loads(k) for k in sorted(records)]+[r['ref'] for r in rows],
        'required_dependencies':rows,'missing_dependencies':[],'boundary_status':'closed','definition_closure_status':'complete',
        'readiness':'not_established','source_cut_evidence':evidence}
    session.final_recheck(source_ids=tuple(cuts))
    return SubnetExportPlan(canonical_bytes(document),tuple(sorted(materials.items())),tuple(sorted(cuts.items())),session,limits)


class _Paths:
    """Reserve *all* preserved paths before allocating generated paths."""
    def __init__(self, paths=()): self.used={safe_path(p).casefold() for p in paths}|{'manifest.json'}
    def allocate(self,wanted):
        safe_path(wanted)
        prefix, dot, suffix = wanted.rpartition('.')
        stem = prefix if dot else wanted
        ext = '.'+suffix if dot else ''
        candidate=wanted;index=1
        while candidate.casefold() in self.used:
            candidate=f'{stem}-{index:04d}{ext}';index+=1
        self.used.add(candidate.casefold());return candidate


def _zip(files):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.external_attr=0o100644<<16
            archive.writestr(info,raw)
    return output.getvalue()


def _selected_packages(root, local_packages, entry_id, limits):
    root=preview_package(root.archive_bytes if isinstance(root,PackagePreview) else root,limits=limits)
    local=tuple(preview_package(p.archive_bytes if isinstance(p,PackagePreview) else p,limits=limits) for p in local_packages)
    lock=resolve_package(root,local,root_entry_id=entry_id,limits=limits)
    candidates={root.manifest_digest:root}
    for p in local:
        previous=candidates.get(p.manifest_digest)
        if p.manifest_digest!=root.manifest_digest and (previous is None or p.archive_digest<previous.archive_digest):
            candidates[p.manifest_digest]=p
    selected=[]
    for node in lock.to_dict()['nodes']:
        p=candidates[node['manifest_digest']]
        if p.archive_digest!=node['archive_digest']: raise SubnetExchangeError('INTEGRITY_FAILED')
        selected.append(p)
    return root,tuple(selected),lock


def _exchange(preview):
    found=[]
    for path,raw in preview.artifacts:
        if next(row for row in preview.manifest['artifacts'] if row['path']==path)['media_type']!='application/json':continue
        value=json.loads(raw)
        if isinstance(value,dict) and value.get('schema_version')==EXCHANGE_SCHEMA: found.append((path,value))
    if len(found)>1: raise SubnetExchangeError('INTEGRITY_FAILED')
    if not found:return None
    path,value=found[0]
    if set(value)!={'schema_version','root_revision_ref','source_cut_evidence','records','materials','preserved_environment_artifacts'}:
        raise SubnetExchangeError('INTEGRITY_FAILED')
    artifacts=dict(preview.artifacts)
    seen=set()
    for row in value['materials']:
        if set(row)!={'ref','artifact_path','bytes','sha256'} or row['artifact_path'] in seen:
            raise SubnetExchangeError('INTEGRITY_FAILED')
        wire_ref(row['ref']);seen.add(row['artifact_path'])
        body=artifacts.get(row['artifact_path'])
        if body is None or len(body)!=row['bytes'] or sha256(body)!=row['sha256']:
            raise SubnetExchangeError('INTEGRITY_FAILED')
    rows={canonical_bytes(row['ref']):row for row in value['materials']}
    if len(rows)!=len(value['materials']):raise SubnetExchangeError('INTEGRITY_FAILED')
    records={canonical_bytes(record['revision_ref']):record for record in value['records']}
    if len(records)!=len(value['records']) or canonical_bytes(value['root_revision_ref']) not in records:
        raise SubnetExchangeError('INTEGRITY_FAILED')
    active=set();done=set()
    def visit(ref,depth=0):
        key=canonical_bytes(ref)
        if key in active:raise SubnetExchangeError('DEPENDENCY_CYCLE')
        if key in done:return
        if depth>=DEFAULT_LIMITS.dependency_depth:raise SubnetExchangeError('LIMIT_EXCEEDED')
        if key not in records:raise SubnetExchangeError('INCOMPLETE_CLOSURE')
        active.add(key);r=records[key]
        for field in ('definition_ref','element_mapping_ref','boundary_mapping_ref','host_requirements_ref'):
            if canonical_bytes(r[field]) not in rows:raise SubnetExchangeError('INCOMPLETE_CLOSURE')
        host=json.loads(artifacts[rows[canonical_bytes(r['host_requirements_ref'])]['artifact_path']])
        for dep in host['declaration_refs']:
            if canonical_bytes(dep['resource_ref']) not in rows:raise SubnetExchangeError('INCOMPLETE_CLOSURE')
        for parent in r['parent_revision_refs']:visit(parent,depth+1)
        from ..module import ModuleDeclaration
        from .materials import _elements,_boundaries
        module_doc=json.loads(artifacts[rows[canonical_bytes(r['definition_ref'])]['artifact_path']])
        _ordinary(module_doc,r)
        module=ModuleDeclaration.from_dict(module_doc)
        elements=json.loads(artifacts[rows[canonical_bytes(r['element_mapping_ref'])]['artifact_path']])
        expected=_elements(module)
        if ({item['locator']:item['kind'] for item in elements['elements']}!=expected
                or len(elements['elements'])!=len(expected)
                or len({item['element_id'] for item in elements['elements']})!=len(expected)):
            raise SubnetExchangeError('INTEGRITY_FAILED')
        boundaries=json.loads(artifacts[rows[canonical_bytes(r['boundary_mapping_ref'])]['artifact_path']])
        if boundaries!=_boundaries(module,elements):raise SubnetExchangeError('INTEGRITY_FAILED')
        active.remove(key);done.add(key)
    visit(value['root_revision_ref'])
    for row in value['materials']:
        document=json.loads(artifacts[row['artifact_path']])
        if isinstance(document,dict) and document.get('schema_version')=='rpnh/stored_public_pn_projection/v1':
            for dep in document['dependencies']:
                found=rows.get(canonical_bytes(dep['ref']))
                if found is None or found['bytes']!=dep['bytes'] or found['sha256']!=dep['sha256']:
                    raise SubnetExchangeError('INCOMPLETE_CLOSURE')
    root=records[canonical_bytes(value['root_revision_ref'])]
    module=artifacts[rows[canonical_bytes(root['definition_ref'])]['artifact_path']]
    declared=artifacts[preview.manifest['entries'][0]['declaration_path']]
    if canonical_bytes(json.loads(module))!=canonical_bytes(json.loads(declared)):
        raise SubnetExchangeError('INTEGRITY_FAILED')
    for row in value['preserved_environment_artifacts']:
        body=artifacts.get(row['artifact_path'])
        if body is None or sha256(body)!=row['artifact_digest']:raise SubnetExchangeError('INTEGRITY_FAILED')
    return value


def export_subnet(session, *, plan, destination):
    if not isinstance(plan,SubnetExportPlan) or plan.session is not session:raise SubnetExchangeError('PLAN_SESSION_MISMATCH')
    document=plan.to_dict();cuts=dict(plan.selected_cuts)
    verified=plan_subnet_export(session,root_revision_ref=_typed_ref(document['root_revision_ref']),cuts=cuts,limits=plan.limits)
    if verified.payload!=plan.payload or verified.materials!=plan.materials:
        raise SubnetExchangeError('PLAN_MISMATCH')
    session.authorize_export(tuple(_typed_ref(ref) for ref in document['included_refs']),destination=destination,at_cuts=cuts)
    # Re-read every body before publication. A plan is not a byte cache that
    # survives revocation or same-size legacy file corruption.
    for reference,raw in plan.materials:
        if _material(session,json.loads(reference),cuts,plan.limits)!=raw:raise SubnetExchangeError('INTEGRITY_FAILED')
    data={key:value for key,value in plan.materials}
    records=document['records'];root=next(r for r in records if r['revision_ref']==document['root_revision_ref'])
    module=json.loads(data[canonical_bytes(root['definition_ref'])])
    host=json.loads(data[canonical_bytes(root['host_requirements_ref'])])
    original=None;dependencies=()
    if root.get('subnet_provenance_ref'):
        provenance=json.loads(data[canonical_bytes(root['subnet_provenance_ref'])])
        original=preview_package(base64.b64decode(provenance['root_archive_base64'],validate=True),limits=plan.limits)
        dependencies=tuple(base64.b64decode(v,validate=True) for v in provenance['dependency_archives_base64'])
        _selected_packages(original,dependencies,provenance['entry_id'],plan.limits)
    # Preserve every inert original artifact, including non-entry environment
    # documents and all original license material; identity is digest+path.
    preserved=dict(original.artifacts) if original is not None else {}
    if original:
        # Replace the previous exchange index, retaining all inert environment
        # documents and their bytes. Two exchange roots would be ambiguous.
        for path,raw in tuple(preserved.items()):
            row=next(row for row in original.manifest['artifacts'] if row['path']==path)
            if row['media_type']=='application/json':
                value=json.loads(raw)
                if isinstance(value,dict) and value.get('schema_version')==EXCHANGE_SCHEMA:
                    del preserved[path]
    allocator=_Paths(preserved)
    files=dict(preserved);roles={}
    original_rows={row['path']:row for row in original.manifest['artifacts']} if original else {}
    for path in preserved:
        row=original_rows[path]
        roles[path]=(row['media_type'],'document' if row['role']=='declaration' else row['role'])
    def add(wanted,raw,media='application/json',role='document'):
        path=allocator.allocate(wanted);files[path]=raw;roles[path]=(media,role);return path
    declaration=add('declarations/main.json',canonical_bytes(module),role='declaration')
    closure=[]
    for index,(key,raw) in enumerate(plan.materials):
        path=add(f'closure/{index:04d}.json',raw)
        closure.append({'ref':json.loads(key),'artifact_path':path,'bytes':len(raw),'sha256':sha256(raw)})
    existing_schemas={json.loads(raw).get('$id') for path,raw in preserved.items() if original_rows[path]['role']=='schema'}
    for index,(key,item) in enumerate(sorted(host['registrations'].get('schema',{}).items())):
        schema=item['schema']
        if key not in existing_schemas:add(f'schemas/{index:04d}.json',canonical_bytes(schema),'application/schema+json','schema')
    env=[]
    if original:
        for preview in (original,*[preview_package(raw,limits=plan.limits) for raw in dependencies]):
            for row in preview.manifest['artifacts']:
                body=dict(preview.artifacts)[row['path']]
                if row['media_type']=='application/json' and isinstance(json.loads(body),dict) and json.loads(body).get('schema_version')=='rpnh/environment_requirements/v1':
                    if preview.manifest_digest==original.manifest_digest:path=row['path']
                    else:path=add('environment/'+preview.manifest_digest[:16]+'/'+row['path'].split('/')[-1],body)
                    env.append({'manifest_digest':preview.manifest_digest,'original_path':row['path'],
                        'artifact_path':path,'artifact_digest':sha256(body)})
    exchange={'schema_version':EXCHANGE_SCHEMA,'root_revision_ref':document['root_revision_ref'],
        'source_cut_evidence':document['source_cut_evidence'],'records':records,'materials':closure,'preserved_environment_artifacts':env}
    add('subnet-exchange.json',canonical_bytes(exchange))
    license_path=add('licenses/UNSPECIFIED.txt',b'No license or disclosure permission is granted by this export. Retain source restrictions.\n','text/plain','license')
    requirements=[]
    for kind,items in sorted(host['registrations'].items()):
        if kind not in {'component','executor','tool','analyzer','terminal','effect'}:continue
        for key in sorted(items):requirements.append({'requirement_id':f'req{len(requirements):04d}',
            'kind':kind,'contract_id':key,'required':True,'effects':[],'data_classes':[]})
    needed={('terminal',row['key']) for row in [module['terminal'],*module.get('terminal_alternatives',[])]}
    needed.update(('analyzer',key) for key in module.get('analyzers',[]))
    declared={(row['kind'],row['contract_id']) for row in requirements}
    for kind,key in sorted(needed-declared):
        requirements.append({'requirement_id':f'req{len(requirements):04d}','kind':kind,'contract_id':key,
            'required':True,'effects':[],'data_classes':[]})
    if original:requirements=deepcopy(original.manifest['requirements'])
    ports={(c['name'],p['name']):p for c in module['components'] for p in c['ports']}
    success=next((name for name,value in module['exit'].items() if value==module['terminal']['source']),None)
    if success is None:raise SubnetExchangeError('UNSUPPORTED_EXCHANGE_CONTRACT')
    entry={'entry_id':original.manifest['entries'][0]['entry_id'] if original else 'main','kind':'closed_module',
        'declaration_path':declaration,'declaration_schema':'rpnh/module_declaration/v1',
        'input_schema_ids':sorted({ports[(e['component'],e['port'])]['schema'] for e in module['entry'].values()}),
        'output_schema_ids':sorted({ports[(e['component'],e['port'])]['schema'] for e in module['exit'].values()}),
        'completion_contract':{'success_exit':success,'failure_exits':sorted(set(module['exit'])-{success}),
            'acceptor_requirement_ids':[],'open_obligations':[]}}
    version='rpnh/share_package/v1'
    if original and original.manifest['schema_version']=='rpnh/share_package/v2':
        version='rpnh/share_package/v2';entry['environment_requirements_path']=original.manifest['entries'][0]['environment_requirements_path']
    licenses=deepcopy(original.manifest['licenses']) if original else []
    license_id='exchange-unspecified'
    while license_id in {row['license_id'] for row in licenses}:license_id+='-next'
    licenses.append({'license_id':license_id,'expression':'LicenseRef-Unspecified','text_paths':[license_path],'notice_paths':[]})
    artifacts=[];provenance=[]
    for path,raw in sorted(files.items()):
        media,role=roles[path];original_row=original_rows.get(path)
        artifacts.append({'path':path,'media_type':media,'role':role,'bytes':len(raw),'sha256':sha256(raw),
            'license_id':original_row['license_id'] if original_row else license_id,'disclosure':'named_recipients'})
        provenance.append({'artifact_path':path,'relation':'copied' if original_row else 'authored',
            'origin_ref':None,'origin_digest':sha256(raw) if original_row else None})
    manifest={'schema_version':version,'package_id':'rpnh/subnet-export','version':'1.0.0','entries':[entry],
        'artifacts':artifacts,'origin':{'repository_url':None,'commit':None,'publisher_claim':'Exact authorized Registry export',
            'source_refs':[document['root_revision_ref']]},'provenance':provenance,
        'dependencies':deepcopy(original.manifest['dependencies']) if original else [],
        'compatibility':{'declaration_schemas':['rpnh/module_declaration/v1'],'runtime_contracts':[],
            'host_contracts':sorted({row['contract_id'] for row in requirements})},'requirements':requirements,'policy_surface':[],
        'licenses':licenses,'disclosure':{'classification':'named_recipients','intended_audience':str(destination),
            'excluded_categories':['runtime_history','checkpoints','results','credentials','leases','local_bindings']}}
    files['manifest.json']=canonical_bytes(manifest)
    if (len(files)>plan.limits.members or sum(map(len,files.values()))>plan.limits.expanded_bytes
            or any(len(raw)>plan.limits.artifact_bytes for raw in files.values())):
        raise SubnetExchangeError('LIMIT_EXCEEDED')
    package=preview_package(_zip(files),limits=plan.limits)
    _selected_packages(package,dependencies,entry['entry_id'],plan.limits)
    _exchange(package) # Verify inner exchange closure, not only outer inventory.
    session.final_recheck(source_ids=tuple(cuts))
    session.authorize_export(tuple(_typed_ref(ref) for ref in document['included_refs']),destination=destination,at_cuts=cuts)
    return SubnetExportResult(package,dependencies,plan.plan_digest)


def plan_subnet_import(package, *, target, entry_id='main', local_packages=(), limits=DEFAULT_LIMITS):
    if isinstance(package,SubnetExportResult):
        local_packages=(*package.dependency_archives,*local_packages);package=package.package
    root,selected,lock=_selected_packages(package,local_packages,entry_id,limits)
    manifest=root.manifest;entry=manifest['entries'][0]
    module=json.loads(dict(root.artifacts)[entry['declaration_path']]);_ordinary(module)
    exchange=_exchange(root)
    target=target.to_dict() if isinstance(target,TargetRegistrySelection) else _dict(target)
    source,ref=wire_ref(target['source_ref'])
    if ref['entity_type']!='task/v1':raise SubnetExchangeError('INVALID_TARGET')
    document={'schema_version':'rpnh/subnet_import_plan/v1','target':target,'entry_id':entry_id,
        'manifest_digest':root.manifest_digest,'archive_digest':root.archive_digest,
        'package_lock':lock.to_dict(),'package_lock_digest':lock.package_lock_digest,
        'origin_kind':'registry_export' if exchange else 'package_artifact','definition_closure_status':'complete',
        'boundary_status':'closed','readiness':'not_established'}
    return SubnetImportPlan(canonical_bytes(document),root.archive_bytes,
        tuple(p.archive_bytes for p in selected if p.manifest_digest!=root.manifest_digest),limits)



def _expected_head(value):
    from ..registry.resources import RegistryHead
    from .registry_read_contracts import PublicRegistryHead
    if type(value) in (RegistryHead,PublicRegistryHead):
        value={'ordinal':value.ordinal,'writer_fencing_epoch':value.writer_fencing_epoch}
    if (type(value) is not dict or set(value)!={'ordinal','writer_fencing_epoch'}
            or any(type(number) is not int or number<0 for number in value.values())):
        raise SubnetExchangeError('INVALID_TARGET_HEAD')
    return value['ordinal'],value['writer_fencing_epoch']


def import_subnet(target_owner_context, *, plan, command_id, expected_target_head):
    from .materials import ClosedModuleAuthor, _elements
    from ..module import ModuleDeclaration
    from ..registry.event_store import RegistryConflict, StaleWriterError
    from ..registry.resource_service import _publish_private_system
    from ..registry.resources import PrivateSystemOrigin, PublishResource
    from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef
    from .assembly_v2 import _material_ref
    if not isinstance(target_owner_context,ClosedModuleAuthor) or not isinstance(plan,SubnetImportPlan):
        raise TypeError('import requires the trusted target owner author and an immutable import plan')
    author=target_owner_context;core=author.core;document=plan.to_dict()
    expected_ordinal,expected_epoch=_expected_head(expected_target_head)
    if not isinstance(command_id,str) or not command_id or command_id.strip()!=command_id:raise ValueError('canonical command required')
    selected=SourceQualifiedVersionRef(author.binding['source_id'],author.gateway._task_ref).to_dict()
    if document['target']['source_ref']!=selected:raise SubnetExchangeError('TARGET_MISMATCH')
    verified=plan_subnet_import(plan.root_archive,target=document['target'],entry_id=document['entry_id'],
        local_packages=plan.dependency_archives,limits=plan.limits)
    if verified.payload!=plan.payload:raise SubnetExchangeError('INTEGRITY_FAILED')
    preview=preview_package(plan.root_archive,limits=plan.limits);entry=preview.manifest['entries'][0]
    exchange=_exchange(preview)
    # Fail before any target fact if preserved material cannot fit the import
    # evidence envelope. The remainder is a bounded deterministic JSON map.
    if (len(plan.root_archive)+sum(map(len,plan.dependency_archives)))*4//3+len(plan.payload)>plan.limits.artifact_bytes//2:
        raise SubnetExchangeError('LIMIT_EXCEEDED')
    if exchange:
        root_record=next(row for row in exchange['records'] if row['revision_ref']==exchange['root_revision_ref'])
        host_path=next(row['artifact_path'] for row in exchange['materials'] if row['ref']==root_record['host_requirements_ref'])
        host=json.loads(dict(preview.artifacts)[host_path])
        from ..registration import RegistrationError
        for kind,items in host['registrations'].items():
            if kind=='schema':continue
            for name,declared in items.items():
                try:actual=author.registration.declaration(kind,name)
                except RegistrationError as exc:raise SubnetExchangeError('HOST_REQUIREMENTS_NOT_SATISFIED') from exc
                if canonical_bytes(actual)!=canonical_bytes(declared):raise SubnetExchangeError('HOST_REQUIREMENTS_MISMATCH')
    if core.writer_epoch!=core.event_store.writer_epoch:raise StaleWriterError('stale subnet import owner')
    key='subnet-import:'+command_id
    reservation_ref=_material_ref(core,author.binding,key+':request')
    original=core.event_store.object_row(reservation_ref.ref.resource_version_id)
    request=canonical_bytes({'schema_version':'rpnh/subnet_import_request/v1','command_id':command_id,
        'plan_digest':plan.plan_digest,'plan':document})
    if original is not None:
        from .materials import _private_document,_binding
        with core.event_store.connect() as db:
            db.execute('BEGIN')
            stored,_=_private_document(db,core,reservation_ref,_binding(db,core))
        if canonical_bytes(stored)!=request:raise RegistryConflict('subnet import command conflicts with original input')
    else:
        if core.event_store.max_ordinal()!=expected_ordinal or core.writer_epoch!=expected_epoch:
            raise RegistryConflict('subnet import expected target head changed')
        # Native transactional CAS is installed by the Registry integration.
        publish=PublishResource(origin=PrivateSystemOrigin(author.gateway._bootstrap_ref),payload=request,
            media_type='application/json',content_schema_ref=None,summary='Subnet import exact command reservation',
            lifetime_ref=author.gateway._bootstrap_ref,descriptors={'content_sha256':sha256(request)},idempotency_key=key+':request')
        _publish_import_reservation(author,publish,expected_ordinal,expected_epoch)
    preview=preview_package(plan.root_archive,limits=plan.limits);entry=preview.manifest['entries'][0]
    module=ModuleDeclaration.from_dict(json.loads(dict(preview.artifacts)[entry['declaration_path']]))
    identities={loc:'element:'+uuid.uuid5(uuid.NAMESPACE_URL,canonical_bytes({'task':str(core.task_id),
        'source':author.binding['source_id'],'command':command_id,'locator':loc}).decode()).hex for loc in _elements(module)}
    # Imported inert schemas are installed in the existing owner Registration;
    # executable implementations still come exclusively from its trusted HOST.
    # Existing schema identities may never be overwritten by package bytes.
    from ..registration import RegistrationError
    from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS
    _,selected_packages,_=_selected_packages(preview,plan.dependency_archives,entry['entry_id'],plan.limits)
    schema_documents={}
    for selected_package in selected_packages:
        for row in selected_package.manifest['artifacts']:
            if row['role']=='schema':
                schema=json.loads(dict(selected_package.artifacts)[row['path']]);schema_documents[schema['$id']]=schema
    for schema_id,schema in sorted(schema_documents.items()):
        try:existing=author.registration.declaration('schema',schema_id)['schema']
        except RegistrationError:existing=None
        if existing is not None:
            if canonical_bytes(existing)!=canonical_bytes(schema):raise SubnetExchangeError('SCHEMA_ID_CONTENT_CONFLICT')
        elif schema_id in PROTECTED_SCHEMA_REFS:raise SubnetExchangeError('SCHEMA_ID_CONTENT_CONFLICT')
        else:author.registration.register_schema(schema_id,schema)
    value=author.publish(module=module,element_ids=identities,command_id=key+':author')
    exchange=_exchange(preview)
    old_elements={}
    if exchange:
        origin_record=next(row for row in exchange['records'] if row['revision_ref']==exchange['root_revision_ref'])
        element_path=next(row['artifact_path'] for row in exchange['materials'] if row['ref']==origin_record['element_mapping_ref'])
        old_elements={row['locator']:row['element_id'] for row in json.loads(dict(preview.artifacts)[element_path])['elements']}
    mapping=[]
    for locator,identity in sorted(identities.items()):
        origin=({'kind':'registry_export','revision_ref':exchange['root_revision_ref'],'element_id':old_elements[locator]}
            if exchange else {'kind':'package_artifact','manifest_digest':preview.manifest_digest,
                'artifact_id':entry['declaration_path'],'entry_id':entry['entry_id'],'element_locator':locator})
        mapping.append({'origin':origin,'local_revision_ref':value.revision.revision_ref.to_dict(),
            'local_element_id':identity,'local_locator':locator,'relation_kind':'copied_from','semantic_claim':'author_correspondence'})
    provenance={'schema_version':PROVENANCE_SCHEMA,'command_id':command_id,'plan_digest':plan.plan_digest,
        'revision_ref':value.revision.revision_ref.to_dict(),'entry_id':entry['entry_id'],'origin_to_local_map':mapping,
        'source_provenance':exchange,'root_archive_base64':base64.b64encode(plan.root_archive).decode(),
        'dependency_archives_base64':[base64.b64encode(raw).decode() for raw in plan.dependency_archives],
        'package_lock':document['package_lock']}
    payload=canonical_bytes(provenance)
    if len(payload)>plan.limits.artifact_bytes:raise SubnetExchangeError('LIMIT_EXCEEDED')
    ref=_publish_private_system(core,author.gateway._task_ref,PublishResource(origin=PrivateSystemOrigin(author.gateway._bootstrap_ref),
        payload=payload,media_type='application/json',content_schema_ref=None,summary='Subnet import exact provenance and inert package closure',
        lifetime_ref=author.gateway._bootstrap_ref,descriptors={'content_sha256':sha256(payload),
            'subnet_import_revision_ref':canonical_bytes(value.revision.revision_ref.to_dict()).decode()},idempotency_key=key+':provenance'))
    return SubnetImportResult(value.revision.revision_ref,SourceQualifiedResourceRef(author.binding['source_id'],ref),tuple(mapping),plan.plan_digest)


def _publish_import_reservation(author,command,ordinal,epoch):
    from ..registry.resource_service import _publish_private_system
    from ..registry.event_store import StaleWriterError
    if author.core.writer_epoch != epoch:
        raise StaleWriterError('stale subnet import owner')
    tx=author.core.begin(idempotency_key=command.idempotency_key)
    tx.expect_registry_ordinal(ordinal)
    reference=_publish_private_system(author.core,author.gateway._task_ref,command,transaction=tx)
    tx.commit()
    return reference
