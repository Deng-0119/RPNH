"""Build the task's schema-conforming validation snapshot without changing runs."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator

ROOT = Path('<WORKSPACE>')
TASK = ROOT / 'task-shared-harness-validation-20261008'
PACKET = TASK / 'RPNH_Shared_Harness_Local_Validation_20261008'

def read(name):
    return json.loads((TASK / name).read_text())

def main():
    result = json.loads((PACKET / 'return_template.json').read_text())
    result['validated_at_utc'] = datetime.now(timezone.utc).isoformat()
    result['source'].update(initial_head='80a17c3ce45ec3c7a1b39c170c36bbb922276de1',
        initial_dirty_status='', final_dirty_status=subprocess.check_output(
            ['git', 'status', '--short'], cwd=ROOT / '.h26/s', text=True),
        file_manifest_path='cloud-handoff/file_manifest.json', baseline_matches=True, patched_hashes_match=True,
        current_head_compatibility='Product fast-forwarded documentation only to exact tested c545621 baseline; '
            'all 17 applied main files byte-equal isolated tested worktree. Validation snapshot precedes authorized GitHub delivery.')
    environment = read('logs/environment.stdout.log')
    result['environment'].update(os=environment['platform'], python=environment['python'],
        pytest=environment['versions']['pytest'], native_socket_supported=True,
        checkout_local_label='isolated exact baseline plus frozen A/B',
        dependencies=[k + '==' + v for k, v in environment['versions'].items()])
    for package in ('A', 'B'):
        result[package]['status'] = 'BLOCKED_ENV' if package == 'A' else 'PASS'
        result[package]['findings'] = ([] if package == 'B' else
            ['Unchanged host-config guard rejects namespace ancestor uid65534; WSL AF_VSOCK retry cannot start pytest.'])
        for case in result[package]['cases']:
            case.update(status='PASS', summary='Passed in the exact frozen local targeted suite; source and test identities retained.',
                evidence_paths=[f'local/logs/{package}-offline.xml', f'local/offline-test-inventory.json'],
                command=shlex.join(read(f'logs/{package}-offline.json')['command']), exit_code=0)
    a = {c['id']: c for c in result['A']['cases']}
    a['A_targeted_registry_and_consumers_242'].update(status='BLOCKED_ENV',
        summary='Actual pytest 241 passed / 1 failed / 0 skipped; one unchanged ownership guard environment blocker. '
            'Ordinary WSL retry fails before pytest at AF_VSOCK setup. No transport/permission/guard bypass.',
        evidence_paths=['local/logs/A-offline.xml','local/logs/A-offline.stdout.log','local/environment-blocker.json'],
        exit_code=1, passed=241, failed=1, skipped=0)
    a['A_same_cut_each_consumer_identity_agreement'].update(
        evidence_paths=['local/probes/A/native-observations.json','local/probes/A/read-only-observations.json',
                        'local/probes/A/consumers.xml'],
        summary='Real native managed run: distinct core-bound consumer cuts select identical exact terminal/result; '
            'head893/epoch2/268objects unchanged; no new owner or writer in read phase.')
    a['A_native_managed_action_export'].update(
        evidence_paths=['local/probes/A/consumers.xml','local/probes/A/native-observations.json'],
        command=shlex.join(read('probes/A/consumers.command.json')['command']),
        summary='Two actual pure-plugin spawned-worker v3 managed returns and current terminal exported together; '
            'no injected rows. Provider delivery remains unproven; four scripted logical calls, zero real provider calls.')
    for key in ('A_existing_live_reproject', 'A_stale_driver_expected_rejection'):
        a[key].update(status='NOT_RUN', summary='No complete existing completed native-live HA prerequisite set '
            'in declared recent task/runtime/archive search scope; no new run/resume/backend/grader to fabricate one.',
            evidence_paths=['local/historical-reproject-prerequisites.json'], command=None, exit_code=None)
    b = {c['id']: c for c in result['B']['cases']}
    b['B_targeted_36'].update(passed=36, failed=0, skipped=0)
    for key in ('B_native_direct_text','B_native_terminal_receipt_authority'):
        b[key].update(evidence_paths=['local/probes/B/gate.xml','local/probes/B/gate-ledger-evidence.json'],
            command=shlex.join(read('probes/B/gate.command.json')['command']),
            summary='Existing main-session native AF_UNIX gate passes; real child terminal receipt, one scripted call, zero real provider.')
    b['B_native_declared_structured_schema'].update(evidence_paths=['local/probes/B/j1.xml','local/probes/B/j1/evidence.json'],
        command=shlex.join(read('probes/B/j1.command.json')['command']),
        summary='Existing trusted HOST ModuleDeclaration/start_run route: actual non-text object schema and native '
            'owner/Harness/AgentLoop; product/workspace/snapshot identical108bytes. High-level AgentTaskSpec remains text-only.')
    for key in ('B_actual_prompt_catalog_registry_refs','B_canonical_recipe_provider_request_agreement'):
        b[key].update(evidence_paths=['local/probes/B/i1/evidence.json','local/probes/B/j1/evidence.json'],
            summary='Fresh exact Registry prompt/catalog and persisted logical recipe in both text and structured runs; '
                'real materializer reconstructs exact provider-visible messages/tool parameters and request bytes. '
                'Recipe and envelope remain distinct documents.')
    b['B_existing_prompt_immutability'].update(status='NOT_RUN', summary='Fresh prompt/catalog bytes unchanged during '
        'readback; no historical resumed run executed or modified. Historical resume coverage not claimed.',
        evidence_paths=['local/probes/B/i1/evidence.json','local/probes/B/j1/evidence.json'], command=None, exit_code=None)
    for case in result['combined']:
        case.update(status='PASS', summary='Mechanical exact baseline, A→B apply and final17 source identity verified; '
            'B production change prompt-only; A scope includes shared core reader and two consumers.',
            evidence_paths=['local/source-verification.json','local/integration-verification.json',
                            'cloud-handoff/file_manifest.json'], command=None, exit_code=0)
    text, structured = read('probes/B/i1/evidence.json'), read('probes/B/j1/evidence.json')
    native = result['native_evidence']
    native.update(real_provider_requests=0, scripted_logical_calls=12,
        text_chain=text, structured_chain=structured,
        recipe_request_agreement={'status':'PASS','text':text['provider_envelope'],
                                  'structured':structured['provider_envelope'],
                                  'scope':'Actual persisted recipe in each native run, not synthetic before/after prompt files.'},
        managed_action_export=read('probes/A/native-observations.json'),
        saved_live_reprojection=read('historical-reproject-prerequisites.json'),
        read_only_fingerprints=[read('probes/A/read-only-observations.json'),
            {'text':text['read_only_head_before_after'],'structured':structured['read_only_head_before_after']}])
    # Ledger12 = A existing4 + A consumer4 + B main gates1+1 + text1 + structured1.
    additions = ['probes/A/test_native_consumers.py','probes/A/run_case.py',
                 'probes/B/test_native_text.py','probes/B/test_native_structured.py']
    diff = ''
    result['local_additions'] = []
    for name in additions:
        path = TASK / name
        diff += ''.join(difflib.unified_diff([], path.read_text().splitlines(keepends=True),
                        fromfile='/dev/null', tofile='local_validation_additions/' + name))
        result['local_additions'].append({'path':'local/'+name,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'diff_path':'local/local_validation_additions.diff',
            'reason':'Independent local-only native acceptance fixture/recorder; not part of frozen A/B production patch.',
            'executed_command':'See local/probes/A/consumers.command.json or local/probes/B/i1.command.json and j1.command.json.'})
    (TASK / 'local_validation_additions.diff').write_text(diff)
    result['artifacts'] = []
    for name in ['logs/A-offline.stdout.log','logs/A-offline.stderr.log','logs/A-offline.xml',
                 'logs/B-offline.stdout.log','logs/B-offline.stderr.log','logs/B-offline.xml',
                 'environment-blocker.json','probes/A/native-observations.json','probes/A/read-only-observations.json',
                 'probes/B/i1/evidence.json','probes/B/j1/evidence.json']:
        original = (TASK/name).read_bytes()
        count = original.decode().count(str(ROOT))
        included = original.decode().replace(str(ROOT), '<WORKSPACE>').encode()
        result['artifacts'].append({'path':'local/'+name,
            'sha256':hashlib.sha256(included).hexdigest(),
            'original_sha256':hashlib.sha256(original).hexdigest(),
            'redactions':[] if not count else [{'rule':'private workspace prefix to <WORKSPACE>', 'count':count}],
            'kind':'validation evidence; included hash after declared prefix substitution'})
    result['safety'].update(no_push=True, no_actions=True, no_real_models=True, dirty_preserved=True,
        historical_scores_unchanged=True, historical_prompts_unchanged=True, no_private_db_or_credentials_uploaded=True)
    result['remaining_blockers'] = ['A one host-config second-process test BLOCKED_ENV; namespace ancestor UID mismatch; '
        'ordinary WSL retry fails AF_VSOCK setup before pytest. No permission or guard changes made.']
    schema = json.loads((PACKET / 'return_schema.json').read_text())
    Draft202012Validator(schema).validate(result)
    (TASK / 'RETURN_VALIDATION_SNAPSHOT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('Return schema PASS; A BLOCKED_ENV/B PASS; native ledger12 scripted, real0; validation snapshot before push')

if __name__ == '__main__':
    main()
