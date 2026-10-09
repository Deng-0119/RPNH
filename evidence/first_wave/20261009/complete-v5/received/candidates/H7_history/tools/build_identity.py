"""Build an exact narrow overlay and identity from frozen local package inputs."""
from pathlib import Path
import difflib,hashlib,json
p=Path(__file__).resolve().parents[1]
def sha(f):return hashlib.sha256(f.read_bytes()).hexdigest()
def manifest(root):
 rows=[{'path':f.relative_to(root).as_posix(),'size':f.stat().st_size,'sha256':sha(f)} for f in sorted(root.rglob('*')) if f.is_file() and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts]
 return {'files':rows,'file_count':len(rows),'manifest_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
base=json.loads((p/'inputs/H7-core-source-manifest.json').read_text());candidate=manifest(p/'source')
assert manifest(p/'inputs/H7-core-source')==base
old={r['path']:r for r in base['files']};new={r['path']:r for r in candidate['files']}
changes=[];parts=[]
for name in sorted(old.keys()|new.keys()):
 if old.get(name)==new.get(name):continue
 before=(p/'inputs/H7-core-source'/name).read_text().splitlines(keepends=True) if name in old else []
 after=(p/'source'/name).read_text().splitlines(keepends=True) if name in new else []
 parts.append('diff --git a/'+name+' b/'+name+'\n')
 if name not in old:parts.append('new file mode 100644\n')
 if name not in new:parts.append('deleted file mode 100644\n')
 parts.extend(difflib.unified_diff(before,after,fromfile='a/'+name if name in old else '/dev/null',tofile='b/'+name if name in new else '/dev/null'))
 changes.append({'path':name,'baseline_sha256':old.get(name,{}).get('sha256'),'candidate_sha256':new.get(name,{}).get('sha256')})
(p/'acceptance-history.patch').write_text(''.join(parts))
(p/'evidence/FINAL_SOURCE_MANIFEST.json').write_text(json.dumps(candidate,indent=2)+'\n')
identity={'repository':'Deng-0119/RPNH','source_kind':'frozen H7 core plus isolated historical acceptance overlay; no remote HEAD assertion','H7_core_manifest_sha256':base['manifest_sha256'],'H7_core_file_count':base['file_count'],'source_manifest':'evidence/FINAL_SOURCE_MANIFEST.json','source_manifest_sha256':candidate['manifest_sha256'],'source_file_count':candidate['file_count'],'overlay_patch_sha256':sha(p/'acceptance-history.patch'),'changed_files':changes,'preserved_inputs':[{'path':f.relative_to(p).as_posix(),'sha256':sha(f)} for f in sorted((p/'inputs').rglob('*')) if f.is_file() and 'H7-core-source' not in f.parts and '__pycache__' not in f.parts and '.pytest_cache' not in f.parts]}
(p/'SOURCE_IDENTITY.json').write_text(json.dumps(identity,indent=2)+'\n')
print(json.dumps({k:identity[k] for k in ('source_file_count','source_manifest_sha256','overlay_patch_sha256','changed_files')},indent=2))
