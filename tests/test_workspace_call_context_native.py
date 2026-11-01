"""Real workspace children; deterministic input ports never contact a provider."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex

from cpn.components.tool_executors import (
    WorkspaceCallContext, execute_bounded_workspace_tool,
)
from cpn.rpnh.agent_tasks import (
    AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task,
)
from cpn.rpnh.registry._registry import _RegistryCore
from test_optional_context_compaction import (
    _completion_response, _configure_offline_task, _objects, _response,
)
from test_workspace_call_context import _runtime


CONTEXT_KEYS = ('RPNH_INVOCATION_ID', 'RPNH_FIRING_ID', 'RPNH_TOOL_CALL_ID')
FORBIDDEN_KEYS = ('PROVIDER_API_KEY', 'RPNH_REGISTRY_PATH')


def _script(outside: Path | None = None) -> str:
    code = (
        'import json,os\n'
        f'keys={CONTEXT_KEYS!r}\n'
        'result={key:os.environ.get(key) for key in keys}\n'
        f'result["forbidden"]=[key for key in {FORBIDDEN_KEYS!r} if key in os.environ]\n'
    )
    if outside is not None:
        code += (
            'try:\n'
            f' with open({str(outside)!r}, "w") as stream: stream.write("unexpected")\n'
            'except PermissionError:\n'
            ' result["outside_write_denied"]=True\n'
            'else:\n'
            ' result["outside_write_denied"]=False\n'
        )
    return 'python -c ' + shlex.quote(code + 'print(json.dumps(result))\n')


def _host_sentinels(monkeypatch):
    for key in CONTEXT_KEYS:
        monkeypatch.setenv(key, 'stale-host-id')
    for key in FORBIDDEN_KEYS:
        monkeypatch.setenv(key, 'synthetic-value-not-for-child')


class _WorkspacePort:
    def __init__(self, outside):
        self.outside = outside
        self.requests = 0

    def request_once(self, attempt):
        self.requests += 1
        if self.requests == 1:
            return _response(tool_calls=[{
                'id': 'native-workspace-call', 'name': 'workspace',
                'arguments': json.dumps({
                    'script': _script(self.outside), 'timeout_seconds': 10,
                }),
            }], finish_reason='tool_calls')
        assert self.requests == 2
        return _completion_response('native workspace complete')

    def close(self):
        pass


def test_registered_workspace_child_receives_exact_ids_and_stays_confined(
        tmp_path, monkeypatch):
    _host_sentinels(monkeypatch)
    outside = tmp_path / 'outside-workspace.txt'
    port = _WorkspacePort(outside)
    selection = _configure_offline_task(tmp_path, monkeypatch, port)
    run = tmp_path / 'run'
    result = run_agent_task(AgentTaskSpec(
        run_dir=run, prompt='Exercise native workspace correlation.',
        stages=(AgentStage('main', 'Return the deterministic result.'),),
        execution_config_path=selection, max_attempts_per_stage=2,
    ))
    assert result['stop_reason'] == 'terminal'
    assert result['output'] == 'native workspace complete'
    assert port.requests == 2
    assert not outside.exists()
    core = _RegistryCore(
        run, create=False, read_only=True, catalog=agent_task_catalog())
    action, = [item for item in _objects(core, 'agent_action/v2')
               if item['tool_name'] == 'workspace']
    metadata = action['result_metadata']
    assert metadata['command_started'] is True
    assert metadata['status'] == 'completed'
    assert metadata['exit_code'] == 0
    child = json.loads(metadata['stdout'])
    loop = core.get_version(action['agent_loop_ref']['version_id']).metadata
    invocation = core.get_version(loop['invocation_ref']['version_id']).metadata
    assert child['RPNH_INVOCATION_ID'] == invocation['invocation_ref']['version_id']
    assert child['RPNH_FIRING_ID'] == invocation['own_transition_firing_ref']['version_id']
    assert child['RPNH_TOOL_CALL_ID'] == action['tool_call_id'] == 'native-workspace-call'
    assert child['forbidden'] == []
    assert child['outside_write_denied'] is True


def test_parallel_native_children_keep_contexts_separate_and_preflight_empty(
        tmp_path, monkeypatch):
    _host_sentinels(monkeypatch)
    contexts = [WorkspaceCallContext(f'inv-{i}', f'firing-{i}', f'call-{i}')
                for i in range(2)] + [None]
    roots = [tmp_path / str(i) for i in range(len(contexts))]
    for root in roots:
        root.mkdir()
    environment, profile = _runtime()

    def run(pair):
        root, context = pair
        result = execute_bounded_workspace_tool(
            script=_script(), cwd=str(root), timeout_seconds=10,
            max_output_bytes=4096, environment=environment, profile=profile,
            call_context=context)
        assert result['command_started'] is True
        assert result['status'] == 'completed'
        assert result['exit_code'] == 0
        return json.loads(result['stdout'])

    with ThreadPoolExecutor(max_workers=3) as pool:
        children = list(pool.map(run, zip(roots, contexts)))
    for context, child in zip(contexts, children):
        expected = (None, None, None) if context is None else (
            context.invocation_id, context.firing_id, context.tool_call_id)
        assert tuple(child[key] for key in CONTEXT_KEYS) == expected
        assert child['forbidden'] == []
