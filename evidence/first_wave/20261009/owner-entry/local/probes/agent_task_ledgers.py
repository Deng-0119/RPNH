"""Read-only summaries of the three already completed scripted native tests."""
import json
from pathlib import Path

from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.registry._registry import _RegistryCore

base = Path('<WORKSPACE>/.o26/a')
result = []
for database in sorted(base.rglob('registry.sqlite3')):
    run = database.parent.parent
    core = _RegistryCore(run, create=False, read_only=True, catalog=agent_task_catalog())
    before = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    counts = list(core.event_store.actual_model_call_counts())
    terminals = [dict(core.get_version(row['version_id']).metadata)
                 for row in core.event_store.object_rows_by_type('run_terminal_evidence/v1')]
    after = (core.event_store.max_ordinal(), core.event_store.writer_epoch)
    assert before == after
    result.append({'run_relative_to_basetemp': run.relative_to(base).as_posix(),
        'scripted_logical_ledger': counts, 'tuple_semantics': ['settled_calls', 'post_limit_excess'],
        'terminal_evidence_refs': [t['terminal_evidence_ref'] for t in terminals],
        'run_outcomes': [t['run_outcome'] for t in terminals],
        'head_epoch_before': before, 'head_epoch_after': after})
assert len(result) == 3
print(json.dumps({'status': 'PASS_READ_ONLY', 'runs': result,
    'real_provider_requests': 0,
    'qualification': 'The selected tests inject scripted ports; ledger entries are logical calls, not real provider requests.'}, indent=2))
