"""Installed native SCB boundary acceptance with real local fixture subprocesses.

No Docker, upstream dependency imports, real model or existing provider profile.
Only run after the parent confirms SCB SOURCE FROZEN and installs its wheel into
--site. All artifacts stay within the dedicated task root.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import traceback
import uuid

from scb_fake_provider import write_profile
from scb_subprocess_fixture import FixtureSession, DockerFixture

ROOT = Path('/home/deng123/RPNH/task-first-wave-examples-20261008')
DOCKER_FIXTURES = []


def require(value, message):
    if not value:
        raise AssertionError(message)


def write_json(path, value):
    path = Path(path)
    require(path.resolve().is_relative_to(ROOT.resolve()), 'output escaped task root')
    def encode(item):
        if isinstance(item, Mapping):
            return dict(item)
        raise TypeError(f'unsupported evidence value: {type(item).__name__}')
    payload = json.dumps(value, default=encode, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(payload + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(site, manifest_path):
    import cpn
    import rpnh_scb
    import rpnh_scb.plugin
    from cpn.rpnh.task_control import TaskControl
    import cpn.plugins.worker
    site = site.resolve(strict=True)
    expected = json.loads(manifest_path.read_text())
    require(expected['parent_source_frozen'] is True, 'parent freeze acknowledgement missing')
    prefix = Path(sys.prefix).resolve()
    require(sys.prefix != sys.base_prefix, 'existing installed venv required')
    require(Path(cpn.__file__).resolve().is_relative_to(prefix), 'CPN source import is forbidden')
    for module in (rpnh_scb, rpnh_scb.plugin):
        require(Path(module.__file__).resolve().is_relative_to(site), 'SCB must import from installed wheel target')
    dist = importlib.metadata.distribution('rpnh-slopcodebench')
    require(Path(dist.locate_file('')).resolve() == site, 'wrong SCB installed distribution')
    entries = [ep for ep in dist.entry_points if ep.group == 'rpnh.plugins' and ep.name == 'scb_session']
    require(len(entries) == 1 and entries[0].value == 'rpnh_scb.plugin:factory', 'SCB entry point mismatch')
    for name, digest in expected['package_files'].items():
        require(sha(site / 'rpnh_scb' / name) == digest, 'installed SCB differs from frozen wheel: ' + name)
    return {'python': sys.executable, 'prefix': str(prefix), 'uid': os.getuid(), 'gid': os.getgid(),
            'cpn': str(Path(cpn.__file__).resolve()), 'rpnh_scb': str(Path(rpnh_scb.__file__).resolve()),
            'core_version': importlib.metadata.version('rpnh-harness'),
            'scb_version': dist.version, 'entry_point': entries[0].value,
            'task_control_sha256': sha(sys.modules[TaskControl.__module__].__file__),
            'plugin_worker_sha256': sha(cpn.plugins.worker.__file__),
            'scb_frozen_manifest': expected,
            'helper_sha256': {name: sha(Path(__file__).parent / name) for name in ('scb_native_acceptance.py', 'scb_fake_provider.py', 'scb_subprocess_fixture.py')},
            'claims': {'actual_native_execution': 'pending', 'docker': 'not_run',
                       'real_provider': 'not_run', 'official_session': 'not_run'}}


def collect(control, handle, directory):
    status = control.status(handle.task_id)
    evidence = control.result_evidence(handle.task_id)
    write_json(directory / 'owner-status.json', status)
    write_json(directory / 'result-evidence.json', evidence)
    return status, evidence


def case_run(output, mode, *, backend, image_id):
    from cpn.rpnh.task_control import TaskControl
    from rpnh_scb.broker import SessionCommandBroker
    from rpnh_scb.pilot import build_spec, DevelopmentCondition

    directory = output / mode
    directory.mkdir(mode=0o700)
    marker = 'scb-native-' + uuid.uuid4().hex
    if backend == 'docker':
        runtime = DockerFixture(directory, mode, marker, image_id)
        DOCKER_FIXTURES.append(runtime)
    else:
        session = FixtureSession(directory / 'fixture', mode, marker)
        runtime = session.spawn()
    prompt = ('Synthetic native wiring fixture only. Read this full registered request, execute the one '
              'provided session command, and publish the observed marker. No benchmark task or grader.\n'
              + runtime.command + '\n')
    fixture = {'case': mode, 'marker': marker, 'prompt': prompt, 'command': runtime.command}
    profile = write_profile(directory / 'profile', fixture)
    broker = SessionCommandBroker(runtime.broker_runtime, checkpoint_key='checkpoint_1', evidence_dir=directory / 'b')
    control = TaskControl(directory / 'c')
    handle = None
    forced = False
    primary = None
    entered = False
    broker_entered = False
    rc = None
    snapshot = None
    stop_response = None
    quiescence = None
    deadline = time.monotonic() + 90
    try:
        broker.__enter__()
        broker_entered = True
        require(len(os.fsencode(broker.endpoint)) <= 107, 'broker AF_UNIX path exceeds sun_path')
        require(stat.S_ISSOCK(os.stat(broker.endpoint).st_mode), 'broker did not create a real Unix socket')
        spec = build_spec(prompt=prompt, run_dir=directory / 'r', execution=profile,
            endpoint=broker.endpoint, checkpoint_key='checkpoint_1',
            condition=DevelopmentCondition(max_model_calls=8, checkpoint_timeout_seconds=90))
        write_json(directory / 'binding.json', {'plugin_configuration': spec.plugin_configuration,
            'catalog_digest': spec.plugin_catalog_digest, 'managed_bindings': spec.managed_bindings,
            'max_attempts_per_stage': spec.max_attempts_per_stage, 'prompt': prompt})
        handle = control.start(spec)
        write_json(directory / 'owner-handle.json', {'task_id': handle.task_id,
            'pid': handle.process.pid, 'socket_path': str(handle.socket_path),
            'log_path': str(handle.log_path), 'run_dir': str(handle.run_dir)})
        while not (runtime.working_dir / 'entered.json').exists():
            require(handle.process.poll() is None, 'owner exited before actual subprocess admission')
            require(time.monotonic() < deadline, '90s fixture deadline before subprocess admission')
            time.sleep(.025)
        child = json.loads((runtime.working_dir / 'entered.json').read_text())
        live_runtime = runtime.capture_live(child, directory)
        entered = True
        require(stat.S_ISSOCK(os.stat(handle.socket_path).st_mode), 'owner did not create a real Unix socket')
        if mode == 'complete':
            # A native operation can occupy the owner's serial control loop.
            # Queue a public snapshot, release the fixture writer, then await
            # the real reply. Do not require a snapshot to preempt that call.
            with ThreadPoolExecutor(max_workers=1) as requests:
                response = requests.submit(control.snapshot, handle.task_id)
                (runtime.working_dir / 'release').touch(exist_ok=False)
                snapshot = response.result(timeout=max(.01, deadline - time.monotonic()))
            write_json(directory / 'owner-snapshot.json', snapshot)
        else:
            stop_response = control.stop(handle.task_id, startup_safe=True)
            write_json(directory / 'stop-response.json', stop_response)
            require(stop_response['status'] == 'STOP_REQUESTED', 'stop did not reach a live owner')
        write_json(directory / 'live-ipc.json', {'owner_socket': str(handle.socket_path),
            'broker_socket': broker.endpoint, 'owner_af_unix_roundtrip': snapshot is not None,
            'snapshot_scope': 'queued while child live, replied after release' if snapshot is not None else 'not_requested_while_inflight_native_call',
            'fixture_child': child, 'owner_pid': handle.process.pid, 'runtime': live_runtime})
        rc = handle.process.wait(timeout=max(.01, deadline - time.monotonic()))
        write_json(directory / 'owner-exit.json', {'return_code': rc, 'forced': False})
    except BaseException as exc:
        primary = exc
        write_json(directory / 'failure.json', {'type': type(exc).__name__, 'error': str(exc),
            'traceback': traceback.format_exc(), 'fixture_subprocess_entered': entered})
    finally:
        cleanup_errors = []
        if handle is not None and handle.process.poll() is None:
            try:
                control.stop(handle.task_id, startup_safe=True)
                rc = handle.process.wait(timeout=20)
            except BaseException as exc:
                cleanup_errors.append(str(exc))
                forced = True
                handle.process.terminate()
                try:
                    rc = handle.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    handle.process.kill()
                    rc = handle.process.wait(timeout=5)
        try:
            if broker_entered:
                broker.__exit__(None, None, None)
            else:
                runtime.cleanup()
        except BaseException as exc:
            cleanup_errors.append(str(exc))
            runtime.cleanup()
        quiescence = runtime.quiescence()
        quiescence.update(owner_exited=handle is not None and handle.process.poll() is not None,
            owner_return_code=rc, forced_owner_termination=forced,
            broker_quiescent=broker.quiescent, broker_poisoned=broker.poisoned,
            broker_socket_removed=not Path(getattr(broker, 'endpoint', directory / 'absent')).exists(),
            broker_thread_exited=broker._thread is None or not broker._thread.is_alive(),
            cleanup_errors=cleanup_errors)
        write_json(directory / 'quiescence.json', quiescence)
        if handle is not None:
            try:
                status, evidence = collect(control, handle, directory)
            except BaseException as exc:
                write_json(directory / 'evidence-read-failure.json', {'type': type(exc).__name__, 'error': str(exc)})
                if primary is None:
                    primary = exc
    if primary:
        raise primary
    require(not forced and not quiescence['cleanup_errors'], 'supported owner/broker shutdown failed')
    require(all(quiescence[k] for k in ('owner_exited', 'process_exited', 'stream_exited',
            'broker_quiescent', 'broker_socket_removed', 'broker_thread_exited')), 'quiescence incomplete')
    require(not quiescence['process_group_alive'] and runtime.calls == 1, 'fixture writer remains or command replayed')
    requests = sorted((directory / 'profile/transcript').glob('request-*.json'))
    tools = sorted(t['function']['name'] for t in json.loads(requests[0].read_text())['tools'])
    write_json(directory / 'tool-inventory.json', {'tools': tools})
    require(evidence['actual_model_call_counts'] == [len(requests), 0], 'Registry/fake process call counts disagree')
    rows = [json.loads(line) for line in (directory / 'b/commands.jsonl').read_text().splitlines()]
    starts = [r for r in rows if r['event'] == 'command_started']
    require(len(starts) == 1, 'expected exactly one broker admission')
    identity = starts[0]['identity']
    require(identity['operation_id'] == 'scb_session/command', 'broker routed wrong plugin operation')
    actions = [a for a in evidence['actions'] if a['tool_call_id'] == identity['call_id']]
    require(len(actions) == 1, 'broker identity lacks unique public registered agent action')
    action = actions[0]
    correlation = {'broker_identity': identity, 'agent_action_ref': action['agent_action_ref'],
        'registered_return': action['registered_return'], 'request_status': action['request_status']}
    write_json(directory / 'receipt-correlation.json', correlation)
    if mode == 'complete':
        require(rc == 0 and not broker.poisoned, 'successful native case exited incorrectly or broker poisoned')
        terminal = control.result(handle.task_id)
        write_json(directory / 'owner-result.json', terminal)
        require(terminal['run_outcome'] == 'complete', 'owner did not reach actual complete terminal')
        require(terminal['terminal_evidence_ref'] and terminal['terminal_result_ref'], 'actual terminal refs missing')
        require(len(requests) == 3, 'expected three synthetic local adapter submissions')
        require(action['registered_return']['outcome'] == 'returned', 'managed tool has no returned action')
        require(action['registered_return']['terminal_receipt_ref'], 'public managed terminal receipt unavailable')
        require(marker in requests[-1].read_text(), 'actual subprocess output never reached fake adapter')
        require((runtime.working_dir / 'artifact.txt').read_text() == marker + '\n', 'real child artifact missing')
    else:
        require(status.get('registry', {}).get('execution_status') == 'stopped_by_owner', 'owner stop not registered')
        try:
            control.result(handle.task_id)
        except RuntimeError as exc:
            write_json(directory / 'no-terminal-result.json', {'error': str(exc)})
        else:
            raise AssertionError('stopped writer unexpectedly has terminal product')
        require(not (runtime.working_dir / 'artifact.txt').exists(), 'stopped command published successful artifact')
        terminal = None
    if backend == 'docker':
        require(quiescence['container_removed'] and quiescence['detached_writer_state_stable'], 'Docker writer was not quiescent before snapshot')
    snapshot_proof = runtime.finish_snapshot() if backend == 'docker' and mode == 'complete' else None
    result = {'case': mode, 'backend': backend, 'snapshot': snapshot_proof, 'status': 'PASS', 'task_id': handle.task_id,
        'owner_return_code': rc, 'fake_model_submissions': len(requests),
        'actual_model_call_counts': evidence['actual_model_call_counts'],
        'real_provider_calls': 0, 'real_provider_call_basis': 'explicit local_process adapter only',
        'subprocess_executions': runtime.calls, 'owner_af_unix_roundtrip': snapshot is not None,
        'managed_receipt': correlation, 'quiescence': quiescence,
        'terminal_evidence_ref': None if terminal is None else terminal['terminal_evidence_ref']}
    write_json(directory / 'case-summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('subprocess','docker'), default='subprocess')
    parser.add_argument('--image-id', default='sha256:a4b6653bf808e94a24dc17dcb67585ec9ee1c8928d57d04fcc893dc36ec0d5a6')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--site', type=Path, required=True)
    parser.add_argument('--frozen-manifest', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.is_relative_to(ROOT / 'work') and output.name.startswith('na'), 'output must be a fresh task/work/na* path')
    require(not output.exists(), 'never overwrite a prior acceptance condition')
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for name in ('tmp', 'cache', 'config'):
        (output / name).mkdir(mode=0o700)
    tmp_root = ROOT / 'work/tmp' if args.backend == 'docker' else output / 'tmp'
    tmp_root.mkdir(mode=0o700, exist_ok=True)
    os.environ.update(TMPDIR=str(tmp_root), XDG_CACHE_HOME=str(output / 'cache'),
                      XDG_CONFIG_HOME=str(output / 'config'), PYTHONDONTWRITEBYTECODE='1')
    import tempfile
    tempfile.tempdir = str(tmp_root)
    summary = {'condition': output.name, 'status': 'RUNNING', 'mode': 'synthetic_native_docker_session' if args.backend == 'docker' else 'synthetic_native_subprocess',
        'official_Docker_Session': 'pending' if args.backend == 'docker' else 'not_run', 'official_evaluator': 'not_run', 'cases': []}
    exit_code = 0
    try:
        write_json(output / 'installed-identity.json', identity(args.site, args.frozen_manifest))
        for mode in ('complete', 'stop'):
            summary['cases'].append(case_run(output, mode, backend=args.backend, image_id=args.image_id))
        summary['status'] = 'PASS'
        if args.backend == 'docker':
            summary['official_Docker_Session'] = 'synthetic_fixture_pass'
            summary['image_id'] = args.image_id
    except BaseException as exc:
        summary.update(status='FAIL', error_type=type(exc).__name__, error=str(exc),
                       traceback=traceback.format_exc())
        exit_code = 1
    session_cleanup_errors = []
    for fixture in DOCKER_FIXTURES:
        try:
            fixture.close_session()
        except BaseException as exc:
            session_cleanup_errors.append(str(exc))
    if session_cleanup_errors:
        summary.update(status='FAIL', session_cleanup_errors=session_cleanup_errors)
        exit_code = 1
    write_json(output / 'native-summary.json', summary)
    print(json.dumps({'condition': output.name, 'status': summary['status'],
                      'cases': [r['case'] for r in summary['cases']], 'error': summary.get('error')}), flush=True)
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
