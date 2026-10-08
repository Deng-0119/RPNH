"""Trusted official Harbor lifecycle. Never expose this handle to the solver.

Runtime requires Harbor 0.24. Verifier specialization changes only reward parsing;
the build adapter authorizes host networking for Compose 5/Bake. Every world
uses a fresh private root, narrow log mounts and an explicit network-none
adaptation. No verifier material is uploaded before proven solver quiescence.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import time

from .source import validate_task


class WorldError(RuntimeError):
    pass


def _digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _verifier_type():
    from harbor.verifier.verifier import Verifier, VerifierOutputParseError

    class OriginalRewardVerifier(Verifier):
        def _parse_reward_json(self):
            # Original JSON includes nested diagnostic objects. Preserve every
            # raw byte and project only the emitted numeric top-level score.
            try:
                raw = json.loads(self.trial_paths.reward_json_path.read_bytes())
                value = raw['overall_score']
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError('nonfinite or nonnumeric overall_score')
            except (ValueError, TypeError, KeyError) as exc:
                raise VerifierOutputParseError('invalid original overall_score') from exc
            return {'overall_score': value}

    return OriginalRewardVerifier


def _docker_environment_type():
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.environments.base import ExecResult

    class HostNetworkDockerEnvironment(DockerEnvironment):
        """Compose 5/Bake requires CLI approval in addition to YAML entitlement.

        Preserve Harbor startup/mounts/exec/teardown; only run its exact emitted
        build definition with the explicitly authorized host-network entitlement.
        """
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            # Harbor Verifier.verify() intentionally has no cwd argument. Its
            # root phase must never inherit the solver-controlled /workspace.
            if (user if user is not None else self.default_user) in ('root', 0) and cwd is None:
                cwd = '/root'
            result = await super().exec(command, cwd=cwd, env=env, timeout_sec=timeout_sec, user=user)
            if (user if user is not None else self.default_user) in ('root', 0):
                self.last_exec_return_code = result.return_code
                if command == getattr(self, 'expected_verifier_command', None):
                    self.last_verifier_exec_return_code = result.return_code
            return result

        async def _run_docker_compose_command(self, command, **kwargs):
            if command != ['build']:
                return await super()._run_docker_compose_command(command, **kwargs)
            definition = await super()._run_docker_compose_command(['build', '--print'])
            root = self.trial_paths.trial_dir.parent
            (root / 'build-definition-raw.txt').write_text(definition.stdout)
            definition_json, _ = json.JSONDecoder().raw_decode(definition.stdout.lstrip())
            payload = json.dumps(definition_json).encode()
            (root / 'build-definition.json').write_bytes(payload)
            argv = ['docker', 'buildx', 'bake', '--allow=network.host', '-f', '-']
            (root / 'build-invocation.json').write_text(json.dumps({'argv': argv, 'stdin': 'build-definition.json'}))
            with (root / 'build.log').open('wb') as output:
                process = await asyncio.create_subprocess_exec(*argv,
                    stdin=asyncio.subprocess.PIPE, stdout=output,
                    stderr=asyncio.subprocess.STDOUT,
                    env=self._compose_env_vars(include_os_env=True))
                try:
                    await process.communicate(payload)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
            if process.returncode:
                raise WorldError(f'official Dockerfile build failed ({process.returncode}); see private build.log')
            return ExecResult(return_code=0, stdout='', stderr='')

    return HostNetworkDockerEnvironment


_READY = "test -f /tmp/saas_setup_complete && curl -fsS --max-time 5 http://127.0.0.1:8069/web/health >/dev/null"
_IDENTITY = r'''
import hashlib, json, os, pathlib, time
p = pathlib.Path('/tmp/saas_setup_complete')
r = pathlib.Path('/tmp/repair_seeded_records.json')
print(json.dumps({'sentinel_mtime_ns': p.stat().st_mtime_ns,
 'sentinel_sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
 'timezone': list(time.tzname), 'tz_env': os.environ.get('TZ'),
 'localtime_sha256': hashlib.sha256(pathlib.Path('/etc/localtime').read_bytes()).hexdigest(),
 'repair_seed_sha256': hashlib.sha256(r.read_bytes()).hexdigest() if r.exists() else None}))
'''
_QUIESCE = r'''
import os, pathlib, pwd, signal, time
try: uid = pwd.getpwnam('agent').pw_uid
except KeyError: raise RuntimeError('solver account has not been prepared')
if uid == 0: raise RuntimeError('invalid solver UID')
for attempt in range(100):
    live = []
    for p in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            status = (p / 'status').read_text()
            uids = next(s for s in status.splitlines() if s.startswith('Uid:')).split()[1:]
            state = next(s for s in status.splitlines() if s.startswith('State:')).split()[1]
            if str(uid) in uids and state != 'Z': live.append(int(p.name))
        except (FileNotFoundError, ProcessLookupError): pass
    if not live: break
    for pid in live:
        try: os.kill(pid, signal.SIGKILL)
        except ProcessLookupError: pass
    time.sleep(.05)
else: raise RuntimeError('solver processes remain')
print('{"solver_uid_processes": 0}')
'''
_PAUSE_ODOO = r'''
import json, os, pathlib, signal
pids = []
for proc in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        args = (proc / 'cmdline').read_bytes().split(b'\0')
        if any(pathlib.PurePosixPath(a.decode()).name == 'odoo' for a in args[:2]):
            pids.append(int(proc.name))
    except (ProcessLookupError, FileNotFoundError): pass
if not pids: raise RuntimeError('no live Odoo process to suspend')
pathlib.Path('/root/rpnh-odoo-pids.json').write_text(json.dumps(pids))
for pid in pids: os.kill(pid, signal.SIGSTOP)
'''
_RESUME_ODOO = r'''
import json, os, pathlib, signal
for pid in json.loads(pathlib.Path('/root/rpnh-odoo-pids.json').read_text()):
    os.kill(pid, signal.SIGCONT)
'''
_SNAPSHOT = r'''set -eu
umask 077
mkdir /root/rpnh-terminal
runuser -u postgres -- pg_dump -Fc bench > /root/rpnh-terminal/bench.dump
python3 -I - <<'INNER'
import pathlib, tarfile
paths = ['/workspace', '/var/lib/odoo', '/root/.local/share/Odoo', '/mnt/extra-addons',
         '/etc/odoo', '/etc/localtime', '/etc/timezone',
         '/tmp/saas_setup_complete', '/tmp/repair_seeded_records.json']
with tarfile.open('/root/rpnh-terminal/files.tar', 'w') as archive:
    for name in paths:
        if pathlib.Path(name).exists(): archive.add(name, arcname=name.lstrip('/'))
INNER
'''


class OfficialWorld:
    """One fresh world; start -> sandbox -> freeze -> grade -> stop.

    Caller must close bridge admission and settle the owner before freeze/grade.
    ``freeze`` independently kills and confirms *all* solver UID processes.
    Returned snapshots contain only hashes/clock identity. Private raw evidence
    lives below ``private_root`` and must never be mounted into the solver.
    """

    def __init__(self, task_dir, private_root, condition_id, *, build_compatibility=False, firewall_packages=None):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', condition_id):
            raise ValueError('condition_id must be a unique lowercase Compose project name')
        if importlib.metadata.version('harbor') != '0.24.0':
            raise WorldError('requires Harbor 0.24.0')
        from harbor.models.task.task import Task
        from harbor.models.trial.paths import TrialPaths
        DockerEnvironment = _docker_environment_type()

        task_dir = Path(task_dir).resolve(strict=True)
        self.firewall_packages = tuple(Path(p).resolve(strict=True) for p in (firewall_packages or ()))
        if any(not p.is_file() or p.suffix != '.deb' for p in self.firewall_packages):
            raise ValueError('firewall packages must be explicit existing .deb files')
        self.source = validate_task(task_dir.parent.parent, task_dir.name)
        self.task = Task(task_dir)
        self.private_root = Path(private_root).absolute()
        if self.private_root.resolve() != self.private_root:
            raise ValueError('private root must not traverse symlinks')
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        source_bytes = Path(__file__).read_bytes()
        (self.private_root / 'environment.launch.py').write_bytes(source_bytes)
        self.condition_id = condition_id
        self.paths = TrialPaths(trial_dir=self.private_root / 'harbor')
        self.paths.mkdir()
        self.overlay = self.private_root / 'isolation.yaml'
        # No explicit networks in Harbor's default build compose; network_mode
        # suppresses Compose's implicit default network (validated by config).
        self.overlay.write_text('services:\n  main:\n    network_mode: none\n    cap_add: [NET_ADMIN]\n    pull_policy: never\n    build:\n      network: host\n      entitlements: [network.host]\n')
        if type(build_compatibility) is not bool:
            raise ValueError('build_compatibility must be boolean')
        self.build_compatibility = None
        if build_compatibility:
            original_path = self.task.paths.environment_dir / 'Dockerfile'
            original = original_path.read_bytes()
            before = b'uv pip install --system --no-cache'
            if original.count(before) != 1:
                raise WorldError('official Dockerfile does not contain the one expected uv install command')
            adapted = original.replace(before, before + b' --break-system-packages', 1)
            adapted_path = self.private_root / 'Dockerfile.build-compatibility'
            adapted_path.write_bytes(adapted)
            difference = ''.join(difflib.unified_diff(original.decode().splitlines(True),
                adapted.decode().splitlines(True), fromfile='official/Dockerfile',
                tofile='private/Dockerfile.build-compatibility'))
            (self.private_root / 'build-compatibility.diff').write_text(difference)
            self.overlay.write_text(self.overlay.read_text() + '      dockerfile: ' + json.dumps(str(adapted_path)) + '\n')
            self.build_compatibility = {'condition': 'declared_uv_break_system_packages',
                'original_dockerfile_sha256': hashlib.sha256(original).hexdigest(),
                'adapted_dockerfile_sha256': hashlib.sha256(adapted).hexdigest(),
                'exact_diff': difference, 'odoo_client_lib_requested': '2.0.0'}
        self.mount_sources = [str(self.paths.agent_dir), str(self.paths.verifier_dir)]
        self.environment = DockerEnvironment(
            environment_dir=self.task.paths.environment_dir,
            environment_name=self.task.short_name, session_id=condition_id,
            trial_paths=self.paths, task_env_config=self.task.config.environment,
            extra_docker_compose=[self.overlay],
            mounts=[{'source': source, 'target': target, 'type': 'bind'}
                    for source, target in zip(self.mount_sources, ['/logs/agent', '/logs/verifier'])])
        self.phase = 'new'
        self._claimed = False
        self.container_id = None
        self.owned_anonymous_volumes = {}
        self.initial_identity = None
        self.snapshot = None
        self.sandbox = None
        self.metadata = {'condition_id': condition_id,
                         'condition': 'adapted_solver_egress_isolation',
                         'harbor_version': '0.24.0',
                         'adapter_sha256': hashlib.sha256(source_bytes).hexdigest(),
                         'build_compatibility': self.build_compatibility,
                         'build_adapter': 'compose_build_print_to_bake_allow_network_host; up_pull_never',
                         'official_limits': self.source.official_limits,
                         'source': self.source.public_identity(), 'stages': {}}
        self._save()

    def _save(self):
        (self.private_root / 'world.json').write_text(json.dumps(self.metadata, indent=2))

    def _stage(self, name, status, **values):
        previous = self.metadata['stages'].get(name, {})
        now = time.time()
        self.metadata['stages'][name] = dict(status=status, at=now,
            started_at=previous.get('started_at', now), **values)
        self._save()

    async def _docker(self, *args, timeout=30):
        proc = await asyncio.create_subprocess_exec('docker', *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise WorldError(f'docker {args[0]} failed: {err.decode(errors="replace")}')
        return out

    async def _owned_ids(self, *, running=False):
        args = ['ps', '-q'] if running else ['ps', '-aq']
        raw = await self._docker(*args, '--filter', f'label=com.docker.compose.project={self.condition_id}')
        return raw.decode().split()

    async def inspect(self):
        ids = await self._owned_ids()
        if len(ids) != 1:
            raise WorldError('expected exactly one owned container')
        document = json.loads(await self._docker('inspect', ids[0]))[0]
        if self.container_id and self.container_id != document['Id']:
            raise WorldError('world container identity changed')
        return document

    async def _exec(self, command, timeout=30):
        result = await self.environment.exec(command=command, user='root', cwd='/root', timeout_sec=timeout)
        if result.return_code:
            raise WorldError(f'privileged world operation failed ({result.return_code}): {result.stderr or ""}')
        return result.stdout or ''

    async def _identity(self):
        return json.loads(await self._exec('python3 -I -c ' + shlex.quote(_IDENTITY)))

    async def _record_dependencies(self, phase):
        script = """import importlib.metadata as m, json, shutil, subprocess, sys
result = {'python': sys.version.split()[0]}
for name in ['odoo-client-lib']:
    try: result[name] = m.version(name)
    except m.PackageNotFoundError: result[name] = None
for name, command in {'uv': ['uv', '--version'],
                       'system_packages': ['dpkg-query', '-W', 'odoo', 'postgresql-18'],
                       'iptables': ['iptables', '--version'],
                       'ip6tables': ['ip6tables', '--version']}.items():
    if shutil.which(command[0]):
        call = subprocess.run(command, capture_output=True, text=True, timeout=10)
        result[name] = {'exit_code': call.returncode, 'version': call.stdout.strip()}
    else: result[name] = None
print(json.dumps(result))
"""
        versions = json.loads(await self._exec('python3 -I -c ' + shlex.quote(script)))
        self.metadata.setdefault('dependency_versions', {})[phase] = versions
        self._save()
        return versions

    def _cancel_compose_clients(self):
        # Harbor 0.24 cancellation does not reap its Compose subprocess. Match
        # only this process's direct child and this exact owned project.
        for path in Path('/proc').glob('[0-9]*'):
            try:
                status = (path / 'status').read_text()
                parent = int(next(x.split()[1] for x in status.splitlines() if x.startswith('PPid:')))
                argv = (path / 'cmdline').read_bytes().split(b'\0')
                if parent == os.getpid() and b'--project-name' in argv:
                    i = argv.index(b'--project-name')
                    if argv[i + 1].decode() == self.condition_id:
                        os.kill(int(path.name), signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                pass

    def _record_image_sources(self):
        path = self.private_root / 'build.log'
        if path.exists():
            self.metadata['resolved_base_images'] = dict(re.findall(
                r'\bFROM ([\w./:-]+)@(sha256:[0-9a-f]{64})', path.read_text(errors='replace')))

    async def start(self):
        if self.phase != 'new':
            raise WorldError('world cannot start twice')
        self.phase = 'starting'
        try:
            if await self._owned_ids():
                raise WorldError('condition already owns containers; use a new condition')
            self._claimed = True
            volumes_before = set((await self._docker('volume', 'ls', '-q')).decode().split())
            info = json.loads(await self._docker('info', '--format', '{{json .}}'))
            # Measure actual cache before any image download. Image size is not
            # the task storage request; record both and free workspace storage.
            self.metadata['preflight'] = {
                'disk_free_bytes': shutil.disk_usage(self.private_root).free,
                'images': (await self._docker('image', 'ls', '--digests', '--no-trunc')).decode(),
                'cache': (await self._docker('system', 'df')).decode(),
                'cpu': info['NCPU'], 'memory_bytes': info['MemTotal'],
                'storage_driver': info['Driver'], 'architecture': info['Architecture']}
            self._save()
            config = await self.environment._run_docker_compose_command(['config', '--format', 'json'])
            (self.private_root / 'compose-config.json').write_text(config.stdout)
            main = json.loads(config.stdout)['services']['main']
            if main.get('network_mode') != 'none' or main.get('networks') or main['build'].get('network') != 'host':
                raise WorldError('Compose did not enforce requested isolation')
            self._stage('build_start', 'running', timeout_seconds=600)
            await asyncio.wait_for(self.environment.start(force_build=False), timeout=600)
            self._record_image_sources()
            self._stage('build_start', 'pass')
            document = await self.inspect()
            self.container_id = document['Id']
            host = document['HostConfig']
            self.metadata['container_limits'] = {name: host.get(name) for name in
                ('NanoCpus', 'Memory', 'MemorySwap', 'StorageOpt', 'NetworkMode', 'CapAdd', 'CapDrop')}
            self.metadata['container_limits']['declared_storage_mb'] = self.task.config.environment.storage_mb
            self.metadata['container_limits']['storage_quota_enforced'] = None
            self.metadata['container_limits']['storage_scope'] = 'declaration only; no verified writable-layer quota'
            (self.private_root / 'container-inspect.json').write_text(json.dumps(document, indent=2))
            self.metadata['image'] = json.loads(await self._docker('image', 'inspect', document['Image']))
            declared_volumes = set(self.metadata['image'][0]['Config'].get('Volumes') or {})
            volume_proof = []
            for mount in document['Mounts']:
                if mount['Type'] != 'volume':
                    continue
                name, target = mount['Name'], mount['Destination']
                if (target not in {'/var/lib/odoo', '/mnt/extra-addons'} or target not in declared_volumes
                        or name in volumes_before or not re.fullmatch('[0-9a-f]{64}', name)):
                    raise WorldError('unexpected or reused image volume')
                detail = json.loads(await self._docker('volume', 'inspect', name))[0]
                if detail['Driver'] != 'local' or detail.get('Options') or detail['Mountpoint'] != mount['Source']:
                    raise WorldError('anonymous volume is not plain Docker local storage')
                self.owned_anonymous_volumes[target] = name
                volume_proof.append({'name': name, 'destination': target, 'new': True, 'driver': detail['Driver']})
            self.metadata['owned_anonymous_volumes'] = volume_proof
            self._stage('readiness', 'running', timeout_seconds=600)
            deadline = time.monotonic() + 600
            while True:
                result = await self.environment.exec(command=_READY, user='root', cwd='/root', timeout_sec=10)
                if result.return_code == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('sentinel + local Odoo readiness timed out (600s)')
                await asyncio.sleep(2)
            await self._record_dependencies('solver')
            self.initial_identity = await self._identity()
            if self.source.task_id.startswith('2299_') and self.initial_identity['repair_seed_sha256'] is None:
                raise WorldError('repair task is missing seeded-record identity')
            self.metadata['initial_identity'] = self.initial_identity
            versions = await self._exec("dpkg-query -W odoo postgresql-18; command -v iptables || true; command -v ip6tables || true; python3 -I -c 'import importlib.metadata; print(importlib.metadata.version(\"odoo-client-lib\"))'")
            (self.private_root / 'container-dependencies.txt').write_text(versions)
            self._stage('readiness', 'pass')
            self.phase = 'ready'
            return self
        except BaseException as exc:
            self.phase = 'failed'
            self._record_image_sources()
            stage = 'readiness' if self.metadata['stages'].get('build_start', {}).get('status') == 'pass' else 'build_start'
            self._stage(stage, 'timeout' if isinstance(exc, TimeoutError) else 'failed', error=str(exc))
            (self.private_root / 'start-error.txt').write_text(str(exc))
            self._cancel_compose_clients()
            try:
                await self.stop()
            except Exception as cleanup_error:
                self._stage('teardown', 'failed', error=str(cleanup_error))
            raise

    async def prepare_solver(self):
        if self.phase != 'ready':
            raise WorldError('world not ready for solver preparation')
        from .sandbox import prepare_sandbox, install_firewall_packages
        self._stage('isolation', 'running')
        try:
            found = await self.environment.exec(
                command='command -v iptables && command -v ip6tables', user='root', cwd='/root', timeout_sec=10)
            if found.return_code:
                self._stage('firewall', 'missing', image_has_firewall=False)
                if not self.firewall_packages:
                    raise WorldError('actual image lacks iptables/ip6tables; supply explicit image-matched firewall_packages')
                package_root = Path(os.path.commonpath([str(p.parent) for p in self.firewall_packages]))
                installed = await install_firewall_packages(self.environment, self.firewall_packages,
                                                            package_root=package_root)
                self._stage('firewall', 'installed_adaptation', installation=installed)
            versions = await self._exec('iptables --version && ip6tables --version')
            self.metadata['firewall_versions'] = versions.splitlines()
            self._save()
            self.sandbox = await prepare_sandbox(self.environment, await self.inspect(),
                                                allowed_mount_sources=self.mount_sources,
                                                allowed_anonymous_volumes=self.owned_anonymous_volumes)
            await self._record_dependencies('solver')
            self._stage('isolation', 'pass', container_id=self.sandbox.container_id,
                        probe=self.sandbox.probe, condition=self.sandbox.condition)
            return self.sandbox
        except BaseException as exc:
            self._stage('isolation', 'failed', error=str(exc))
            raise

    @staticmethod
    def _require_quiescent(owner_quiescent, bridge_quiescent):
        if owner_quiescent is not True or bridge_quiescent is not True:
            raise WorldError('owner and closed bridge must both be quiescent')

    async def freeze(self, *, owner_quiescent, bridge_quiescent):
        self._require_quiescent(owner_quiescent, bridge_quiescent)
        if self.phase != 'ready' or self.sandbox is None:
            raise WorldError('freeze requires a ready, prepared world')
        self.phase = 'freezing'  # No solver resume, even if snapshot fails.
        self.sandbox.ready = False
        try:
            await self.inspect()
            await self._exec('python3 -I -c ' + shlex.quote(_QUIESCE), timeout=10)
            identity = await self._identity()
            if identity != self.initial_identity:
                raise WorldError('sentinel/seed/timezone identity changed')
            # Suspend the application's cron and HTTP writers while taking the
            # DB + filestore pair; PostgreSQL stays online for pg_dump.
            await self._exec('python3 -I -c ' + shlex.quote(_PAUSE_ODOO))
            try:
                await self._exec(_SNAPSHOT, timeout=120)
            finally:
                await self._exec('python3 -I -c ' + shlex.quote(_RESUME_ODOO))
            private = self.private_root / 'snapshot'
            private.mkdir(mode=0o700)
            for name in ('bench.dump', 'files.tar'):
                await self.environment.download_file('/root/rpnh-terminal/' + name, private / name)
                (private / name).chmod(0o600)
            if await self._identity() != identity:
                raise WorldError('clock/seed identity changed during snapshot')
            self.snapshot = dict(condition_id=self.condition_id, container_id=self.container_id,
                identity=identity, files={name: {'sha256': _digest(private / name),
                'bytes': (private / name).stat().st_size} for name in ('bench.dump', 'files.tar')},
                scope='quiescent_solver_original_live_world; no restore performed')
            (private / 'manifest.json').write_text(json.dumps(self.snapshot, indent=2))
            self.phase = 'frozen'
            self._stage('freeze', 'pass', snapshot=self.snapshot)
            return self.snapshot
        except BaseException as exc:
            self.phase = 'failed'
            self._stage('freeze', 'failed', error=str(exc))
            raise

    async def grade(self, *, owner_quiescent, bridge_quiescent):
        self._require_quiescent(owner_quiescent, bridge_quiescent)
        if self.phase != 'frozen' or not self.snapshot:
            raise WorldError('grade requires a proven private terminal snapshot')
        await self.inspect()
        if await self._identity() != self.snapshot['identity']:
            raise WorldError('frozen identity changed before verifier')
        for name, record in self.snapshot['files'].items():
            if _digest(self.private_root / 'snapshot' / name) != record['sha256']:
                raise WorldError('private snapshot changed')
        await self._exec('python3 -I -c ' + shlex.quote(_QUIESCE), timeout=10)
        self.phase = 'verifying'  # Permanently closes SUT phase.
        self.environment.default_user = 'root'
        from harbor.utils.scripts import build_execution_command
        self.environment.expected_verifier_command = build_execution_command(
            '/tests/test.sh', stdout_path='/logs/verifier/test-stdout.txt', task_os=self.environment.os)
        self.environment.last_verifier_exec_return_code = None
        self.metadata['verifier_exit_code'] = None
        self._stage('verifier', 'running', timeout_seconds=300)
        verification = asyncio.create_task(_verifier_type()(task=self.task,
            trial_paths=self.paths, environment=self.environment).verify())
        try:
            done, _ = await asyncio.wait({verification}, timeout=300)
            if not done:
                raise TimeoutError('official verifier exceeded 300 seconds')
            result = verification.result()
            exit_code = self.environment.last_verifier_exec_return_code
            self.metadata['verifier_exit_code'] = exit_code
            self._save()
            if type(exit_code) is not int:
                raise WorldError('official verifier execution return code was not observed')
            await self._record_dependencies('verifier')
            from .scoring import parse_original_score
            original = parse_original_score(self.paths.verifier_dir, self.source.task_id, verifier_exit_code=exit_code)
            if original is None:
                raise WorldError('verifier produced no original reports')
            self.phase = 'graded'
            projection = original.public_projection()
            projection['verifier_exit_code'] = exit_code
            self._stage('verifier', 'completed', rewards=result.rewards,
                        original=projection)
            return projection
        except BaseException as exc:
            # Killing the container stops contained verifier descendants; merely
            # cancelling the host Compose process does not. Partial raw stays.
            self.metadata['verifier_exit_code'] = self.environment.last_verifier_exec_return_code
            self._stage('verifier', 'incomplete', error=str(exc))
            try:
                ids = await self._owned_ids(running=True)
                if ids:
                    await self._docker('kill', *ids)
                if await self._owned_ids(running=True):
                    raise WorldError('verifier container still running after kill')
            finally:
                verification.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await verification
                self._cancel_compose_clients()
                self.phase = 'failed'
            raise

    async def stop(self):
        if self.phase == 'stopped' or not self._claimed:
            return
        if self.sandbox is not None:
            self.sandbox.ready = False
        try:
            # delete=False preserves shared build cache. Remove only this
            # project's anonymous volumes with explicit container identities.
            ids = await self._owned_ids()
            if ids:
                with contextlib.suppress(Exception):
                    await self.environment.prepare_logs_for_host()
                with contextlib.suppress(Exception):
                    logs = await self._docker('logs', ids[0])
                    (self.private_root / 'container.log').write_bytes(logs)
                await self._docker('rm', '-f', '-v', *ids)
            await self.environment.stop(delete=False)
            if await self._owned_ids(running=True):
                raise WorldError('owned containers still running after teardown')
            remaining_volumes = set((await self._docker('volume', 'ls', '-q')).decode().split())
            if remaining_volumes.intersection(self.owned_anonymous_volumes.values()):
                raise WorldError('owned anonymous volumes survived teardown')
            self.phase = 'stopped'
            self._stage('teardown', 'pass', owned_running_containers=0)
        except BaseException as exc:
            self._stage('teardown', 'failed', error=str(exc))
            raise

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.stop()
