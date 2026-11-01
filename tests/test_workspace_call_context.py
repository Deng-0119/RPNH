"""Correlation transport checks; mocked workers do not qualify OS confinement."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cpn.components import tool_executors as workers
from cpn.components.agent_loop.optional_execution import OptionalAgentLoopRegistryService
from cpn.components.agent_loop.tool_validation import ValidatedAgentToolAction
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef


def _runtime():
    ref = VersionRef('execution_environment_identity/v1',
                     new_id('execution_environment'), new_id('execution_environment_version'))
    profile_ref = VersionRef('numerical_tool_profile/v1',
                            new_id('numerical_tool_profile'), new_id('numerical_tool_profile_version'))
    return (workers.ExecutionEnvironmentIdentity(ref, 'research-exp', sys.executable, sys.prefix),
            workers.NumericalToolProfile(profile_ref, ref, 30, 512 * 1024 * 1024,
                                         16, 1024 * 1024, 1024 * 1024))


def _fake_worker(monkeypatch, *, fail_namespace=False):
    calls = []

    class Process:
        pid = 99999999

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            namespace_failure = fail_namespace and command[0] == '/usr/bin/unshare'
            self.returncode = 1 if namespace_failure else 0
            streams = (b'', b'unshare: write failed /proc/self/uid_map: Operation not permitted\n') if namespace_failure else (
                json.dumps(kwargs['env']).encode(), b'')
            self.stdout, self.stderr = [self._stream(value) for value in streams]
            if not namespace_failure:
                os.write(kwargs['pass_fds'][0], b'R')

        @staticmethod
        def _stream(payload):
            read, write = os.pipe()
            os.write(write, payload)
            os.close(write)
            return os.fdopen(read, 'rb')

        def poll(self):
            return self.returncode

        def wait(self):
            return self.returncode

    monkeypatch.setattr(workers.subprocess, 'Popen', Process)
    return calls


def _run(root, context):
    environment, profile = _runtime()
    result = workers.execute_bounded_workspace_tool(
        script='opaque script', cwd=str(root), timeout_seconds=5,
        max_output_bytes=4096, environment=environment, profile=profile,
        call_context=context)
    assert result['command_started'] is True
    return json.loads(result['stdout'])


@pytest.mark.parametrize('fallback', [False, True])
def test_workspace_context_overrides_stale_host_without_expanding_environment(
        tmp_path, monkeypatch, fallback):
    calls = _fake_worker(monkeypatch, fail_namespace=fallback)
    for name in ('RPNH_INVOCATION_ID', 'RPNH_FIRING_ID', 'RPNH_TOOL_CALL_ID'):
        monkeypatch.setenv(name, 'stale-host-value')
    monkeypatch.setenv('RPNH_REGISTRY_PATH', '/not-authorized')
    monkeypatch.setenv('PROVIDER_API_KEY', 'not-forwarded')
    context = workers.WorkspaceCallContext('invocation', 'firing', 'call')
    env = _run(tmp_path, context)
    assert env['RPNH_INVOCATION_ID'] == 'invocation'
    assert env['RPNH_FIRING_ID'] == 'firing'
    assert env['RPNH_TOOL_CALL_ID'] == 'call'
    assert set(env) == {'HOME', 'TMPDIR', 'PATH', 'CONDA_PREFIX', 'CONDA_DEFAULT_ENV',
                        'LANG', 'LC_ALL', 'RPNH_INVOCATION_ID', 'RPNH_FIRING_ID', 'RPNH_TOOL_CALL_ID'}
    assert len(calls) == (2 if fallback else 1)
    for command, kwargs in calls:
        assert workers._LANDLOCK_WORKSPACE_BOOTSTRAP in command
        assert kwargs['shell'] is False
        assert kwargs['start_new_session'] is True
        assert kwargs['cwd'] == str(tmp_path)
        assert len(kwargs['pass_fds']) == 1
    assert os.environ['RPNH_TOOL_CALL_ID'] == 'stale-host-value'


def test_preflight_without_context_does_not_inherit_ids(tmp_path, monkeypatch):
    _fake_worker(monkeypatch)
    monkeypatch.setenv('RPNH_TOOL_CALL_ID', 'stale')
    assert not any(key.startswith('RPNH_') for key in _run(tmp_path, None))


def test_parallel_workspace_contexts_do_not_cross(tmp_path, monkeypatch):
    _fake_worker(monkeypatch)
    roots = [tmp_path / str(i) for i in range(8)]
    for root in roots:
        root.mkdir()
    contexts = [workers.WorkspaceCallContext(f'inv-{i}', f'fire-{i}', f'call-{i}') for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_run, roots, contexts))
    for context, env in zip(contexts, results):
        assert (env['RPNH_INVOCATION_ID'], env['RPNH_FIRING_ID'], env['RPNH_TOOL_CALL_ID']) == (
            context.invocation_id, context.firing_id, context.tool_call_id)


def test_owner_prepares_existing_execution_and_tool_call_references(tmp_path):
    service = object.__new__(OptionalAgentLoopRegistryService)
    environment, profile = _runtime()
    context = SimpleNamespace(invocation_ref=SimpleNamespace(version_id='existing-invocation'))
    execution = SimpleNamespace(operation=SimpleNamespace(
        canonical=SimpleNamespace(context=context),
        firing=SimpleNamespace(transition_firing_ref=SimpleNamespace(version_id='existing-firing'))))
    loop, turn = object(), object()
    validated = []
    service._execution = lambda given, given_loop: validated.append((given, given_loop)) or execution
    service._current = lambda given: None
    service.hydrate_current_agent_turn_v1 = lambda given: turn
    service._project_workspace = lambda given, given_loop: None
    service._workspace_root = lambda given: tmp_path
    service._workspace_runtime = lambda: (environment, profile)
    arguments = {'script': 'echo ok', 'timeout_seconds': 2,
                 'invocation_id': 'untrusted-argument', 'tool_call_id': 'untrusted-argument'}
    action = SimpleNamespace(
        validation=ValidatedAgentToolAction('action', 'loop', 1, 'existing-call',
                                           'workspace', '{}', arguments, 0),
        tool_call=SimpleNamespace(tool_call_id='existing-call'))
    request = service.prepare_workspace_action_execution_v1(execution, loop, turn, action)
    assert validated == [(execution, loop)]
    assert request['call_context'] == workers.WorkspaceCallContext(
        'existing-invocation', 'existing-firing', 'existing-call')
    assert request['environment'] is environment
    assert request['profile'] is profile
    with pytest.raises(Exception, match='current stored turn'):
        service.prepare_workspace_action_execution_v1(execution, loop, object(), action)


@pytest.mark.parametrize('bad', ['', None, 'contains\0nul'])
def test_invalid_context_is_rejected(bad):
    with pytest.raises(ValueError):
        workers.WorkspaceCallContext('invocation', 'firing', bad)
