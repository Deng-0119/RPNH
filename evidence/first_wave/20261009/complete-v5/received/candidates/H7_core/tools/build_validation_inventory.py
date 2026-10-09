"""Collect only completed, source-stable final runs; preserve every exact node ID."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import xml.etree.ElementTree as ET

p=Path(__file__).resolve().parents[1]
r=p/'independent-review'
if not r.exists():
    r=p.parent/'rpnh-parent-child-core-boundary-review'
manifest=json.loads((p/'evidence/final21-source-after.json').read_text())
assert (p/'evidence/final21-source-before.json').read_bytes()==(p/'evidence/final21-source-after.json').read_bytes()
result=json.loads((p/'evidence/final21-result.json').read_text())
assert result=={'pytest_exit':0,'source_unchanged':True}
root=ET.parse(p/'evidence/final21.xml').getroot()
cases=[]
for case in root.iter('testcase'):
    assert not list(case), 'Non-passing candidate testcase'
    name='source/'+case.attrib['classname'].replace('.','/')+'.py::'+case.attrib['name']
    cases.append(name)
assert len(cases)==99 and len(set(cases))==99
review=json.loads((r/'FINAL_V2_RESULTS.json').read_text())
assert review['exit_status']==0 and review['sentinel_blocked_events']==[]
assert len(review['collected'])==85
assert len(review['outcomes'])==85
assert all(x['when']=='call' and x['outcome']=='passed' for x in review['outcomes'])
assert (r/'FINAL_V2_SOURCE_BEFORE.json').read_bytes()==(r/'FINAL_V2_SOURCE_AFTER.json').read_bytes()
review_manifest=json.loads((r/'FINAL_V2_SOURCE_AFTER.json').read_text())
assert review_manifest['manifest_sha256']==manifest['manifest_sha256']
reviewcases=[]
for node in review['collected']:
    file, name = node.split('::',1)
    filename=Path(file).name
    prefix='independent-review/tests/' if filename.startswith('test_boundary_') else 'source/tests/'
    reviewcases.append(prefix+filename+'::'+name)
assert len(set(reviewcases))==len(reviewcases)
allcases=sorted(set(cases)|set(reviewcases))
overlap=sorted(set(cases)&set(reviewcases))
assert len(allcases)==139 and len(overlap)==45
review_original=sorted(x for x in reviewcases if x.startswith('independent-review/'))
assert len(review_original)==40
v={'scope':'narrow Registry core offline candidate; not full H7 D0/D1',
   'source_file_count':manifest['file_count'],'source_aggregate_sha256':manifest['manifest_sha256'],
   'final_run_groups':[{'name':'candidate-final21','executions':99,'passed':99,'failed':0,'errors':0,'skipped':0,'deselected':3,'source_unchanged':True,'cases':cases},
      {'name':'independent-final-v2','executions':85,'passed':85,'failed':0,'errors':0,'skipped':0,'source_unchanged':True,'sentinel_blocked_events':[],'cases':reviewcases}],
   'final_execution_count':184,'unique_testcases':139,'overlap_between_final_run_groups':45,
   'unique_groups':{'author_core':31,'adjacent_regression':68,'reviewer_original':40},
   'overlapping_testcases':overlap,'all_unique_testcases':allcases,
   'explicit_subprocess_exclusions':[
     'source/tests/test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs',
     'source/tests/test_invocation_functional_boundary.py::test_functional_modules_import_without_preloading_invocations',
     'source/tests/test_operation_functional_split.py::test_operation_facade_wrappers_delegate_and_preserve_contracts'],
   'unverified':['full H7 66-item D0 matrix','all 36 D1 specifications','H7b 10 specifications','full repository regression','native peer/receipt/physical reservation/real wrapper integration','historical acceptance classifier','parent terminal observation/completion'],
   'counting_rule':'Parameterized node IDs count once across final runs. Exploratory, failed, interrupted, wrapper-invalid and superseded-source runs contribute zero to these final counts.'}
(p/'evidence/FINAL_VALIDATION_INVENTORY.json').write_text(json.dumps(v,indent=2)+'\n')
print(json.dumps({k:v[k] for k in ('source_aggregate_sha256','final_execution_count','unique_testcases','overlap_between_final_run_groups','unique_groups')},indent=2))
