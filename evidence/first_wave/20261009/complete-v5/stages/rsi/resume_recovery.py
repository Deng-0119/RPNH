"""Resume one recorded RSI batch at a time; never replay preparation implicitly."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import run_lane as lane

ROOT, OUT, PY = lane.ROOT, lane.OUT, lane.PY
TMP = ROOT / '.v26/urtmp'

def owned(path):
    p = Path(path)
    roots = [OUT, ROOT/'.v26/r1', ROOT/'.v26/r2']
    resolved = p.resolve()
    assert any(resolved.is_relative_to(r.resolve()) for r in roots) or (
        resolved.is_relative_to(ROOT/'.v26') and resolved.relative_to(ROOT/'.v26').parts[0].startswith('ur')), p
    if p.is_file():
        assert p.stat().st_nlink == 1, p
    return p

lane.owned = owned
owned(TMP).mkdir(exist_ok=True)
lane.ENV['TMPDIR'] = str(TMP)

def budget(label):
    disk = subprocess.run(['df','-B1','/mnt/d'], capture_output=True, text=True, check=True)
    free = int(disk.stdout.splitlines()[1].split()[3])
    usage = subprocess.run(['du','-s','-B1',str(ROOT/'.v26'),str(lane.TASK)], capture_output=True, text=True, check=True)
    used = sum(int(row.split()[0]) for row in usage.stdout.splitlines())
    runtime = subprocess.run(['du','-s','-B1',*[str(p) for p in (ROOT/'.v26').glob('ur*')]], capture_output=True, text=True, check=True)
    generated = sum(int(row.split()[0]) for row in runtime.stdout.splitlines())
    value = dict(utc=lane.utc(), host_free_bytes=free, whole_v26_and_task_bytes=used,
                 expanded_resume_runtime_bytes=generated, host_df=disk.stdout)
    lane.dump(OUT/f'{label}-budget.json', value)
    assert free >= 25*1024**3 and used <= 8*1024**3 and generated < 1024**3, value

def source(repo):
    result = subprocess.run(['git','ls-files','--cached','--others','--exclude-standard','-z'], cwd=repo,
                            capture_output=True, check=True, env=lane.ENV)
    rows = []
    for name in sorted(set(result.stdout.decode().split('\0'))- {''}):
        p = repo/name
        assert p.resolve().is_relative_to(repo)
        if p.is_symlink():
            rows.append([name,'symlink',os.readlink(p)])
        elif p.is_file():
            rows.append([name,lane.sha(p),p.stat().st_mode & 0o777])
    return dict(files=len(rows), fingerprint=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest())

def command(label, repo, paths, args, pythonpath=None, category='static', junit=None):
    assert label.startswith('resume-')
    before = lane.snap(label+'-before',repo,paths)
    before_source = source(repo)
    paths_out = {k: owned(OUT/f'{label}.{k}') for k in ['stdout.log','stderr.log','json']}
    assert all(not p.exists() for p in paths_out.values())
    env = dict(lane.ENV, PYTHONPATH=pythonpath or str(repo))
    start, tick = lane.utc(), time.monotonic()
    with paths_out['stdout.log'].open('xb') as out, paths_out['stderr.log'].open('xb') as err:
        proc = subprocess.Popen(list(map(str,args)), cwd=repo, env=env, stdout=out, stderr=err)
        print(json.dumps(dict(label=label,pid=proc.pid,utc=start)),flush=True)
        code = proc.wait()
    metadata = dict(command=list(map(str,args)),cwd=str(repo),pid=proc.pid,started_at_utc=start,
        finished_at_utc=lane.utc(),elapsed_seconds=time.monotonic()-tick,exit_code=code,
        environment={k:env.get(k) for k in ['PYTHONPATH','TMPDIR','PYTHONDONTWRITEBYTECODE','PYTEST_DISABLE_PLUGIN_AUTOLOAD']})
    lane.dump(paths_out['json'],metadata)
    after = lane.snap(label+'-after',repo,paths)
    after_source = source(repo)
    gate = dict(label=label,category=category,status='PASS' if code==0 else 'FAIL',exit_code=code,
        record=str(paths_out['json']),before_identity=str(OUT/f'{label}-before.identity.json'),
        after_identity=str(OUT/f'{label}-after.identity.json'),source_before=before_source,
        source_after=after_source,source_unchanged=before_source==after_source,
        junit=str(junit) if junit else None)
    if junit:
        try:
            data = Path(junit).read_bytes()
            assert b'\0' not in data
            xml = ET.fromstring(data)
            cases = xml.findall('.//testcase')
            gate['junit_counts'] = dict(tests=len(cases),failures=sum(c.find('failure') is not None for c in cases),
                errors=sum(c.find('error') is not None for c in cases),skipped=sum(c.find('skipped') is not None for c in cases))
        except Exception as exc:
            gate.update(status='INCONCLUSIVE',junit_error=repr(exc))
    lane.dump(OUT/f'{label}.gate.json',gate)
    print(json.dumps(gate),flush=True)
    assert before_source==after_source or category=='patch', 'unexpected source mutation'
    return code

lane.command = command

def main():
    action, label, tree, *args = sys.argv[1:]
    repo = ROOT/'.v26'/tree
    paths = lane.CANDS['R1_v2_full']['changed_paths'] + (lane.CANDS['R2']['changed_paths'] if tree=='r2' else [])
    if action=='test':
        base, *selectors = args
        budget(label)
        extra = []
        pythonpath = str(repo)
        if any('test_terminal_registry_independent' in s for s in selectors):
            pythonpath += ':'+str(repo/'cpn')
        lane.pytest(label,repo,paths,selectors,ROOT/'.v26'/base,pythonpath=pythonpath,
                    category='AF_UNIX_native' if 'native' in label else 'offline',extra=extra)
    elif action=='readback':
        base, = args
        command(label,repo,paths,[PY,lane.package('R2')/'h2a_readback.py',ROOT/'.v26'/base,
            OUT/f'{label}-result.json'],category='readonly')
    elif action=='apply':
        cid, = args
        lane.apply(label,cid,repo,paths)
    elif action=='import':
        lane.imports(label,repo,paths,r2=tree=='r2')
    elif action=='command':
        command(label,repo,paths,args)
    else:
        raise ValueError(action)

if __name__=='__main__':
    main()
