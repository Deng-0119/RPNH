"""Eligible unchanged client windows; one heavy slot, no installs/provider calls."""
from pathlib import Path
import json, os, hashlib, subprocess, datetime
ROOT=Path('<WORKSPACE>').resolve(); TASK=ROOT/'task-complete-v5-20261009'; PY=ROOT/'.p26/v/bin/python'
def write(p,d):
 assert p.resolve().is_relative_to(ROOT) and not p.exists(),p
 p.write_text(json.dumps(d,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def spec(lane,n=None):
 if lane=='codex':
  m=json.loads((TASK/f'stages/codex/package{n}/file-manifest.json').read_text());return m['files']
 return [{'path':a['path'],'sha256':a['expected_sha256']} for a in json.loads((TASK/'stages/dsh/post-manifest.json').read_text())['changed']]
def inventory(repo, rows):
 names=set(subprocess.check_output(['git','ls-files','-z'],cwd=repo).decode().split('\0'))-{''}; names.update(a['path'] for a in rows)
 result=[]
 for name in sorted(names):
  p=repo/name; assert p.resolve().is_relative_to(ROOT) and not p.is_symlink() and p.is_file(),p
  result.append({'path':name,'sha256':sha(p),'size':p.stat().st_size,'executable':bool(p.stat().st_mode&0o111)})
 for a in rows: assert sha(repo/a['path'])==a['sha256'],a['path']
 return {'scope':'all Git tracked files plus declared candidate paths; generated pyc/cache excluded','head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'files':result}
def run(lane,label,wt,rows,tests,temp):
 repo=ROOT/'.v26'/wt;out=TASK/'stages'/lane
 host=os.statvfs('/mnt/d'); used=sum(int(subprocess.check_output(['du','-sx','-B1',str(p)],text=True).split()[0]) for p in [ROOT/'.v26',TASK])
 mem=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))*1024
 assert host.f_bavail*host.f_frsize>25*2**30 and used<8*2**30 and mem>4*2**30
 state={'active':label,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'host_D_available_bytes':host.f_bavail*host.f_frsize,'task_runtime_bytes':used,'mem_available':mem}
 (TASK/'stages/client-resume-state.json').write_text(json.dumps(state,indent=2)+'\n')
 before=inventory(repo,rows);write(out/f'{label}-source-before.json',before)
 cmd=[str(PY),str(TASK/'record.py'),'--out',str(out/label),'--cwd',str(repo),'--pythonpath',str(repo)+':'+str(repo/'tests'),'--',str(PY),'-m','pytest','-q','-p','no:cacheprovider','-o','junit_family=xunit1',*tests,'--basetemp='+str(ROOT/'.v26'/temp),'--junitxml='+str(out/f'{label}.xml')]
 result=subprocess.run(cmd)
 after=inventory(repo,rows);write(out/f'{label}-source-after.json',after);assert before==after
 write(out/f'{label}-acceptance.json',{'exit_code':result.returncode,'source_unchanged':True,'source_files':len(before['files']),'original_candidate_guard_paths':len(rows),'pyc_files_generated':[str(p.relative_to(repo)) for p in repo.rglob('*.pyc')]})
 subprocess.run([str(PY),str(TASK/'compact_runtime.py'),temp],check=True)
 print('FINISHED',label,result.returncode,flush=True)
rows2={a['path']:a for a in spec('codex',2)}
run('codex','resume-effort-bridge','x2',list(rows2.values()),['tests/test_frontend_boundary.py::test_bridge_process_loads_rpnh_instructions_without_altering_registered_request','--tb=short'],'uxeb')
rows3={a['path']:a for n in (1,2,3) for a in spec('codex',n)}
run('codex','resume-history-offline','x3',list(rows3.values()),['tests/test_codex_history.py','tests/test_codex_compat.py','tests/test_main_thread_history.py','tests/test_frontend_session_access.py','tests/test_frontend_boundary.py','-k','not test_codex_frontend_uses_external_socket_for_absent_long_root','--tb=short'],'uxho')
original=json.loads((TASK/'stages/codex/package3/native-regression-command.json').read_text())
assert len(original[4:-2])==43
run('codex','resume-history-registry','x3',list(rows3.values()),original[4:-1],'uxhr')
run('dsh','resume-owner-socket','d',spec('dsh'),['tests/test_dsh_backend.py','tests/test_dsh_long_path.py','tests/test_net_view_dsh_adapter_integration.py','--tb=short'],'udn')
(TASK/'stages/client-resume-state.json').write_text(json.dumps({'status':'COMMANDS_FINISHED_REQUIRES_ACCEPTANCE','utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},indent=2)+'\n')
