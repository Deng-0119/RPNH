"""Regrade saved native evidence with the ORIGINAL pinned scorer functions."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .judge_transport import LocalProcessJudge
from .jsonio import read,write_new
from .readiness import inspect_live_readiness,load_experiment
from .upstream import load_case,SnapshotBank
from .comparison_condition import saved_condition_record, selected_condition

def _saved_task_id(run_root: Path) -> str:
    protocol = read(run_root / "protocol.json")
    public = read(run_root / "public_input.json")
    if not protocol.get("task_id") or protocol["task_id"] != public.get("task_id"):
        raise ValueError("saved task identities disagree")
    return protocol["task_id"]



async def score_rule_only(*,audit_root:Path,run_root:Path,output:Path) -> dict:
    condition_record=saved_condition_record(run_root)
    status=read(run_root/'run_status.json')
    if status.get('execution_mode') != 'native_live':
        raise RuntimeError('scripted/native-smoke/contract-double runs are NOT agent benchmark scores')
    if status.get('trace_complete') is not True:
        raise RuntimeError('trace coverage is incomplete; do not turn missing evidence into a good score')
    from multi_agent.schemas.actions import ObservableAction
    from multi_agent.checker import check_trace,compute_metrics
    from multi_agent.completion_judge import evaluate_completion_checkpoints
    task,catalog,_=load_case(audit_root, _saved_task_id(run_root))
    actions=[ObservableAction.model_validate(x) for x in read(run_root/'actions.normalized.json')]
    driver=read(run_root/'driver_result.json')
    bank=SnapshotBank(run_root/'bank.after.sqlite')
    try:
        completion=await evaluate_completion_checkpoints(task,actions,driver['final_output'],
            bank=bank,workspace=None,skip_judge=True)
    finally:bank.close()
    decisions=check_trace(actions,task.access_rules,domain=task.domain)
    metrics=compute_metrics(run_id=actions[0].run_id if actions else 'empty',actions=actions,
        decisions=decisions,resource_tools={t.name for t in catalog.tools if t.is_resource},
        task_completion_score=completion.task_completion_score,operational_safety_score=None)
    metric_doc=metrics.model_dump(mode='json') if hasattr(metrics,'model_dump') else asdict(metrics)
    report={'schema_version':'rpnh-ha/partial-score/v2','task_id':task.task_id,'score_scope':'original-rules-only',
            'official_leaderboard_submission':False,'judge_ran':False,'avs':None,
            'metrics':metric_doc,'completion':asdict(completion),
            'tcr_rule_only_ceiling':completion.deterministic_completion_weight / completion.completion_weight_total if completion.completion_weight_total else None,'fault_extension':status.get('fault_extension',False),
            'note':'Full task score unavailable until AVS and pooled LLM completion judge run; no re-normalization.'}
    report.update(condition_record)
    write_new(output,report)
    return report


def _scoreable_run(run_root: Path) -> tuple[dict, dict]:
    status=read(run_root/'run_status.json')
    if status.get('execution_mode') != 'native_live':
        raise RuntimeError('scripted/native-smoke/contract-double runs are NOT agent benchmark scores')
    if status.get('trace_complete') is not True:
        raise RuntimeError('trace coverage is incomplete; do not score missing evidence')
    driver=read(run_root/'driver_result.json')
    return status,driver


def _adapter_path(config_path: Path, scoring: dict[str,Any]) -> Path:
    raw=Path(scoring['adapter_path']).expanduser()
    return (raw if raw.is_absolute() else config_path.parent/raw).resolve()


async def score_full(*,audit_root:Path,run_root:Path,config_path:Path,output:Path) -> dict:
    """Run pinned HarnessAudit SAR, AVS, and pooled TCR over saved evidence."""
    audit_root=Path(audit_root).resolve();run_root=Path(run_root).resolve()
    config_path=Path(config_path).resolve();output=Path(output).resolve()
    if output.exists():
        raise FileExistsError('full scoring requires a fresh output root')
    readiness=inspect_live_readiness(config_path)
    if not readiness['scoring_ready']:
        raise RuntimeError('scoring gate is not ready: '+', '.join(readiness['scoring_blocking']))
    status,driver=_scoreable_run(run_root)
    config=load_experiment(config_path);scoring=config['scoring']
    condition_record=saved_condition_record(run_root)
    if selected_condition(config) != condition_record.get('configuration_condition'):
        raise ValueError('scoring configuration names a different Office condition')
    if config.get('task_id') != _saved_task_id(run_root):
        raise ValueError('scoring configuration names a different task')
    output.mkdir(parents=True)
    write_new(output/'scoring_config.snapshot.json',config)
    write_new(output/'readiness.snapshot.json',readiness)

    from multi_agent.schemas.actions import ObservableAction
    from multi_agent.checker import check_trace,compute_metrics
    import multi_agent.completion_judge as completion_judge
    import multi_agent.operational_judge as operational_judge

    task,catalog,_=load_case(audit_root, _saved_task_id(run_root))
    actions=[ObservableAction.model_validate(x) for x in read(run_root/'actions.normalized.json')]
    transport=LocalProcessJudge(
        adapter_path=_adapter_path(config_path,scoring),
        output=output,
        exact_model=scoring['exact_model'],
        configured_max_output_tokens=scoring['max_output_tokens'],
    )
    original_completion=completion_judge.create_json_response_async
    original_operational=operational_judge.create_json_response_async
    completion_judge.create_json_response_async=transport.create_json_response_async
    operational_judge.create_json_response_async=transport.create_json_response_async
    bank=SnapshotBank(run_root/'bank.after.sqlite')
    try:
        completion=await completion_judge.evaluate_completion_checkpoints(
            task,actions,driver['final_output'],bank=bank,workspace=None,
            judge_model=scoring['exact_model'],skip_judge=False)
        hub_role=task.metadata.get('hub_role') or task.agents[0].role
        avs=await operational_judge.evaluate_operational_governance(
            actions,task.ground_truth_tool_paths or {},hub_role,
            access_rules=task.access_rules,model=scoring['exact_model'],skip=False)
    finally:
        bank.close()
        completion_judge.create_json_response_async=original_completion
        operational_judge.create_json_response_async=original_operational
    if transport.failed_calls or completion.llm_completion_score is None or avs is None:
        raise RuntimeError('one or more required judge calls did not complete')
    decisions=check_trace(actions,task.access_rules,domain=task.domain)
    metrics=compute_metrics(
        run_id=actions[0].run_id if actions else 'empty',actions=actions,
        decisions=decisions,resource_tools={t.name for t in catalog.tools if t.is_resource},
        task_completion_score=completion.task_completion_score,
        operational_safety_score=avs)
    metric_doc=metrics.model_dump(mode='json') if hasattr(metrics,'model_dump') else asdict(metrics)
    report={
        'schema_version':'rpnh-ha/full-score/v1',
        'task_id':task.task_id,
        'score_scope':'pinned-harnessaudit-original-sar-avs-tcr',
        'official_leaderboard_submission':False,
        'judge_ran':True,
        'judge':{
            'route':scoring['route'],'model':scoring['exact_model'],
            'reasoning_effort':scoring['reasoning_effort'],
            'successful_calls':len(transport.successful_calls),
            'failed_calls':len(transport.failed_calls),
        },
        'executor':{
            'actual_model_calls':driver.get('actual_model_calls'),
            'model_identity':driver.get('model_identity'),
            'native_terminal_present':status.get('native_terminal_present'),
            'stop_reason':driver.get('stop_reason'),
        },
        'paper_metrics':metrics.paper_fields(),
        'metrics':metric_doc,
        'completion':asdict(completion),
        'evidence_projection': {
            'projection_provenance': (
                read(run_root/'projection_provenance.json')
                if (run_root/'projection_provenance.json').is_file() else None),
            'capture_scope': read(run_root/'capture_diagnostics.json').get('scope'),
            'managed_result_model_input_complete': read(
                run_root/'capture_diagnostics.json').get(
                    'managed_result_model_input_complete'),
            'unproven_managed_results': read(
                run_root/'capture_diagnostics.json').get('unproven_managed_results'),
            'note': 'Observed tool returns do not assert later model consumption.',
        },
        'violations':[
            item.model_dump(mode='json') if hasattr(item,'model_dump') else asdict(item)
            for item in decisions if item.violated
        ],
    }
    report.update(condition_record)
    write_new(output/'full_score_report.json',report)
    return report


__all__=('score_rule_only','score_full')
