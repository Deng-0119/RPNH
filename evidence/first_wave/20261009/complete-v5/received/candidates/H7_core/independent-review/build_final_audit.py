"""Recompute final evidence accounting without importing any product module."""
from pathlib import Path
from datetime import datetime, timezone
import json, hashlib, collections, sys, xml.etree.ElementTree as ET
R=Path(__file__).resolve().parent
A=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else R.parent/'rpnh-parent-child-core-implementation'
if not A.exists() and (R.parent/'source').is_dir(): A=R.parent
FINAL_SOURCE=R/'final-source-v2' if (R/'final-source-v2').is_dir() else A/'source'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def manifest(root):
    rows=[{'path':p.relative_to(root).as_posix(),'size':p.stat().st_size,'sha256':sha(p)}
          for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts]
    return {'files':rows,'file_count':len(rows),'manifest_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
def load(p):return json.loads(p.read_text())
def norm(node):
    filename, testcase = node.split('::', 1)
    return filename.rsplit('/', 1)[-1] + '::' + testcase
results=load(R/'FINAL_V2_RESULTS.json')
xml=ET.parse(R/'final-v2.xml').getroot()
suites=xml.findall('.//testsuite'); cases=xml.findall('.//testcase')
assert results['exit_status']==0 and not results['sentinel_blocked_events']
assert len(results['collected'])==85 and len(results['outcomes'])==85
assert len(set(results['collected']))==85
assert all(o['when']=='call' and o['outcome']=='passed' for o in results['outcomes'])
assert set(results['collected'])=={o['nodeid'] for o in results['outcomes']}
assert len(cases)==85 and all(int(s.attrib[k])==0 for s in suites for k in ('errors','failures','skipped'))
assert sorted(c.attrib['name'] for c in cases)==sorted(n.split('::',1)[1] for n in results['collected'])
before=load(R/'FINAL_V2_SOURCE_BEFORE.json'); after=load(R/'FINAL_V2_SOURCE_AFTER.json')
actual=manifest(FINAL_SOURCE);candidate=manifest(A/'source')
assert before==after==actual==candidate==load(R/'FINAL_V2_SOURCE_MANIFEST.json')
assert before['manifest_sha256']=='ac68327e442b7bda8a6aa2ba93c0cd20ad72181a661b0497627b08713334907e'
counts=collections.Counter(n.split('::')[0].split('/')[-1] for n in results['collected'])
original=[norm(n) for n in results['collected'] if '/tests/test_boundary_' in n]
reruns=[norm(n) for n in results['collected'] if '/tests/test_boundary_' not in n]
assert len(original)==40 and len(reruns)==45 and len(set(original+reruns))==85
history=[]
for p in sorted(R.glob('*RESULTS.json')):
    d=load(p);c=d.get('collected',[]);o=d.get('outcomes',[])
    history.append({'path':p.name,'sha256':sha(p),'source':d.get('source'),'exit_status':d.get('exit_status'),
        'collected':len(c),'distinct_collected':len(set(c)),
        'passed_call':sum(x['when']=='call' and x['outcome']=='passed' for x in o),
        'failed_reports':[x for x in o if x['outcome']!='passed'],
        'sentinel_blocked_events':d.get('sentinel_blocked_events'),
        'credited_as_final':p.name=='FINAL_V2_RESULTS.json'})
snapshot_audit=[]
for rootname,mp in [('round1-source','ROUND1_SOURCE_MANIFEST.json'),('round2-source','ROUND2_SOURCE_MANIFEST.json'),('round3-source','ROUND3_SOURCE_MANIFEST.json'),('final-source','FINAL_SOURCE_MANIFEST.json'),('final-source-v2','FINAL_V2_SOURCE_MANIFEST.json')]:
    expected=load(R/mp)
    if (R/rootname).is_dir(): m=manifest(R/rootname)
    elif rootname=='final-source-v2': m=actual
    else:
        delta=R/'historical-source-deltas'/rootname
        contract=load(delta/'RESTORE.json'); assert contract['base_manifest_sha256']==actual['manifest_sha256']
        virtual={row['path']:row for row in actual['files']}
        for relative in contract['copy_overlay_files_over_base']:
            p=delta/'overlay'/relative; virtual[relative]={'path':relative,'size':p.stat().st_size,'sha256':sha(p)}
        for relative in contract['remove_files_from_base']: virtual.pop(relative)
        rows=sorted(virtual.values(),key=lambda row:row['path'])
        m={'files':rows,'file_count':len(rows),'manifest_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    assert m['files']==expected['files']
    snapshot_audit.append({'source':rootname,'manifest':mp,'file_count':m['file_count'],'aggregate_sha256':m['manifest_sha256'],'mismatches':[]})
old=load(R/'FINAL_SOURCE_MANIFEST.json'); oldmap={x['path']:x for x in old['files']}
changed=[x['path'] for x in before['files'] if oldmap.get(x['path'])!=x]
assert changed==['cpn/rpnh/registry/parent_bound.py']
author_before=load(A/'evidence/final21-source-before.json')
author_after=load(A/'evidence/final21-source-after.json')
author_result=load(A/'evidence/final21-result.json')
assert author_before==author_after==actual
assert author_result=={'pytest_exit':0,'source_unchanged':True}
author_xml=ET.parse(A/'evidence/final21.xml').getroot()
author_suites=author_xml.findall('.//testsuite');author_cases=author_xml.findall('.//testcase')
assert len(author_cases)==99 and all(int(s.attrib[k])==0 for s in author_suites for k in ('errors','failures','skipped'))
author_ids={c.attrib['classname'].rsplit('.',1)[-1]+'.py::'+c.attrib['name'] for c in author_cases}
reviewer_ids=set(original+reruns)
assert len(author_ids)==99 and len(author_ids & reviewer_ids)==45 and len(author_ids | reviewer_ids)==139
joint={'source_manifest_sha256':actual['manifest_sha256'],'author_final_executions':99,
    'independent_final_executions':85,'total_final_executions':184,'overlapping_case_ids':45,'unique_case_ids':139,
    'author_original_core':31,'author_existing_adjacent':68,'reviewer_original':40,
    'author_deselected_not_run':3,'overlap_ids':sorted(author_ids & reviewer_ids),
    'unique_ids':sorted(author_ids | reviewer_ids),
    'author_evidence':[{ 'path':'evidence/'+name,'sha256':sha(A/'evidence'/name)}for name in ['final21.xml','final21.log','final21-result.json','final21-source-before.json','final21-source-after.json']],
    'independent_evidence':[{ 'path':name,'sha256':sha(R/name)}for name in ['FINAL_V2_RESULTS.json','final-v2.xml','final-v2.log','FINAL_V2_SOURCE_BEFORE.json','FINAL_V2_SOURCE_AFTER.json']]}
(R/'JOINT_FINAL_COUNTS.json').write_text(json.dumps(joint,ensure_ascii=False,indent=2)+'\n')
s1=load(A/'inputs/S1-source-identity.json')
for item in s1['candidate_files']:
    assert sha(A/'inputs/S1-source'/item['path'])==item['sha256']
s1_audit={'files_checked':len(s1['candidate_files']),'mismatches':[],
    'source_identity_file_sha256':sha(A/'inputs/S1-source-identity.json'),
    'patch_sha256':sha(A/'inputs/S1-static-lease-reads.patch')}
artifacts=[p for p in sorted(R.iterdir()) if p.is_file() and p.name not in {'FINAL_AUDIT.json','FINAL_REVIEW.md','DELIVERY_MANIFEST.json'}]
artifacts.extend(sorted((R/'tests').glob('*.py')))
artifacts.extend(p for p in sorted((R/'historical-source-deltas').rglob('*')) if p.is_file())
out={'review_utc':datetime.now(timezone.utc).isoformat(),'decision':'APPROVE_NARROW_REGISTRY_CORE_D0_ONLY',
 'product':'rpnh-parent-child-core-implementation','source_file_count':before['file_count'],
 'source_manifest_sha256':before['manifest_sha256'],'candidate_matches_independent_snapshot':True,
 'source_unchanged_during_final_run':True,'changed_from_superseded_61421_snapshot':changed,
 'tests':{'reviewer_authored':40,'author_case_reruns':31,'existing_S1_controls':5,'existing_Module_recovery_controls':9,
   'total_collected':85,'total_passed':85,'total_failed':0,'total_error':0,'total_skipped':0,
   'unique_normalized_pytest_nodeids':85,'sentinel_blocked_events':[],
   'counts_by_file':dict(counts),'reviewer_authored_ids':original,'rerun_ids':reruns,
   'deduplication_rule':'Normalize path prefixes to filename::testcase[param]. Repeated execution does not create a new testcase. Semantic overlap is not claimed absent.'},
 'final_run':{'json':'FINAL_V2_RESULTS.json','junit':'final-v2.xml','log':'final-v2.log','source_before':'FINAL_V2_SOURCE_BEFORE.json','source_after':'FINAL_V2_SOURCE_AFTER.json'},
 'scope':{'native_production_execution':'UNSUPPORTED','full_H7_O66_D0':'NOT_VERIFIED','H7_N36_D1':'NOT_RUN','H7b_R10':'NOT_RUN','full_repository':'NOT_VERIFIED'},
 'joint_final_counts':joint,'historical_result_windows':history,'snapshot_audit':snapshot_audit,'S1_input_recheck':s1_audit,
 'incomplete_windows':[{'path':'final-rerun.log','sha256':sha(R/'final-rerun.log'),'status':'INTERRUPTED_WITHOUT_TERMINAL_RESULT','credited_pass_count':0}],
 'evidence_files':[{'path':str(p.relative_to(R)),'size':p.stat().st_size,'sha256':sha(p)}for p in artifacts]}
(R/'FINAL_AUDIT.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:out[k]for k in ['decision','source_manifest_sha256','source_file_count','candidate_matches_independent_snapshot','source_unchanged_during_final_run']},indent=2))
print(json.dumps(out['tests']['counts_by_file'],indent=2))
