"""Static local installation-layout fixtures, not a production installation.

The original numbers/files definition/catalog/Registration/compiler are used.
Runtime assets are a finite trusted fixture declaration, not inferred execution
purity or proof of loader selection. No NumPy import/business execution occurs.
"""
from pathlib import Path
import importlib.metadata as metadata
import importlib.util
import json
import sys
from types import SimpleNamespace
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.plugins.runtime import build_plugin_module, plugin_registration
from cpn.plugins.host import schema_key

ROOT=Path(__file__).parents[1]
FIXTURE=ROOT/'tests/fixtures/environment_neutral/rpnh_neutral_fixture/__init__.py'
_spec=importlib.util.spec_from_file_location('rpnh_neutral_fixture',FIXTURE)
neutral=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=neutral;_spec.loader.exec_module(neutral)

class LayoutDistribution:
    def __init__(self,name,version,paths):
        self.metadata={'Name':name};self.version=version;self.paths=dict(paths);self.files=tuple(self.paths)
    def locate_file(self,member):return self.paths[str(member)]


def _paths(distribution):
    return {str(f):Path(distribution.locate_file(f)) for f in distribution.files or ()
        if '__pycache__' not in str(f) and not str(f).endswith('.pyc') and '..' not in Path(str(f)).parts}


def make_layout(path,monkeypatch,story='numbers',*,factory=None):
    path.mkdir(parents=True)
    rule={'story':story,'variant':'P1' if story=='numbers' else 'P0','rule_version':'1.0',
        'factor':2 if story=='numbers' else 1,'exclude_empty':story=='files'}
    config={'rule':rule};catalog=neutral.catalog(config)
    registration=plugin_registration(catalog)
    config_schema='rpnh/neutral_public_configuration/v1'
    registration.register_schema(config_schema,{**neutral.definition().config_schema,'$id':config_schema,'$schema':'http://json-schema.org/draft-07/schema#'})
    operation='neutral_fixture/'+story;module=build_plugin_module(catalog,operation)
    values={'values':[1.0,3.0,5.0],'unit':'m'} if story=='numbers' else {'files':{'a.txt':'alpha','empty.txt':''}}
    request={'schema_version':'rpnh/parent_child_request/v1','slot_id':'child','child_kind':'module','definition':module.to_dict(),
        'public_configuration':{'configuration':config,'inputs':values}}
    manifest=path/'rpnh_public_materials.json'
    boundary=path/'public-boundary.json';boundary.write_bytes(w.canonical({'boundary':'finite trusted offline installer declaration','native_execution':False}))
    distribution=LayoutDistribution('rpnh-neutral-fixture','1.0',{'rpnh_neutral_fixture/__init__.py':FIXTURE,'rpnh_public_materials.json':manifest,'public-boundary.json':boundary})
    rpnh_files={p.relative_to(ROOT).as_posix():p for p in (ROOT/'cpn').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.json')}
    rpnh_dist=LayoutDistribution('rpnh-candidate-layout','20261009',rpnh_files)
    # A fixed public runtime fixture distribution provides explicit file members.
    # It claims no OS package version or actual loader mapping.
    stdlib=Path(__import__('sysconfig').get_path('stdlib')).resolve()
    runtime_paths={'python':Path(sys.executable).resolve()}
    runtime_paths.update({'stdlib/'+p.relative_to(stdlib).as_posix():p for p in stdlib.rglob('*')
        if p.is_file() and 'site-packages' not in p.parts and '__pycache__' not in p.parts and p.suffix in ('.py','.so')})
    for name in ('libc.so.6','libm.so.6','libstdc++.so.6','libgcc_s.so.1','libz.so.1','libpthread.so.0','ld-linux-x86-64.so.2','libdl.so.2','libutil.so.1','librt.so.1'):
        runtime_paths['native/'+name]=Path('/lib/x86_64-linux-gnu')/name
    runtime_dist=LayoutDistribution('rpnh-public-runtime-layout','python-'+'.'.join(map(str,sys.version_info[:3])),runtime_paths)
    distributions={d.metadata['Name']:d for d in (distribution,rpnh_dist,runtime_dist)}
    for name in ('numpy','packaging','jsonschema','referencing','rpds-py','attrs','jsonschema-specifications'):
        dist=metadata.distribution(name);distributions[dist.metadata['Name']]=dist
    units=[];assets=[];name_to_unit={}
    for i,(name,dist) in enumerate(sorted(distributions.items())):
        unit_id='u.'+str(i);name_to_unit[name]=unit_id
        paths=dist.paths if isinstance(dist,LayoutDistribution) else _paths(dist)
        ids=[]
        for j,(member,p) in enumerate(sorted(paths.items())):
            aid='a.'+str(i)+'.'+str(j);ids.append(aid)
            raw=None if p==manifest else p.read_bytes()
            assets.append({'asset_id':aid,'member':member,'size':None if raw is None else len(raw),'sha256':None if raw is None else w.sha(raw)})
        units.append({'unit_id':unit_id,'distribution':{'name':name,'version':dist.version},'runtime_abi':sys.implementation.cache_tag,
            'mode':'installed_digest_identity','asset_ids':sorted(ids),'dependency_unit_ids':[]})
    runtime=name_to_unit[runtime_dist.metadata['Name']]
    for unit in units:
        if unit['unit_id']!=runtime:unit['dependency_unit_ids']=[runtime]
    prep=sorted(name_to_unit[n] for n in distributions if n not in ('numpy','packaging'))
    # Packaging is used in preparation by original plugins' identity validation.
    prep=sorted(set(prep)|{name_to_unit['packaging']})
    declarations=sorted(registration.declarations(),key=lambda d:(d['kind'],d['key']))
    registration_rows=[]
    for d in declarations:
        dependencies=[] if d['kind']=='schema' else [name_to_unit['rpnh-neutral-fixture'],name_to_unit['rpnh-candidate-layout']]
        if d['kind']=='executor':
            dependencies.append(name_to_unit['numpy'] if d['key']==catalog.operation_key('neutral_fixture/numbers') else name_to_unit['packaging'])
        registration_rows.append({'kind':d['kind'],'key':d['key'],'implementation_unit_ids':sorted(dependencies),'public_slot_ids':[]})
    def slot(identity,role,schema):return {'slot_id':identity,'role':role,'body_schema_id':schema,'cardinality':'one',
        'selection_rule':{'kind':'always','payload_kind':None,'registration_key':None}}
    contract={'schema_version':w.CONTRACT,'contract_id':'neutral-fixture','revision':'v1','profile_id':neutral.PROFILE_ID,
        'preparation_abi':'rpnh/public_material_preparation/v1','coverage_basis':'installer_declared_public_closure/v1','supported_payloads':['module'],
        'implementation_units':sorted(units,key=lambda v:v['unit_id']),
        'preparation_roots':{k:prep for k in ('profile_factory','registration_factory','lowerer','serializer','compiler','catalog_factory')},
        'execution_roots':{k:[] for k in ('executors','tools','services','host_bindings','configuration_sources','before_dispatch','adapter')},
        'registrations':registration_rows,'public_slots':[slot('budget','budget','rpnh/module_declaration/v1'),slot('configuration','public_host',config_schema),slot('input','input',schema_key(catalog,operation,'input'))],
        'binding_slots':[],'supported_shape':{'module_operations':['files','numbers'],
            'agent_task_mode':None,'local_process':False,'workflow':False,'managed_plugins':False},
        'assumptions':['binding_identity','dependency_completeness','preparation_side_effect_contract']}
    document={'schema_version':'rpnh/public_material_installation/v1','profiles':[{'contract':contract,'installation_id':'offline-layout',
        'runtime':{'implementation':sys.implementation.name,'version':'.'.join(map(str,sys.version_info[:3])),'abi_tag':sys.implementation.cache_tag,'platform_tag':sys.platform},'assets':sorted(assets,key=lambda a:a['asset_id'])}]}
    manifest.write_bytes(w.canonical(document))
    loads=[]
    def load():loads.append(True);return factory or neutral.profile
    ep=SimpleNamespace(name=neutral.PROFILE_ID,dist=distribution,load=load)
    actual_distribution=metadata.distribution
    monkeypatch.setattr(metadata,'distribution',lambda name: distributions[name] if name in distributions else actual_distribution(name))
    monkeypatch.setattr(metadata,'entry_points',lambda **kwargs: [ep] if kwargs.get('group')=='rpnh.environment_hosts' else [])
    return SimpleNamespace(request=request,module=module,registration=registration,contract=contract,manifest=manifest,document=document,entrypoint=ep,loads=loads,distributions=distributions,boundary=boundary)


def parent_with_request(path,request):
    # Original temporary owner/Start fixture, with exact new request substituted
    # before start_run. This modifies fixture input only, not product validation.
    from parent_child_fixtures import parent_owner
    return parent_owner(path,request_document=request)
