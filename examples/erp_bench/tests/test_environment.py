"""Synthetic lifecycle tests: no Docker, ERP writes or model calls."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rpnh_erp_bench.environment import OfficialWorld, WorldError, _digest, _verifier_type


def run(awaitable):
    return asyncio.run(awaitable)


def fake_world(tmp_path):
    world = OfficialWorld.__new__(OfficialWorld)
    world.private_root = tmp_path
    world.phase = 'ready'
    world._claimed = True
    world.condition_id = 'fixture-world'
    world.container_id = 'container-original'
    world.owned_anonymous_volumes = {}
    world.metadata = {'stages': {}}
    world.initial_identity = {'sentinel_mtime_ns': 123456789,
                              'timezone': ['UTC', 'UTC'], 'repair_seed_sha256': None}
    world.snapshot = None
    world.sandbox = SimpleNamespace(ready=True)
    world.source = SimpleNamespace(task_id='2000_easy_01_buy_only_baseline')
    world.task = object()
    world.paths = SimpleNamespace(verifier_dir=tmp_path / 'verifier')
    world.paths.verifier_dir.mkdir()
    world.environment = SimpleNamespace(default_user=None, os='linux',
        exec=AsyncMock(return_value=SimpleNamespace(return_code=0, stdout='', stderr='')),
        download_file=AsyncMock(), prepare_logs_for_host=AsyncMock(), handoff_verifier_logs=AsyncMock(), stop=AsyncMock())
    async def download(source, target):
        target.write_bytes(b'private terminal bytes')
    world.environment.download_file.side_effect = download
    world.inspect = AsyncMock(return_value={'Id': world.container_id})
    world._identity = AsyncMock(return_value=world.initial_identity.copy())
    world._docker = AsyncMock(return_value=b'')
    world._owned_ids = AsyncMock(return_value=[])
    world._cancel_compose_clients = lambda: None
    world._record_dependencies = AsyncMock(return_value={'odoo-client-lib': '2.0.0'})
    return world


@pytest.mark.parametrize('owner,bridge', [(False, True), (True, False), (1, True), (True, None)])
def test_freeze_requires_both_exact_quiescent_flags(tmp_path, owner, bridge):
    w = fake_world(tmp_path)
    with pytest.raises(WorldError, match='quiescent'):
        run(w.freeze(owner_quiescent=owner, bridge_quiescent=bridge))
    w.environment.exec.assert_not_called()


def test_grade_rejects_unfrozen_world_without_upload(tmp_path):
    w = fake_world(tmp_path)
    with pytest.raises(WorldError, match='snapshot'):
        run(w.grade(owner_quiescent=True, bridge_quiescent=True))
    w.environment.exec.assert_not_called()


def test_freeze_kills_solver_before_backup_and_returns_hashes(tmp_path):
    w = fake_world(tmp_path)
    result = run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    commands = [call.kwargs['command'] for call in w.environment.exec.call_args_list]
    assert 'SIGKILL' in commands[0]
    assert 'SIGSTOP' in commands[1]
    assert 'pg_dump' in commands[2]
    assert 'SIGCONT' in commands[3]
    assert w.phase == 'frozen' and not w.sandbox.ready
    assert result['identity']['sentinel_mtime_ns'] == 123456789
    assert result['files']['files.tar']['sha256'] == _digest(tmp_path / 'snapshot/files.tar')
    assert 'private terminal bytes' not in json.dumps(result)


def test_freeze_identity_failure_is_terminal(tmp_path):
    w = fake_world(tmp_path)
    w._identity.return_value = {'sentinel_mtime_ns': 999}
    with pytest.raises(WorldError, match='identity changed'):
        run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    assert w.phase == 'failed' and not w.sandbox.ready
    w.environment.download_file.assert_not_called()


def test_failed_quiescence_never_creates_snapshot(tmp_path):
    w = fake_world(tmp_path)
    w.environment.exec.return_value.return_code = 1
    with pytest.raises(WorldError):
        run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    assert w.snapshot is None and w.phase == 'failed'
    w.environment.download_file.assert_not_called()


def test_stale_private_snapshot_blocks_verifier(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    (tmp_path / 'snapshot/bench.dump').write_bytes(b'changed')
    with pytest.raises(WorldError, match='snapshot changed'):
        run(w.grade(owner_quiescent=True, bridge_quiescent=True))
    assert 'verifier' not in w.metadata['stages']


def test_verifier_timeout_kills_contained_writer_and_retains_partial(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    (w.paths.verifier_dir / 'partial').write_bytes(b'original')
    w._owned_ids.side_effect = [['owned-container'], []]
    async def hang():
        await asyncio.Event().wait()
    verifier = SimpleNamespace(verify=hang)
    async def deadline(tasks, *, timeout):
        assert timeout == 300
        await asyncio.sleep(0)
        return set(), tasks
    with patch('rpnh_erp_bench.environment._verifier_type', return_value=lambda **kwargs: verifier), \
         patch('rpnh_erp_bench.environment.asyncio.wait', deadline):
        with pytest.raises(TimeoutError):
            run(w.grade(owner_quiescent=True, bridge_quiescent=True))
    w._docker.assert_awaited_once_with('kill', 'owned-container')
    assert w.environment.default_user == 'root'
    assert w.phase == 'failed'
    assert w.metadata['stages']['verifier']['status'] == 'incomplete'
    assert (w.paths.verifier_dir / 'partial').read_bytes() == b'original'


def test_stop_removes_only_owned_containers(tmp_path):
    w = fake_world(tmp_path)
    w._owned_ids.side_effect = [['owned-container'], []]
    run(w.stop())
    assert ('rm', '-f', '-v', 'owned-container') in [c.args for c in w._docker.call_args_list]
    assert w.phase == 'stopped'


def test_existing_project_is_never_cleaned_without_claim(tmp_path):
    w = fake_world(tmp_path)
    w._claimed = False
    run(w.stop())
    w._docker.assert_not_called()
    w.environment.stop.assert_not_called()


@pytest.mark.parametrize('value', [False, '1', float('nan'), float('inf'), None])
def test_original_reward_rejects_invalid_numeric(tmp_path, value):
    pytest.importorskip('harbor')
    cls = _verifier_type()
    verifier = cls.__new__(cls)
    path = tmp_path / 'reward.json'
    path.write_text(json.dumps({'overall_score': value, 'constraint': {'earned': 1}}))
    verifier.trial_paths = SimpleNamespace(reward_json_path=path)
    with pytest.raises(Exception):
        verifier._parse_reward_json()


def test_original_reward_parser_preserves_nested_raw_bytes(tmp_path):
    pytest.importorskip('harbor')
    from harbor.verifier.verifier import Verifier
    cls = _verifier_type()
    assert cls.verify is Verifier.verify
    verifier = cls.__new__(cls)
    path = tmp_path / 'reward.json'
    original = b'{ "overall_score": 12.5, "constraint": {"earned": 1}, "passed": false }\n'
    path.write_bytes(original)
    verifier.trial_paths = SimpleNamespace(reward_json_path=path)
    assert verifier._parse_reward_json() == {'overall_score': 12.5}
    assert path.read_bytes() == original


def test_completed_verifier_delegates_safe_score_projection(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    async def verify():
        w.environment.last_verifier_exec_return_code = 0
        return SimpleNamespace(rewards={'overall_score': 0})
    verifier = SimpleNamespace(verify=verify)
    original = SimpleNamespace(public_projection=lambda: {'overall_score': 0, 'passed': False})
    with patch('rpnh_erp_bench.environment._verifier_type', return_value=lambda **kw: verifier), \
         patch('rpnh_erp_bench.scoring.parse_original_score', return_value=original) as parser:
        assert run(w.grade(owner_quiescent=True, bridge_quiescent=True)) == dict(original.public_projection(), verifier_exit_code=0)
    parser.assert_called_once_with(w.paths.verifier_dir, w.source.task_id, verifier_exit_code=0)
    assert w.phase == 'graded' and not w.sandbox.ready
    with pytest.raises(WorldError):
        run(w.freeze(owner_quiescent=True, bridge_quiescent=True))


def test_snapshot_failure_resumes_odoo_but_never_solver(tmp_path):
    w = fake_world(tmp_path)
    async def execute(*, command, user, cwd, timeout_sec):
        return SimpleNamespace(return_code=1 if 'pg_dump' in command else 0, stdout='', stderr='')
    w.environment.exec.side_effect = execute
    with pytest.raises(WorldError):
        run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    assert 'SIGCONT' in w.environment.exec.call_args.kwargs['command']
    assert not w.sandbox.ready and w.phase == 'failed'


def test_bake_uses_exact_compose_json_and_explicit_host_entitlement(tmp_path):
    pytest.importorskip('harbor')
    from harbor.environments.docker.docker import DockerEnvironment
    from rpnh_erp_bench.environment import _docker_environment_type
    adapter = _docker_environment_type().__new__(_docker_environment_type())
    adapter.trial_paths = SimpleNamespace(trial_dir=tmp_path / 'harbor')
    adapter._compose_env_vars = lambda **kw: {}
    definition = {'target': {'main': {'context': '/official/task', 'network': 'host'}}}
    emitted = json.dumps(definition) + '\nwarning: No services to build\n'
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(), wait=AsyncMock())
    with patch.object(DockerEnvironment, '_run_docker_compose_command',
                      AsyncMock(return_value=SimpleNamespace(stdout=emitted))) as compose, \
         patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as subprocess:
        result = run(adapter._run_docker_compose_command(['build']))
    compose.assert_awaited_once_with(['build', '--print'])
    assert subprocess.call_args.args == ('docker', 'buildx', 'bake', '--allow=network.host', '-f', '-')
    assert json.loads(process.communicate.call_args.args[0]) == definition
    assert result.return_code == 0
    assert (tmp_path / 'build-definition-raw.txt').read_text() == emitted


def test_bake_cancellation_kills_host_build_process(tmp_path):
    pytest.importorskip('harbor')
    from unittest.mock import Mock
    from harbor.environments.docker.docker import DockerEnvironment
    from rpnh_erp_bench.environment import _docker_environment_type
    adapter_type = _docker_environment_type()
    adapter = adapter_type.__new__(adapter_type)
    adapter.trial_paths = SimpleNamespace(trial_dir=tmp_path / 'harbor')
    adapter._compose_env_vars = lambda **kw: {}
    process = SimpleNamespace(pid=987654, returncode=None, communicate=AsyncMock(side_effect=asyncio.CancelledError),
                              kill=Mock(), wait=AsyncMock())
    with patch.object(DockerEnvironment, '_run_docker_compose_command',
                      AsyncMock(return_value=SimpleNamespace(stdout='{}'))), \
         patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)), \
         patch('os.killpg') as kill_group:
        with pytest.raises(asyncio.CancelledError):
            run(adapter._run_docker_compose_command(['build']))
    kill_group.assert_called_once_with(process.pid, 9)
    process.wait.assert_awaited_once()


def test_missing_firewall_without_explicit_packages_fails_closed(tmp_path):
    w = fake_world(tmp_path)
    w.firewall_packages = ()
    w.environment.exec.return_value.return_code = 1
    with patch('rpnh_erp_bench.sandbox.install_firewall_packages', AsyncMock()) as install:
        with pytest.raises(WorldError, match='actual image lacks'):
            run(w.prepare_solver())
    install.assert_not_called()
    assert w.metadata['stages']['firewall']['status'] == 'missing'
    assert w.metadata['stages']['isolation']['status'] == 'failed'


@pytest.mark.parametrize('missing', [True, False])
def test_firewall_install_is_explicit_and_conditional(tmp_path, missing):
    w = fake_world(tmp_path)
    package = tmp_path / 'firewall.deb'
    package.write_bytes(b'fixture package only')
    w.firewall_packages = (package,)
    w.mount_sources = []
    w.environment.exec.side_effect = [
        SimpleNamespace(return_code=int(missing), stdout='', stderr=''),
        SimpleNamespace(return_code=0, stdout='iptables v1.test\nip6tables v1.test', stderr='')]
    sandbox = SimpleNamespace(container_id=w.container_id, condition='fixture', probe={'uid': 1001})
    with patch('rpnh_erp_bench.sandbox.install_firewall_packages', AsyncMock(return_value={'packages': [package.name]})) as install, \
         patch('rpnh_erp_bench.sandbox.prepare_sandbox', AsyncMock(return_value=sandbox)):
        assert run(w.prepare_solver()) is sandbox
    if missing:
        install.assert_awaited_once_with(w.environment, (package,), package_root=tmp_path)
    else:
        install.assert_not_called()
    assert w.metadata['firewall_versions'] == ['iptables v1.test', 'ip6tables v1.test']


def test_build_compatibility_changes_only_private_uv_flag(tmp_path):
    pytest.importorskip('harbor')
    from unittest.mock import Mock
    environment_dir = tmp_path / 'upstream/tasks/task/environment'
    environment_dir.mkdir(parents=True)
    original = b'FROM odoo:19\nRUN uv pip install --system --no-cache \\\n    "odoo-client-lib==2.0.0"\n'
    (environment_dir / 'Dockerfile').write_bytes(original)
    task = SimpleNamespace(paths=SimpleNamespace(environment_dir=environment_dir),
        short_name='fixture', config=SimpleNamespace(environment=object()))
    source = SimpleNamespace(official_limits={}, public_identity=lambda: {})
    with patch('harbor.models.task.task.Task', return_value=task), \
         patch('rpnh_erp_bench.environment.validate_task', return_value=source), \
         patch('rpnh_erp_bench.environment._docker_environment_type', return_value=Mock()):
        w = OfficialWorld(environment_dir.parent, tmp_path / 'private', 'fixture', build_compatibility=True)
    assert (environment_dir / 'Dockerfile').read_bytes() == original
    assert (w.private_root / 'Dockerfile.build-compatibility').read_bytes() == original.replace(
        b'--no-cache', b'--no-cache --break-system-packages')
    assert w.metadata['build_compatibility']['odoo_client_lib_requested'] == '2.0.0'
    assert w.metadata['build_compatibility']['original_dockerfile_sha256'] == _digest(environment_dir / 'Dockerfile')
    assert 'dockerfile:' in w.overlay.read_text()


def test_privileged_helpers_use_isolated_python_and_root_cwd(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    for call in w.environment.exec.call_args_list:
        assert call.kwargs['cwd'] == '/root'
        assert call.kwargs['user'] == 'root'
        assert 'python3 -I' in call.kwargs['command']


def test_official_verifier_inherits_trusted_root_cwd():
    pytest.importorskip('harbor')
    from harbor.environments.docker.docker import DockerEnvironment
    from rpnh_erp_bench.environment import _docker_environment_type
    cls = _docker_environment_type()
    adapter = cls.__new__(cls)
    adapter.default_user = 'root'
    with patch.object(DockerEnvironment, 'exec', AsyncMock()) as execute:
        run(adapter.exec('official verifier'))
    assert execute.call_args.kwargs['cwd'] == '/root'
    with patch.object(DockerEnvironment, 'exec', AsyncMock()) as execute:
        run(adapter.exec('actor script', cwd='/workspace', user='agent'))
    assert execute.call_args.kwargs['cwd'] == '/workspace'


def test_exact_verifier_command_records_real_exit_without_cleanup_overwrite():
    pytest.importorskip('harbor')
    from harbor.environments.docker.docker import DockerEnvironment
    from rpnh_erp_bench.environment import _docker_environment_type
    cls = _docker_environment_type()
    adapter = cls.__new__(cls)
    adapter.default_user = 'root'
    adapter.handoff_verifier_logs = AsyncMock()
    adapter.expected_verifier_command = '(/tests/test.sh) > /logs/verifier/test-stdout.txt 2>&1'
    with patch.object(DockerEnvironment, 'exec', AsyncMock(return_value=SimpleNamespace(return_code=7))):
        run(adapter.exec(adapter.expected_verifier_command))
    with patch.object(DockerEnvironment, 'exec', AsyncMock(return_value=SimpleNamespace(return_code=0))):
        run(adapter.exec('dependency probe'))
    adapter.handoff_verifier_logs.assert_awaited_once()
    assert adapter.last_verifier_exec_return_code == 7
    assert adapter.last_exec_return_code == 0


def test_nonzero_verifier_exit_never_returns_score(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    async def verify():
        w.environment.last_verifier_exec_return_code = 7
        return SimpleNamespace(rewards={'overall_score': 100})
    with patch('rpnh_erp_bench.environment._verifier_type', return_value=lambda **kw: SimpleNamespace(verify=verify)), \
         patch('rpnh_erp_bench.scoring.parse_original_score', side_effect=ValueError('verifier failed')) as parser:
        with pytest.raises(ValueError, match='verifier failed'):
            run(w.grade(owner_quiescent=True, bridge_quiescent=True))
    parser.assert_called_once_with(w.paths.verifier_dir, w.source.task_id, verifier_exit_code=7)
    assert w.metadata['verifier_exit_code'] == 7
    assert w.metadata['stages']['verifier']['status'] == 'incomplete'


def test_verifier_first_error_is_not_masked_by_cleanup(tmp_path):
    w = fake_world(tmp_path)
    run(w.freeze(owner_quiescent=True, bridge_quiescent=True))
    verifier = SimpleNamespace(verify=AsyncMock(side_effect=PermissionError('original reward read failed')))
    w._owned_ids.return_value = ['owned-container']
    w._docker.side_effect = WorldError('secondary cleanup failure')
    with patch('rpnh_erp_bench.environment._verifier_type', return_value=lambda **kw: verifier):
        with pytest.raises(PermissionError, match='original reward read failed'):
            run(w.grade(owner_quiescent=True, bridge_quiescent=True))
    assert w.metadata['stages']['verifier']['error'] == 'original reward read failed'
    assert w.metadata['stages']['verifier_cleanup']['error'] == 'secondary cleanup failure'


def test_log_handoff_uses_captured_host_identity():
    pytest.importorskip('harbor')
    from rpnh_erp_bench.environment import _docker_environment_type
    cls = _docker_environment_type()
    adapter = cls.__new__(cls)
    adapter.log_host_uid, adapter.log_host_gid = 1234, 1235
    adapter.exec = AsyncMock(return_value=SimpleNamespace(return_code=0))
    run(adapter.handoff_verifier_logs())
    adapter.exec.assert_awaited_once_with('chown -R 1234:1235 /logs/verifier',
                                       user='root', cwd='/root', timeout_sec=30)
