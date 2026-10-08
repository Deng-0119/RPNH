from pathlib import Path
import hashlib,json,zipfile,xml.etree.ElementTree as ET
r=Path(__file__).resolve().parent

def stats(name):
 doc=ET.parse(r/name).getroot()
 suites=list(doc.iter('testsuite'))
 return {k:sum(int(s.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
summary={
 'base_commit':'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4',
 'patch_sha256':hashlib.sha256((r/'taskcontrol-reader-convergence.patch').read_bytes()).hexdigest(),
 'source_manifest_sha256':hashlib.sha256((r/'tested-source.sha256').read_bytes()).hexdigest(),
 'outcome':'offline_reader_convergence_passed; native_AF_UNIX_validation_blocked',
 'batches':[
  {'name':'final_focused_real_registry_consumers','status':'passed','junit':'focused-delivery.xml',**stats('focused-delivery.xml')},
  {'name':'existing_affected_compatibility','status':'passed','junit':'compatibility.xml',**stats('compatibility.xml')},
  {'name':'existing_shared_reader_consumers','status':'passed','junit':'shared-reader.xml',**stats('shared-reader.xml')},
  {'name':'unchanged_baseline_old_red','status':'expected_regression_failures','junit':'old-red.xml',**stats('old-red.xml')},
  {'name':'independent_review','status':'passed','tests':11,'log':'independent-review/new-green.log'},
  {'name':'independent_legal_large_descriptor','status':'passed','tests':1,'log':'independent-review/large-descriptor.log'},
  {'name':'independent_shared_reader_regressions','status':'passed','tests':24,'log':'independent-review/shared-core-regressions.log'},
  {'name':'native_taskcontrol_status','status':'blocked','blocked_tests':1,'passed_tests':6,'reason':'socket.socket(AF_UNIX, SOCK_STREAM): EPERM','junit':'independent-review/native-taskcontrol-status.xml',**stats('independent-review/native-taskcontrol-status.xml')},
  {'name':'native_owner_resume','status':'blocked','blocked_tests':1,'passed_tests':0,'reason':'OwnerEventLoop socket.socket(AF_UNIX, SOCK_STREAM): EPERM before provider dispatch; resume assertions not reached','junit':'independent-review/native-resume.xml',**stats('independent-review/native-resume.xml')}
 ],
 'counts_overlap':True,
 'external_model_calls':0,'github_actions':0,'remote_writes':0,'pushes':0,
 'accounting_evidence':'Native synthetic imported accounting baseline=2 remains cumulative across generation; separate (13,2) spy tests delegation/shape only.',
 'limits':'Registered object size is a physical backing-read bound; no fixed consumer size cap. Explicit caller descriptor budgets retain precedence.',
 'scope_exclusions':['new CLI/read command','H1 owner-entry changes','business task policy/scoring','viewer/net/help/parser changes','second Registry/read authority'],
 'local_gate':'COMMANDS.md native AF_UNIX taskcontrol/status and owner resume commands',
 'review':'independent-review/REVIEW.md'
}
(r/'validation-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
files=[]
for p in r.iterdir():
 if p.is_file() and p.suffix in {'.md','.json','.log','.xml','.patch','.txt','.sha256','.py'}:files.append(p)
for p in (r/'independent-review').iterdir():
 if p.is_file():files.append(p)
for path in (r/'changed-files.txt').read_text().splitlines():
 files.append(r/'source'/path)
 if (r/'baseline'/path).is_file():files.append(r/'baseline'/path)
files=sorted(set(files))
checksum=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(r))+'\n' for p in files)
(r/'SHA256SUMS').write_text(checksum)
files.append(r/'SHA256SUMS')
archive=r.parent/'RPNH_H2a_TaskControl_Reader_Convergence_20261008.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
 for p in files:z.write(p,'rpnh-taskcontrol-reader-convergence/'+str(p.relative_to(r)))
print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'size':archive.stat().st_size,'files':len(files)},indent=2))
