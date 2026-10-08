import json,hashlib,shutil
from pathlib import Path
root=Path(__file__).resolve().parent
ws=root.parent
tree=json.loads((root/'remote-tree.json').read_text())
candidates=[ws/'rpnh-registry-reader-fix/source',ws/'rpnh-owner-execution-convergence/baseline',ws/'rpnh-continuous-iteration/audit-sources',ws/'rpnh-output-guidance-fix/source',ws/'rpnh-convergence-fix/source',ws/'rpnh-recovery-20261003/source']
def blob(b): return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
proof=[]; missing=[]
for entry in tree['tree']:
 p=entry['path']
 if entry['type']!='blob' or p.startswith('evidence/'):continue
 for candidate in candidates:
  file=candidate/p
  if file.is_file() and blob(file.read_bytes())==entry['sha']:
   for dst in ('baseline','source'):
    out=root/dst/p;out.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,out)
   proof.append({'path':p,'git_blob':entry['sha'],'local_source':str(candidate.relative_to(ws))});break
 else:missing.append(entry)
(root/'baseline-provenance.json').write_text(json.dumps({'commit':'ec9077e95b830d09e7252c6d5d8ebcdbb9eb4ac4','tree':tree['sha'],'verified':proof,'missing':missing},indent=2)+'\n')
print('Verified',len(proof),'missing',len(missing)); print('Missing cpn and focused docs:',[(x['path'],x['sha']) for x in missing if x['path'].startswith('cpn/') or x['path'] in ['docs/ARCHITECTURE.md','docs/ARCHITECTURE_ZH.md']])
