"""Synthetic, read-only boundary contracts; no Registry run is created.

Run directly with PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python this-file.
These tests do not claim real execution/firing or writer-concurrency coverage.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from cpn.frontend.checkpoint_view import selector, _BoundedReads, _tokens, _chain, _inventory, _identity
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import handle_request


def ref(kind, n):
    stem = kind.split('/')[0]
    return {'entity_type': kind, 'logical_id': stem + ':' + str(n) * 32,
            'version_id': stem + '_version:' + str(n) * 32}


NET = ref('net_instance/v1', 1)
CHECKPOINT = ref('marking_checkpoint/v1', 2)
SELECTED = selector(NET, CHECKPOINT, 75)


def path(selected=SELECTED):
    return '/api/v2/checkpoint-view?' + urlencode({k: json.dumps(v) if isinstance(v, dict) else v
                                                 for k, v in selected.items()})


class Provider:
    def __init__(self): self.calls = []
    def checkpoint_view(self, **selected):
        self.calls.append(selected)
        return {'schema_version': 'rpnh/checkpoint_view/v1', 'selector': selected,
                'frame': {'net': {'schema_version': 'rpnh/net_view/v1', 'source': {'mode': 'registry_current'},
                                  'summary': {}, 'nodes': [], 'edges': []}}}


class CheckpointContracts(unittest.TestCase):
    def test_exact_get_and_head(self):
        provider = Provider()
        response = handle_request(provider, 'GET', path())
        self.assertEqual(response.status, 200)
        self.assertEqual(provider.calls, [SELECTED])
        self.assertEqual(handle_request(provider, 'HEAD', path()).body, b'')
        self.assertEqual(handle_request(provider, 'POST', path()).status, 405)

    def test_parameters_never_infer_latest(self):
        provider = Provider()
        cases = ['/api/v2/checkpoint-view', path() + '&cut=75', path() + '&cursor=75',
                 path().replace('cut=75', 'cut=0'), path().replace('cut=75', 'cut=075'),
                 path().replace('cut=75', 'cut=-1'), path().replace('cut=75', 'cut=1.0'),
                 path().replace('cut=75', 'cut='), '/api/v2/checkpoint-view?net_ref={}&checkpoint_ref={}&cut=75']
        for target in cases:
            with self.subTest(target=target): self.assertEqual(handle_request(provider, 'GET', target).status, 400)
        self.assertEqual(provider.calls, [])

    def test_duplicate_json_keys_rejected_before_provider(self):
        provider = Provider()
        value = json.dumps(NET)[:-1] + ',"logical_id":"' + NET['logical_id'] + '"}'
        target = '/api/v2/checkpoint-view?' + urlencode({'net_ref': value, 'checkpoint_ref': json.dumps(CHECKPOINT), 'cut': 75})
        self.assertEqual(handle_request(provider, 'GET', target).status, 400)
        self.assertEqual(provider.calls, [])

    def test_wrong_types_and_extra_reference_fields(self):
        for selected in [dict(SELECTED, cut=True), dict(SELECTED, net_ref=dict(NET, extra=1)),
                         dict(SELECTED, net_ref=dict(NET, entity_type='task/v1')),
                         dict(SELECTED, checkpoint_ref=NET)]:
            with self.assertRaises((ValueError, TypeError)): selector(**selected)

    def test_unsupported_and_changed_source_fail_closed(self):
        self.assertEqual(handle_request(object(), 'GET', path()).status, 501)
        class Moving(Provider):
            def checkpoint_view(self, **selected): raise RuntimeError('source changed')
        self.assertEqual(handle_request(Moving(), 'GET', path()).status, 503)
        core = SimpleNamespace(event_store=SimpleNamespace(max_ordinal=lambda: 131, writer_epoch=2))
        with self.assertRaises(RuntimeError):
            RegistryDashboard._stable(core, {'source': {'verified_head_ordinal': 130, 'writer_fencing_epoch': 2}})
        core.event_store.max_ordinal = lambda: 130
        core.event_store.writer_epoch = 3
        with self.assertRaises(RuntimeError):
            RegistryDashboard._stable(core, {'source': {'verified_head_ordinal': 130, 'writer_fencing_epoch': 2}})

    def test_endpoint_detects_provider_selector_substitution(self):
        class Wrong(Provider):
            def checkpoint_view(self, **selected):
                result = super().checkpoint_view(**selected)
                result['selector'] = dict(selected, cut=76)
                return result
        self.assertEqual(handle_request(Wrong(), 'GET', path()).status, 400)

    def test_static_model_and_existing_dependency_routes(self):
        for module in ('checkpoint-view', 'agent-members', 'observation-panel'):
            self.assertEqual(handle_request(object(), 'GET', '/' + module + '.mjs').status, 200)

    def test_complete_commit_lookup_is_read_only_dictionary_query(self):
        reads = object.__new__(_BoundedReads)
        event = SimpleNamespace(ordinal=75)
        reads.commits = {'transaction:a': event}
        self.assertIs(reads.transaction_commit_event('transaction:a'), event)
        with self.assertRaises(ValueError): reads.transaction_commit_event('transaction:missing')

    def test_descriptor_publication_bytes_and_self_are_all_checked(self):
        reference = ref('marking_checkpoint/v1', 3)
        metadata = {'marking_checkpoint_ref': reference}
        row = {'object_type': 'marking_checkpoint/v1', 'logical_id': reference['logical_id'],
               'version_id': reference['version_id'], 'metadata_json': json.dumps(metadata),
               'transaction_id': 'transaction:a', 'published_event_id': 'event:a', 'producer_invocation_id': None,
               'size': 3, 'media_type': 'application/json', 'schema_ref': 'schema', 'storage_locator': 'test'}
        def reads_for(row_value, payload=None, data=None):
            reads = object.__new__(_BoundedReads); reads.cache = {}; reads.view = 'fixed-75'; reads.cut = 75
            reads.verify_transaction = lambda _: None  # This unit isolates descriptor validation, not terminal validation.
            publication = {k: row_value[k] for k in ('object_type','logical_id','version_id','size','media_type','schema_ref','storage_locator')}
            publication['metadata'] = json.loads(row_value['metadata_json'])
            reads.events = [SimpleNamespace(event_id='event:a', event_type='object_version_published/v1', transaction_id='transaction:a',
                                           producer_invocation_id=None, payload=publication if payload is None else payload)]
            reads.core = SimpleNamespace(event_store=SimpleNamespace(object_row_for_view=lambda view, version: row_value),
                catalog=SimpleNamespace(validate_instance=lambda *a, **k: None),
                object_store=SimpleNamespace(validate_envelope=lambda _: None,
                    read_registered=lambda _: json.dumps(metadata if data is None else data).encode()))
            return reads
        self.assertEqual(reads_for(row).metadata(reference, reference['entity_type']), metadata)
        with self.assertRaises(ValueError): reads_for(row, payload={}).metadata(reference, reference['entity_type'])
        with self.assertRaises(ValueError): reads_for(row, data={}).metadata(reference, reference['entity_type'])
        wrong = dict(row, metadata_json=json.dumps({'marking_checkpoint_ref': CHECKPOINT}))
        with self.assertRaises(ValueError): reads_for(wrong).metadata(reference, reference['entity_type'])

    def test_terminal_outbox_and_bounded_transaction_rows(self):
        # A synthetic read adapter, never SQLite or a generated Registry run.
        terminal = SimpleNamespace(event_id='event:terminal', event_type='transaction_committed/v1',
            transaction_id='transaction:a', task_id='task:a', writer_fencing_epoch=2, ordinal=75,
            criticality='authoritative', payload={})
        ordinary = SimpleNamespace(**{**vars(terminal), 'event_id':'event:fact', 'event_type':'fact/v1', 'ordinal':74})
        transaction = {'status':'committed','task_id':'task:a','writer_epoch':2}
        outbox = {'task_id':'task:a','writer_epoch':2,'event_ids_json':json.dumps(['event:fact','event:terminal'])}
        queries = []
        class DB:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self, sql, args):
                queries.append((sql,args))
                if 'FROM transactions' in sql: return SimpleNamespace(fetchone=lambda:transaction)
                if 'FROM outbox' in sql: return SimpleNamespace(fetchone=lambda:outbox)
                if 'FROM events' in sql:
                    self_test.assertIn('ordinal<=?',sql); self_test.assertEqual(args[-1],75)
                    return SimpleNamespace(fetchall=lambda:[ordinary,terminal])
                raise AssertionError('unexpected unbounded SQL')
        self_test=self
        reads=object.__new__(_BoundedReads);reads.cut=75;reads.commits={'transaction:a':terminal}
        reads.verified_transactions=set()
        reads.core=SimpleNamespace(event_store=SimpleNamespace(connect=DB,_row_to_envelope=lambda value:value),
            catalog=SimpleNamespace(validate_fact_envelope=lambda value:None,validate_event_payload=lambda *a,**k:None))
        with patch('cpn.frontend.checkpoint_view.fact_event_envelope',lambda value:vars(value)):
            reads.verify_transaction('transaction:a'); self.assertEqual(len(queries),3)
            reads.verify_transaction('transaction:a'); self.assertEqual(len(queries),3)
            reads.verified_transactions.clear();outbox['event_ids_json']=json.dumps(['event:fact','event:terminal','event:later'])
            with self.assertRaises(ValueError): reads.verify_transaction('transaction:a')
            outbox['event_ids_json']=json.dumps(['event:fact','event:terminal']);transaction['status']='prepared'
            with self.assertRaises(ValueError): reads.verify_transaction('transaction:a')
            transaction['status']='committed';outbox['writer_epoch']=3
            with self.assertRaises(ValueError): reads.verify_transaction('transaction:a')

    def test_token_exact_occurrences_and_foreign_resources(self):
        token_ref = ref('petri_token/v1', 3)
        data = {'petri_token_ref': token_ref, 'net_instance_ref': NET, 'token_id': 0, 'place': 'p', 'epoch': 0,
                'kind': None, 'consumed_by': None, 'resource_ref': None, 'work_resource_ref': None}
        checkpoint = {'token_refs': [token_ref], 'epoch': 0, 'next_token_id': 1}
        reads = SimpleNamespace(metadata=lambda reference, kind: data)
        self.assertEqual(len(_tokens(reads, checkpoint, NET, {'p': 1}, {})), 1)
        for change in ({'net_instance_ref': {}}, {'place': 'missing'}, {'petri_token_ref': CHECKPOINT}, {'token_id': 1}):
            original = deepcopy(data); data.update(change)
            with self.assertRaises(ValueError): _tokens(reads, checkpoint, NET, {'p': 1}, {})
            data.clear(); data.update(original)
        with self.assertRaises(ValueError): _tokens(reads, dict(checkpoint, token_refs=[token_ref,token_ref]), NET, {'p': 1}, {})

    def test_reader_limit_is_partial_and_cannot_full_scan_for_target(self):
        newer = selector(ref('net_instance/v1',4), ref('marking_checkpoint/v1',5),130)
        fixtures = {newer['checkpoint_ref']['version_id']: ({'net_instance_ref': newer['net_ref'], 'previous_checkpoint_ref': CHECKPOINT},130),
                    CHECKPOINT['version_id']: ({'net_instance_ref': NET,'previous_checkpoint_ref':None},75)}
        class Reads:
            def __init__(self, core, cut): pass
            def checkpoint(self, reference):
                value,cut = fixtures[reference['version_id']]
                return value,SimpleNamespace(ordinal=cut,recorded_at='saved')
        observation = {'source':{'verified_head_ordinal':130}, 'net':{'marking':{'checkpoint_ref':newer['checkpoint_ref']}}}
        with patch('cpn.frontend.checkpoint_view._BoundedReads',Reads), patch('cpn.frontend.checkpoint_view._identity',lambda *a:(None,None,{})):
            provider = SimpleNamespace(max_checkpoints=1)
            _,navigation,_ = _chain(provider,None,observation,newer)
            self.assertEqual(navigation['coverage'],'partial');self.assertEqual(navigation['end_reason'],'reader_limit')
            with self.assertRaisesRegex(ValueError,'reader_limit'): _chain(provider,None,observation,SELECTED)
            provider.max_checkpoints=2
            _,navigation,_ = _chain(provider,None,observation,newer)
            self.assertEqual(navigation['previous_net_segment'],SELECTED)


def synthetic_inventory():
    """Detached display-contract data; never a Registry run or writer fixture."""
    def make(kind, logical, version, n):
        return {'entity_type':kind,'logical_id':logical+':'+format(n,'032x'),'version_id':version+':'+format(n,'032x')}
    kinds = [('net_instance/v1','net_instance','net_instance_version'),('team_design_root/v1','team_design_root','team_design_root_version'),
             ('task/v1','task','task_version'),('native_run_identity/v1','run','run_version'),('task_round/v1','task_round','task_round_version'),
             ('principal/v1','principal','principal_version'),('plan_version/v1','plan','plan_version'),
             ('node_declaration/v1','node','node_declaration_version'),('operation_binding/v1','operation_binding','operation_binding_version'),
             ('operation_spec/v1','operation_spec','operation_spec_version'),('output_binding/v1','output_binding','output_binding_version'),
             ('executable_transition_binding/v1','executable_transition_binding','executable_transition_binding_version'),
             ('user_authority_decision/v1','user_authority_decision','user_authority_decision_version'),
             ('resource_version/v1','resource','resource_version'),('resource_version/v1','resource','resource_version'),
             ('resource_version/v1','resource','resource_version')]
    r = {name:make(*kind,i+1) for i,(name,kind) in enumerate(zip(
        ['net','root','task','run','round','owner','plan','node','binding','spec','output','executable','authority','wire','schema','input'],kinds))}
    pair=lambda name:{'resource_id':r[name]['logical_id'],'resource_version_id':r[name]['version_id']}
    schema_id='application/checkpoint_contract/v1'; schema={'$id':schema_id,'type':'string'}
    product=SimpleNamespace(port='step.out',minimum=1,maximum=1)
    operation=SimpleNamespace(name='step.op',inputs=['step.in'],outputs=['step.out'],tools=[],request_port=None,
                              outcomes=[SimpleNamespace(name='complete',products=[product])])
    item=SimpleNamespace(operation_id='op_0',executor_key='contract/executor/v1',declaration=operation,
                         executor_declaration={'identity':{'id':'fixed'},'contracts':{'transport':'deterministic'}})
    ports=[SimpleNamespace(name='step.in',port_id='port_0',place='step.request',schema=schema_id,minimum=1,maximum=1),
           SimpleNamespace(name='step.out',port_id='port_1',place='step.result',schema=schema_id,minimum=1,maximum=1)]
    compiled=SimpleNamespace(schema_version='rpnh/executable_net/v1',operations=[item],ports=ports,
        registrations={'schema':{schema_id:{'schema':schema}}},
        symbolic=SimpleNamespace(transitions=[SimpleNamespace(name='step.run',operation='step.op')],
            arcs=[SimpleNamespace(transition='step.run',direction='input',place='step.request'),
                  SimpleNamespace(transition='step.run',direction='output',place='step.result')]))
    shared={'team_design_root_ref':r['root'],'task_round_ref':r['round'],'llm_macro_net_ref':r['wire'],
        'team_net_declaration_resource_ref':pair('wire'),'node_refs':[r['node']],'operation_binding_refs':[r['binding']],
        'output_binding_refs':[r['output']],'executable_transition_binding_refs':[r['executable']]}
    port_record=lambda port:{'port_id':port.port_id,'place':port.place,'schema_ref':r['schema'],'content_schema_ref':pair('schema'),
                             'cardinality':{'minimum':1,'maximum':1},'lease_identity_ref':None}
    values={
        'net':dict(shared,net_instance_ref=r['net'],plan_ref=r['plan'],module_resource_bindings={'owner_resource_inputs':{},'slot_refs':{},'lease_refs':{},'slot_bindings':{}}),
        'root':dict(shared,owner_principal_ref=r['owner'],task_ref=r['task'],run_ref=r['run'],resource_refs=[r['wire'],r['schema'],r['input']],artifact_refs=[r['plan'],r['spec'],r['authority']]),
        'task':{}, 'run':{'task_ref':r['task'],'task_branch_ref':{'branch':'one'}},
        'round':{'task_id':r['task']['logical_id'],'task_branch_ref':{'branch':'one'}},
        'owner':{'principal_id':r['owner']['logical_id'],'principal_version_id':r['owner']['version_id']},
        'plan':{'node_ids':[r['node']['logical_id']]},
        'node':{'node_ref':r['node'],'transition_id':'step.run','team_design_root_ref':r['root'],'plan_ref':r['plan'],
            'producer_operation_binding_ref':r['binding'],'opaque_role_artifact_ref':r['spec'],'offered_output_binding_refs':[r['output']],
            'activation_ref':None,'input_resource_refs':[r['wire'],r['input']],'input_schema_refs':[r['schema']],'output_schema_refs':[r['schema']]},
        'binding':{'operation_binding_ref':r['binding'],'node_ref':r['node'],'team_design_root_ref':r['root'],'operation_spec_ref':r['spec'],
            'principal_ref':r['owner'],'authority_decision_ref':r['authority'],'output_binding_refs':[r['output']],
            'input_binding_refs':[r['wire'],r['input'],r['schema']],'input_schema_refs':[r['schema']],'output_schema_refs':[r['schema']],
            'discoverable_resource_refs':[r['wire'],r['input']],'readable_resource_refs':[r['wire'],r['input']]},
        'spec':{'operation_id':'op_0','executor_key':item.executor_key,'implementation_identity':deepcopy(item.executor_declaration['identity']),
            'implementation_contracts':deepcopy(item.executor_declaration['contracts']),'allowed_tool_ids':[],'llm_prompt_port_id':None,
            'input_ports':[port_record(ports[0])],'output_ports':[port_record(ports[1])]},
        'output':{'node_ref':r['node'],'team_design_root_ref':r['root'],'net_ref':r['net'],'task_round_ref':r['round'],
            'opaque_action_ref':r['spec'],'output_port_id':'port_1','place':'step.result','place_ref':r['schema'],
            'content_schema_ref':pair('schema'),'content_schema_id':schema_id,'normal_output_cardinality':{'minimum':1,'maximum':1},'declared_outcome_id':'complete'},
        'executable':{'net_instance_ref':r['net'],'transition_id':'step.run','node_ref':r['node'],'operation_binding_ref':r['binding'],
            'declaration_resource_ref':pair('wire'),'declaration_schema_ref':'rpnh/executable_net/v1','principal_ref':r['owner'],
            'activation_ref':None,'input_place_ids':['step.request'],'output_place_ids':['step.result']},
        'authority':{'status':'effective','user_principal_ref':r['owner'],'governed_artifact_refs':[r['task']]},
        'wire':{'task_ref':r['task']},'schema':{'task_ref':r['task']},'input':{'task_ref':r['task']}}
    class Reads:
        core=SimpleNamespace(task_id=r['task']['logical_id'])
        def metadata(self,reference,kind):
            name=next((name for name,value in r.items() if value==reference),None)
            if name is None or reference['entity_type']!=kind:raise ValueError('not canonical in synthetic read bag')
            return values[name]
        def declaration(self,*args):return compiled
        def port_schema(self,reference,root,key,expected):
            if reference!=r['schema'] or key!=schema_id or canonical_json(expected)!=canonical_json(schema):
                raise ValueError('schema differs from synthetic fixed material')
    checkpoint={'net_instance_ref':r['net'],'team_design_root_ref':r['root']}
    selected=selector(r['net'],CHECKPOINT,75)
    return Reads(),selected,checkpoint,r,values,compiled


class InventoryClosureContracts(unittest.TestCase):
    def test_valid_detached_material_inventory(self):
        reads,selected,checkpoint,refs,values,compiled=synthetic_inventory()
        result=_inventory(reads,selected,checkpoint,None)
        self.assertIs(result[0],compiled);self.assertEqual(result[1][0]['transition_id'],'step.run')

    def test_missing_same_cut_material_and_root_membership_matrix(self):
        def missing(reference):return dict(reference,version_id=reference['version_id'].split(':')[0]+':'+'0'*32)
        for label in ['owner','principal','role','plan','input-resource','input-schema','binding-input','binding-output-schema','authority',
                      'root-spec','root-plan','root-authority','root-schema']:
            with self.subTest(label=label):
                reads,selected,checkpoint,r,v,_=synthetic_inventory()
                if label=='owner':v['root']['owner_principal_ref']=missing(r['owner'])
                elif label=='principal':v['binding']['principal_ref']=v['executable']['principal_ref']=missing(r['owner'])
                elif label=='role':v['node']['opaque_role_artifact_ref']=missing(r['spec'])
                elif label=='plan':v['net']['plan_ref']=v['node']['plan_ref']=missing(r['plan'])
                elif label=='input-resource':v['node']['input_resource_refs'][1]=missing(r['input'])
                elif label=='input-schema':v['node']['input_schema_refs']=[missing(r['schema'])]
                elif label=='binding-input':v['binding']['input_binding_refs'][1]=missing(r['input'])
                elif label=='binding-output-schema':v['binding']['output_schema_refs']=[missing(r['schema'])]
                elif label=='authority':v['binding']['authority_decision_ref']=missing(r['authority'])
                elif label=='root-schema':v['root']['resource_refs'].remove(r['schema'])
                else:v['root']['artifact_refs'].remove(r[label.removeprefix('root-')])
                with self.assertRaises(ValueError):_inventory(reads,selected,checkpoint,None)

    def test_wire_port_and_outcome_semantic_matrix(self):
        for label in ['output-place','input-place','spec-output-place','place-ref','outcome','schema-id','cardinality','schema-ref','executor-contract']:
            with self.subTest(label=label):
                reads,selected,checkpoint,r,v,_=synthetic_inventory()
                if label=='output-place':v['output']['place']='step.request'
                elif label=='input-place':v['spec']['input_ports'][0]['place']='step.result'
                elif label=='spec-output-place':v['spec']['output_ports'][0]['place']='step.request'
                elif label=='place-ref':v['output']['place_ref']=r['wire']
                elif label=='outcome':v['output']['declared_outcome_id']='undeclared'
                elif label=='schema-id':v['output']['content_schema_id']='application/other/v1'
                elif label=='cardinality':v['spec']['input_ports'][0]['cardinality']['maximum']=2
                elif label=='schema-ref':v['spec']['input_ports'][0]['schema_ref']=r['wire']
                elif label=='executor-contract':v['spec']['implementation_contracts']={'transport':'llm'}
                with self.assertRaises(ValueError):_inventory(reads,selected,checkpoint,None)

    def test_compiler_contract_comparison_preserves_json_types(self):
        reads,selected,checkpoint,refs,values,compiled=synthetic_inventory()
        compiled.operations[0].executor_declaration['contracts']['flag']=True
        values['spec']['implementation_contracts']['flag']=1
        with self.assertRaises(ValueError):_inventory(reads,selected,checkpoint,None)

    def test_distinct_resolved_principal_is_not_blindly_replaced_by_owner(self):
        reads,selected,checkpoint,r,v,_=synthetic_inventory()
        r['delegate']=dict(r['owner'],logical_id='principal:'+'f'*32,version_id='principal_version:'+'f'*32)
        v['delegate']={'principal_id':r['delegate']['logical_id'],'principal_version_id':r['delegate']['version_id']}
        v['binding']['principal_ref']=v['executable']['principal_ref']=r['delegate']
        self.assertEqual(len(_inventory(reads,selected,checkpoint,None)[1]),1)

    def test_port_schema_read_is_scoped_and_json_type_sensitive(self):
        schema_ref={'entity_type':'resource_version/v1','logical_id':'resource:'+'1'*32,'version_id':'resource_version:'+'1'*32}
        root={'resource_refs':[schema_ref],'task_ref':NET}
        meta={'task_ref':NET}
        reads=object.__new__(_BoundedReads);reads.schema_reads={}
        reads.metadata=lambda *args:meta;reads.row=lambda *args:{}
        reads.prepared=lambda *args:SimpleNamespace(media_type='application/schema+json')
        document={'$id':'application/schema_test/v1','additionalProperties':True}
        reads.core=SimpleNamespace(object_store=SimpleNamespace(read_registered=lambda prepared:json.dumps(document).encode()))
        reads.port_schema(schema_ref,root,document['$id'],document)
        wrong=dict(document,additionalProperties=1)
        with self.assertRaises(ValueError):reads.port_schema(schema_ref,root,document['$id'],wrong)
        with self.assertRaises(ValueError):reads.port_schema(schema_ref,dict(root,resource_refs=[]),document['$id'],document)
        reads.prepared=lambda *args:SimpleNamespace(media_type='application/json')
        with self.assertRaises(ValueError):reads.port_schema(schema_ref,root,document['$id'],document)


if __name__ == '__main__':
    unittest.main()
