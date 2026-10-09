import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

ROOT=Path('<WORKSPACE>')
TASK=ROOT/'task-complete-v5-20261009'
OUT=TASK/'stages/codex'
PY=ROOT/'.p26/v/bin/python'
INDEX=json.loads((TASK/'rpnh-complete-local-bundle-v5/CANDIDATE_INDEX.json').read_text())
CAND=[c for c in INDEX['candidates'] if c['lane']=='codex']

def safe(p):
    p=Path(p); assert p.resolve().is_relative_to(ROOT),p
    if p.exists() and p.is_file(): assert p.stat().st_nlink==1,p
    return p
def raw(n):
    c=CAND[n-1]; return TASK/'raw'/Path(c['archive_path']).stem/c['root_member']
def pkg(n): return OUT/f'package{n}'
def wt(n): return ROOT/f'.v26/x{n}'
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def blob(p):
    b=p.read_bytes(); return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
def write(p,d): safe(p).write_text(json.dumps(d,indent=2)+'\n')
def rec(label,cwd,cmd,extra=None):
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',TMPDIR=str(ROOT/'.v26/tmp'),GIT_OPTIONAL_LOCKS='0')
    if extra: env.update(extra)
    return subprocess.run([str(PY),str(TASK/'record.py'),'--out',str(OUT/label),'--cwd',str(cwd),'--pythonpath',str(cwd),'--',*map(str,cmd)],env=env).returncode
def identity(n,label):
    r=wt(n); rows=[]
    for p in sorted(r.rglob('*')):
        if p.is_file() and p.name!='.git':
            assert not p.is_symlink(),p
            rows.append(dict(path=str(p.relative_to(r)),sha256=digest(p),mode=p.stat().st_mode & 0o777))
    write(OUT/f'{label}.identity.json',dict(worktree=str(r),files=rows))
    print('IDENTITY',n,len(rows))
def verify(n,c,phase):
    p=pkg(c); r=wt(n); m=json.loads((p/'file-manifest.json').read_text()); rows=m.get('files',m.get('changed_files')); results=[]
    patch=p/Path(CAND[c-1]['selected_final_patch_member']).name
    assert digest(patch)==CAND[c-1]['patch_sha256']==m['patch_sha256']
    for row in rows:
        f=safe(r/row['path']); before=row.get('baseline_git_blob_sha1',row.get('base_sha256'))
        actual={'path':row['path'],'expected_old':before,'expected_new_sha256':row['sha256'],'actual_sha256':digest(f) if f.exists() else None,'actual_git_blob':blob(f) if f.exists() else None}
        actual['match']=(actual['actual_sha256']==row['sha256']) if phase=='post' else ((not f.exists()) if before is None else (actual['actual_git_blob']==before if 'baseline_git_blob_sha1' in row else actual['actual_sha256']==before))
        results.append(actual)
    write(OUT/f'x{n}-patch{c}-{phase}.json',{'patch_sha256':digest(patch),'rows':results})
    assert all(x['match'] for x in results),[x for x in results if not x['match']]
    print('PASS',phase,len(rows),patch)
def apply(n,c):
    label=f'x{n}-patch{c}'
    assert rec(label+'-pre',wt(n),[PY,__file__,'verify',n,c,'pre'])==0
    patch=pkg(c)/Path(CAND[c-1]['selected_final_patch_member']).name
    assert rec(label+'-check',wt(n),['git','apply','--check','--whitespace=error',patch])==0
    assert rec(label+'-apply',wt(n),['git','apply','--whitespace=error',patch])==0
    assert rec(label+'-post',wt(n),[PY,__file__,'verify',n,c,'post'])==0
def lock(n,phase):
    m=json.loads((pkg(4)/('BASE_SOURCE_LOCK.json' if phase=='base' else 'file-manifest.json')).read_text())
    rows=m['files'] if phase=='base' else m['source_files']
    results=[dict(path=x['path'],expected=x['sha256'],actual=digest(wt(n)/x['path']) if (wt(n)/x['path']).exists() else None) for x in rows]
    write(OUT/f'x{n}-{phase}-lock.json',dict(files=results,manifest_sha256=digest(pkg(4)/('BASE_SOURCE_LOCK.json' if phase=='base' else 'file-manifest.json'))))
    bad=[x for x in results if x['expected']!=x['actual']]
    print('LOCK',phase,len(rows),'mismatches',bad)
    assert not bad
def tests(n,label,args):
    before=f'{label}-before'; after=f'{label}-after'
    identity(n,before)
    code=rec(label,wt(n),[PY,'-m','pytest','-q','-p','no:cacheprovider','-o','junit_family=xunit1','--basetemp='+str(ROOT/f'.v26/tx{label}'),'--junitxml='+str(OUT/f'{label}.xml'),*args])
    identity(n,after)
    assert (OUT/f'{before}.identity.json').read_bytes()==(OUT/f'{after}.identity.json').read_bytes()
    return code

if __name__=='__main__':
    op=sys.argv[1]
    if op=='copy':
        for n in range(1,5):
            assert not pkg(n).exists(); safe(pkg(n))
            for f in raw(n).rglob('*'): assert not f.is_symlink(),f
            shutil.copytree(raw(n),pkg(n))
            print('COPIED',raw(n),pkg(n))
    elif op=='identity': identity(int(sys.argv[2]),sys.argv[3])
    elif op=='verify': verify(int(sys.argv[2]),int(sys.argv[3]),sys.argv[4])
    elif op=='apply': apply(int(sys.argv[2]),int(sys.argv[3]))
    elif op=='lock': lock(int(sys.argv[2]),sys.argv[3])
    elif op=='tests': sys.exit(tests(int(sys.argv[2]),sys.argv[3],sys.argv[4:]))
