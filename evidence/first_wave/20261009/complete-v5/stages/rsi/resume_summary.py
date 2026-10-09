"""Summarize completed exit records and valid JUnit only, preserving interruptions."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import resume_recovery as r

OUT, ROOT = r.OUT, r.ROOT
gates=[]
executions=[]
collection_errors=[]
stage_counts={}
for p in sorted(OUT.glob('resume-*.gate.json')):
    gate=json.loads(p.read_text())
    meta=json.loads(Path(gate['record']).read_text())
    gate['command_record']=meta
    gates.append(gate)
    path=gate.get('junit')
    if not path:
        continue
    if gate['status']=='INCONCLUSIVE':
        continue
    xml=ET.parse(path)
    local=[]
    for tc in xml.findall('.//testcase'):
        if meta['exit_code'] in (2,4,5) and tc.find('error') is not None:
            collection_errors.append(dict(gate=gate['label'],attributes=tc.attrib,error=tc.find('error').text))
            continue
        file=tc.get('file') or tc.get('classname','').replace('.','/')+'.py'
        scope='package_independent' if 'independent' in gate['label'] else 'repository'
        if scope=='package_independent':
            cid='R1_v2_full' if 'r1-' in gate['label'] else 'R2'
            file=cid+'/review/'+Path(file).name
        verdict=('FAIL' if tc.find('failure') is not None else 'ERROR' if tc.find('error') is not None
                 else 'SKIP' if tc.find('skipped') is not None else 'PASS')
        row=dict(gate=gate['label'],scope=scope,id=file+'::'+tc.get('name'),verdict=verdict,
                 attributes=tc.attrib,properties={q.get('name'):q.get('value') for q in tc.findall('./properties/property')})
        executions.append(row)
        local.append(row)
    stage_counts[gate['label']]=dict(executions=len(local),unique_ids=len({e['id'] for e in local}),
        verdicts={v:sum(e['verdict']==v for e in local) for v in ['PASS','FAIL','ERROR','SKIP']})

stages={}
for tree in ['r1','r2']:
    repo=ROOT/'.v26'/tree
    paths=r.lane.CANDS['R1_v2_full']['changed_paths']+(r.lane.CANDS['R2']['changed_paths'] if tree=='r2' else [])
    identity=r.lane.snap('resume-'+tree+'-final',repo,paths)
    assert identity['git']['head']==r.lane.BASE
    assert all(line.startswith('?? ') for line in identity['git']['status'].splitlines())
    assert {line[3:] for line in identity['git']['status'].splitlines()}==set(paths)
    baseline_gate='resume-r1-author' if tree=='r1' else 'resume-r2-delta-apply'
    expected_source=json.loads((OUT/(baseline_gate+'.gate.json')).read_text())[
        'source_before' if tree=='r1' else 'source_after']
    assert r.source(repo)==expected_source
    stages[tree]=dict(identity_file=str(OUT/('resume-'+tree+'-final.identity.json')),identity=identity,
                      source=r.source(repo))
    for cid in ['R1_v2_full']+(['R2'] if tree=='r2' else []):
        manifest=json.loads((r.lane.package(cid)/'file-manifest.json').read_text())
        assert all(r.lane.sha(repo/f['path'])==f['sha256'] for f in manifest['files'])
stages['R1_v2_full_on_r2_before_R2']=dict(
    identity_file=str(OUT/'resume-r2-r1-stage.identity.json'),
    identity=json.loads((OUT/'resume-r2-r1-stage.identity.json').read_text()),
    dependency=json.loads((OUT/'resume-r2-r1-dependency.json').read_text()))

receipt=json.loads((OUT/'package-receipt.json').read_text())
for package in receipt:
    for f in package['raw_inventory']:
        assert r.lane.sha(Path(package['raw'])/f['path'])==f['sha256']
        assert r.lane.sha(Path(package['stage_copy'])/f['path'])==f['sha256']
immutability=dict(status='PASS',raw_and_package_files=sum(len(p['raw_inventory']) for p in receipt))
r.lane.dump(OUT/'resume-raw-immutability-final.json',immutability)
r.lane.dump(OUT/'resume-junit-ids.json',dict(executions=executions,collection_errors=collection_errors))
extracts={p.name:json.loads(p.read_text()) for p in sorted(OUT.glob('resume-*-extracted.json'))}
for value in extracts.values():
    value['cli_attempts']=sum(row.get('cli') is not None for row in value.get('rows',[]))
    if value['cli_attempts']==0:
        value['cli_status']='NOT_APPLICABLE'
native=[dict(id=e['id'],verdict=e['verdict'],evidence=json.loads(e['properties']['runtime_evidence']))
        for e in executions if e['gate']=='resume-r2-native' and 'runtime_evidence' in e['properties']]
r.lane.dump(OUT/'resume-native-results.json',native)
expected={'resume-r1-author':150,'resume-r1-independent-registry':3,'resume-r2-r1-compiler':150,
          'resume-r2-author-step':11,'resume-r2-independent':5,'resume-r2-native':4}
coverage={label:dict(expected=n,actual=stage_counts.get(label),complete=(stage_counts.get(label,{}).get('executions')==n))
          for label,n in expected.items()}
gaps=[dict(phase='R1_independent_compiler',status='NOT_RUN',expected_tests=33,
    reason='The frozen successor test imports test_inert_profile_independent, absent from selected package and existing worktrees; no helper recreated or external source installed.',
    evidence=['resume-r1-independent-compiler.xml','resume-r1-independent-compiler.stdout.log'])]
tooling_incident=json.loads((OUT/'resume-evidence-tooling-correction.json').read_text())
corrected=json.loads((OUT/'resume-r2-native-export.gate.json').read_text())['status']=='PASS'
for gate in gates:
    if gate['label']=='resume-r2-native-extract':
        gate['classification']='LOCAL_EVIDENCE_FORMAT_DIAGNOSTIC'
        gate['corrected_by']='resume-r2-native-export' if corrected else None
failed=[g['label'] for g in gates if g['status']!='PASS' and g['label']!='resume-r1-independent-compiler'
        and not (g['label']=='resume-r2-native-extract' and corrected)]
counts=dict(actual_executions=len(executions),unique_repository_ids=len({e['id'] for e in executions if e['scope']=='repository'}),
    unique_package_independent_ids=len({e['id'] for e in executions if e['scope']=='package_independent'}),
    unique_total_ids=len({(e['scope'],e['id']) for e in executions}),
    verdicts={v:sum(e['verdict']==v for e in executions) for v in ['PASS','FAIL','ERROR','SKIP']},
    collection_errors=len(collection_errors),collection_errors_are_executions=False,
    dedup_rule='Repository-relative file::name including parameters; repeated R1 cases on R2 retain execution counts but add no unique coverage. Independent package paths have their own namespace.')
summary=dict(lane='rsi',TASK_MODE='software',status='PARTIAL_PACKAGE_GAP' if not failed and all(c['complete'] for c in coverage.values()) else 'PARTIAL',
    finished_at_utc=r.lane.utc(),baseline_commit=r.lane.BASE,selected_candidates=['R1_v2_full','R2'],
    stage_identities=stages,packages=receipt,gates=gates,stage_tests=stage_counts,test_counts=counts,
    required_windows=coverage,actual_failed_or_inconclusive_gates=failed,gaps=gaps,
    junit_ids_artifact=str(OUT/'resume-junit-ids.json'),native_results=native,readback_and_synthetic_evidence=extracts,
    native_transport_contract=dict(default='native',factory='cpn.rpnh.control_server.OwnerEventLoop',
        conftest_sha256=r.lane.sha(ROOT/'.v26/r2/examples/rsi_workflows/tests/conftest.py'),
        actual_factory_names=[item['evidence']['transport'] for item in native],
        pipe_argument_present=False,scope='Original fixture AF_UNIX OwnerEventLoop with deterministic immediate operation futures; no real provider or external worker claim'),
    raw_immutability=immutability,prior_interruption_audit=str(OUT/'resume-prior-interruption-audit.json'),
    test_source_audit=str(OUT/'resume-test-source-audit.json'),adapted_test_source_created=False,
    tooling_incidents=[tooling_incident],original_failed_tooling_gate_retained=True,
    prior_interrupted_windows_counted_as_pass=False,retained_original_failures='Raw/staged historical packages and original interrupted records retained unchanged; no partial XML, dots, NUL or missing exit classified PASS.',
    patch_sequence=['Existing r1 six-file R1v2 verified; not reapplied','Pristine r2 exact R1v2 full patch','r2 exact five-file R2 patch'],
    model_calls=[0,0],constraints=dict(real_provider_model_calls=0,pipe_used=False,install=False,
        Actions=False,Docker=False,permissions_changed=False,research=False,redelegation=False,
        product_fixes_or_guard_relaxations=False,main_or_other_lanes_changed=False,commit_push=False,
        large_copies_or_snapshots_or_zip=False,max_heavy_test_processes=1),
    scope='Finite isolated R1v2 compiler/schema/SQLite terminal steps then R2 Registry steps and original Orchestrator AF_UNIX; no combined lane certification.',
    not_covered=['Real provider/model','registered-model scripted port/R3','Physical persisted outcome_unknown recovery',
                 'full owner interruption/resume races','author binding/CAS','R4 campaign migration','cross-child budgets',
                 'other lanes or final combined product'],
    completed_temp_dirs=[str(ROOT/'.v26'/name) for name in ['ur1a','ur1i','ur2c','ur2s','ur2i','ur2n']],
    runtime_compaction='Parent only, after batch completion and extraction; no DB/failure store deleted by worker')
r.lane.dump(OUT/'lane.json',summary)
print(json.dumps(dict(status=summary['status'],counts=counts,stage_tests=stage_counts,failed=failed,artifact=str(OUT/'lane.json')),indent=2))
