"""Extract existing synthetic Registries before their parent-owned compression."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from dataclasses import asdict
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.run_authority import read_run_execution, read_run_terminal_bytes
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.task_control import TaskControl

ROOT = Path('<WORKSPACE>')
OUT = ROOT/'task-complete-v5-20261009/stages/rsi'
base, label, *flags = sys.argv[1:]
base = ROOT/'.v26'/base
assert base.name.startswith('ur') and base.resolve().parent == ROOT/'.v26'
assert label.startswith('resume-')

def write(path,data):
    assert path.resolve().is_relative_to(OUT) and not path.exists()
    path.write_text(json.dumps(data,indent=2)+'\n')

def files(run):
    return {str(p.relative_to(run)):[p.stat().st_size,hashlib.sha256(p.read_bytes()).hexdigest()]
            for p in sorted(run.rglob('*')) if p.is_file()}

def ref(value):
    return dict(entity_type=value.entity_type,logical_id=str(value.entity_id),version_id=str(value.version_id))

rows=[]
for fixture in sorted(base.iterdir()):
    run=fixture/'run'
    if fixture.is_symlink() or not (run/'.registry_v1/registry.sqlite3').is_file():
        continue
    before_files=files(run)
    core=_RegistryCore(run,create=False,read_only=True)
    head,epoch=core.event_store.max_ordinal(),core.writer_epoch
    read=read_run_execution(core,_ResourceServiceKernel(core))
    checkpoint=hydrate_module_runtime(core)[2].checkpoint_ref
    assert checkpoint==read.checkpoint_ref
    status=TaskControl._read_terminal_status(run)
    result=None if read.terminal is None else json.loads(read_run_terminal_bytes(core,read))
    cli=None
    if '--cli' in flags:
        command=[sys.executable,'-m','cpn','net','show','--run',str(run),'--format','json']
        started=datetime.now(timezone.utc).isoformat()
        proc=subprocess.run(command,cwd=os.getcwd(),capture_output=True)
        prefix=OUT/(label+'-cli-'+fixture.name)
        for suffix,data in [('.stdout.log',proc.stdout),('.stderr.log',proc.stderr)]:
            path=Path(str(prefix)+suffix)
            assert path.resolve().is_relative_to(OUT) and not path.exists()
            path.write_bytes(data)
        cli=dict(command=command,started_at_utc=started,finished_at_utc=datetime.now(timezone.utc).isoformat(),
                 exit_code=proc.returncode,stdout=str(prefix)+'.stdout.log',stderr=str(prefix)+'.stderr.log')
        write(Path(str(prefix)+'.json'),cli)
        if proc.returncode==0:
            projection=json.loads(proc.stdout)
            cli['projection_keys']=sorted(projection)
            assert projection['execution']['read_only'] is True
            assert projection['execution']['head_ordinal']==head
            assert projection['source']['verified_head_ordinal']==head
            assert projection['source']['writer_fencing_epoch']==epoch
            assert projection['marking']['checkpoint_ref']==asdict(checkpoint)
            assert projection['execution']['firing_count']==len(core.event_store.object_rows_by_type('operation_result/v1'))
            cli['verified_head_ordinal']=head
            cli['verified_checkpoint_version']=str(checkpoint.version_id)
            cli['verified_writer_epoch']=epoch
            cli['firing_count']=projection['execution']['firing_count']
    again=read_run_execution(core,_ResourceServiceKernel(core))
    read.cut.assert_unchanged(core)
    assert (head,epoch,checkpoint)==(core.event_store.max_ordinal(),core.writer_epoch,again.checkpoint_ref)
    assert status==TaskControl._read_terminal_status(run)
    assert status['actual_model_call_counts']==[0,0]
    after_files=files(run)
    changes=[dict(path=p,before=before_files.get(p),after=after_files.get(p))
             for p in sorted(before_files.keys()|after_files.keys()) if before_files.get(p)!=after_files.get(p)]
    for change in changes:
        if change['path']=='.registry_v1/registry.sqlite3-shm':
            continue
        assert change['path']=='.registry_v1/registry.sqlite3-wal',changes
        assert all(v is None or v[0]==0 for v in [change['before'],change['after']]),changes
    rows.append(dict(fixture=fixture.name,head_before=head,head_after=core.event_store.max_ordinal(),
        writer_epoch_before=epoch,writer_epoch_after=core.writer_epoch,checkpoint_before=ref(checkpoint),
        checkpoint_after=ref(again.checkpoint_ref),status=read.status,task_control_status=status,result=result,
        run_ref=ref(read.run_ref),terminal_ref=None if read.terminal is None else ref(read.terminal.evidence_ref),
        model_calls=[0,0],canonical_unchanged=True,sidecar_changes=changes,cli=cli))
properties=[]
xml=OUT/(label+'.xml')
if xml.exists():
    for tc in ET.parse(xml).findall('.//testcase'):
        props={p.get('name'):p.get('value') for p in tc.findall('./properties/property')}
        if props:
            properties.append(dict(attributes=tc.attrib,properties=props))
result=dict(base=str(base),registry_count=len(rows),rows=rows,junit_properties=properties,
            cli_status=('NOT_APPLICABLE' if '--cli' not in flags else
                        'PASS' if rows and all(r['cli']['exit_code']==0 for r in rows) else 'FAIL'),
            readback_status='PASS',model_calls=[0,0])
write(OUT/(label+'-extracted.json'),result)
print(json.dumps(dict(registries=len(rows),cli_status=result['cli_status'],artifact=str(OUT/(label+'-extracted.json')))))
