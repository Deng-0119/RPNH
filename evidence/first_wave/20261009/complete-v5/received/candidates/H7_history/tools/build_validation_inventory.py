"""Summarize only final frozen-source executions; never count historical reruns."""
from pathlib import Path
import json,xml.etree.ElementTree as ET
p=Path(__file__).resolve().parents[1]
identity=json.loads((p/'SOURCE_IDENTITY.json').read_text());expected=json.loads((p/'evidence/FINAL_SOURCE_MANIFEST.json').read_text())
runs=[];all_ids=[]
for name in ('author','adjacent','core-review'):
 stem='final2-'+name
 assert json.loads((p/'evidence'/f'{stem}-result.json').read_text())=={'pytest_exit':0,'source_unchanged':True}
 for ending in ('before','after'):
  assert json.loads((p/'evidence'/f'{stem}-source-{ending}.json').read_text())==expected
 cases=ET.parse(p/'evidence'/f'{stem}.xml').getroot().findall('.//testcase')
 rows=[]
 for case in cases:
  assert not any(case.find(x) is not None for x in ('failure','error','skipped'))
  ident=case.attrib['classname'].split('.')[-1]+'::'+case.attrib['name']
  rows.append(ident);all_ids.append(ident)
 runs.append({'run':stem,'passed':len(rows),'test_ids':rows})
review=p/'review/evidence/round3.xml'
cases=ET.parse(review).getroot().findall('.//testcase');rows=[]
for case in cases:
 assert not any(case.find(x) is not None for x in ('failure','error','skipped'))
 ident=case.attrib['classname'].split('.')[-1]+'::'+case.attrib['name'];rows.append(ident);all_ids.append(ident)
runs.append({'run':'independent-review-round3','passed':len(rows),'test_ids':rows})
assert len(all_ids)==len(set(all_ids)), 'Overlapping final node IDs must be explicitly reconciled'
result={'source_manifest_sha256':identity['source_manifest_sha256'],'unique_passing_testcases':len(set(all_ids)),'successful_executions':len(all_ids),'overlapping_test_ids':[],'runs':runs,'subprocess_exclusions':['test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs','test_invocation_functional_boundary.py::test_functional_modules_import_without_preloading_invocations','test_operation_functional_split.py::test_operation_facade_wrappers_delegate_and_preserve_contracts'],'scope':'Selected deterministic offline Registry tests only; not full repository, H7 native D1 or later lifecycle coverage','historical_run_prefixes_excluded':['first','debug','suite1','hardening-smoke','hardening2-smoke','hardening3-smoke','final-author','final-adjacent','final-core-review','round1','round2']}
(p/'evidence/FINAL_VALIDATION_INVENTORY.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('source_manifest_sha256','unique_passing_testcases','successful_executions')},indent=2))
