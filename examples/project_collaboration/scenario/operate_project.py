"""One local, explicitly authorized end-to-end controlled-project journey.

Uses only the installed project CLI and fixed client sockets. This program is
not run by cloud validation. It never installs dependencies or creates provider
profiles. Its input definitions are controlled human fixtures, not model authors.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import socket
import subprocess
import sys
import time

from cpn.rpnh.collaboration.project_framing import read_response, ResponseFrameError, response_error


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


class ProjectCommandError(RuntimeError):
    def __init__(self, response):
        self.response = response
        super().__init__(json.dumps(response, ensure_ascii=False))


class Journey:
    def __init__(self, root, package, profile, choice):
        self.root, self.package, self.profile, self.choice = root.resolve(), package.resolve(), profile.resolve(), choice
        self.root.mkdir(parents=True, exist_ok=False)
        self.events = self.root / 'requests'
        self.events.mkdir()
        self.sequence = 0
        self.services = []
        self.summary = {'scope': 'controlled fixed clients, human-submitted definitions, real business agents only when run',
                        'task_finished': False, 'viewer_reviewed': False, 'stages': []}

    def record(self, name, value):
        path = self.events / f'{self.sequence:03d}-{name}.json'
        self.sequence += 1
        path.write_bytes(canonical(value))
        return path

    def checkpoint(self, name, value):
        self.summary['stages'].append({'name': name, **value})
        (self.root / 'JOURNEY_RESULT.json').write_bytes(canonical(self.summary))

    def client(self, project, route, request, *, expect_error=None):
        self.record(f'{project}-{route}-request', request)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            # Business run may last according to the existing authorized profile.
            # No short transport timeout cancels or misreports its native state.
            connection.connect(str(self.root / project / (route + '.sock')))
            stream = connection.makefile('rwb')
            stream.write(canonical(request) + b'\n'); stream.flush()
            try:
                response = read_response(stream)
            except ResponseFrameError as exc:
                response = response_error(str(exc))
        self.record(f'{project}-{route}-response', response)
        if expect_error is not None:
            if response.get('ok'):
                raise RuntimeError('expected denied operation unexpectedly succeeded')
            error_type, reason = expect_error
            if response.get('error') != error_type or reason not in response.get('message', ''):
                raise RuntimeError('unexpected internal error is not a successful permission/stale probe: ' + canonical(response).decode())
            return response
        if not response.get('ok'):
            raise ProjectCommandError(response)
        return response['result']

    def initialize(self, name, archive, *, dependencies=()):
        from cpn.rpnh.collaboration.package_preview import preview_package
        from preflight_environment import preflight, workspace_platform_preflight
        preview = preview_package(archive.read_bytes())
        platform_check = workspace_platform_preflight()
        self.record(name + '-readonly-workspace-platform', platform_check)
        if not platform_check['necessary_conditions_passed']:
            self.checkpoint(name + '-platform-blocked', platform_check)
            raise RuntimeError('stock workspace requires supported Linux x86_64 and its original executable paths; see platform report')
        readiness = preflight(archive.read_bytes(), tuple(p.read_bytes() for p in dependencies))
        self.record(name + '-readonly-environment-check', readiness)
        if not readiness['metadata_scope_passed']:
            self.checkpoint(name + '-environment-blocked', {'status': 'blocked_before_model_or_install',
                'blockers': readiness['metadata_blockers']})
            raise RuntimeError('selected Python/package dependency closure does not meet this exact package; see read-only environment report')
        entries = dict(preview.artifacts)
        # The receiver reads its own downloaded package's fixture, not the old
        # source directory. Registry lineage still comes from original import.
        inputs = self.root / (name + '-inputs.json')
        inputs.write_bytes(entries['fixtures/inputs.json'])
        command = [sys.executable, '-m', 'cpn.rpnh.collaboration.project_local', 'init',
                   '--root', str(self.root / name), '--package', str(archive), '--inputs', str(inputs),
                   '--contributors', '2', '--execution-profile', str(self.profile),
                   '--read-closure-limit', str(16 * 1024 * 1024)]
        for dependency in dependencies:
            command.extend(['--dependency', str(dependency)])
        initialized = subprocess.run(command, check=True, capture_output=True, text=True)
        initial = json.loads(initialized.stdout)
        self.record(name + '-initial', initial)
        self.checkpoint(name + '-package-import', {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'manifest_sha256': preview.manifest_digest, 'environment_requirements': preview.manifest['entries'][0].get('environment_requirements_path'),
            'host_read_closure_limit_bytes': 16 * 1024 * 1024,
            'initial_revision': initial['initial_revision'], 'profile': 'existing local profile, not copied into evidence'})
        self.start_service(name)
        return initial

    def start_service(self, name):
        log_path = self.root / (name + f'-host-{len(self.services):02d}.log')
        log = log_path.open('xb')
        process = subprocess.Popen([sys.executable, '-m', 'cpn.rpnh.collaboration.project_local', 'serve',
                                    '--root', str(self.root / name)], stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        self.services.append((name, process, log))
        # READY, rather than mere socket existence, proves owners have resumed.
        deadline = time.monotonic() + 120
        while True:
            if process.poll() is not None:
                raise RuntimeError('HOST exited before READY; inspect ' + log_path.name)
            text = log_path.read_text(errors='replace')
            if any(line.startswith('{') and json.loads(line).get('status') == 'READY' for line in text.splitlines() if line.startswith('{')):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('HOST startup has not established READY; preserve process and inspect before any retry')
            time.sleep(0.1)

    def reopen_source(self):
        original = self.client('source', 'owner', {'op': 'status'})
        self.client('source', 'owner', {'op': 'stop-host', 'command_id': 'explicit-pre-collaboration-stop'})
        service = next(row for row in reversed(self.services) if row[0] == 'source')
        service[1].wait(timeout=120)
        service[2].close()
        self.start_service('source')
        resumed = self.client('source', 'owner', {'op': 'status'})
        if resumed['session'] != original['session'] or resumed['actor'] != original['actor']:
            raise RuntimeError('original identity changed across supported stop/resume')
        self.checkpoint('explicit-reopen', {'same_original_session_and_actor': True,
            'scope': 'before business execution and without reviving old read grants'})

    def grant(self, project, session, revision, *, runtime=False, destination='local-receiver', seconds=55 * 60):
        plan = self.client(project, 'owner', {'op': 'read-plan', 'revision': revision, 'include_runtime': runtime})
        scopes = [plan]
        if runtime:
            scopes.extend(getattr(self, 'summary', {}).get('runtime_read_scopes', {}).get(project, []))
        unique = lambda values: list({canonical(value): value for value in values}.values())
        return self.client(project, 'owner', {'op': 'grant-read', 'subject_session': session,
            'command_id': f'grant-{self.sequence}', 'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(),
            'record_types': sorted({kind for scope in scopes for kind in scope['record_types']}),
            'material_refs': unique([ref for scope in scopes for ref in scope['material_refs']]),
            'export_refs': unique([ref for scope in scopes for ref in scope['export_refs']]),
            'export_destinations': [destination]})

    def state(self):
        return self.client('source', 'owner', {'op': 'status'})['branch']

    def proposal(self, route, document, ids, branch, *, previous=None, reason):
        request = {'op': 'propose', 'base': branch['head_revision_ref'], 'branch': branch['branch_ref'],
            'head': branch['head_revision_ref'], 'document': document, 'element_ids': ids,
            'reason': reason, 'command_id': f'proposal-{self.sequence}'}
        if previous is not None:
            request['previous_proposal'] = previous
        return self.client('source', route, request)

    def approve(self, proposal, reason):
        return self.client('source', 'owner', {'op': 'review', 'proposal': proposal, 'disposition': 'approve',
            'reason': reason, 'command_id': f'review-{self.sequence}'})

    def accept(self, revision, proposals, reviews):
        branch = self.state()
        before = self.client('source', 'owner', {'op': 'runtime-status'})
        accepted = self.client('source', 'owner', {'op': 'accept', 'revision': revision,
            'branch': branch['branch_ref'], 'head': branch['head_revision_ref'],
            'proposals': proposals, 'reviews': reviews, 'command_id': f'accept-{self.sequence}'})
        after = self.client('source', 'owner', {'op': 'runtime-status'})
        if any(canonical(before[key]) != canonical(after[key]) for key in ('net_ref', 'checkpoint_ref', 'marking')):
            raise RuntimeError('Branch acceptance unexpectedly changed native runtime state')
        self.checkpoint('acceptance-runtime-separation', {'unchanged_net': before['net_ref'],
            'unchanged_checkpoint': before['checkpoint_ref'], 'marking_sha256': hashlib.sha256(canonical(before['marking'])).hexdigest()})
        return accepted

    def adopt(self, accepted):
        status = self.client('source', 'owner', {'op': 'runtime-status'})
        if status.get('active_firings') or status.get('pending_edits'):
            raise RuntimeError('runtime is busy or has pending edits; do not guess or retract protected pending state')
        if not status.get('marking'):
            raise RuntimeError('no source marking to carry; this scenario does not resurrect terminal tokens')
        from cpn.rpnh.agent_tasks import agent_task_registration
        from cpn.rpnh.compiler import compile_module
        from cpn.rpnh.module import ModuleDeclaration
        desired = self.client('source', 'owner', {'op': 'module', 'revision': accepted['branch']['head_revision_ref']})['module']
        old = compile_module(ModuleDeclaration.from_dict(status['declaration']), agent_task_registration())
        new = compile_module(ModuleDeclaration.from_dict(desired), agent_task_registration())
        old_places = {p.name: p for p in old.symbolic.places}
        new_places = {p.name: p for p in new.symbolic.places}
        counts = {}
        for row in status['marking']:
            place = row['place']
            if place not in old_places or place not in new_places:
                raise RuntimeError('fixed scenario place correspondence is absent; do not submit a guessed adoption')
            a, b = old_places[place], new_places[place]
            # Runtime Token.kind is a nullable execution marker, distinct from
            # the symbolic place token_kind checked on both declarations here.
            if any(getattr(a, field) != getattr(b, field) for field in
                   ('schema', 'channel', 'token_kind', 'schema_variants', 'colours', 'reusable')):
                raise RuntimeError('fixed scenario token/schema/colour correspondence changed')
            counts[place] = counts.get(place, 0) + 1
            if b.capacity is not None and counts[place] > b.capacity:
                raise RuntimeError('candidate place capacity cannot hold current exact marking')
        mapping = {row['token_ref']['version_id']: row['place'] for row in status['marking']}
        if len(mapping) != len(status['marking']):
            raise RuntimeError('duplicate token identity')
        branch = accepted['branch']
        result = self.client('source', 'owner', {'op': 'adopt', 'receipt': accepted['receipt'],
            'branch': branch['branch_ref'], 'head': branch['head_revision_ref'], 'runtime': status['net_ref'],
            'marking_mapping': mapping, 'retire_token_refs': [], 'owner_inputs': {},
            'command_id': f'adopt-{self.sequence}'})
        if 'receipt' not in result:
            raise RuntimeError('no original adoption receipt; preserve pending evidence and stop before executing')
        # Record the original scope while this exact adopted net is current.
        # Its compiler wire remains an explicitly selected body when inspecting
        # an earlier net segment after subsequent content restores.
        scope = self.client('source', 'owner', {'op': 'read-plan',
            'revision': branch['head_revision_ref'], 'include_runtime': True})
        self.summary.setdefault('runtime_read_scopes', {}).setdefault('source', []).append(scope)
        return result

    def restore(self, selected, reason):
        branch = self.state()
        proposal = self.client('source', 'owner', {'op': 'restore-content', 'restore_from': selected,
            'base': branch['head_revision_ref'], 'branch': branch['branch_ref'], 'head': branch['head_revision_ref'],
            'reason': reason, 'command_id': f'restore-{self.sequence}'})
        review = self.approve(proposal['proposal'], reason)
        accepted = self.accept(proposal['revision'], [proposal['proposal']], [review])
        adopted = self.adopt(accepted)
        return {'proposal': proposal, 'accepted': accepted, 'adopted': adopted}

    def run(self, definitions):
        initial = self.initialize('source', self.package)
        self.reopen_source()
        branch = self.state()
        base = branch['head_revision_ref']
        inspected = self.client('source', 'owner', {'op': 'module', 'revision': base})
        for route in ('contributor-1', 'contributor-2'):
            session = initial['routes'][route]['session']
            self.grant('source', session, base)
            invited = self.client('source', 'owner', {'op': 'invite', 'subject_session': session,
                'role': 'propose', 'command_id': 'invite-' + route})
            self.client('source', route, {'op': 'join', 'invitation': invited, 'command_id': 'join-' + route})
        probe_session = initial['routes']['contributor-2']['session']
        expiring = self.grant('source', probe_session, base, seconds=5)
        deadline = datetime.fromisoformat(expiring['grant']['expires_at'])
        time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()) + 0.2)
        expired = self.client('source', 'contributor-2', {'op': 'status'},
                              expect_error=('RegistryConflict', 'collaboration response read fence is stale or belongs to another actor'))
        renewed = self.grant('source', probe_session, base)
        self.client('source', 'contributor-2', {'op': 'status'})
        self.client('source', 'owner', {'op': 'revoke-read', 'grant': renewed['grant']['grant_ref'],
            'command_id': 'explicit-read-revocation-probe'})
        revoked = self.client('source', 'contributor-2', {'op': 'status'},
                              expect_error=('RegistryConflict', 'collaboration response read authority is revoked'))
        self.grant('source', probe_session, base)
        self.client('source', 'contributor-2', {'op': 'status'})
        self.checkpoint('native-read-fences', {'expiry_denial': expired, 'revocation_denial': revoked,
            'subsequent_access': 'new explicit owner grants; neither join nor reopening revived an old grant'})
        self.client('source', 'contributor-1', {'op': 'status', 'actor': 'owner'},
                    expect_error=('ValueError', 'identity is HOST-bound'))
        before_proposals = self.client('source', 'owner', {'op': 'runtime-status'})
        a = self.proposal('contributor-1', definitions['a'], inspected['element_ids'], branch,
                          reason='Controlled contributor A: implementer handoff and independent reviewer source-byte checks')
        b = self.proposal('contributor-2', definitions['b'], inspected['element_ids'], branch,
                          reason='Controlled contributor B: alternative implementer handoff and bounded single-handoff PN capacity')
        first = self.client('source', 'owner', {'op': 'analyze', 'base': base, 'local': a['revision'],
            'incoming': b['revision'], 'command_id': 'first-conflict-analysis'})
        if not first['document']['conflicts']:
            raise RuntimeError('expected real connected-contract conflict was not established')
        # Retain exact B provenance in the original proposal reason. Do not
        # relabel A's revision as authored by B or fabricate Viewer attribution.
        reconciled = self.proposal('contributor-1', definitions['resolved'], inspected['element_ids'], branch,
            previous=a['proposal'], reason='Explicit coordinated revision preserving A reviewer checks and adopting B capacity change. '
            'Original B proposal=' + canonical(b['proposal']).decode() + '; B revision=' + canonical(b['revision']).decode())
        ra = self.approve(reconciled['proposal'], 'Reviewed exact coordinated Module and cited B source; A is its actual proposer')
        rb = self.approve(b['proposal'], 'Reviewed original B proposal; retain its contribution and exact revision as incoming merge parent')
        analysis = self.client('source', 'owner', {'op': 'analyze', 'base': base, 'local': reconciled['revision'],
            'incoming': b['revision'], 'command_id': 'resolved-analysis'})
        if self.choice != 'local':
            raise ValueError('this supplied coordinated fixture requires explicit local selection to retain both approved edits')
        choices = [{'conflict_id': row['conflict_id'], 'choice': self.choice,
            'reason': 'Explicit operator-selected reconciled proposal; preserve both cited useful contributions',
            'delete_element_ids': []} for row in analysis['document']['conflicts']]
        merged = self.client('source', 'owner', {'op': 'merge', 'analysis': analysis['analysis'],
            'choices': choices, 'command_id': 'merge'})
        m = merged['revision']['revision_ref']
        if len(merged['revision']['parent_revision_refs']) != 2:
            raise RuntimeError('result is not the required genuine dual-parent revision')
        if merged['module'] != definitions['resolved']:
            raise RuntimeError('explicit resolution did not preserve the complete coordinated Module')
        after_proposals = self.client('source', 'owner', {'op': 'runtime-status'})
        if any(canonical(before_proposals[key]) != canonical(after_proposals[key]) for key in ('net_ref', 'checkpoint_ref', 'marking')):
            raise RuntimeError('proposal/review/merge changed native runtime before acceptance/adoption')
        accepted = self.accept(m, [reconciled['proposal'], b['proposal']], [ra, rb])
        self.client('source', 'contributor-2', {'op': 'propose', 'base': base, 'branch': branch['branch_ref'],
            'head': base, 'document': definitions['b'], 'element_ids': inspected['element_ids'],
            'reason': 'Expected stale initial Branch rejection before validation', 'command_id': 'stale-proposal-probe'},
            expect_error=('RegistryConflict', 'proposal branch is stale'))
        adopted = self.adopt(accepted)
        self.checkpoint('merge-adopt', {'merge_revision': m, 'parents': merged['revision']['parent_revision_refs'],
            'acceptance': accepted, 'adoption': adopted, 'conflict_count_first': len(first['document']['conflicts']),
            'conflict_count_final': len(choices), 'business_executed': False})
        rollback = self.restore(base, 'Restore original base content as a new auditable successor before any business firing')
        continuation = self.restore(m, 'Restore exact accepted M content as a new successor, preserving rollback and original merge history')
        continued = self.client('source', 'owner', {'op': 'module', 'revision': continuation['proposal']['revision']})
        if continued['module'] != merged['module']:
            raise RuntimeError('continued successor content differs from exact M')
        self.checkpoint('content-rollback-before-execution', {'rollback': rollback, 'continuation': continuation,
            'scope': 'no terminal token resurrection or filesystem-effect rollback; final source successor has exact M content'})
        owner_session = initial['routes']['owner']['session']
        self.grant('source', owner_session, m, runtime=True)
        downloaded = self.client('source', 'owner', {'op': 'download', 'revision': m, 'destination': 'local-receiver'})
        archive = self.root / 'merged-project.zip'
        archive.write_bytes(base64.b64decode(downloaded['archive_base64'], validate=True))
        dependencies = []
        for index, data in enumerate(downloaded['dependency_archives_base64']):
            path = self.root / f'dependency-{index}.zip'
            path.write_bytes(base64.b64decode(data, validate=True)); dependencies.append(path)
        target = self.initialize('receiver', archive, dependencies=dependencies)
        receiver_branch = self.client('receiver', 'owner', {'op': 'status'})['branch']
        received_module = self.client('receiver', 'owner', {'op': 'module', 'revision': receiver_branch['head_revision_ref']})
        source_module = self.client('source', 'owner', {'op': 'module', 'revision': m})
        if received_module['module'] != merged['module'] or receiver_branch['head_revision_ref'] == m:
            raise RuntimeError('receiver content or new revision identity is invalid')
        if set(received_module['element_ids'].values()) & set(source_module['element_ids'].values()):
            raise RuntimeError('receiver reused source element identities')
        if (self.root / 'source-inputs.json').read_bytes() != (self.root / 'receiver-inputs.json').read_bytes():
            raise RuntimeError('receiver fixture bytes differ from source package')
        self.checkpoint('receiver-new-identity', {'revision': receiver_branch['head_revision_ref'],
            'source_M': m, 'module_equal': True, 'disjoint_element_ids': True,
            'fixture_sha256': hashlib.sha256((self.root / 'receiver-inputs.json').read_bytes()).hexdigest()})
        source_run = self.business_run('source')
        receiver_run = self.business_run('receiver')
        artifact_results = {
            'source': self.collect_artifacts('source', owner_session),
            'receiver': self.collect_artifacts('receiver', target['routes']['owner']['session']),
        }
        self.checkpoint('native-business-runs', {'source': source_run, 'receiver': receiver_run,
            'source_revision': continuation['proposal']['revision'], 'downloaded_original_M': m,
            'independent_original_assertions': artifact_results,
            'note': 'terminal refs are not original verifier scores; inspect both actual outputs independently'})
        # Fresh explicit runtime selection after execution, never resurrect an
        # expired read session. Viewer URLs remain actual running original UI.
        self.grant('source', owner_session, self.state()['head_revision_ref'], runtime=True)
        target_branch = self.client('receiver', 'owner', {'op': 'status'})['branch']
        self.grant('receiver', target['routes']['owner']['session'], target_branch['head_revision_ref'], runtime=True)
        viewers = {name: self.client(name, 'owner', {'op': 'viewer-open'}) for name in ('source', 'receiver')}
        self.summary['viewers'] = viewers
        self.summary['hosts_remain_running_for_viewer_review'] = True
        self.summary['required_remaining_evidence'] = ['actual official Viewer overview/contribution/diff/resource navigation screenshots',
            'original output files, implementation, exact verifier outcomes and dependencies for both runs',
            'parent invocation/firing and subordinate execution-net evidence', 'actual calls and failures; no blind-score claim']
        self.checkpoint('viewer-open', viewers)
        print(json.dumps(self.summary, ensure_ascii=False, indent=2))

    def business_run(self, project):
        command_id = 'formal-' + project + '-run'
        result = self.client(project, 'owner', {'op': 'run', 'command_id': command_id})
        launch = result.get('execution_ref')
        # Current project dispatcher queues native work on its sole owner lane.
        # Poll original runtime state until it reports the same launch returned.
        while result.get('status') in {'REQUESTED', 'RUNNING'}:
            time.sleep(1)
            status = self.client(project, 'owner', {'op': 'runtime-status'})
            result = status.get('host_run_request')
            if result is None:
                raise RuntimeError('native launch state disappeared; inspect original execution receipt')
            if result.get('command_id') != command_id or result.get('execution_ref') != launch:
                raise RuntimeError('native status is not the exact requested original execution')
        if result.get('status') == 'FAILED':
            raise RuntimeError('native run failed: ' + canonical(result).decode())
        terminal_result = result.get('result') if result.get('status') == 'RETURNED' else result
        if terminal_result.get('stop_reason') != 'terminal' or terminal_result.get('terminal') is None:
            raise RuntimeError('native run returned without original terminal; preserve evidence and inspect: ' + canonical(result).decode())
        return result

    def collect_artifacts(self, project, owner_session):
        plan = self.client(project, 'owner', {'op': 'artifacts-plan'})
        branch = self.client(project, 'owner', {'op': 'status'})['branch']
        reads = self.client(project, 'owner', {'op': 'read-plan', 'revision': branch['head_revision_ref'], 'include_runtime': True})
        def unique(rows):
            return list({canonical(row): row for row in rows}.values())
        read_selection = {'op': 'grant-read', 'subject_session': owner_session,
            'record_types': sorted(set(reads['record_types']) | set(plan['record_types'])),
            'material_refs': unique(reads['material_refs'] + plan['material_refs']),
            'export_refs': unique(reads['export_refs'] + plan['export_refs']),
            'export_destinations': ['local-receiver']}
        def fresh_reader():
            self.client(project, 'owner', {**read_selection,
                'command_id': f'artifact-grant-{self.sequence}',
                'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=55)).isoformat()})
        fresh_reader()
        bundle = self.root / 'artifacts' / project
        output = bundle / 'workspace'
        output.mkdir(parents=True, exist_ok=False)
        reports = bundle / 'reports'
        reports.mkdir()
        seen = set()
        receipts = []
        for row in plan['files']:
            relative = PurePosixPath(row['path'])
            if (relative.is_absolute() or not relative.parts or '..' in relative.parts
                    or str(relative) != row['path'] or '\\' in row['path'] or '\x00' in row['path'] or str(relative) in seen):
                raise RuntimeError('unsafe or duplicate artifact path from settled checkpoint')
            seen.add(str(relative))
            request = {'op': 'artifact-read', 'checkpoint': plan['checkpoint_ref'],
                'workspace': row['workspace_revision_ref'], 'path': row['path']}
            try:
                value = self.client(project, 'owner', request)
            except ProjectCommandError as exc:
                if (exc.response.get('error'), exc.response.get('message')) != (
                        'RegistryReadSessionError', 'limit exceeded'):
                    raise
                # Each capture retains a bounded snapshot. Release the old
                # reader through the original same-scope owner grant, then
                # retry this exact artifact once under unchanged read limits.
                # Expired/revoked access and an oversized single body still fail.
                fresh_reader()
                value = self.client(project, 'owner', request)
            body = base64.b64decode(value['content_base64'], validate=True)
            if len(body) != row['byte_count'] or hashlib.sha256(body).hexdigest() != row['sha256']:
                raise RuntimeError('artifact body differs from exact settled manifest')
            destination = output.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
            receipts.append(row)
        (reports / 'EXACT_REGISTRY_ARTIFACTS.json').write_bytes(canonical({'checkpoint_ref': plan['checkpoint_ref'], 'files': receipts}))
        # Use the shipped trusted adapter and frozen original grader, never an
        # agent-edited test script. This subprocess only evaluates produced data.
        scenario = Path(__file__).resolve().parent
        report = reports / 'ORIGINAL_ASSERTIONS_PATH_ADAPTED.json'
        run = subprocess.run([sys.executable, str(scenario / 'verifier_adapter.py'),
            '--verifier', str(scenario.parent / 'materials/upstream/multi-source-data-merger/tests/test_outputs.py'),
            '--outputs', str(output / 'outputs'), '--report', str(report)], capture_output=True, text=True)
        (reports / 'VERIFIER_STDOUT.txt').write_text(run.stdout)
        (reports / 'VERIFIER_STDERR.txt').write_text(run.stderr)
        return {'artifact_count': len(receipts), 'checkpoint_ref': plan['checkpoint_ref'],
            'adapter_exit_code': run.returncode,
            'scorer_report': json.loads(report.read_text()) if report.exists() else None,
            'status': 'passed_original_assertions_with_disclosed_path_relocation' if run.returncode == 0 else 'failed_or_blocked',
            'full_instruction_review': 'separate agent report and human review required', 'official_test_sh_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='fresh local directory; short socket-compatible path')
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--definitions', type=Path, required=True, help='directory of generated contributor definitions')
    parser.add_argument('--existing-profile', type=Path, required=True)
    parser.add_argument('--conflict-choice', required=True, choices=['local'])
    parser.add_argument('--authorized-model-run', action='store_true', required=True,
                        help='Use only after local task has existing provider/model/budget authorization')
    args = parser.parse_args()
    definitions = {name: json.loads((args.definitions / filename).read_text()) for name, filename in
        [('a', 'contributor-a.json'), ('b', 'contributor-b.json'), ('resolved', 'contributor-a-resolved.json')]}
    journey = Journey(args.root, args.package, args.existing_profile, args.conflict_choice)
    try:
        journey.run(definitions)
    except Exception as exc:
        journey.checkpoint('blocked', {'error_type': type(exc).__name__, 'message': str(exc),
            'recovery': 'Preserve original requests, receipts and owners. Do not rerun with new IDs, kill writers, alter permissions, or bypass pending gates.'})
        raise


if __name__ == '__main__':
    main()
