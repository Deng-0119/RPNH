from pathlib import Path
import difflib, hashlib, json, sys
root=Path(__file__).resolve().parent
paths=['cpn/rpnh/task_control.py','cpn/rpnh/registry/run_authority.py','tests/test_task_control_registry_reads.py','docs/ARCHITECTURE.md','docs/ARCHITECTURE_ZH.md']
rows=[]; patch=[]
def sha(data): return hashlib.sha256(data).hexdigest()
def blob(data): return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
for p in paths:
 old=root/'baseline'/p; new=root/'source'/p
 before=old.read_bytes() if old.exists() else b''; after=new.read_bytes()
 rows.append({'path':p,'baseline_git_blob':blob(before) if old.exists() else None,'baseline_sha256':sha(before) if old.exists() else None,'source_git_blob':blob(after),'source_sha256':sha(after),'size':len(after)})
 patch.extend(difflib.unified_diff(before.decode().splitlines(True),after.decode().splitlines(True),fromfile='a/'+p if old.exists() else '/dev/null',tofile='b/'+p))
(root/'taskcontrol-reader-convergence.patch').write_text(''.join(patch))
(root/'changed-files.txt').write_text('\n'.join(paths)+'\n')
(root/'file-manifest.json').write_text(json.dumps({'base_commit':'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4','files':rows},indent=2)+'\n')
proof=json.loads((root/'baseline-provenance.json').read_text())
manifest=[]
for row in proof['verified']:
 p=row['path']; data=(root/'source'/p).read_bytes()
 manifest.append(sha(data)+'  '+p)
for p in paths:
 if p not in {x['path'] for x in proof['verified']}:
  manifest.append(sha((root/'source'/p).read_bytes())+'  '+p)
text='\n'.join(sorted(manifest))+'\n'
(root/'tested-source.sha256').write_text(text)
(root/'tested-source-digest.txt').write_text(sha(text.encode())+'  tested-source.sha256\n')
print('Changed files:',len(rows)); print('Source manifest digest:',sha(text.encode())); print('Patch:',sha((root/'taskcontrol-reader-convergence.patch').read_bytes()))
