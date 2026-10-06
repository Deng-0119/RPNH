"""Pure dictionaries / connection spies. Never creates a DB or Registry run."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from cpn.frontend.firing_activity import parse_query, encode_cursor, decode_cursor, ActivityQueryError
from cpn.frontend.server import handle_request
from cpn.rpnh.registry._event_store.views import (
    ACTIVITY_TYPES, ActivityStaleError, ActivityCursorError, activity_reference, firing_activity_page, _activity_descriptor_bytes)
from cpn.rpnh.registry._event_store.queries import ACTIVITY_EVENT_COLUMNS, _activity_rows
from cpn.rpnh.registry.schema_catalog import canonical_json


def ref(stem, n, kind=None):
    return {'entity_type': (kind or stem) + '/v1', 'logical_id': stem + ':' + f'{n:032x}',
            'version_id': stem + '_version:' + f'{n:032x}'}


TASK=ref('task',1);RUN=ref('run',2,'native_run_identity');NET=ref('net_instance',3);CP=ref('marking_checkpoint',4)
SELECTOR={'net_ref':NET,'checkpoint_ref':CP,'cut':1}
NODE={**ref('node',5,'node_declaration'),'version_id':'node_declaration_version:'+f'{5:032x}'}
FIRE=ref('transition_firing',6);INV=ref('invocation',7);ADM=ref('firing_admission',8);LEASE=ref('operation_execution_lease',9)
BIND=ref('operation_binding',10);ROOT=ref('team_design_root',11);BRANCH=ref('task_branch',12);ROUND=ref('task_round',13)
SPEC=ref('operation_spec',14);PRINCIPAL=ref('principal',15);AUTH=ref('user_authority_decision',16);EXE=ref('executable_transition_binding',17)
SCOPE={'task_ref':TASK,'run_ref':RUN,'net_ref':NET,'transition_ids':['step.run'],'team_design_root_ref':ROOT,'task_branch_ref':BRANCH,'task_round_ref':ROUND,'branch_id':'main'}
BINDINGS=[{'transition_id':'step.run','node_ref':NODE,'operation_binding_ref':BIND,'executable_binding_ref':EXE,'operation_spec_ref':SPEC,'principal_ref':PRINCIPAL,'authority_decision_ref':AUTH}]
TX=lambda n:'transaction:'+f'{n:032x}'
EVENT=lambda n:'event:'+f'{n:032x}'


class Rows:
    def __init__(self, rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


class Adapter:
    """A deterministic read adapter, not a producer, actual DB or faulted Core."""
    read_only=True
    def __init__(self):
        self.connections=[];self.sql=[];self.materials=[];self.head=12;self.epoch=2;self.fresh=None;self.progress=None
        self.pub={'firing_version_id':FIRE['version_id'],'firing_logical_id':FIRE['logical_id'],'invocation_version_id':INV['version_id'],'invocation_logical_id':INV['logical_id'],
            'net_version_id':NET['version_id'],'operation_binding_version_id':BIND['version_id'],'admission_checkpoint_version_id':CP['version_id'],'state':'PROVISIONAL','opened_transaction_id':TX(1),'published_transaction_id':None}
        common={'task_ref':TASK,'net_instance_ref':NET,'task_branch_ref':BRANCH,'task_round_ref':ROUND,'agent_ref':None,'principal_ref':PRINCIPAL,'authority_decision_ref':AUTH}
        self.metadata={FIRE['version_id']:{**common,'transition_firing_ref':FIRE,'node_ref':NODE,'transition_id':'step.run','operation_binding_ref':BIND,'attempt_index':1,'firing_admission_ref':ADM,'admission_marking_checkpoint_ref':CP,'logical_tau':0,'claim_marking_delta_ref':ref('marking_delta',18)},
            INV['version_id']:{**common,'invocation_ref':INV,'own_transition_firing_ref':FIRE,'own_node_ref':NODE,'operation_binding_ref':BIND,'team_design_root_ref':ROOT,'operation_execution_lease_ref':LEASE,'admission_marking_checkpoint_ref':CP},
            ADM['version_id']:{'firing_admission_ref':ADM,'transition_firing_ref':FIRE,'invocation_ref':INV,'operation_execution_lease_ref':LEASE,'admission_marking_checkpoint_ref':CP,'logical_tau':0,'claim_marking_delta_ref':ref('marking_delta',18)},
            LEASE['version_id']:{'operation_execution_lease_ref':LEASE,'invocation_ref':INV}}
        self.events=[];self.objects={};self.members={};self.txs={};self.outbox={}
        def event(n,typ,payload,tx):
            return dict(ordinal=n,event_id=EVENT(n),event_type=typ,payload=payload,transaction_id=TX(tx),task_id=TASK['logical_id'],net_instance_id=NET['logical_id'],task_round_id=ROUND['logical_id'],producer_invocation_id=INV['logical_id'],branch_id='main',writer_fencing_epoch=2,criticality='authoritative',recorded_at='recorded')
        shared={'transition_firing_ref':FIRE,'invocation_ref':INV,'operation_execution_lease_ref':LEASE}
        for n,typ in [(3,ACTIVITY_TYPES[0]),(4,ACTIVITY_TYPES[1])]:
            self.events.append(event(n,typ,{**shared,**{k:self.metadata[ADM['version_id']][k] for k in ('firing_admission_ref','logical_tau','claim_marking_delta_ref')}},1))
        for n,r in enumerate([FIRE,INV,ADM,LEASE],5):
            meta=self.metadata[r['version_id']];body=canonical_json(meta)
            row={'object_type':r['entity_type'],'logical_id':r['logical_id'],'version_id':r['version_id'],'size':len(body),'media_type':'application/json','schema_ref':'test_schema',
                 'storage_locator':'test_descriptor','metadata_json':body.decode(),'producer_invocation_id':None if r==INV else INV['logical_id'],'transaction_id':TX(1),'published_event_id':EVENT(n)}
            self.objects[r['version_id']]=row
            ev=event(n,'object_version_published/v1',{**{k:row[k] for k in ('object_type','logical_id','version_id','size','media_type','schema_ref','storage_locator')},'metadata':meta},1)
            ev['producer_invocation_id']=row['producer_invocation_id'];self.events.append(ev)
            self.members[('object',r['version_id'])]=[{'firing_version_id':FIRE['version_id'],'transaction_id':TX(1)}]
        self.events.append(event(10,'transaction_committed/v1',{},1))
        self.events.append(event(11,ACTIVITY_TYPES[2],{**shared,**{k:BINDINGS[0][k] for k in ('operation_binding_ref','operation_spec_ref','principal_ref','authority_decision_ref')},'executable_transition_binding_ref':EXE,'agent_ref':None},2))
        self.events.append(event(12,'transaction_committed/v1',{},2))
        for e in self.events:self.members[('event',e['event_id'])]=[{'firing_version_id':FIRE['version_id'],'transaction_id':e['transaction_id']}]
        for tx in (TX(1),TX(2)):
            self.members[('transaction',tx)]=[{'firing_version_id':FIRE['version_id'],'transaction_id':tx}]
            self.txs[tx]={'transaction_id':tx,'task_id':TASK['logical_id'],'status':'committed','writer_epoch':2}
            self.outbox[tx]={'task_id':TASK['logical_id'],'writer_epoch':2,'event_ids_json':json.dumps([e['event_id'] for e in self.events if e['transaction_id']==tx])}
        self.catalog=SimpleNamespace(validate_fact_envelope=lambda _:None,validate_event_payload=lambda *a,**k:None,validate_instance=lambda *a,**k:None)
        self.object_store=SimpleNamespace(validate_envelope=lambda _:None,read_registered=lambda p:canonical_json(self.metadata[str(p.version_id)]))
    def _row_to_envelope(self,row):
        return SimpleNamespace(**{k:v for k,v in row.items() if k not in ('activity_firing_version_id','payload_json')},payload=json.loads(row['payload_json']))
    def connect(self):
        db=Connection(self,len(self.connections));self.connections.append(db);return db
    def page(self,**kw):
        with patch('cpn.rpnh.registry.event_store.fact_event_envelope',lambda e:vars(e)), patch(
                'cpn.rpnh.registry._event_store.views._activity_descriptor_bytes',lambda store,p:store.read_registered(p)):
            return firing_activity_page(self,catalog=self.catalog,object_store=self.object_store,scope=SCOPE,bindings=BINDINGS,expected_capture=(12,2),limit=kw.pop('limit',2),**kw)


class Connection:
    def __init__(self,owner,index):self.owner=owner;self.index=index;self.in_transaction=False
    def __enter__(self):return self
    def __exit__(self,*a):assert not self.in_transaction
    def set_progress_handler(self,p,n):self.owner.progress=p
    def execute(self,sql,args=()):
        o=self.owner;o.sql.append((self.index,sql,args))
        if sql=='BEGIN':self.in_transaction=True;return Rows([])
        if sql=='ROLLBACK':self.in_transaction=False;return Rows([])
        assert self.in_transaction,'autocommit facts forbidden'
        he=o.fresh if self.index and o.fresh else (o.head,o.epoch)
        if 'MAX(ordinal)' in sql:return Rows([(he[0],)])
        if 'FROM registry_meta' in sql:return Rows([(len(str(he[1])) if 'LENGTH' in sql else str(he[1]),)])
        import re
        stage,label=re.match(r'/\* activity-(preflight|material):([a-z-]+) \*/',sql).groups()
        values=args[:-1];limit=args[-1]
        def event_row(e):
            # SQL transport rows use serialized payload JSON, not a Python dict.
            row={k:e.get(k) for k in ACTIVITY_EVENT_COLUMNS}
            row['payload_json']=json.dumps(e['payload'],ensure_ascii=False,separators=(',',':'))
            return row
        if label=='head-transaction':rows=[{'transaction_id':TX(2)}]
        elif label=='transaction':rows=[o.txs[values[0]]]
        elif label=='outbox':rows=[o.outbox[values[0]]]
        elif label=='transaction-events':rows=[event_row(e) for e in o.events if e['transaction_id']==values[0]]
        elif label=='candidates':
            assert 'LIMIT ?' in sql and 'OFFSET' not in sql and 'ORDER BY e.ordinal,e.event_id' in sql
            ordinal,event_id=values[6],values[8]
            rows=[{**event_row(e),'activity_firing_version_id':FIRE['version_id']} for e in o.events
                  if e['event_type'] in ACTIVITY_TYPES and (e['ordinal']>ordinal or e['ordinal']==ordinal and e['event_id']>event_id)]
        elif label=='publication':rows=[o.pub]
        elif label=='member':rows=o.members.get(tuple(values),[])
        elif label=='descriptor':rows=[o.objects[values[0]]]
        else:raise AssertionError(sql)
        rows=rows[:limit]
        if stage=='preflight':
            assert 'LENGTH(CAST(' in sql and ' AS BLOB)' in sql
            if label in ('candidates','transaction-events'):
                for column in ACTIVITY_EVENT_COLUMNS:
                    assert 'CAST('+('e.' if label=='candidates' else '')+column+' AS BLOB)' in sql
            # Independent SQLite byte-count model; Unicode counts encoded bytes.
            cell=lambda v:0 if v is None else len(v) if isinstance(v,bytes) else len(str(v).encode('utf8'))
            sizes=[32+sum(8+cell(v) for v in row.values()) for row in rows]
            return Rows([(len(rows),sum(sizes),max((cell(v) for row in rows for v in row.values()),default=0))])
        o.materials.append((label,values,len(rows)))
        return Rows(rows)



class ActivityContracts(unittest.TestCase):
    def test_strict_query(self):
        good={**SELECTOR,'transition_ids':['step.run']}
        def query(v):return urlencode({k:json.dumps(x) if isinstance(x,(list,dict)) else x for k,x in v.items()})
        self.assertEqual(parse_query(query(good))['limit'],50)
        for changed in [dict(good,cut=v) for v in [0,-1,'01','1.0','true','NaN']]+[dict(good,limit=v) for v in [0,101,True,'1.2']]+[dict(good,transition_ids=v) for v in [[],['x','x'],['x']*65,True,'x']]+[dict(good,cursor=''),dict(good,through_ordinal=12)]:
            with self.assertRaises(ActivityQueryError):parse_query(query(changed))
        for q in [query(good)+'&cut=1',query(good)+'&x=y','x'*16385,query(good).replace('net_ref=', 'net_ref=%7B%22x%22%3A1%2C%22x%22%3A2%7D&unused=')]:
            with self.assertRaises(ActivityQueryError):parse_query(q)
    def test_typed_references_and_cursor(self):
        for value,kind in [(TASK,'task/v1'),(RUN,'native_run_identity/v1'),(NODE,'node_declaration/v1')]:self.assertEqual(activity_reference(value,kind),value)
        for v in [dict(TASK,logical_id=RUN['logical_id']),dict(TASK,extra=1),dict(TASK,version_id=True)]:
            with self.assertRaises((ValueError,TypeError)):activity_reference(v,'task/v1')
        capture={'head_ordinal':12,'writer_fencing_epoch':0};token=encode_cursor(SCOPE,capture,[4,EVENT(4)])
        self.assertEqual(decode_cursor(token)['capture'],capture)
        for bad in [token+'=', '', '!', 'a'*8193]:
            with self.assertRaises(ActivityQueryError):decode_cursor(bad)
    def test_api_methods_errors_and_identity(self):
        target='/api/v2/firing-activity?'+urlencode({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in {**SELECTOR,'transition_ids':['step.run']}.items()})
        class P:
            def firing_activity(self,**kw):return {'schema_version':'rpnh/firing_activity/v1','selector':{k:kw[k] for k in SELECTOR}}
        self.assertEqual(handle_request(P(),'GET',target).status,200)
        self.assertEqual(handle_request(P(),'HEAD',target).body,b'')
        self.assertEqual(handle_request(P(),'POST',target).status,405)
        self.assertEqual(handle_request(object(),'GET',target).status,501)
        for error,status in [(ActivityStaleError(),409),(ValueError(),503),(ActivityCursorError(),400)]:
            provider=SimpleNamespace(firing_activity=lambda **kw:(_ for _ in ()).throw(error))
            self.assertEqual(handle_request(provider,'GET',target).status,status)
        provider=SimpleNamespace(firing_activity=lambda **kw:{'schema_version':'rpnh/firing_activity/v1','selector':{}})
        self.assertEqual(handle_request(provider,'GET',target).status,503)
    def test_snapshot_keyset_and_complete_commits(self):
        a=Adapter();one=a.page();self.assertEqual([r['recorded_ordinal'] for r in one['records']],[3,4]);self.assertEqual([r['transaction_commit_ordinal'] for r in one['records']],[10,10]);self.assertTrue(one['has_more'])
        self.assertEqual(one['firings'][0]['publication_class_at_evidence'],'PROVISIONAL');self.assertIsNone(one['firings'][0]['publication_visible_position'])
        self.assertEqual(len(a.connections),2);self.assertEqual([sql for _,sql,_ in a.sql].count('BEGIN'),2)
        two=Adapter().page(after=[4,EVENT(4)]);self.assertEqual([r['recorded_ordinal'] for r in two['records']],[11]);self.assertEqual(two['records'][0]['transaction_commit_ordinal'],12);self.assertFalse(two['has_more'])
    def test_changed_head_and_same_head_new_epoch(self):
        for capture in [(13,2),(12,3)]:
            a=Adapter();a.head,a.epoch=capture
            with self.assertRaises(ActivityStaleError):a.page()
            a=Adapter();a.fresh=capture
            with self.assertRaises(ActivityStaleError):a.page()
    def test_cursor_must_exist_in_scope(self):
        with self.assertRaises(ActivityCursorError):Adapter().page(after=[4,EVENT(999)])
    def test_wrong_event_identity_and_exact_logical_id(self):
        for field,bad in [('invocation_ref',dict(INV,logical_id=ref('invocation',999)['logical_id'])),('transition_firing_ref',dict(FIRE,logical_id=ref('transition_firing',999)['logical_id'])),('operation_execution_lease_ref',ref('operation_execution_lease',999))]:
            a=Adapter();a.events[0]['payload'][field]=bad
            with self.assertRaises(ValueError):a.page()
    def test_ambiguous_member_transaction_and_publication(self):
        for mutate in [lambda a:a.members[('event',EVENT(3))].append(a.members[('event',EVENT(3))][0]),lambda a:a.members[('transaction',TX(1))].clear(),lambda a:a.pub.update(state='BROKEN'),lambda a:a.pub.update(net_version_id=ref('net_instance',999)['version_id'])]:
            a=Adapter();mutate(a)
            with self.assertRaises(ValueError):a.page()
    def test_terminal_outbox_and_descriptor_bytes(self):
        for mutate in [lambda a:a.outbox[TX(1)].update(event_ids_json='[]'),lambda a:a.txs[TX(1)].update(status='prepared'),lambda a:a.events.append({**a.events[-1],'ordinal':13,'event_id':EVENT(13)}),lambda a:a.objects[FIRE['version_id']].update(logical_id=ref('transition_firing',999)['logical_id']),lambda a:a.objects[FIRE['version_id']].update(size=256*1024+1)]:
            a=Adapter();mutate(a)
            with self.assertRaises((ValueError,RuntimeError)):a.page()
        a=Adapter();a.object_store.read_registered=lambda _:b'{}'
        with self.assertRaises(ValueError):a.page()
    def test_cached_second_and_later_full_reference_axes(self):
        for ordinal in (4,11):
            for bad in [dict(FIRE,logical_id=ref('transition_firing',999)['logical_id']),
                        dict(FIRE,entity_type='invocation/v1'),dict(FIRE,version_id=ref('transition_firing',999)['version_id']),
                        dict(FIRE,extra='alias')]:
                with self.subTest(ordinal=ordinal,bad=bad):
                    a=Adapter();next(e for e in a.events if e['ordinal']==ordinal)['payload']['transition_firing_ref']=bad
                    with self.assertRaises(ValueError):a.page(limit=3)
        a=Adapter();page=a.page(limit=3)
        self.assertEqual(len(page['records']),3)
        self.assertTrue(all(r['firing_ref']==page['firings'][0]['firing_ref'] for r in page['records']))

    def test_preflight_refuses_large_candidate_and_full_envelope_before_fetch(self):
        # Test a non-payload envelope field as well as a payload field: the
        # complete selected row, not just payload character length, is bounded.
        for label,ordinal,field in [('candidates',3,'recorded_at'),('transaction-events',12,'recorded_at')]:
            a=Adapter();next(e for e in a.events if e['ordinal']==ordinal)[field]='x'*131073
            with patch('cpn.rpnh.registry._event_store.views.ACTIVITY_MAX_BYTES',131072):
                with self.assertRaisesRegex(RuntimeError,'byte budget'):a.page()
            self.assertFalse(any(name==label for name,_,_ in a.materials),a.materials)
        a=Adapter();a.events[0]['payload']['large_unknown_field']='x'*131073
        with patch('cpn.rpnh.registry._event_store.views.ACTIVITY_MAX_BYTES',131072):
            with self.assertRaises(RuntimeError):a.page()
        self.assertFalse(any(name=='candidates' for name,_,_ in a.materials))

    def test_outbox_bytes_cannot_escape_budget_or_materialize_first(self):
        a=Adapter();a.outbox[TX(1)]['event_ids_json']=' '*131073+a.outbox[TX(1)]['event_ids_json']
        with patch('cpn.rpnh.registry._event_store.views.ACTIVITY_MAX_BYTES',131072):
            with self.assertRaisesRegex(RuntimeError,'byte budget'):a.page()
        self.assertFalse(any(name=='outbox' and args==(TX(1),) for name,args,_ in a.materials))

    def test_descriptor_cells_preflight_before_metadata_fetch(self):
        a=Adapter();a.objects[FIRE['version_id']]['metadata_json']=' '*262145
        with self.assertRaisesRegex(RuntimeError,'cell byte budget'):a.page()
        self.assertFalse(any(name=='descriptor' and args==(FIRE['version_id'],) for name,args,_ in a.materials))

    def test_byte_accounting_exact_boundary_unicode_and_cached_reads(self):
        a=Adapter();a.outbox[TX(1)]['event_ids_json']=' '*127+a.outbox[TX(1)]['event_ids_json']
        a.events[0]['recorded_at']='界'*31
        a.page(limit=3)
        self.assertEqual(sum(name=='outbox' and args==(TX(1),) for name,args,_ in a.materials),1)
        self.assertEqual(sum(name=='descriptor' for name,_,_ in a.materials),4)
        # A direct bounded-row adapter isolates the exact UTF-8 boundary without
        # inventing invalid Registry objects or creating a database.
        row={'text':'界'*31};bytes_needed=32+8+len(row['text'].encode('utf8'))
        class ByteDB:
            in_transaction=True
            def __init__(self):self.fetched=False
            def execute(self,sql,args):
                if 'activity-preflight' in sql:
                    self_test.assertIn('LENGTH(CAST(text AS BLOB))',sql)
                    return Rows([(1,bytes_needed,len(row['text'].encode('utf8')))])
                self.fetched=True;return Rows([row])
        self_test=self
        for limit,passes in [(bytes_needed,True),(bytes_needed-1,False),(32+8+len(row['text']),False)]:
            db=ByteDB();charged=[]
            def reserve(n):
                if n>limit:raise RuntimeError('over byte budget')
                charged.append(n)
            call=lambda:_activity_rows(db,columns=('text',),source_sql='dictionary',parameters=(),reserve=reserve,max_rows=1,label='unicode')
            if passes:self.assertEqual(call(),[row]);self.assertEqual(charged,[bytes_needed])
            else:
                with self.assertRaises(RuntimeError):call()
            self.assertEqual(db.fetched,passes)

    def test_bounded_descriptor_stream_growth_shortening_and_allowlist(self):
        calls=[]
        class Stream:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):calls.append(('read',n));return self.data[:n]
        class PathSpy:
            def __init__(self,actual,contents):self.actual,self.contents=actual,contents
            def stat(self):calls.append(('stat',));return SimpleNamespace(st_size=self.actual)
            def open(self,mode):calls.append(('open',mode));return Stream(self.contents)
        prepared=SimpleNamespace(object_type='transition_firing/v1',media_type='application/json',size=2,storage_locator='exact',version_id='version')
        store=SimpleNamespace(validate_envelope=lambda p:calls.append(('envelope',)),locator_for_version=lambda v:'exact')
        for stat_size,body,ok in [(2,b'{}',True),(100000,b'{}',False),(1,b'{',False),(2,b'{}later growth',False),(2,b'{',False)]:
            calls.clear();store.path_for_version=lambda v:PathSpy(stat_size,body)
            if ok:self.assertEqual(_activity_descriptor_bytes(store,prepared),b'{}')
            else:
                with self.assertRaises(ValueError):_activity_descriptor_bytes(store,prepared)
            if stat_size!=2:self.assertFalse(any(c[0]=='read' for c in calls))
            else:self.assertIn(('read',3),calls)
        for change in [{'object_type':'resource_version/v1'},{'media_type':'text/plain'},{'size':262145},{'storage_locator':'other'}]:
            calls.clear();wrong=SimpleNamespace(**{**vars(prepared),**change})
            with self.assertRaises(ValueError):_activity_descriptor_bytes(store,wrong)
            self.assertFalse(any(c[0]=='read' for c in calls))

    def test_finite_budget_and_sql_cancellation(self):
        a=Adapter()
        with patch('cpn.rpnh.registry._event_store.views.ACTIVITY_MAX_BYTES',1):
            with self.assertRaises(RuntimeError):a.page()
        a=Adapter()
        with patch('cpn.rpnh.registry._event_store.views.ACTIVITY_TX_EVENTS',1):
            with self.assertRaises(RuntimeError):a.page()
        a=Adapter()
        with patch('time.monotonic',side_effect=[0,10,10,10]):
            with self.assertRaises(RuntimeError):a.page()
        self.assertIsNone(a.progress)


if __name__=='__main__':unittest.main()
