import json,hashlib,sqlite3
from pathlib import Path
package=Path(__file__).resolve().parents[1]
fixture=package/'evidence/final-tmp21/test_bound_origin_true_admissi0'
out={'source_manifest':'evidence/final21-source-after.json','source_aggregate_sha256':json.loads((package/'evidence/final21-source-after.json').read_text())['manifest_sha256'],'fixture':'test_bound_origin_true_admission_start_success','evidence_kind':'real temporary Registry/PN, explicitly injected offline native observation; no OS peer/Popen/reservation proof','parent':{},'child':{}}
for side in ('parent','child'):
 root=fixture/side/'.registry_v1'
 with sqlite3.connect((root/'registry.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  records=[]
  for row in db.execute("SELECT * FROM objects WHERE object_type IN ('native_run_identity/v1','native_genesis_manifest/v1','collaboration_source_binding/v1','parent_child_intent/v1','parent_child_dispatch/v1','parent_child_worker/v1','parent_child_acceptance/v1','parent_bound_bootstrap/v1','parent_bound_origin/v1') ORDER BY rowid"):
   version=row['version_id'].split(':')
   payload=(root/'objects'/version[0]/version[1]).read_bytes()
   records.append({'type':row['object_type'],'logical_id':row['logical_id'],'version_id':row['version_id'],
       'transaction_id':row['transaction_id'],'producer_invocation_id':row['producer_invocation_id'],
       'published_event_id':row['published_event_id'],'payload_sha256':hashlib.sha256(payload).hexdigest(),
       'metadata':json.loads(row['metadata_json'])})
  out[side]={'writer_epoch':int(db.execute("SELECT value FROM registry_meta WHERE key='writer_epoch'").fetchone()[0]),
    'records':records,'firing_roots':[dict(row) for row in db.execute('SELECT * FROM firing_publications')],
    'event_counts':{r[0]:r[1] for r in db.execute('SELECT event_type,COUNT(*) FROM events GROUP BY event_type')}}
  if side=='child':
   tokens=[dict(row) for row in db.execute("SELECT * FROM objects WHERE object_type='petri_token/v1'") if json.loads(row['metadata_json'])['place']=='step.h7origin']
   out[side]['protected_origin_token_count']=len(tokens)
   out[side]['protected_origin_tokens']=[json.loads(row['metadata_json']) for row in tokens]
out['assertions']={'parent_h7_record_count':sum(r['type'].startswith('parent_child_') for r in out['parent']['records']),
    'parent_unsettled_roots':sum(r['state']=='PROVISIONAL' for r in out['parent']['firing_roots']),
    'child_settled_roots':sum(r['state']=='PUBLISHED' for r in out['child']['firing_roots']),
    'child_ordinary_starts':out['child']['event_counts'].get('operation_execution_started/v1',0),
    'child_successes':out['child']['event_counts'].get('transition_firing_settled/v1',0),
    'one_exact_origin_token':out['child']['protected_origin_token_count']==1,
    'native_physical_target_reservation':'NOT_RUN','native_final_child_terminal':'NOT_ASSERTED',
    'parent_observation_completion':'UNSUPPORTED'}
(package/'evidence/NATIVE_D0_EXAMPLE.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out['assertions'],indent=2))
