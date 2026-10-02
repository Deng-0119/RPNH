"""Frozen benchmark/execution identities, independent of the launch surface.

Paths and process identities live in the private launch request, not in logical
benchmark digests. This module never imports a provider or starts a process.
"""
from __future__ import annotations
from pathlib import Path
import re
from .io import file_sha, load, sha, write_new
from .native import profile_identity
from .constants import UPSTREAM_COMMIT, PLUGIN_RESULT_BYTES, OPERATION_TIMEOUT_SECONDS

HOSTS = frozenset({'native', 'dsh'})
FRONTENDS = frozenset({'cli', 'codex', 'opencode', 'dsh'})
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
    from .upstream import git_identity
    root=Path(checkout).resolve()
    identity=git_identity(root)
    expected=load(Path(str(files('integrations.dsh').joinpath('UPSTREAM.json'))))
    if identity['commit']!=expected['revision']:
        raise ValueError('DSH checkout differs from pinned integration revision')
    paths=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
    identity['tracked_source_sha256']=sha({p:file_sha(root/p) for p in paths if p and (root/p).is_file()})
    identity['integration_upstream']=expected
    return identity


def acceptance_identity(benchmark, execution):
    # Scripted and live provider identities deliberately differ. Acceptance
    # validates host/tool/code, not the production provider's network access.
    return {'benchmark_sha256':sha(benchmark), 'executor_host':execution['executor_host'],
            'rpnh':execution['rpnh'], 'example_sources':execution['example_sources'],
            'host_identity':execution['host_identity'], 'limits':execution['limits'],
            'tool_schemas_sha256':benchmark['tool_schemas_sha256']}


def validate_acceptance(path, benchmark, execution):
    value = load(Path(path))
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
    for key in REQUIRED_ACCEPTANCE:
        case=cases[key]
        evidence=Path(case.get('evidence_path',''))
        if not evidence.is_file() or file_sha(evidence)!=case.get('evidence_sha256'):
            raise ValueError('acceptance evidence unavailable or changed: '+key)
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
            raise ValueError('RPNH runtime/DSH/frontend source changed after preparation')
    if validate_current and value['executor_host']=='dsh':
        if not value['dsh_checkout'] or dsh_identity(value['dsh_checkout'])!=execution['host_identity']:
            raise ValueError('DSH checkout/patch identity differs from prepared condition')
    if validate_current and execution['example_sources']!=source_identity():
        raise ValueError('example sources changed after preparation')
    frozen_profile=execution['configured_model']
    if validate_current and profile_identity(Path(value['profile']))!=frozen_profile:
        raise ValueError('execution selection changed after preparation')
    if selected_profile is not None and profile_identity(Path(selected_profile))!=frozen_profile:
        raise ValueError('frontend model selection conflicts with frozen execution selection')
    if require_acceptance:
        validate_acceptance(value['acceptance'],benchmark,execution)
    return value
