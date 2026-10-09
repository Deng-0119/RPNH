"""Resume incomplete core gates sequentially with a host-disk budget."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('<WORKSPACE>').resolve()
TASK = ROOT / 'task-complete-v5-20261009'
OUT = TASK / 'stages/core'
PY = str(ROOT / '.p26/v/bin/python')
S1 = TASK / 'raw/RPNH_Static_Lease_Reads_Offline_Candidate_20261008/RPNH_Static_Lease_Reads_Offline_Candidate_20261008'
CORE = TASK / 'raw/rpnh-parent-child-core-candidate-20261008/rpnh-parent-child-core-implementation'
HIST = TASK / 'raw/rpnh-acceptance-history-validator/rpnh-acceptance-history-validator'
LOW = TASK / 'raw/rpnh-bound-child-material-lowering/rpnh-bound-child-material-lowering'
DESELECT = [
    '--deselect=tests/test_static_lease_reads.py::test_new_process_cold_registry_reconstructs_active_and_settled_refs',
    '--deselect=tests/test_invocation_functional_boundary.py::test_functional_modules_import_without_preloading_invocations',
    '--deselect=tests/test_operation_functional_split.py::test_operation_facade_wrappers_delegate_and_preserve_contracts']
ADJACENT = ['tests/test_static_lease_reads.py', 'tests/test_static_lease_exact_selection.py',
            'tests/test_static_lease_interactions.py', 'tests/test_event_store_functional_split.py',
            'tests/test_invocation_functional_boundary.py', 'tests/test_operation_functional_split.py',
            'tests/test_registered_operation_recovery.py']


def resource_gate():
    host = os.statvfs('/mnt/d')
    available = host.f_bavail * host.f_frsize
    used = sum(int(subprocess.check_output(['du', '-sx', '-B1', str(p)], text=True).split()[0])
               for p in (ROOT / '.v26', TASK))
    assert available >= 25 * 1024**3, ('HOST_DISK_RESERVE', available)
    assert used < 8 * 1024**3, ('TASK_STORAGE_CEILING', used)
    return {'host_D_available_bytes': available, 'task_runtime_bytes': used}


def compact(name):
    if (ROOT / '.v26' / name).exists():
        subprocess.run([PY, str(TASK / 'compact_runtime.py'), name], check=True)


def run(label, repo, command, temp=None, xml=None, category='offline'):
    state = {'active': label, 'utc': datetime.now(timezone.utc).isoformat(),
             'resources_before': resource_gate(), 'category': category}
    (OUT / 'resume-queue-state.json').write_text(json.dumps(state, indent=2) + '\n')
    actual = [str(a) for a in command]
    if temp:
        actual.append('--basetemp=' + str(ROOT / '.v26' / temp))
    if xml:
        actual.append('--junitxml=' + str(OUT / xml))
    args = [PY, str(TASK / 'record.py'), '--out', str(OUT / label), '--cwd', str(repo),
            '--pythonpath', str(repo) + ':' + str(repo / 'tests'), '--', *actual]
    result = subprocess.run(args)
    print(json.dumps({'finished': label, 'exit_code': result.returncode}), flush=True)
    # Raw failures remain failures; other independently frozen stage gates continue.
    if temp:
        compact(temp)
    return result.returncode


def main():
    # The first resumed author batch was already started by the parent.
    record = OUT / 'resume-S1-author.json'
    while not record.exists():
        try:
            os.kill(17719, 0)
        except ProcessLookupError:
            raise RuntimeError('S1 author ended without a complete exit record')
        time.sleep(1)
    compact('usa')
    c1, c2, c3, c4 = [ROOT / '.v26' / ('c' + str(n)) for n in range(1, 5)]
    run('resume-S1-regression', c1, [PY, S1 / 'tools/offline_pytest.py', '-q', '-p', 'no:cacheprovider',
        'tests/test_marking_modularization.py', 'tests/test_registered_operation_recovery.py',
        'tests/test_structural_evidence.py', '-k',
        'not test_inspector_routes_real_execution_and_preserves_exact_request and not test_scheduler_cannot_turn_disabled_operation_into_enabled_one'],
        'usr', 'S1/resume-regression.xml')
    run('resume-S1-probe-read-edit', c1, [PY, S1 / 'tools/run_independent_review.py',
        '--source', c1, '--mode', 'read-edit'])
    run('resume-core-author', c2, [PY, CORE / 'tools/offline_pytest.py', '-q', '-p', 'no:cacheprovider',
        'tests/test_parent_child_core.py', *ADJACENT, *DESELECT], 'uca', 'H7_core/resume-author.xml')
    run('resume-core-independent', c2, [PY, CORE / 'independent-review/run_d0.py', c2,
        OUT / 'H7_core/resume-independent-results.json', '-q', '-p', 'no:cacheprovider',
        CORE / 'independent-review/tests'], 'uci', 'H7_core/resume-independent.xml')
    run('resume-history-author', c3, [PY, HIST / 'tools/offline_pytest.py', '-q', '-p', 'no:cacheprovider',
        'tests/test_acceptance_history.py', 'tests/test_parent_child_core.py', *ADJACENT,
        HIST / 'inputs/H7-core-review-tests', *DESELECT], 'uha', 'H7_history/resume-author.xml')
    # Original independent wrapper retains its exact 994-file source guard.
    run('resume-history-independent', c3, ['env', 'RPNH_PYTHON=' + PY,
        'RPNH_SOURCE=' + str(HIST / 'source'),
        'REVIEW_RESULTS_DIR=' + str(OUT / 'H7_history/review-resume-exact'),
        'PYTEST_ADDOPTS=--basetemp=' + str(ROOT / '.v26/uhi'),
        'bash', HIST / 'review/tools/run-review.sh'], category='exact_curated_994_offline')
    compact('uhi')
    runner = LOW / 'tools/offline_recorded.py'
    for label, tests, temp, xml in [
        ('resume-lower-new', ['tests/test_bound_child_lowering.py', 'tests/test_bound_child_declarations.py'], 'uln', 'new'),
        ('resume-lower-compiler', ['tests/test_typed_author_interoperation.py', 'tests/test_compiler_json_contract.py',
                                  'tests/test_worker_schema_compat.py', 'tests/test_control_ir_compiler.py'], 'ulc', 'compiler'),
        ('resume-lower-core', ['tests/test_parent_child_core.py', 'tests/test_acceptance_history.py',
                             'tests/test_static_lease_reads.py', 'tests/test_static_lease_interactions.py',
                             'tests/test_static_lease_exact_selection.py', DESELECT[0]], 'ulr', 'core')]:
        run(label, c4, [PY, runner, c4, OUT / ('H7_lowering/' + label + '-results.json'),
            '-q', '-p', 'no:cacheprovider', *tests], temp, 'H7_lowering/resume-' + xml + '.xml')
    run('resume-lower-independent', c4, [PY, LOW / 'review/tools/offline_pytest.py', '-q', '-p', 'no:cacheprovider',
        LOW / 'review/tests/test_independent_bound_lowering.py', LOW / 'review/tests/test_independent_bound_revision.py',
        LOW / 'review/tests/test_independent_bound_catalog.py', 'tests/test_local_takeover_ordinary_revision.py'],
        'uli', 'H7_lowering/resume-independent.xml')
    (OUT / 'resume-queue-state.json').write_text(json.dumps({
        'status': 'COMMANDS_FINISHED_REQUIRES_ACCEPTANCE', 'utc': datetime.now(timezone.utc).isoformat(),
        'resources_after': resource_gate()}, indent=2) + '\n')


if __name__ == '__main__':
    main()
