from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..io import load, write_new
from ..plugin import bindings, configuration
from ..upstream import split_prompt


_SESSION = re.compile(r'^RPNH session: ([A-Za-z0-9_-]{1,100})$', re.MULTILINE)


def _group_alive(process: subprocess.Popen) -> bool:
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_group(process: subprocess.Popen) -> bool:
    """Stop the dedicated launcher process group; return whether SIGKILL was needed."""
    if not _group_alive(process):
        return False
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + 15
    while _group_alive(process) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _group_alive(process):
        if process.poll() is None:
            process.wait(timeout=1)
        return False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while _group_alive(process) and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    return True


def _history_evidence(history):
    if not isinstance(history, dict):
        return False, False, None
    committed = history.get('committed_history')
    admitted = (
        isinstance(committed, list) and bool(committed)
        or isinstance(history.get('active_turn_ref'), dict)
    )
    terminal = (
        isinstance(committed, list) and bool(committed)
        and history.get('active') is None
        and history.get('latest_turn_ref') == history.get('latest_committed_turn_ref')
    )
    answer = committed[-1].get('answer') if terminal and isinstance(committed[-1], dict) else None
    return admitted, terminal, answer


class DshDriver:
    host = 'dsh'

    def run(self, *, run_dir, profile, broker, messages, schemas, control_root,
            dsh_checkout=None, stop_path=None, **unused):
        del control_root
        if not dsh_checkout:
            raise ValueError('dsh executor requires the pinned DSH checkout')
        run_dir.mkdir(parents=True, exist_ok=False)
        stop = Path(stop_path) if stop_path is not None else run_dir / 'stop.request'
        if stop.exists():
            result = {
                'schema_version': 'rpnh/automationbench_dsh_host_result/v1',
                'session_id': None, 'reported_session_identities': [],
                'status': 'stop_requested_before_launch', 'admitted': False,
                'outcome': {'status': 'nonterminal', 'active': None},
                'stop_requested': True, 'forced_termination': False,
                'process_exit_confirmed': True, 'process_quiescent': True,
                'process_group_alive': False, 'host_quiescent': True,
                'return_code': None, 'history_return_code': None,
                'registry_path': None, 'history': None,
                'observation_error': 'stop was requested before host launch',
                'start_error': None,
            }
            write_new(run_dir / 'host-result.json', result)
            return lifecycle_from_result(
                result, run_dir=run_dir, session=None, return_code=None)
        plugin = (run_dir / 'plugins.json').resolve()
        write_new(plugin, configuration(broker.endpoint, broker.run_id))
        system, prompt = split_prompt(messages)
        task = (system + '\n\n' if system else '') + prompt
        task += ('\n\nComplete the business task using the visible tools. '
                 'Finish with a factual assistant report. Tool raw_result is '
                 'the exact upstream response. Do not request interactive user input.')
        executor_binding = bindings(schemas)['executor']
        selected = executor_binding['tools']
        expected_tools = {'api_search', 'api_fetch', 'base64_encode'}
        if set(selected) != expected_tools:
            raise ValueError('AutomationBench DSH requires exactly its three managed tools')
        managed_bindings = (run_dir / 'managed-bindings.json').resolve()
        write_new(managed_bindings, {
            'schema_version': 'rpnh/dsh_managed_bindings/v1',
            'tools': selected,
            'admitted_effects': executor_binding['admitted_effects'],
        })
        registry_root = (run_dir / 'registry').resolve()
        command = [
            sys.executable, '-m', 'cpn.dsh.launcher', str(Path(dsh_checkout).resolve()),
            '--execution', str(Path(profile).resolve()), '--root', str(registry_root),
            '--task', task, '--attempt-budget', 'unmetered',
            '--plugin-config', str(plugin),
            '--managed-bindings', str(managed_bindings),
        ]

        log_path = run_dir / 'host.log'
        stop_requested = stop.exists()
        forced = False
        with log_path.open('wb') as log:
            process = subprocess.Popen(
                command, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            try:
                while process.poll() is None:
                    if stop.exists():
                        stop_requested = True
                        forced = _stop_group(process)
                        break
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
            except BaseException:
                stop_requested = True
                forced = _stop_group(process) or forced
                raise
            finally:
                if process.poll() is None:
                    forced = _stop_group(process) or forced
                log.flush()

        log_text = log_path.read_text(encoding='utf-8', errors='replace')
        reported = _SESSION.findall(log_text)
        session = reported[0] if len(reported) == 1 else None
        history = None
        history_error = None
        history_return_code = None
        if session is not None:
            history_command = [
                sys.executable, '-m', 'cpn.dsh.launcher',
                str(Path(dsh_checkout).resolve()), '--history', '--root',
                str(registry_root), '--session-id', session,
            ]
            observed = subprocess.run(
                history_command, text=True, capture_output=True, check=False)
            history_return_code = observed.returncode
            with log_path.open('ab') as log:
                if observed.stderr:
                    log.write(observed.stderr.encode('utf-8', errors='replace'))
            if observed.returncode == 0:
                try:
                    history = json.loads(observed.stdout)
                except (TypeError, json.JSONDecodeError) as exc:
                    history_error = f'invalid Registry history: {exc}'
            else:
                history_error = (
                    f'history command exited {observed.returncode}: '
                    f'{observed.stderr.strip()}')
        else:
            history_error = 'DSH application did not report exactly one session identity'

        group_alive = _group_alive(process)
        if group_alive:
            forced = _stop_group(process) or forced
            group_alive = _group_alive(process)
        process_quiescent = process.poll() is not None and not group_alive
        admitted, registry_terminal, answer = _history_evidence(history)
        clean_exit = process.returncode == 0 and not forced and not stop_requested
        history_observed = history_error is None and isinstance(history, dict)
        host_quiescent = (
            process.poll() is not None and process_quiescent and history_observed
            and (stop_requested or (
                clean_exit and history.get('active') is None)))
        terminal = (
            registry_terminal and host_quiescent and clean_exit)
        outcome = (
            {'status': 'terminal', 'answer': answer}
            if terminal else {
                'status': 'nonterminal',
                'active': history.get('active') if isinstance(history, dict) else None,
            })
        result = {
            'schema_version': 'rpnh/automationbench_dsh_host_result/v1',
            'session_id': session,
            'reported_session_identities': reported,
            'status': ('terminal' if terminal else
                       'stop_requested_nonterminal' if stop_requested else
                       'nonterminal'),
            'admitted': admitted,
            'outcome': outcome,
            'stop_requested': stop_requested,
            'forced_termination': forced,
            'process_exit_confirmed': process.poll() is not None,
            'process_quiescent': process_quiescent,
            'process_group_alive': group_alive,
            'host_quiescent': host_quiescent,
            'return_code': process.returncode,
            'history_return_code': history_return_code,
            'registry_path': str(registry_root / session) if session is not None else None,
            'history': history,
            'observation_error': history_error,
            'start_error': None if session is not None else history_error,
        }
        write_new(run_dir / 'host-result.json', result)
        return lifecycle_from_result(
            result, run_dir=run_dir, session=session,
            return_code=process.returncode)

    def project(self, run_dir, output):
        del output
        result = load(run_dir / 'host-result.json')
        return {'source': 'dsh_registered_host',
                'registry_path': result.get('registry_path'),
                'raw_host_result': str(run_dir / 'host-result.json'),
                'status': 'raw_retained',
                'observation_error': result.get('observation_error'),
                'proves_model_consumption_of_each_tool_result': False}


def lifecycle_from_result(result, *, run_dir, session, return_code):
    """Project only durable process and Registry evidence from the DSH run."""
    outcome = result.get('outcome')
    terminal_evidence = isinstance(outcome, dict) and outcome.get('status') == 'terminal'
    admitted = result.get('admitted') is True and not result.get('start_error')
    quiet = (
        result.get('host_quiescent') is True
        and result.get('process_quiescent') is True
        and result.get('process_exit_confirmed') is True)
    terminal = terminal_evidence and admitted and quiet and return_code == 0
    stopped = result.get('stop_requested') is True and not terminal
    return {'executor_host': 'dsh', 'task_id': session,
        'process_exit_confirmed': result.get('process_exit_confirmed') is True,
        'host_quiescent': quiet,
        'manual_stop': stopped, 'admitted': admitted,
        'terminal': result if terminal else None,
        'execution_status': ('host_terminal' if terminal else
                             'manual_stop_uncertain' if stopped else
                             'host_nonterminal'),
        'status': result.get('status'), 'registry_path': result.get('registry_path'),
        'raw_host_result': str(run_dir / 'host-result.json'),
        'observation_error': result.get('observation_error'),
        'return_code': return_code}
