"""Trusted real subprocess fixture, not a Docker Session or untrusted sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)


class FixtureSession:
    kind = 'local_subprocess_fixture'

    def __init__(self, working_dir, mode, marker):
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(mode=0o700)
        self.mode, self.marker = mode, marker
        self.runtime = None

    def spawn(self):
        if self.runtime is not None:
            raise RuntimeError('fixture Session is single-use')
        self.runtime = SubprocessFixtureRuntime(self.working_dir, self.mode, self.marker)
        return self.runtime


class SubprocessFixtureRuntime:
    def __init__(self, working_dir, mode, marker):
        self.working_dir, self.mode, self.marker = working_dir, mode, marker
        self.command = shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                                  '--child', mode, str(working_dir), marker])
        self._lock = threading.Lock()
        self._log_lock = threading.Lock()
        self.closed = False
        self.process = None
        self.calls = 0
        self.cleanup_calls = 0
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.pid = None
        self.exit_code = None

    @property
    def broker_runtime(self):
        return self

    def capture_live(self, child, directory):
        if child['pid'] != self.pid or self.process.poll() is not None:
            raise RuntimeError('actual fixture child is not live')
        return {'kind': 'local_subprocess', 'pid': self.pid}

    def record(self, event, **fields):
        with self._log_lock:
            with (self.working_dir / 'subprocess-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'event': event, 'at': time.time(), **fields}, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())

    def stream(self, *, command, env, timeout):
        if command != self.command or env != {} or timeout != 60:
            raise ValueError('fixture permits only its exact harmless command and broker deadline')
        with self._lock:
            if self.closed or self.calls:
                raise RuntimeError('fixture closed or duplicate execution')
            self.calls += 1
            child_env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.working_dir),
                         'TMPDIR': str(self.working_dir), 'PYTHONDONTWRITEBYTECODE': '1',
                         'LANG': 'C.UTF-8'}
            self.process = subprocess.Popen(shlex.split(command), cwd=self.working_dir,
                env=child_env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True)
            proc = self.process
            self.pid = proc.pid
            self.record('spawned', pid=proc.pid, pgid=os.getpgid(proc.pid), timeout_seconds=timeout)
            self.entered.set()
        started = time.monotonic()
        timed_out = False
        try:
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self.kill()
                out, err = proc.communicate(timeout=5)
            self.exit_code = proc.returncode
            self.record('exited', pid=proc.pid, exit_code=proc.returncode, timed_out=timed_out)
            if out:
                yield SimpleNamespace(kind='stdout', text=out)
            if err:
                yield SimpleNamespace(kind='stderr', text=err)
            yield SimpleNamespace(kind='finished', result=SimpleNamespace(
                stdout=out, stderr=err, exit_code=proc.returncode,
                timed_out=timed_out, elapsed=time.monotonic() - started))
        finally:
            self.exited.set()

    def kill(self):
        with self._lock:
            self.closed = True
            proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
        self.record('kill_completed', pid=None if proc is None else proc.pid,
                    exit_code=None if proc is None else proc.poll())

    def cleanup(self):
        self.cleanup_calls += 1
        self.kill()
        self.record('cleanup_completed', ordinal=self.cleanup_calls)

    def quiescence(self):
        with self._lock:
            proc = self.process
            closed = self.closed
        code = None if proc is None else proc.poll()
        group_alive = False
        if proc is not None:
            try:
                os.killpg(proc.pid, 0)
                group_alive = True
            except ProcessLookupError:
                pass
        return {'closed': closed, 'pid': self.pid, 'exit_code': code,
                'process_exited': proc is not None and code is not None,
                'process_group_alive': group_alive, 'stream_exited': self.exited.is_set(),
                'executions': self.calls, 'cleanup_calls': self.cleanup_calls}


def child(mode, working_dir, marker, *, detached=False):
    root = Path(working_dir).resolve(strict=True)
    if mode not in {'complete', 'stop'} or Path.cwd().resolve() != root:
        raise ValueError('invalid fixture child context')
    detached_pid = None
    if detached:
        program = ("from pathlib import Path; import time; p=Path('detached-heartbeat.txt'); n=0\n"
                   "while True:\n n+=1; p.write_text(str(n)); time.sleep(.025)\n")
        writer = subprocess.Popen([sys.executable, '-B', '-c', program], cwd=root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        detached_pid = writer.pid
    write_json(root / 'entered.pending.json', {'pid': os.getpid(), 'pgid': os.getpgrp(),
                                      'mode': mode, 'marker': marker, 'detached_pid': detached_pid})
    (root / 'entered.pending.json').rename(root / 'entered.json')
    count = 0
    until = time.monotonic() + 65
    while not (root / 'release').exists():
        if time.monotonic() >= until:
            raise TimeoutError('fixture coordinator did not release/stop child')
        count += 1
        (root / 'heartbeat.txt').write_text(str(count))
        time.sleep(.025)
    if mode != 'complete':
        raise RuntimeError('stop fixture must never be released as successful')
    (root / 'artifact.txt').write_text(marker + '\n')
    print(marker, flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] not in ('--child', '--docker-child'):
        raise SystemExit('only explicit fixture child mode is supported')
    child(*sys.argv[2:], detached=sys.argv[1] == '--docker-child')


class DockerFixture:
    """Observe a real official Session/runtime; the broker gets runtime itself."""
    def __init__(self, directory, mode, marker, image_id):
        from slop_code.execution import DockerEnvironmentSpec, Session
        self.directory, self.mode, self.marker = directory, mode, marker
        self.container_id = None
        self.pid = None
        self.image_id = image_id
        seed = directory / 'seed'
        seed.mkdir(mode=0o700)
        (seed / 'fixture.py').write_bytes(Path(__file__).read_bytes())
        spec = DockerEnvironmentSpec.model_validate({
            'type': 'docker', 'name': 'rpnh-native-fixture',
            'docker': {'image': image_id, 'network': 'none', 'mount_workspace': True,
                       'workdir': '/workspace', 'extra_mounts': {}},
            'environment': {'include_os_env': False, 'env': {'PYTHONDONTWRITEBYTECODE': '1'}},
            'setup': {'commands': [], 'eval_commands': [], 'resume_commands': []}})
        self.session = Session.from_environment_spec(spec, base_dir=seed, static_assets={}, is_agent_infer=True)
        self.session.__enter__()
        try:
            self.working_dir = self.session.working_dir
            self.runtime = self.session.spawn(disable_setup=True, image=image_id)
        except BaseException:
            self.session.cleanup()
            raise
        self.command = shlex.join(['python', '-B', '/workspace/fixture.py', '--docker-child',
                                   mode, '/workspace', marker])

    @property
    def broker_runtime(self):
        return self.runtime

    @property
    def calls(self):
        path = self.directory / 'b/commands.jsonl'
        return sum(json.loads(line)['event'] == 'command_started'
                   for line in path.read_text().splitlines()) if path.exists() else 0

    def capture_live(self, child, directory):
        container = self.runtime.container
        container.reload()
        document = container.attrs
        self.container_id = container.id
        self.pid = child['pid']
        host, mounts = document['HostConfig'], document['Mounts']
        if (document['Image'] != self.image_id or host['NetworkMode'] != 'none'
                or host['Privileged'] or len(mounts) != 1):
            raise RuntimeError('Docker fixture image/network/mount isolation mismatch')
        mount = mounts[0]
        if (mount['Type'] != 'bind' or mount['Destination'] != '/workspace'
                or Path(mount['Source']).resolve() != self.working_dir.resolve()):
            raise RuntimeError('Docker fixture mounted a non-workspace host path')
        if not child.get('detached_pid'):
            raise RuntimeError('actual detached writer was not started')
        deadline = time.monotonic() + 3
        while not (self.working_dir / 'detached-heartbeat.txt').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('detached writer did not mutate its disposable workspace')
            time.sleep(.025)
        write_json(directory / 'docker-inspect.json', document)
        write_json(directory / 'docker-top.json', container.top())
        return {'kind': 'official_DockerStreamingRuntime', 'container_id': container.id,
                'child_pid_in_container': child['pid'], 'detached_pid_in_container': child['detached_pid'],
                'network': host['NetworkMode'], 'mounts': mounts}

    def cleanup(self):
        self.runtime.cleanup()

    def quiescence(self):
        import docker
        gone = self.container_id is not None
        if self.container_id:
            client = docker.from_env()
            try:
                client.containers.get(self.container_id)
                gone = False
            except docker.errors.NotFound:
                gone = True
            finally:
                client.close()
        heartbeat = self.working_dir / 'detached-heartbeat.txt'
        before = heartbeat.read_bytes() if heartbeat.exists() else None
        time.sleep(.1)
        stable = (heartbeat.read_bytes() if heartbeat.exists() else None) == before
        return {'closed': gone, 'pid': self.pid, 'pid_scope': 'container',
                'exit_code': self.runtime.poll(), 'container_id': self.container_id,
                'container_removed': gone, 'process_exited': gone,
                'process_group_alive': not gone, 'stream_exited': gone,
                'executions': self.calls, 'cleanup_calls': None,
                'detached_writer_state_stable': stable,
                'cleanup_basis': 'official runtime cleanup and exact container NotFound; no manufactured process exit code'}

    def finish_snapshot(self):
        from slop_code.execution import Session
        from rpnh_scb.pilot import selected_snapshot_manifest
        self.session.finish_checkpoint(self.directory / 'snapshot')
        manifest = selected_snapshot_manifest(self.session.workspace.initial_snapshot)
        write_json(self.directory / 'snapshot-manifest.json', manifest)
        expected = (self.marker + '\n').encode()
        selected = self.session.workspace.initial_snapshot.extract_contents()
        if selected.get(Path('artifact.txt')) != expected:
            raise RuntimeError('original Snapshot did not preserve actual child artifact')
        # A second Session restores files through the original Snapshot factory;
        # it creates no container and never runs an evaluator.
        with Session.from_environment_spec(self.session.spec, base_dir=self.directory / 'snapshot',
                                          static_assets={}, is_agent_infer=True) as restored:
            if (restored.working_dir / 'artifact.txt').read_bytes() != expected:
                raise RuntimeError('original Session snapshot restore changed fixture artifact')
        return {'source_persistence': True, 'manifest_sha256': manifest['sha256'],
                'official_snapshot': True, 'official_evaluator': 'not_run'}

    def close_session(self):
        import shutil
        if self.working_dir.exists():
            retained = self.directory / 'workspace-retained'
            if not retained.exists():
                shutil.copytree(self.working_dir, retained)
        self.session.cleanup()
