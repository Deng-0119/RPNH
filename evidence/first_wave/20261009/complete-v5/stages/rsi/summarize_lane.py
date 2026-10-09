"""Parse real JUnit executions; do not count collection errors as test runs."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import run_lane as lane

OUT = lane.OUT
gates = [json.loads(p.read_text()) for p in sorted(OUT.glob('*.gate.json'))]
executions = []
collection_errors = []
native = []
counts = {}
for gate in gates:
    label = gate['label']
    if gate['category'] == 'patch':
        gate['phase'] = 'patch_precheck' if label.endswith('-check') else 'patch_apply'
    elif gate['category'] == 'collection':
        gate.update(phase='collection', classification='PACKAGE_MISSING_TEST_DEPENDENCY')
    elif label == 'r2-import':
        gate.update(phase='import_diagnostic', classification='LOCAL_RECORDER_DIAGNOSTIC_ERROR',
            reason='Diagnostic requested cpn.rpnh.orchestration; the frozen test actually imports cpn.orchestrator.runner.',
            corrected_by='r2-import-corrected', original_failure_preserved=True)
    else:
        gate['phase'] = gate['category']
    metadata = json.loads(Path(gate['record']).read_text())
    gate.update(started_at_utc=metadata['started_at_utc'], finished_at_utc=metadata['finished_at_utc'],
        command=metadata['command'], elapsed_seconds=metadata['elapsed_seconds'])
    file = gate.get('junit')
    if not file or not Path(file).is_file():
        continue
    xml = ET.parse(file)
    local = []
    for tc in xml.findall('.//testcase'):
        raw = dict(tc.attrib)
        if gate['category'] == 'collection':
            collection_errors.append(dict(gate=label, attributes=raw,
                errors=[n.text for n in tc.findall('error')]))
            continue
        f = tc.get('file', '')
        name = tc.get('name')
        scope = 'package_independent' if 'independent' in label else 'repository'
        if not f:
            scope = 'package_independent' if 'independent' in label else 'repository'
            f = tc.get('classname', '').replace('.', '/') + '.py'
        identity = f + '::' + name
        # Normalize only test location; retain each original JUnit identity below.
        if scope == 'package_independent':
            cid = 'R1_v2_full' if label.startswith('r1-') else 'R2'
            f = Path(f).name
            identity = cid + '/review/' + f + '::' + name
        verdict = ('FAIL' if tc.find('failure') is not None else 'ERROR' if tc.find('error') is not None
                   else 'SKIP' if tc.find('skipped') is not None else 'PASS')
        props = {p.get('name'):p.get('value') for p in tc.findall('./properties/property')}
        row = dict(gate=label, scope=scope, id=identity, junit_attributes=raw, verdict=verdict, properties=props)
        executions.append(row)
        local.append(row)
        if label == 'r2-native':
            native.append(dict(id=identity, verdict=verdict, evidence=json.loads(props['runtime_evidence'])
                if 'runtime_evidence' in props else None))
    counts[label] = dict(executions=len(local), unique_ids=len({r['id'] for r in local}),
        verdicts={s:sum(r['verdict'] == s for r in local) for s in ['PASS', 'FAIL', 'ERROR', 'SKIP']})

lane.dump(OUT / 'junit-ids.json', dict(actual_executions=executions, collection_errors=collection_errors))
lane.dump(OUT / 'native-results.json', native)
receipt = json.loads((OUT / 'package-receipt.json').read_text())
stages = {}
for name, file in [('R1_v2_full', 'r1-final.identity.json'),
                   ('R1_v2_full_on_r2_before_R2', 'r2-r1-stage.identity.json'),
                   ('R2', 'r2-final.identity.json')]:
    stages[name] = dict(artifact=str(OUT / file), identity=json.loads((OUT / file).read_text()))
summary = {
    'lane':'rsi', 'TASK_MODE':'software', 'status':'PARTIAL', 'finished_at_utc':lane.utc(),
    'baseline_commit':lane.BASE, 'selected_candidates':['R1_v2_full','R2'],
    'stage_identities':stages, 'packages':receipt, 'gates':gates,
    'stage_tests':counts,
    'test_counts':dict(actual_executions=len(executions),
        unique_repository_ids=len({r['id'] for r in executions if r['scope']=='repository'}),
        unique_package_independent_ids=len({r['id'] for r in executions if r['scope']=='package_independent'}),
        unique_total_ids=len({(r['scope'],r['id']) for r in executions}),
        verdicts={s:sum(r['verdict'] == s for r in executions) for s in ['PASS','FAIL','ERROR','SKIP']},
        collection_errors=len(collection_errors), collection_errors_are_test_executions=False),
    'native_results':native, 'junit_ids_artifact':str(OUT/'junit-ids.json'),
    'raw_immutability':json.loads((OUT/'raw-immutability-final.json').read_text()),
    'terminal_outcomes':dict(status='Preserved frozen profile mapping',
        evidence=['r1-author.xml','r1-independent-registry.xml','r2-author-step.xml','r2-step-readback-result.json'],
        rule='Canonical run_outcome comes from declared stop/final_select/final_retain; explicit failed stays failed.'),
    'gaps':[
        dict(status='NOT_RUN', phase='R1_independent_compiler', expected_tests=33,
             reason='R1v2 review/test_terminal_revision_independent.py imports test_inert_profile_independent; module is absent from the selected package. Original collection attempt remains FAIL/exit2.',
             evidence=['rs04-guards.stdout.log','rs07-helper-archive-check.stdout.log','r1-independent-compiler-collection.stdout.log','r1-independent-compiler-collection.xml']),
        dict(status='NOT_RUN', phase='R1_Orchestrator_native',
             reason='R1 gate provides compiler/schema and RunOwner/SQLite step tests only; no R1 Orchestrator/AF_UNIX scenario fixture. R2 native results are a separate stage.'),
        dict(status='NOT_RUN', phase='physical_outcome_unknown_and_recovery',
             reason='No actual persisted physical-unknown/recovery fixture in these existing gates; an evaluator exception leaving execution unsettled is narrower evidence.'),
        dict(status='NOT_RUN', phase='author_CAS_registered_model_R3_R4',
             reason='No applicable existing author/CAS or R3/R4 fixture; outside frozen R1/R2 finite local gates.'),
        dict(status='NOT_RUN', phase='stock_certification',
             reason='R1/R2 have no stock-client certification gate; real providers/models and other lanes excluded.'),
        dict(status='NOT_RUN', phase='core_Codex_final_combination',
             reason='This lane contains only R1v2 then R2; no combination implemented or validated.')],
    'retained_original_failures':'All candidate historical bytes preserved in raw and staged package copies; new successful windows do not reclassify old failures.',
    'tooling_incidents':[
        dict(record='rs03-inspect.json', phase='read_only_inspection', exit_code=1,
             reason='Inspection requested absent root tests/conftest.py; already-read independent/runtime sources retained; no test executed.'),
        dict(record='r2-import.json', phase='local_import_diagnostic', exit_code=1,
             reason='Local diagnostic used an incorrect module name; corrected diagnostic uses the actual frozen test import, without product or frozen-tool edits.')],
    'constraints':dict(real_provider_model_calls=0, pipe_used=False, installs_downloads_logins=False,
        agents=False, product_source_edits_beyond_frozen_patches=False, main_mutated=False,
        commit_push_performed=False, final_commit_push_owner='parent', zip_created=False),
}
for key, label in [('native_readback','r2-native-readback-result.json'),('step_readback','r2-step-readback-result.json')]:
    p = OUT / label
    summary[key] = dict(artifact=str(p), result=json.loads(p.read_text())) if p.exists() else dict(status='NOT_RUN',reason='No completed output')
lane.dump(OUT / 'lane.json', summary)
print(json.dumps(dict(status=summary['status'], counts=summary['test_counts'], stage_tests=counts,
    native=native, artifact=str(OUT/'lane.json')), ensure_ascii=False, indent=2))
