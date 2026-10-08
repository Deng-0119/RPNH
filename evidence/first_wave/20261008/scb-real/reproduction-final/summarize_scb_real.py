"""Derive a final, separate summary without editing original trial artifacts."""
from pathlib import Path
import json
from rpnh_scb.contracts import CheckpointLedger, CheckpointScope

ROOT = Path('/home/deng123/RPNH').resolve()
TASK = ROOT / 'task-first-wave-examples-20261008'
RUN = ROOT / '.s26/scb01'
exit_receipt = json.loads((TASK / 'evidence/scb-real01-exit.json').read_text())
assert exit_receipt['exit_code'] == 0
scope = CheckpointScope()
records = CheckpointLedger(RUN / 'lineage', scope).records()
assert len(records) == 3 and all(row['handoff_status'] == 'submitted' for row in records)
rows = []
previous = None
for name in scope.names:
    directory = RUN / name
    before = json.loads((directory / 'before.json').read_text())
    after = json.loads((directory / 'after.json').read_text())
    if previous is not None:
        assert before['sha256'] == previous
    previous = after['sha256']
    grade = json.loads((directory / 'evaluation.json').read_text())
    result = json.loads((directory / 'rpnh-result.json').read_text())
    calls, excess = result['actual_model_call_counts']
    events = [json.loads(line) for line in (directory / 'commands.jsonl').read_text().splitlines()]
    started = [row for row in events if row['event'] == 'command_started']
    finished = [row for row in events if row['event'] == 'command_finished']
    assert len(started) == len(finished)
    passed = sum(grade['pass_counts'].values())
    total = sum(grade['total_counts'].values())
    rows.append({'checkpoint': name, 'passed_cases': passed, 'total_cases': total,
        'pass_counts': grade['pass_counts'], 'total_counts': grade['total_counts'],
        'all_original_cases_passed': passed == total and not grade['infrastructure_failure'],
        'infrastructure_failure': grade['infrastructure_failure'],
        'pytest_exit_code': grade['pytest_exit_code'], 'settled_model_calls': calls,
        'post_limit_excess_calls': excess, 'commands_started': len(started),
        'commands_finished': len(finished),
        'command_exit_codes': [row['response'].get('result', {}).get('exit_code') for row in finished],
        'owner_run_outcome': result['run_outcome'],
        'source_snapshot': str((directory / 'snapshot').relative_to(ROOT))})
summary = {'status': 'SELECTED_PREFIX_ALL_CASES_PASS' if all(row['all_original_cases_passed'] for row in rows) else 'SELECTED_PREFIX_COMPLETED_WITH_BUSINESS_FAILURE',
    'mode': 'adapted_development_prefix', 'task': 'code_search', 'scope': 'first_3_of_5_checkpoints',
    'selected_pass_policy': 'any-case', 'official_agent_runner': 'not_run',
    'runner_revision': '31ceea3add480edb33431e70475c4c70597e6b31',
    'problems_revision': '9cd9ca3a51c3d3e2a99d2488a25baf73a2204451',
    'runtime_byte_identity_record': 'scb-real-install-byte-identity.json',
    'runtime_matches_product': '74fad32d369876841686d10d33361c016e3d3648',
    'model': 'codex/gpt-5.6-terra', 'max_model_calls_per_checkpoint': 48,
    'owner_wait_seconds_per_checkpoint': 7200, 'solver_network': 'none',
    'evaluation_network': 'host', 'image_build_network': 'host',
    'image_build_adaptation': 'same-version download proxy/pipefail/retry; original recipe, failed log and patch retained',
    'upstream_cost_net_cost_step_limits': [0, 0, 0],
    'checkpoints': rows, 'settled_real_model_calls': sum(row['settled_model_calls'] for row in rows),
    'real_call_classification': 'Actual official Codex endpoint/unchanged selected subscription bridge; no fixture provider in this real run. Registry tuple alone does not classify real versus fake.',
    'post_limit_excess_calls': sum(row['post_limit_excess_calls'] for row in rows),
    'source_continuity_checked': True, 'grader_feedback_to_solver': False,
    'automatic_retry_or_resume': False, 'normalized_tokens': None, 'cost_usd': None,
    'usage_status': 'unavailable', 'elapsed_seconds': exit_receipt['elapsed_seconds'],
    'raw_original_manifests_rewritten': False,
    'limitations': ['Partial prefix, not full5 or unmodified official benchmark.',
                    'No quality judge/official AgentRunner or causal harness advantage measurement.',
                    'Per-checkpoint tests include regression cases; case counts must not be added as unique benchmark tasks.']}
path = TASK / 'evidence/scb-real-summary.json'
assert path.resolve().is_relative_to(ROOT) and not path.exists()
with path.open('x') as stream:
    json.dump(summary, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps(summary, ensure_ascii=False))
