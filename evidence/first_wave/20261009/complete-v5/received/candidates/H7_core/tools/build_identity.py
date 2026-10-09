from pathlib import Path
import json,hashlib,difflib
package=Path(__file__).resolve().parents[1]
base=package/'inputs/S1-source'
source=package/'source'
frozen=json.loads((package/'evidence/final21-source-after.json').read_text())
inputs=json.loads((package/'inputs/S1-source-identity.json').read_text())
old={r['path']:r for r in inputs['candidate_files']}
changed=[];parts=[]
for row in frozen['files']:
 path=row['path'];payload=(source/path).read_bytes();assert hashlib.sha256(payload).hexdigest()==row['sha256']
 previous=(base/path).read_bytes() if path in old else b''
 if path in old:assert hashlib.sha256(previous).hexdigest()==old[path]['sha256']
 if previous==payload:continue
 changed.append({'path':path,'S1_sha256':old.get(path,{}).get('sha256'),'candidate_sha256':row['sha256'],'size':row['size']})
 parts.extend(difflib.unified_diff(previous.decode().splitlines(keepends=True),payload.decode().splitlines(keepends=True),
     fromfile='a/'+path if previous else '/dev/null',tofile='b/'+path))
patch=''.join(parts).encode();(package/'H7-core.patch').write_bytes(patch)
record={'repository':'Deng-0119/RPNH','baseline_commit':inputs['base_commit'],'product_commit':inputs['base_product_commit'],
    'source_kind':'pinned curated S1 candidate plus H7 offline core overlay; no remote state assertion',
    'S1_file_count':len(old),'S1_patch_sha256':hashlib.sha256((package/'inputs/S1-static-lease-reads.patch').read_bytes()).hexdigest(),
    'S1_source_identity_sha256':hashlib.sha256((package/'inputs/S1-source-identity.json').read_bytes()).hexdigest(),
    'H7_patch_sha256':hashlib.sha256(patch).hexdigest(),'source_manifest_sha256':frozen['manifest_sha256'],
    'source_file_count':len(frozen['files']),'changed_files':changed,
    'source_manifest':'evidence/final21-source-after.json','source_unchanged_during_final21':True,
    'native_status':'NOT_RUN / production transport issuer and bound writable entry not installed'}
(package/'SOURCE_IDENTITY.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:v for k,v in record.items() if k!='changed_files'},indent=2))
print('Changed files:',len(changed))
