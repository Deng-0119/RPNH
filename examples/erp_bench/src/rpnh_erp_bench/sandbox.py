"""Trusted container preparation and Harbor execution, outside the tool catalog.

Harbor 0.24.0 BaseEnvironment.exec(command, cwd, env, user, timeout_sec) is
the only execution seam. Actor Python is never evaluated by the host process.
The caller supplies Docker inspect evidence from the very same environment.
Network-none is an explicit adaptation; it does not preserve allow_internet.
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import shlex
import threading
import time
import uuid

from .bridge import MAX_OUTPUT, check_arguments

CONDITION = "adapted_network_none_nonroot_script"
# Transport/process cleanup grace, never extra solver execution time.
RPC_CLEANUP_SECONDS = 15


class IsolationError(RuntimeError):
    pass


async def install_firewall_packages(environment, package_files, *, package_root):
    """Privileged offline package staging, only for an explicitly adapted trial.

    Parent supplies official distro/architecture-matched .debs after inspecting
    the actual image. No downloads, host install, solver operation or upstream
    manifest change occurs here. Dependencies must be included when absent.
    """
    root = Path(package_root).resolve(strict=True)
    packages = [Path(path).resolve(strict=True) for path in package_files]
    if not packages or any(not path.is_relative_to(root) or not path.is_file() or path.suffix != ".deb"
                           for path in packages):
        raise ValueError("firewall packages must be .deb files inside the trusted package root")
    staging = "/root/rpnh-firewall-" + uuid.uuid4().hex
    created = await environment.exec(command="mkdir -m 700 " + shlex.quote(staging),
                                     user="root", cwd="/root", timeout_sec=10)
    if created.return_code != 0:
        raise IsolationError("cannot prepare private container package staging")
    targets = []
    for index, path in enumerate(packages):
        target = f"{staging}/{index}.deb"
        await environment.upload_file(source_path=path, target_path=target)
        targets.append(target)
    installed = await environment.exec(command="dpkg -i " + " ".join(map(shlex.quote, targets)),
                                       user="root", cwd="/root", timeout_sec=120)
    if installed.return_code != 0:
        raise IsolationError("container firewall package installation failed; check image-matched dependencies")
    return {"status": "installed", "condition": CONDITION, "packages": [path.name for path in packages]}


def validate_container_inspect(document, allowed_mount_sources=(), *, allowed_anonymous_volumes=None,
                               network_policy_applied=False):
    """Validate trusted inspect and exact fresh image-volume ownership proof.

    allowed_anonymous_volumes maps declared image destinations to fresh IDs.
    The lifecycle owner must prove these IDs were absent before this world's
    creation, are declared by its image, and use local storage without options.
    Neither this mapping nor mount authorization may originate from the model.
    """
    host = document.get("HostConfig", {})
    network = host.get("NetworkMode", "")
    network_safe = network == "none" or (network_policy_applied and network.startswith("container:"))
    if (not network_safe or host.get("Privileged") is not False
            or host.get("PidMode") not in (None, "", "private")
            or host.get("IpcMode") in ("host", "shareable")
            or host.get("Devices") or host.get("VolumesFrom")):
        raise IsolationError("requires private namespaces, network none, and an unprivileged container")
    if any(str(cap).upper().removeprefix("CAP_") in {"ALL", "SYS_ADMIN", "SYS_PTRACE"}
           for cap in host.get("CapAdd") or []):
        raise IsolationError("container grants unsafe capabilities")
    volumes = {} if allowed_anonymous_volumes is None else allowed_anonymous_volumes
    if (not isinstance(volumes, dict) or any(target not in {"/var/lib/odoo", "/mnt/extra-addons"}
            or not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}", name)
            for target, name in volumes.items()) or len(set(volumes.values())) != len(volumes)):
        raise IsolationError("invalid fresh anonymous image-volume ownership proof")
    seen_volumes = set()
    for mount in document.get("Mounts", []):
        if mount.get("Type") == "volume":
            target = mount.get("Destination")
            if (target not in volumes or mount.get("Name") != volumes[target]
                    or target not in (document.get("Config", {}).get("Volumes") or {})
                    or mount.get("Driver") != "local" or target in seen_volumes):
                raise IsolationError("unexpected, reused or unowned image volume")
            seen_volumes.add(target)
            continue
        if (mount.get("Destination") not in {"/logs/agent", "/logs/verifier"}
                or mount.get("Type") != "bind" or mount.get("Source") not in allowed_mount_sources):
            raise IsolationError("unexpected container mount; host source must be explicitly owned")
    if seen_volumes != set(volumes):
        raise IsolationError("image-volume ownership proof does not match this container")
    if not document.get("Id"):
        raise IsolationError("container identity missing")
    return document["Id"]


# Runs before any SUT script; never imports or mounts grader/solution material.
_PREPARE = r'''set -eu
id agent >/dev/null 2>&1 || useradd --create-home --shell /bin/bash agent
test "$(id -u agent)" -ne 0
test "$(id -G agent)" = "$(id -g agent)"
install -d -o agent -g agent -m 700 /workspace
for p in /root /setup /tests /solution /logs/verifier; do
  if test -e "$p"; then chown root:root "$p"; chmod 700 "$p"; fi
done
for p in /tmp/repair_seeded_records.json /etc/odoo/odoo.conf; do
  if test -e "$p"; then chown root:root "$p"; chmod 600 "$p"; fi
done
test -f /tmp/saas_setup_complete
test -f /etc/odoo/api_key
chown root:agent /etc/odoo/api_key
chmod 640 /etc/odoo/api_key
chown postgres:postgres /var/run/postgresql
chmod 750 /var/run/postgresql
command -v setpriv >/dev/null
command -v pkill >/dev/null
command -v iptables >/dev/null
command -v ip6tables >/dev/null
uid=$(id -u agent)
iptables -I OUTPUT 1 -p tcp --dport 5432 -m owner --uid-owner "$uid" -j REJECT
ip6tables -I OUTPUT 1 -p tcp --dport 5432 -m owner --uid-owner "$uid" -j REJECT
'''

_PROBE = r'''
import json, os, pathlib, socket, urllib.request
assert os.getuid() != 0
status = pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert int(next(x.split()[1] for x in status.splitlines() if x.startswith('CapEff:')), 16) == 0
for name in ['/root', '/setup', '/tests', '/solution', '/logs/verifier',
             '/tmp/repair_seeded_records.json', '/etc/odoo/odoo.conf',
             '/var/run/docker.sock', '/run/docker.sock', '/host', '/host_mnt']:
    path = pathlib.Path(name)
    try:
        if path.is_dir(): list(path.iterdir())
        else: path.open('rb').close()
    except (PermissionError, FileNotFoundError, NotADirectoryError): pass
    else: raise RuntimeError('forbidden path readable')
try:
    local_database = socket.socket(socket.AF_UNIX)
    local_database.connect('/var/run/postgresql/.s.PGSQL.5432')
except (PermissionError, FileNotFoundError): pass
else: raise RuntimeError('agent can reach PostgreSQL Unix socket')
finally: local_database.close()
for address in [('127.0.0.1', 5432), ('::1', 5432)]:
    try: connection = socket.create_connection(address, timeout=1)
    except OSError: pass
    else:
        connection.close()
        raise RuntimeError('agent can reach PostgreSQL directly')
with urllib.request.urlopen('http://127.0.0.1:8069/web/health', timeout=5) as response:
    assert response.status == 200
print(json.dumps({'uid': os.getuid(), 'network': 'none', 'odoo_loopback': True}))
'''


@dataclass
class PreparedSandbox:
    environment: object
    container_id: str
    probe: dict
    condition: str = CONDITION
    ready: bool = True


async def prepare_sandbox(environment, docker_inspect, *, allowed_mount_sources=(), allowed_anonymous_volumes=None):
    """Privileged setup/probe. Failure blocks model launch; manifest stays intact.

    Parent must obtain fresh inspect for this environment and never attach mounts,
    reconnect networks or install verifier files while the solver can run.
    Requires iptables/ip6tables and NET_ADMIN for this trusted preparation only.
    """
    # Harbor 0.24 uses a dedicated egress-control network namespace and deny-all
    # policy, not necessarily Docker's literal network_mode=none. Phase policy
    # support must have been requested when the environment was constructed.
    if docker_inspect.get("HostConfig", {}).get("NetworkMode") != "none":
        from harbor.models.task.config import NetworkMode, NetworkPolicy
        await environment.set_network_policy(NetworkPolicy(network_mode=NetworkMode.NO_NETWORK))
    container_id = validate_container_inspect(docker_inspect, allowed_mount_sources,
                                             allowed_anonymous_volumes=allowed_anonymous_volumes,
                                             network_policy_applied=True)
    prepared = await environment.exec(command=_PREPARE, user="root", cwd="/root", timeout_sec=30)
    if prepared.return_code != 0:
        raise IsolationError("privileged isolation preparation failed; no SUT launch permitted")
    probe = await environment.exec(command="setpriv --no-new-privs python3 -I -c " + shlex.quote(_PROBE),
                                   user="agent", cwd="/workspace", timeout_sec=15)
    if probe.return_code != 0:
        raise IsolationError("actual nonroot isolation probe failed; no SUT launch permitted")
    try:
        evidence = json.loads(probe.stdout)
        if evidence["uid"] <= 0 or evidence["network"] != "none" or evidence["odoo_loopback"] is not True:
            raise ValueError("invalid probe")
    except (ValueError, TypeError, KeyError) as exc:
        raise IsolationError("malformed isolation evidence") from exc
    return PreparedSandbox(environment, container_id, evidence)


# This supervisor runs INSIDE the container as agent. Drain pipes continuously,
# retain bounded output, and wait for the one script process. Cleanup below also
# kills descendants that changed session/process group.
_RUNNER = r'''
import base64, json, os, subprocess, threading
path, timeout, limit = __import__('sys').argv[1:]
streams = [bytearray(), bytearray()]
def drain(pipe, destination):
    while True:
        block = pipe.read(8192)
        if not block: break
        destination.extend(block[:max(0, int(limit) - len(destination))])
process = subprocess.Popen(['python3', '-B', path], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
threads = [threading.Thread(target=drain, args=(pipe, output), daemon=True)
           for pipe, output in zip((process.stdout, process.stderr), streams)]
for thread in threads: thread.start()
status = 'completed'
try: code = process.wait(timeout=float(timeout))
except subprocess.TimeoutExpired:
    status = 'unknown'
    os.killpg(process.pid, 9)
    code = process.wait()
if status == 'completed' and code: status = 'failed'
for thread in threads: thread.join(timeout=0.2)
with open('/etc/odoo/api_key') as secret_file: secret = secret_file.read().strip()
def visible(stream):
    text = stream.decode('utf-8', 'replace')
    return text.replace(secret, '[REDACTED]') if secret else text
print(json.dumps({'status': status, 'stdout': visible(streams[0]),
                  'stderr': visible(streams[1]), 'exit_code': code}))
'''


class HarborBackend:
    """Synchronous bridge backend over a trusted Harbor handle and running loop.

    Construct after prepare_sandbox(), call only from bridge worker threads.
    Unknown/interrupted poisons the handle until parent teardown/reconciliation;
    cancelling an asyncio future alone is never claimed to stop container writes.
    """

    def __init__(self, environment, loop, sandbox):
        if sandbox.environment is not environment or not sandbox.ready:
            raise IsolationError("prepared sandbox must belong to this environment")
        if not loop.is_running():
            raise ValueError("Harbor event loop must already be running")
        self.environment, self.loop, self.sandbox = environment, loop, sandbox
        self._serial = threading.Lock()
        self._admission = threading.Lock()
        self._submissions = {}
        self._rpc_tasks = {}
        self._rpc_fault = False

    async def quiesce(self):
        """Call on the Harbor loop after Bridge.close_admission(), before freeze."""
        with self._admission:
            self.sandbox.ready = False
            submissions = tuple(self._submissions.items())
        # Never cancel the submitted coroutines or environment.exec. Harbor's
        # buffered Compose path does not reap its subprocess on CancelledError,
        # and create_subprocess_exec occurs before Harbor's timeout begins.
        # Stop current writers promptly, then repeat while observing delayed
        # dispatches. This cleanup window is independent of the solver budget.
        cleanup_deadline = time.monotonic() + RPC_CLEANUP_SECONDS
        submitted = {asyncio.wrap_future(future) for future, _ in submissions}
        await self._kill_agent(cleanup_deadline=cleanup_deadline)
        while True:
            if self._rpc_fault:
                raise IsolationError("Harbor RPC outcome uncertain; container teardown required before freeze")
            pending = {task for task in submitted | set(self._rpc_tasks) if not task.done()}
            if not pending:
                if any(task.cancelled() for task in submitted):
                    raise IsolationError("Harbor execution wrapper cancelled; cannot prove quiescence")
                for task in submitted:
                    task.exception()
                break
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                raise IsolationError("Harbor RPC still pending; cannot prove quiescence or freeze")
            await asyncio.wait(pending, timeout=min(0.25, remaining))
            await self._kill_agent(cleanup_deadline=cleanup_deadline)
        await self._kill_agent(cleanup_deadline=cleanup_deadline)
        return {"status": "quiescent", "container_id": self.sandbox.container_id,
                "condition": self.sandbox.condition, "solver_uid_processes": 0}

    async def _settle(self, tasks):
        """Bounded observation only: timeout/caller cancellation leaves RPCs alive."""
        pending = {task for task in tasks if not task.done()}
        if pending:
            remaining = max(tasks[task] for task in pending) - time.monotonic()
            _, pending = await asyncio.wait(pending, timeout=max(0, remaining))
            if pending:
                raise IsolationError("Harbor RPC still pending; cannot prove quiescence or freeze")
        for task in tasks:
            if task.cancelled():
                raise IsolationError("Harbor execution wrapper cancelled; cannot prove quiescence")
            # Retrieve errors without treating an exception as process evidence.
            task.exception()

    async def _rpc(self, *, cleanup_deadline=None, **arguments):
        if cleanup_deadline is not None and time.monotonic() >= cleanup_deadline:
            raise IsolationError("Harbor RPC still pending at cleanup deadline; cannot freeze")
        task = asyncio.create_task(self.environment.exec(**arguments))
        task.add_done_callback(self._observe_rpc)
        self._rpc_tasks[task] = time.monotonic() + arguments["timeout_sec"] + RPC_CLEANUP_SECONDS
        if cleanup_deadline is not None:
            self._rpc_tasks[task] = min(self._rpc_tasks[task], cleanup_deadline)
        try:
            await self._settle({task: self._rpc_tasks[task]})
            return task.result()
        except BaseException:
            # An RPC exception cannot establish whether Compose dispatched an
            # exec into the container. Keep it unknown even after wrapper exit.
            self._rpc_fault = True
            self.sandbox.ready = False
            raise
        finally:
            if task.done():
                self._rpc_tasks.pop(task)

    def _observe_rpc(self, task):
        # Even if its waiter already failed, retain late RPC failure as a world
        # barrier and retrieve the exception instead of abandoning the task.
        if task.cancelled() or task.exception() is not None:
            self._rpc_fault = True
            self.sandbox.ready = False

    async def _kill_agent(self, *, cleanup_deadline=None):
        verify = '''import os, pathlib, pwd, time
uid = pwd.getpwnam('agent').pw_uid
for attempt in range(50):
    active = False
    for process in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            if process.stat().st_uid == uid and ') Z' not in (process / 'stat').read_text(): active = True
        except (FileNotFoundError, ProcessLookupError): pass
    if not active: break
    time.sleep(0.05)
else: raise RuntimeError('agent processes still active')
'''
        stopped = await self._rpc(
            cleanup_deadline=cleanup_deadline,
            command=("pkill -KILL -u agent; rc=$?; test $rc -eq 0 -o $rc -eq 1 && python3 -I -c "
                     + shlex.quote(verify) + " && printf '%s' \"$rc\""),
            user="root", cwd="/root", timeout_sec=5)
        if stopped.return_code != 0:
            raise IsolationError("agent process stop failed")
        if (stopped.stdout or "").strip() not in {"0", "1"}:
            raise IsolationError("agent stop confirmation missing")
        return stopped.stdout.strip() == "0"

    async def _execute(self, source, deadline, execution_id):
        decoded = None
        path = "/workspace/rpnh-" + execution_id + ".py"

        def remaining():
            seconds = deadline - time.monotonic()
            if not self.sandbox.ready or seconds <= 0:
                raise IsolationError("sandbox admission closed or execution deadline reached")
            return seconds

        try:
            payload = source.encode()
            # Bound each exec argument below Linux MAX_ARG_STRLEN. These trusted
            # uploads write bytes only; the one runner below evaluates the source.
            for offset in range(0, max(1, len(payload)), 32768):
                stage = ("import base64; " + "open(" + repr(path) + ", "
                         + repr("xb" if offset == 0 else "ab") + ").write(base64.b64decode("
                         + repr(base64.b64encode(payload[offset:offset + 32768]).decode()) + "))")
                uploaded = await self._rpc(
                    command="setpriv --no-new-privs python3 -I -c " + shlex.quote(stage),
                    user="agent", cwd="/workspace", timeout_sec=min(10, math.ceil(remaining())))
                if uploaded.return_code != 0:
                    raise RuntimeError("source upload failed")
            timeout = remaining()
            command = "setpriv --no-new-privs python3 -I -c " + shlex.quote(_RUNNER) + " " + " ".join(
                shlex.quote(str(arg)) for arg in (path, timeout, MAX_OUTPUT))
            response = await self._rpc(command=command, user="agent", cwd="/workspace",
                                       timeout_sec=math.ceil(timeout + 2))
            if response.return_code != 0:
                raise RuntimeError("container supervisor did not return a result")
            decoded = json.loads(response.stdout)
            return decoded
        finally:
            # Only run cleanup once the actual RPC has settled normally. An
            # abandoned or failed RPC can dispatch later: fail closed instead.
            if not self._rpc_fault and not any(not task.done() for task in self._rpc_tasks):
                killed = await self._kill_agent()
                if killed and isinstance(decoded, dict):
                    decoded["status"] = "unknown"
                    decoded["stderr"] = "background agent processes stopped; effects require reconciliation"

    def execute_python(self, source, timeout_seconds, cancellation_requested, identity):
        check_arguments({"source": source})
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("invalid backend deadline")
        try:
            if asyncio.get_running_loop() is self.loop:
                raise RuntimeError("HarborBackend must run in a worker thread")
        except RuntimeError as exc:
            if str(exc) != "no running event loop":
                raise
        execution_id = uuid.uuid4().hex
        answer = {"status": "unknown", "stdout": "", "stderr": "container response unavailable",
                  "exit_code": None, "execution_id": execution_id}
        with self._serial:
            with self._admission:
                if not self.sandbox.ready or cancellation_requested():
                    return dict(answer, status="interrupted", stderr="sandbox unavailable or call cancelled")
                deadline = time.monotonic() + timeout_seconds
                future = asyncio.run_coroutine_threadsafe(self._execute(source, deadline, execution_id), self.loop)
                self._submissions[future] = deadline + RPC_CLEANUP_SECONDS
            while True:
                if cancellation_requested() or time.monotonic() >= deadline:
                    self.sandbox.ready = False
                    # Keep the real RPC alive and tracked. Only quiesce may
                    # prove settlement and then stop the container processes.
                    return dict(answer, stderr="interrupted; pending RPC must settle before quiescence")
                try:
                    raw = future.result(timeout=0.05)
                    if (not isinstance(raw, dict) or raw.get("status") not in {"completed", "failed", "unknown"}
                            or not isinstance(raw.get("stdout"), str) or not isinstance(raw.get("stderr"), str)
                            or type(raw.get("exit_code")) is not int):
                        raise ValueError("malformed container result")
                    answer = dict(raw, execution_id=execution_id)
                    break
                except FutureTimeout:
                    if future.done():
                        break  # A completed RPC raised TimeoutError; this is not a poll timeout.
                    continue
                except Exception:
                    break
            if answer["status"] == "unknown":
                self.sandbox.ready = False
            return answer
