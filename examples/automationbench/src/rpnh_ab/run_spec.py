"""Frozen benchmark/execution identities, independent of the launch surface.

Paths and process identities live in the private launch request, not in logical
benchmark digests. This module never imports a provider or starts a process.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from .io import dumps, file_sha, load, sha, write_new
from .native import profile_identity
from .constants import UPSTREAM_COMMIT, PLUGIN_RESULT_BYTES, OPERATION_TIMEOUT_SECONDS

HOSTS = frozenset({'native', 'dsh'})
REQUIRED_ACCEPTANCE = ('installed_runtime', 'managed_schema_effect', 'host_execution',
                       'result_boundaries', 'null_budget_over_48', 'quiescent_stop',
                       'matched_upstream_world_rubric')


def source_identity():
    root = Path(__file__).resolve().parent
    return {str(p.relative_to(root)): file_sha(p) for p in sorted(root.rglob('*.py'))}


def benchmark_spec(plan, schemas, *, business_mode):
    if business_mode not in {'native_real_api', 'offline_synthetic'}:
        raise ValueError('business backend must be explicit; placeholder mode is not an accepted condition')
    if plan.get('upstream_commit') != UPSTREAM_COMMIT:
        raise ValueError('upstream pin differs')
    return {'schema':'rpnh-ab/benchmark-spec/v1', 'upstream_commit':UPSTREAM_COMMIT,
            'split':plan['split'], 'manifest_sha256':plan['manifest_sha256'],
            'task_contracts':[t['task_contract_sha256'] for t in plan['tasks']],
            'tool_schemas_sha256':sha(schemas), 'rubric':'automationbench.rubric:partial_credit,task_completed_correctly',
            'business_tool_mode':business_mode}


def execution_spec(profile, host, rpnh_identity, *, host_identity=None):
    if host not in HOSTS:
        raise ValueError('unsupported executor_host')
    return {'schema':'rpnh-ab/execution-spec/v1', 'executor_host':host,
            'rpnh':rpnh_identity, 'example_sources':source_identity(),
            'configured_model':profile_identity(Path(profile)), 'host_identity':host_identity or {},
            'managed_effects':['pure','external_read','external_write'],
            'limits':{'cumulative_model_calls':None,'cumulative_tool_calls':None,
                      'max_attempts_per_stage':None,'whole_task_seconds':None,
                      'operation_timeout_seconds':OPERATION_TIMEOUT_SECONDS,
                      'plugin_serialized_result_bytes':PLUGIN_RESULT_BYTES},
            'completion':'native_write_file_and_complete_interaction' if host=='native' else 'dsh_assistant_finalize'}


def dsh_identity(checkout):
    import subprocess
    from importlib.resources import files
    from integrations.dsh.patch_upstream import FACTORY_PATH, prepared_source
    from .upstream import git_identity
    root=Path(checkout).resolve()
    actual=git_identity(root)
    expected=load(Path(str(files('integrations.dsh').joinpath('UPSTREAM.json'))))
    if actual['commit']!=expected['revision']:
        raise ValueError('DSH checkout differs from pinned integration revision')
    changed = subprocess.check_output(
        ['git', '-C', str(root), 'diff', '--name-only', 'HEAD', '-z']
    ).decode().split('\0')
    if any(path and path != FACTORY_PATH for path in changed):
        raise ValueError('DSH checkout has tracked changes outside the exact factory seam')
    clean = subprocess.check_output(
        ['git', '-C', str(root), 'show', f'HEAD:{FACTORY_PATH}'], text=True)
    prepared, _changed = prepared_source(clean)
    current = (root / FACTORY_PATH).read_text(encoding='utf-8')
    if current not in {clean, prepared}:
        raise ValueError('DSH checkout factory seam is neither clean nor exactly prepared')
    paths=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    digests = {p:file_sha(root/p) for p in paths if p and (root/p).is_file()}
    digests[FACTORY_PATH] = hashlib.sha256(prepared.encode('utf-8')).hexdigest()
    identity={'commit':actual['commit'],
              'checkout_state':'clean-or-exact-prepared-factory-seam',
              'prepared_factory_sha256':digests[FACTORY_PATH]}
    identity['tracked_source_sha256']=sha(digests)
    identity['integration_upstream']=expected
    return identity


def acceptance_identity(benchmark, execution):
    # Scripted and live provider identities deliberately differ. Acceptance
    # validates host/tool/code, not the production provider's network access.
    return {'benchmark_sha256':sha(benchmark), 'executor_host':execution['executor_host'],
            'rpnh':execution['rpnh'], 'example_sources':execution['example_sources'],
            'host_identity':execution['host_identity'], 'limits':execution['limits'],
            'tool_schemas_sha256':benchmark['tool_schemas_sha256']}


def _validate_dsh_host_result(path: Path, *, terminal: bool) -> dict:
    value=load(path)
    if (value.get('schema_version') != 'rpnh/automationbench_dsh_host_result/v1'
            or value.get('admitted') is not True
            or value.get('process_exit_confirmed') is not True
            or value.get('process_quiescent') is not True
            or value.get('process_group_alive') is not False
            or value.get('host_quiescent') is not True
            or not isinstance(value.get('session_id'), str)):
        raise ValueError('DSH acceptance raw host lifecycle differs')
    history=value.get('history')
    committed=history.get('committed_history') if isinstance(history, dict) else None
    if not isinstance(committed, list):
        raise ValueError('DSH acceptance raw Registry history is unavailable')
    if terminal:
        if (value.get('stop_requested') is not False or value.get('return_code') != 0
                or value.get('status') != 'terminal'
                or value.get('outcome', {}).get('status') != 'terminal'
                or not committed or history.get('active') is not None
                or history.get('latest_turn_ref') != history.get('latest_committed_turn_ref')):
            raise ValueError('DSH acceptance raw terminal/history evidence differs')
    elif (value.get('stop_requested') is not True
          or value.get('outcome', {}).get('status') != 'nonterminal'):
        raise ValueError('DSH acceptance raw stop evidence differs')
    return value


def _validate_dsh_managed_bindings(path: Path, schemas: list[dict]) -> dict:
    from .plugin import bindings
    expected = {
        'schema_version': 'rpnh/dsh_managed_bindings/v1',
        **bindings(schemas)['executor'],
    }
    value = load(path)
    if value != expected:
        raise ValueError('DSH managed bindings differ from frozen upstream schemas/effects')
    return value


def validate_acceptance(path, benchmark, execution):
    manifest_path = Path(path).resolve()
    value = load(manifest_path)
    if value.get('schema') != 'rpnh-ab/acceptance-manifest/v1':
        raise ValueError('acceptance manifest schema differs')
    if value.get('identity') != acceptance_identity(benchmark, execution):
        raise ValueError('acceptance is stale or belongs to another host/code/tool condition')
    cases = value.get('cases', {})
    missing = [key for key in REQUIRED_ACCEPTANCE if cases.get(key, {}).get('status') != 'passed']
    if missing:
        raise ValueError('required host acceptance missing or blocked: '+', '.join(missing))
    if value.get('real_provider_calls') != 0 or value.get('real_business_api_calls') != 0:
        raise ValueError('offline acceptance must have zero real API calls')
    if (value.get('historical_benchmark_tasks_executed') != 0
            or value.get('synthetic_acceptance_only') is not True):
        raise ValueError('acceptance must be explicitly synthetic and execute no historical task')
    root = manifest_path.parent
    success_attempt = root / 'success-attempt'
    stop_attempt = root / 'stop-attempt'
    records = {}
    for key in REQUIRED_ACCEPTANCE:
        case=cases[key]
        evidence_input=Path(case.get('evidence_path',''))
        evidence=evidence_input.resolve()
        expected_evidence=(root / 'evidence' / f'{key}.json').resolve()
        if (evidence != expected_evidence or evidence_input.is_symlink()
                or not evidence.is_file()
                or file_sha(evidence)!=case.get('evidence_sha256')):
            raise ValueError('acceptance evidence unavailable or changed: '+key)
        record=load(evidence)
        if (record.get('schema') != 'rpnh-ab/acceptance-evidence/v1'
                or record.get('case') != key or record.get('status') != 'passed'
                or record.get('executor_host') != execution['executor_host']
                or record.get('real_provider_calls') != 0
                or record.get('real_business_api_calls') != 0
                or Path(record.get('success_attempt','')).resolve() != success_attempt
                or Path(record.get('stop_attempt','')).resolve() != stop_attempt):
            raise ValueError('acceptance evidence semantics differ: '+key)
        records[key]=record

    from .scoring import attempt_eligibility
    success_lifecycle=load(success_attempt/'lifecycle.json')
    if any(success_lifecycle.get(field) != expected for field, expected in (
            ('admitted', True), ('execution_status', 'host_terminal'),
            ('host_quiescent', True), ('world_owner_quiescent', True))):
        raise ValueError('acceptance success lifecycle is not terminal and quiescent')
    if records['installed_runtime'].get('lifecycle') != success_lifecycle:
        raise ValueError('acceptance installed-runtime evidence differs')
    scores=sorted(success_attempt.glob('score-*.json'))
    if len(scores) != 1:
        raise ValueError('acceptance success must have exactly one score record')
    score=load(scores[0])
    eligible, reason=attempt_eligibility(success_attempt, score=score)
    if not eligible:
        raise ValueError('acceptance success evidence is ineligible: '+str(reason))
    if (score.get('status') != 'scored' or score.get('partial_credit') != 1.0
            or score.get('task_completed_correctly') != 1.0):
        raise ValueError('acceptance upstream world/rubric score differs')

    events=[json.loads(line) for line in
            (success_attempt/'tool_events.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
    started={event.get('sequence') for event in events
             if event.get('kind') == 'dispatch_started'}
    finished=[event for event in events if event.get('kind') == 'dispatch_finished']
    finished_sequences={event.get('sequence') for event in finished}
    if (len(started) <= 48 or len(finished) != len(started)
            or finished_sequences != started
            or any(event.get('response', {}).get('ok') is not True for event in finished)):
        raise ValueError('acceptance does not prove more than 48 successful managed dispatches')
    largest_result=max(len(dumps(event['response']).encode('utf-8')) for event in finished)
    if largest_result <= 64 * 1024:
        raise ValueError('acceptance does not cross the former 64 KiB result boundary')

    stop_lifecycle=load(stop_attempt/'lifecycle.json')
    if (stop_lifecycle.get('execution_status') != 'manual_stop'
            or stop_lifecycle.get('admitted') is not True
            or stop_lifecycle.get('host_quiescent') is not True
            or stop_lifecycle.get('world_owner_quiescent') is not False
            or (stop_attempt/'final_world.json').exists()
            or list(stop_attempt.glob('score-*.json'))):
        raise ValueError('acceptance stop evidence is not quiescent and unscored')

    expected_tools=['api_fetch','api_search','base64_encode']
    if (records['managed_schema_effect'].get('tool_names') != expected_tools
            or records['managed_schema_effect'].get('admitted_effects')
            != ['pure','external_read','external_write']):
        raise ValueError('acceptance managed schema/effect evidence differs')
    if (records['host_execution'].get('execution_status') != 'host_terminal'
            or records['host_execution'].get('admitted') is not True):
        raise ValueError('acceptance host execution evidence differs')
    bounds=records['result_boundaries'].get('declared_operation_bytes')
    if (bounds != {name: PLUGIN_RESULT_BYTES for name in expected_tools}
            or records['result_boundaries'].get('crossed_legacy_64k_fixture') is not True):
        raise ValueError('acceptance managed result-bound evidence differs')
    if (records['null_budget_over_48'].get('successful_dispatches') != len(finished)
            or records['null_budget_over_48'].get('configured_cumulative_model_calls') is not None
            or records['null_budget_over_48'].get('configured_cumulative_tool_calls') is not None):
        raise ValueError('acceptance unmetered dispatch evidence differs')
    if any(records['quiescent_stop'].get(field) != expected for field, expected in (
            ('execution_status','manual_stop'), ('host_quiescent',True),
            ('world_owner_quiescent',False), ('final_world_written',False),
            ('score_written',False))):
        raise ValueError('acceptance stop summary differs')
    if any(records['matched_upstream_world_rubric'].get(field) != expected
           for field, expected in (('score_status','scored'), ('partial_credit',1.0),
                                   ('task_completed_correctly',1.0))):
        raise ValueError('acceptance rubric summary differs')

    host=execution['executor_host']
    host_evidence=load(success_attempt/f'{host}_evidence.json')
    if host == 'native':
        from .evidence import crosscheck
        from .native import inspect_registry
        success_registry=(root/'success-host-run').resolve()
        stop_registry=(root/'stop-host-run').resolve()
        if (Path(success_lifecycle.get('registry_path','')).resolve() != success_registry
                or Path(stop_lifecycle.get('registry_path','')).resolve() != stop_registry):
            raise ValueError('native acceptance Registry path differs')
        inspected, rows=inspect_registry(success_registry)
        stop_inspected, _stop_rows=inspect_registry(stop_registry)
        check=crosscheck(success_attempt, registry_rows=rows)
        if (host_evidence != inspected
                or load(stop_attempt/'native_evidence.json') != stop_inspected
                or inspected.get('managed_action_records') != len(finished)
                or check.get('registry_projection_available') is not True
                or check.get('all_environment_returns_registered') is not True
                or check.get('matched_unique_sequences') != len(finished)):
            raise ValueError('native acceptance Registry evidence differs')
    else:
        success_raw=(root/'success-host-run'/'host-result.json').resolve()
        stop_raw=(root/'stop-host-run'/'host-result.json').resolve()
        schemas=load(root.parent/'tool_schemas.json')
        if sha(schemas) != benchmark.get('tool_schemas_sha256'):
            raise ValueError('DSH acceptance tool schemas differ from frozen condition')
        _validate_dsh_managed_bindings(
            root/'success-host-run'/'managed-bindings.json', schemas)
        _validate_dsh_managed_bindings(
            root/'stop-host-run'/'managed-bindings.json', schemas)
        if (Path(host_evidence.get('raw_host_result','')).resolve() != success_raw
                or Path(success_lifecycle.get('raw_host_result','')).resolve() != success_raw
                or Path(load(stop_attempt/'dsh_evidence.json').get('raw_host_result','')).resolve() != stop_raw
                or Path(stop_lifecycle.get('raw_host_result','')).resolve() != stop_raw):
            raise ValueError('DSH acceptance raw host path differs')
        success_result=_validate_dsh_host_result(success_raw, terminal=True)
        stop_result=_validate_dsh_host_result(stop_raw, terminal=False)
        if (host_evidence.get('source') != 'dsh_registered_host'
                or host_evidence.get('registry_path') != success_result.get('registry_path')
                or load(stop_attempt/'dsh_evidence.json').get('registry_path')
                != stop_result.get('registry_path')):
            raise ValueError('DSH acceptance Registry evidence differs')
    return value


def write_launch(path, *, work, upstream, profile, host='native', acceptance,
                 native_run_root=None, dsh_checkout=None, parent_session_root=None):
    work=Path(work).resolve(); conditions=load(work/'conditions.json')
    benchmark=conditions['benchmark_spec']; execution=conditions['execution_spec']
    if host != execution['executor_host']:
        raise ValueError('launch host differs from frozen condition')
    payload={'schema':'rpnh-ab/launch/v1','example_id':'automationbench',
             'batch_id':'ab-'+sha({'benchmark':benchmark,'execution':execution})[:20],
             'work':str(work),'upstream':str(Path(upstream).resolve()),
             'profile':str(Path(profile).resolve()),'executor_host':host,
             'benchmark_sha256':sha(benchmark),'condition_digest':sha(execution),
             'acceptance':str(Path(acceptance).resolve()),
             'native_run_root':str(Path(native_run_root).resolve()) if native_run_root else None,
             'dsh_checkout':str(Path(dsh_checkout).resolve()) if dsh_checkout else None,
             'parent_session_root':str(Path(parent_session_root).resolve()) if parent_session_root else None}
    payload['request_sha256']=sha(payload)
    write_new(Path(path),payload)
    return payload


def load_launch(path, *, selected_profile=None, require_acceptance=True, validate_current=True):
    path=Path(path).resolve(); value=load(path)
    required={'schema','example_id','batch_id','work','upstream','profile','executor_host',
              'benchmark_sha256','condition_digest','acceptance','native_run_root',
              'dsh_checkout','parent_session_root','request_sha256'}
    if set(value)!=required or value.get('schema')!='rpnh-ab/launch/v1' or value.get('example_id')!='automationbench':
        raise ValueError('invalid prepared AutomationBench launch request')
    if value['executor_host'] not in HOSTS or not re.fullmatch(r'ab-[0-9a-f]{20}',value['batch_id']):
        raise ValueError('invalid host or batch identity')
    unsigned={k:v for k,v in value.items() if k!='request_sha256'}
    if sha(unsigned)!=value['request_sha256']:
        raise ValueError('launch request digest mismatch')
    work=Path(value['work']); conditions=load(work/'conditions.json')
    benchmark=conditions['benchmark_spec']; execution=conditions['execution_spec']
    if sha(benchmark)!=value['benchmark_sha256'] or sha(execution)!=value['condition_digest']:
        raise ValueError('frozen benchmark or execution digest differs')
    if execution['executor_host']!=value['executor_host']:
        raise ValueError('frozen executor host differs')
    if validate_current:
        from .experiment import installed_rpnh_identity
        if installed_rpnh_identity()!=execution['rpnh']:
            raise ValueError('RPNH runtime or adapter source changed after preparation')
    if validate_current and value['executor_host']=='dsh':
        if not value['dsh_checkout'] or dsh_identity(value['dsh_checkout'])!=execution['host_identity']:
            raise ValueError('DSH checkout/patch identity differs from prepared condition')
    if validate_current and execution['example_sources']!=source_identity():
        raise ValueError('example sources changed after preparation')
    frozen_profile=execution['configured_model']
    if validate_current and profile_identity(Path(value['profile']))!=frozen_profile:
        raise ValueError('execution selection changed after preparation')
    if selected_profile is not None and profile_identity(Path(selected_profile))!=frozen_profile:
        raise ValueError('selected model profile conflicts with frozen execution selection')
    if require_acceptance:
        validate_acceptance(value['acceptance'],benchmark,execution)
    return value
