"""Thin multi-task control facade shared by the CLI and a future dashboard.

Task execution remains in independent processes and Registry roots.  This
facade owns only process handles and task-local owner-channel clients; it never
constructs a Registry writer or settles a firing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agent_tasks import AgentTaskSpec, agent_task_catalog
from .control_client import ControlClient
from .inspection import project_registry_net
from .registry._registry import _RegistryCore
from .registry.publication import _ref_payload, _version_from_payload
from .registry.resource_service import _ResourceServiceKernel
from .registry.resources import ResourceVersionRef


@dataclass(slots=True)
class TaskHandle:
    """Session-local handle; Registry and process projections remain separate."""

    task_id: str
    kind: str
    run_dir: Path
    socket_path: Path
    log_path: Path
    spec: AgentTaskSpec
    process: Any
    launch_state: str = "active"
    launch_mode: str = "fresh"
    resume_checkpoint_version_id: str | None = None
    reopen_command_id: str | None = None
    reopen_reason: str | None = None


def _process_start_ticks(pid: int) -> int | None:
    try:
        value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        tail = value[value.rfind(")") + 2:].split()
        return int(tail[19])
    except (OSError, ValueError, IndexError):
        return None


@dataclass(slots=True)
class _DetachedProcess:
    """Validated process identity for a task recovered by a later frontend."""

    pid: int
    start_ticks: int

    def poll(self):
        return None if _process_start_ticks(self.pid) == self.start_ticks else 1

    def send_signal(self, value):
        if self.poll() is not None:
            raise ProcessLookupError(self.pid)
        os.kill(self.pid, value)


def owner_socket_path(
        control_root: Path, task_id: str, run_dir: Path,
) -> Path:
    """Return the owner endpoint local to its child Registry."""
    if (not isinstance(control_root, Path)
            or not isinstance(run_dir, Path)
            or not isinstance(task_id, str)
            or not task_id):
        raise TypeError("owner socket identity is invalid")
    del control_root, task_id
    return run_dir.resolve() / "owner.sock"


def _relative_reference(path: Path, parent: Path) -> str:
    value = Path(os.path.relpath(path, parent)).as_posix()
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value:
        raise ValueError("task path reference must be relative")
    return value


def _resolve_reference(parent: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} must be a relative POSIX path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value:
        raise ValueError(f"{label} must be a relative POSIX path")
    return (parent / Path(*pure.parts)).resolve()


def _acquire_task_launch_lock(root: Path, task_id: str):
    """Acquire the worker-lifetime lock, or observe another launch owner."""
    import fcntl

    lock_root = root / "launch_locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock = (lock_root / f"{task_id}.lock").open("a+b")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    # A daemon may have closed one or more standard descriptors, allowing the
    # lock open above to reuse fd 0, 1, or 2.  ``Popen`` subsequently installs
    # the worker's standard streams and would replace such a descriptor before
    # ``pass_fds`` can preserve it.  Duplicate the same locked open-file
    # description outside the standard range before handing it to a worker.
    try:
        inherited_fd = fcntl.fcntl(
            lock.fileno(), fcntl.F_DUPFD_CLOEXEC, 3)
    except Exception:
        lock.close()
        raise
    inherited_lock = os.fdopen(inherited_fd, "a+b", closefd=True)
    lock.close()
    return inherited_lock


class TaskControl:
    """Create and control independent RPNH task objects."""

    def __init__(
            self, root: Path, *,
            popen_factory: Callable[..., Any] = subprocess.Popen,
            channel_ready: Callable[[Path], bool] | None = None,
            recover_pending_launches: bool = True,
    ) -> None:
        if not isinstance(root, Path):
            raise TypeError("TaskControl root requires pathlib.Path")
        if not isinstance(recover_pending_launches, bool):
            raise TypeError("recover_pending_launches requires bool")
        self.root = root.resolve()
        self._popen = popen_factory
        self._channel_ready = channel_ready or (lambda path: path.is_socket())
        self._tasks: dict[str, TaskHandle] = {}
        self._load_persisted_tasks()
        if recover_pending_launches:
            self._recover_pending_launches()

    @property
    def _manifest_root(self) -> Path:
        return self.root / "manifests"

    def _owner_socket_path(self, task_id: str, run_dir: Path) -> Path:
        """Return this task's short, durable owner-channel endpoint."""
        return owner_socket_path(self.root, task_id, run_dir)

    def _upgrade_legacy_owner_socket(self, handle: TaskHandle) -> None:
        """Move a stopped legacy endpoint into its child Registry."""
        socket_path = self._owner_socket_path(handle.task_id, handle.run_dir)
        if handle.spec.owner_socket_path == socket_path:
            return
        handle.spec = replace(handle.spec, owner_socket_path=socket_path)
        handle.socket_path = socket_path
        self._write_spec(handle)

    def _write_spec(self, handle: TaskHandle) -> Path:
        spec_path = self.root / "specs" / f"{handle.task_id}.json"
        spec_path.write_text(
            json.dumps(handle.spec.as_worker_document(
                document_root=spec_path.parent), ensure_ascii=False,
                indent=2) + "\n",
            encoding="utf-8")
        return spec_path

    def _write_manifest(
            self, handle: TaskHandle, *, launch_state: str,
            launch_mode: str, pid: int | None = None,
            process_start_ticks: int | None = None,
    ) -> None:
        if launch_state not in {"pending", "active"}:
            raise ValueError("task launch state is invalid")
        if launch_mode not in {"fresh", "resume", "reopen"}:
            raise ValueError("task launch mode is invalid")
        if launch_state == "pending":
            pid = process_start_ticks = None
        elif (not isinstance(pid, int)
              or not isinstance(process_start_ticks, int)):
            raise ValueError("active task manifest requires exact process identity")
        self._manifest_root.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": "rpnh/task_handle/v2",
            "task_id": handle.task_id,
            "kind": handle.kind,
            "run_relative_path": _relative_reference(
                handle.run_dir, self.root),
            "socket_relative_path": _relative_reference(
                handle.socket_path, handle.run_dir),
            "log_relative_path": _relative_reference(
                handle.log_path, self.root),
            "spec_relative_path": _relative_reference(
                self.root / "specs" / f"{handle.task_id}.json",
                self.root),
            "pid": pid,
            "process_start_ticks": process_start_ticks,
            "launch_state": launch_state,
            "launch_mode": launch_mode,
            "resume_checkpoint_version_id": (
                handle.resume_checkpoint_version_id),
            "reopen_command_id": handle.reopen_command_id,
            "reopen_reason": handle.reopen_reason,
        }
        path = self._manifest_root / f"{handle.task_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, path)
        handle.launch_state = launch_state
        handle.launch_mode = launch_mode

    def _refresh_handle_process(self, handle: TaskHandle) -> None:
        path = self._manifest_root / f"{handle.task_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return
        if (not isinstance(value, Mapping)
                or value.get("task_id") != handle.task_id):
            return
        launch_state = value.get("launch_state", "active")
        launch_mode = value.get("launch_mode", "fresh")
        if launch_state not in {"pending", "active"}:
            return
        if launch_mode not in {"fresh", "resume", "reopen"}:
            return
        handle.launch_state = launch_state
        handle.launch_mode = launch_mode
        checkpoint = value.get("resume_checkpoint_version_id")
        if checkpoint is None or isinstance(checkpoint, str):
            handle.resume_checkpoint_version_id = checkpoint
        reopen_command_id = value.get("reopen_command_id")
        reopen_reason = value.get("reopen_reason")
        if reopen_command_id is None or isinstance(reopen_command_id, str):
            handle.reopen_command_id = reopen_command_id
        if reopen_reason is None or isinstance(reopen_reason, str):
            handle.reopen_reason = reopen_reason
        pid = value.get("pid")
        ticks = value.get("process_start_ticks")
        if (launch_state == "active" and isinstance(pid, int)
                and isinstance(ticks, int)):
            current_pid = getattr(handle.process, "pid", None)
            if (current_pid == pid
                    and not isinstance(handle.process, _DetachedProcess)):
                # A locally spawned Popen retains the exact exit code after
                # /proc has removed the process.  Do not replace that known
                # result with the detached observer's synthetic "gone" code.
                return
            current_ticks = (
                _process_start_ticks(current_pid)
                if isinstance(current_pid, int) else None)
            if current_pid != pid or current_ticks != ticks:
                handle.process = _DetachedProcess(pid, ticks)

    def _load_persisted_tasks(self) -> None:
        if not self._manifest_root.is_dir():
            return
        for path in sorted(self._manifest_root.glob("task-*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, Mapping):
                    raise ValueError("manifest schema")
                task_id = value["task_id"]
                version = value.get("schema_version")
                if version == "rpnh/task_handle/v2":
                    spec_path = _resolve_reference(
                        self.root, value.get("spec_relative_path"),
                        label="spec_relative_path")
                    run_dir = _resolve_reference(
                        self.root, value.get("run_relative_path"),
                        label="run_relative_path")
                    socket_path = _resolve_reference(
                        run_dir, value.get("socket_relative_path"),
                        label="socket_relative_path")
                    log_path = _resolve_reference(
                        self.root, value.get("log_relative_path"),
                        label="log_relative_path")
                elif version == "rpnh/task_handle/v1":
                    spec_path = Path(value["spec_path"]).resolve()
                    run_dir = Path(value["run_dir"]).resolve()
                    socket_path = Path(value["socket_path"]).resolve()
                    log_path = Path(value["log_path"]).resolve()
                else:
                    raise ValueError("manifest schema")
                if (not isinstance(task_id, str)
                        or path.name != f"{task_id}.json"
                        or spec_path != (
                            self.root / "specs" / f"{task_id}.json").resolve()
                        or log_path != (
                            self.root / "logs" / f"{task_id}.log").resolve()):
                    raise ValueError("manifest identity")
                spec = AgentTaskSpec.from_worker_document(json.loads(
                    spec_path.read_text(encoding="utf-8")),
                    document_root=spec_path.parent)
                pid = value.get("pid")
                ticks = value.get("process_start_ticks")
                launch_state = value.get("launch_state", "active")
                launch_mode = value.get("launch_mode", "fresh")
                resume_checkpoint_version_id = value.get(
                    "resume_checkpoint_version_id")
                reopen_command_id = value.get("reopen_command_id")
                reopen_reason = value.get("reopen_reason")
                if (launch_state not in {"pending", "active"}
                        or launch_mode not in {"fresh", "resume", "reopen"}
                        or (resume_checkpoint_version_id is not None
                            and not isinstance(
                                resume_checkpoint_version_id, str))
                        or (reopen_command_id is not None
                            and not isinstance(reopen_command_id, str))
                        or (reopen_reason is not None
                            and not isinstance(reopen_reason, str))
                        or (launch_mode == "reopen")
                        != all(value is not None for value in (
                            resume_checkpoint_version_id,
                            reopen_command_id, reopen_reason))):
                    raise ValueError("manifest launch lifecycle")
                process = (
                    _DetachedProcess(pid, ticks)
                    if isinstance(pid, int) and isinstance(ticks, int)
                    else _DetachedProcess(-1, -1))
                handle = TaskHandle(
                    task_id=task_id,
                    kind=value["kind"],
                    run_dir=run_dir,
                    socket_path=socket_path,
                    log_path=log_path,
                    spec=spec,
                    process=process,
                    launch_state=launch_state,
                    launch_mode=launch_mode,
                    resume_checkpoint_version_id=(
                        resume_checkpoint_version_id),
                    reopen_command_id=reopen_command_id,
                    reopen_reason=reopen_reason,
                )
                if (handle.kind != spec.kind
                        or handle.run_dir != spec.run_dir.resolve()
                        or (spec.owner_socket_path is not None
                            and handle.socket_path != spec.owner_socket_path)
                        or (spec.owner_socket_path is None
                            and handle.socket_path
                            != handle.run_dir / "owner.sock")):
                    raise ValueError("manifest differs from task spec")
                self._tasks[task_id] = handle
            except (KeyError, OSError, TypeError, ValueError,
                    json.JSONDecodeError):
                continue

    def _recover_pending_launches(self) -> None:
        """Compensate durable intents without creating another task identity."""
        for handle in tuple(self._tasks.values()):
            if handle.launch_state != "pending":
                continue
            launch_lock = _acquire_task_launch_lock(
                self.root, handle.task_id)
            if launch_lock is None:
                # An original worker may be between taking the lock and
                # recording its active PID. Never overwrite that claim or
                # launch a competing process from a stale pending snapshot.
                self._refresh_handle_process(handle)
                continue
            self._refresh_handle_process(handle)
            if handle.launch_state == "active":
                launch_lock.close()
                continue
            try:
                self._upgrade_legacy_owner_socket(handle)
                spec_path = self.root / "specs" / f"{handle.task_id}.json"
                self._write_manifest(
                    handle, launch_state="pending",
                    launch_mode=handle.launch_mode)
            except Exception:
                launch_lock.close()
                raise
            handle.process = self._spawn(
                handle.task_id, spec_path, handle.log_path,
                resume=handle.launch_mode == "resume",
                checkpoint_version_id=(
                    handle.resume_checkpoint_version_id),
                reopen_command_id=handle.reopen_command_id,
                reopen_reason=handle.reopen_reason,
                launch_lock=launch_lock)

    def _spawn(self, task_id: str, spec_path: Path, log_path: Path, *,
               resume: bool, checkpoint_version_id: str | None = None,
               reopen_command_id: str | None = None,
               reopen_reason: str | None = None,
               launch_lock=None) -> Any:
        argv = [sys.executable, "-m", "cpn.rpnh.task_worker"]
        if resume:
            argv.append("--resume")
        reopen_values = (
            checkpoint_version_id, reopen_command_id, reopen_reason)
        if any(value is not None for value in reopen_values):
            if resume or not all(isinstance(value, str) and value
                                 for value in reopen_values):
                raise ValueError(
                    "checkpoint reopen requires one complete non-resume intent")
            argv.extend(("--reopen", checkpoint_version_id,
                         reopen_command_id, reopen_reason))
        if launch_lock is not None:
            argv.extend(("--launch-lock-fd", str(launch_lock.fileno())))
        argv.append(str(spec_path))
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[2])
        inherited_pythonpath = environment.get("PYTHONPATH")
        pythonpath = (
            [] if not inherited_pythonpath else
            inherited_pythonpath.split(os.pathsep))
        environment["PYTHONPATH"] = os.pathsep.join((
            package_root,
            *(entry for entry in pythonpath if entry != package_root),
        ))
        log = log_path.open("ab", buffering=0)
        try:
            return self._popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(self.root),
                env=environment,
                pass_fds=(
                    () if launch_lock is None else
                    (launch_lock.fileno(),)),
                start_new_session=True,
            )
        finally:
            log.close()
            if launch_lock is not None:
                launch_lock.close()

    def start(self, spec: AgentTaskSpec) -> TaskHandle:
        if not isinstance(spec, AgentTaskSpec):
            raise TypeError("TaskControl.start requires AgentTaskSpec")
        task_id = "task-" + uuid4().hex[:12]
        specs = self.root / "specs"
        logs = self.root / "logs"
        specs.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        spec_path = specs / f"{task_id}.json"
        log_path = logs / f"{task_id}.log"
        run_dir = spec.run_dir.resolve()
        socket_path = self._owner_socket_path(task_id, run_dir)
        spec = replace(spec, owner_socket_path=socket_path)
        handle = TaskHandle(
            task_id=task_id,
            kind=spec.kind,
            run_dir=run_dir,
            socket_path=socket_path,
            log_path=log_path,
            spec=spec,
            process=_DetachedProcess(-1, -1),
            launch_state="pending",
            launch_mode="fresh",
            resume_checkpoint_version_id=None,
            reopen_command_id=None,
            reopen_reason=None,
        )
        self._tasks[task_id] = handle
        self._write_spec(handle)
        # The durable intent precedes process creation.  The worker changes it
        # to active only after winning the per-task launch lock, before any
        # Registry/provider work.
        launch_lock = _acquire_task_launch_lock(self.root, task_id)
        if launch_lock is None:
            raise RuntimeError(
                "new task launch identity is already owned")
        try:
            self._write_manifest(
                handle, launch_state="pending", launch_mode="fresh")
        except Exception:
            launch_lock.close()
            raise
        handle.process = self._spawn(
            task_id, spec_path, log_path, resume=False,
            checkpoint_version_id=None,
            reopen_command_id=None,
            reopen_reason=None,
            launch_lock=launch_lock)
        return handle

    def get(self, task_id: str) -> TaskHandle:
        try:
            handle = self._tasks[task_id]
        except KeyError as exc:
            raise ValueError(f"unknown task: {task_id}") from exc
        self._refresh_handle_process(handle)
        return handle

    def list(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.status(task_id) for task_id in self._tasks)

    def status(self, task_id: str) -> dict[str, Any]:
        handle = self.get(task_id)
        registry_status = None
        if (handle.run_dir / ".registry_v1" / "registry.sqlite3").is_file():
            try:
                registry_status = self._read_terminal_status(handle.run_dir)
            except (OSError, RuntimeError, TypeError, ValueError):
                registry_status = None
        observed_return_code = handle.process.poll()
        return_code = (
            None if isinstance(handle.process, _DetachedProcess)
            else observed_return_code)
        process_status = (
            "EXITED" if registry_status is not None
            and registry_status.get("execution_status") == "terminal" else
            "STOPPED" if registry_status is not None
            and registry_status.get("execution_status") == "stopped_by_owner" else
            "STARTING" if handle.launch_state == "pending" else
            "RUNNING" if observed_return_code is None else
            "EXITED" if observed_return_code == 0 else
            "FAILED"
        )
        result: dict[str, Any] = {
            "task_id": task_id,
            "kind": handle.kind,
            "process_status": process_status,
            "return_code": return_code,
            "run_dir": str(handle.run_dir),
            "socket_ready": self._channel_ready(handle.socket_path),
            "log_path": str(handle.log_path),
        }
        if self._channel_ready(handle.socket_path):
            try:
                snapshot = ControlClient(str(handle.socket_path)).snapshot()
                result["registry"] = {
                    key: snapshot.get(key) for key in (
                        "run_ref", "task_ref", "net_ref", "checkpoint_ref",
                        "enabled_transitions", "active_firings",
                    )
                }
            except (OSError, RuntimeError, ValueError) as exc:
                result["control_error"] = str(exc)
        elif (handle.run_dir / ".registry_v1" / "registry.sqlite3").is_file():
            try:
                result["registry"] = (
                    registry_status or self._read_terminal_status(handle.run_dir))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                result["registry_error"] = str(exc)
        return result

    @staticmethod
    def _read_terminal_status(run_dir: Path) -> dict[str, Any]:
        core = _RegistryCore(
            run_dir, create=False, read_only=True, catalog=agent_task_catalog())
        terminal = core.event_store.canonical_object_rows(
            object_type="run_terminal_evidence/v1")
        final = core.event_store.canonical_object_rows(
            object_type="final_result_index/v1")
        from .registry.run_authority import current_run_execution_authority
        _authority_ref, authority = current_run_execution_authority(
            core, _ResourceServiceKernel(core))
        return {
            "task_ref": str(core.task_id),
            "execution_status": authority["status"],
            "checkpoint_ref": authority["latest_checkpoint_ref"],
            "execution_generation": authority.get(
                "execution_generation", 0),
            "terminal_evidence_count": len(terminal),
            "final_result_index_count": len(final),
            "actual_model_call_counts": list(
                core.event_store.actual_model_call_counts()),
        }

    def result_evidence(self, task_id: str) -> Mapping[str, Any]:
        """Inspect existing registered result/request facts without starting an owner."""
        from .agent_result_inspection import project_registry_agent_results
        return project_registry_agent_results(
            self.get(task_id).run_dir, catalog=agent_task_catalog())

    def snapshot(self, task_id: str) -> Mapping[str, Any]:
        handle = self.get(task_id)
        if not self._channel_ready(handle.socket_path):
            raise RuntimeError("task owner channel is not currently available")
        return ControlClient(str(handle.socket_path)).snapshot()

    def result(self, task_id: str) -> dict[str, Any]:
        """Read the exact registered terminal product without opening a writer."""
        handle = self.get(task_id)
        database = handle.run_dir / ".registry_v1" / "registry.sqlite3"
        if not database.is_file():
            raise RuntimeError("task Registry is not currently available")
        core = _RegistryCore(
            handle.run_dir, create=False, read_only=True,
            catalog=agent_task_catalog())
        from .registry.run_authority import current_run_execution_authority
        _authority_ref, authority = current_run_execution_authority(
            core, _ResourceServiceKernel(core))
        evidence_payload = authority.get("terminal_evidence_ref")
        if (authority.get("status") != "terminal"
                or evidence_payload is None):
            raise RuntimeError("task has no registered terminal result")
        evidence_ref = _version_from_payload(evidence_payload)
        evidence = dict(_ResourceServiceKernel(core)._exact_object(
            evidence_ref,
            expected_type="run_terminal_evidence/v1").metadata)
        result_ref = _version_from_payload(evidence["terminal_result_ref"])
        raw = _ResourceServiceKernel(core)._read_registered(
            ResourceVersionRef(result_ref.entity_id, result_ref.version_id))
        return {
            "task_id": task_id,
            "kind": handle.kind,
            "terminal_evidence_ref": evidence["terminal_evidence_ref"],
            "terminal_result_ref": evidence["terminal_result_ref"],
            "run_outcome": evidence["run_outcome"],
            "execution_generation": authority.get(
                "execution_generation", 0),
            "output": json.loads(raw),
            "actual_model_call_counts": list(
                core.event_store.actual_model_call_counts()),
        }

    def message(
            self, task_id: str, body: str, *, target: str | None = None,
    ) -> Mapping[str, Any]:
        handle = self.get(task_id)
        if not self._channel_ready(handle.socket_path):
            raise RuntimeError("task owner channel is not currently available")
        if target is None:
            if handle.kind == "workflow":
                raise ValueError(
                    "workflow message requires an explicit TARGET transition")
            target = handle.spec.stages[0].stage_id + ".run"
        return ControlClient(str(handle.socket_path)).message(target, body)

    def stop(
            self, task_id: str, *, startup_safe: bool = False,
    ) -> dict[str, Any]:
        """Forward an explicit user stop to this exact launched process."""
        handle = self.get(task_id)
        if handle.process.poll() is not None:
            return {"task_id": task_id, "status": "ALREADY_EXITED",
                    "return_code": handle.process.poll()}
        if not self._channel_ready(handle.socket_path):
            if startup_safe:
                # run_agent_task installs its SIGINT handler before creating
                # the Registry or owner socket.  This closes the small launch
                # window without waiting for a control channel that may never
                # appear; at this point no provider dispatch is reachable yet.
                handle.process.send_signal(signal.SIGINT)
                return {
                    "task_id": task_id,
                    "status": "STARTUP_STOP_REQUESTED",
                }
            return {"task_id": task_id, "status": "OWNER_CHANNEL_NOT_READY"}
        handle.process.send_signal(signal.SIGINT)
        return {"task_id": task_id, "status": "STOP_REQUESTED"}

    def checkpoints(self, task_id: str) -> dict[str, Any]:
        """List exact committed checkpoint cuts selectable for reentry."""
        handle = self.get(task_id)
        core = _RegistryCore(
            handle.run_dir, create=False, read_only=True,
            catalog=agent_task_catalog())
        from .registry.checkpoint_reentry import committed_checkpoint_refs
        from .registry.run_authority import current_run_execution_authority
        refs = committed_checkpoint_refs(core)
        _authority_ref, authority = current_run_execution_authority(
            core, _ResourceServiceKernel(core))
        return {
            "task_id": task_id,
            "current_checkpoint_ref": authority["latest_checkpoint_ref"],
            "execution_generation": authority.get(
                "execution_generation", 0),
            "checkpoints": [_ref_payload(ref) for ref in refs],
        }

    def resume(self, task_id: str) -> dict[str, Any]:
        """Resume the task's current owner-stopped checkpoint."""
        handle = self.get(task_id)
        if handle.process.poll() is None:
            return {"task_id": task_id, "status": "ALREADY_RUNNING"}
        status = self._read_terminal_status(handle.run_dir)
        if status["execution_status"] == "terminal":
            return {"task_id": task_id, "status": "ALREADY_TERMINAL"}
        if status["execution_status"] != "stopped_by_owner":
            raise RuntimeError(
                "task resume requires Registry status stopped_by_owner")
        spec_path = self.root / "specs" / f"{task_id}.json"
        self._upgrade_legacy_owner_socket(handle)
        launch_lock = _acquire_task_launch_lock(self.root, task_id)
        if launch_lock is None:
            raise RuntimeError(
                "task resume launch identity is already owned")
        try:
            handle.resume_checkpoint_version_id = None
            handle.reopen_command_id = None
            handle.reopen_reason = None
            self._write_manifest(
                handle, launch_state="pending", launch_mode="resume")
        except Exception:
            launch_lock.close()
            raise
        handle.process = self._spawn(
            task_id, spec_path, handle.log_path, resume=True,
            checkpoint_version_id=None,
            reopen_command_id=None,
            reopen_reason=None,
            launch_lock=launch_lock)
        return {
            "task_id": task_id,
            "status": "RESUME_STARTED",
        }

    def reopen(
            self, task_id: str, checkpoint_version_id: str, *,
            reason: str = "Owner selected an exact Registry checkpoint.",
    ) -> dict[str, Any]:
        """Start a new execution generation at an exact committed cut."""
        if (not isinstance(checkpoint_version_id, str)
                or not checkpoint_version_id
                or not isinstance(reason, str) or not reason
                or len(reason) > 600):
            raise ValueError("task reopen requires one checkpoint and reason")
        handle = self.get(task_id)
        if handle.process.poll() is None:
            return {"task_id": task_id, "status": "ALREADY_RUNNING"}
        core = _RegistryCore(
            handle.run_dir, create=False, read_only=True,
            catalog=agent_task_catalog())
        from .registry.checkpoint_reentry import resolve_committed_checkpoint
        from .registry.run_authority import current_run_execution_authority
        selected = resolve_committed_checkpoint(core, checkpoint_version_id)
        _authority_ref, authority = current_run_execution_authority(
            core, _ResourceServiceKernel(core))
        status = authority["status"]
        reopen_authorization = authority.get("reopen_authorization_ref")
        reentry_checkpoint = None
        if reopen_authorization is not None:
            reentry_checkpoint = dict(
                _ResourceServiceKernel(core)._exact_object(
                    _version_from_payload(reopen_authorization),
                    expected_type="run_reopen_authorization/v1"
                ).metadata)["reentry_checkpoint_ref"]
        recover_existing = (
            reopen_authorization is not None
            and (status == "running"
                 or (status == "stopped_by_owner"
                     and authority["latest_checkpoint_ref"]
                     == reentry_checkpoint))
            and handle.launch_mode == "reopen"
            and handle.resume_checkpoint_version_id
            == str(selected.version_id)
            and isinstance(handle.reopen_command_id, str)
            and isinstance(handle.reopen_reason, str))
        if (status not in {"terminal", "stopped_by_owner", "running"}
                and not recover_existing):
            raise RuntimeError(
                "task reopen requires terminal/owner-stopped authority or its durable recovery intent")
        if recover_existing:
            command_id = handle.reopen_command_id
            reason = handle.reopen_reason
        else:
            command_id = "owner-reopen-" + uuid4().hex
        spec_path = self.root / "specs" / f"{task_id}.json"
        self._upgrade_legacy_owner_socket(handle)
        launch_lock = _acquire_task_launch_lock(self.root, task_id)
        if launch_lock is None:
            raise RuntimeError("task reopen launch identity is already owned")
        try:
            handle.resume_checkpoint_version_id = str(selected.version_id)
            handle.reopen_command_id = command_id
            handle.reopen_reason = reason
            self._write_manifest(
                handle, launch_state="pending", launch_mode="reopen")
        except Exception:
            launch_lock.close()
            raise
        handle.process = self._spawn(
            task_id, spec_path, handle.log_path, resume=False,
            checkpoint_version_id=str(selected.version_id),
            reopen_command_id=command_id, reopen_reason=reason,
            launch_lock=launch_lock)
        return {
            "task_id": task_id,
            "status": ("REOPEN_RECOVERY_STARTED" if recover_existing
                       else "CHECKPOINT_REOPEN_STARTED"),
            "checkpoint_version_id": str(selected.version_id),
            "reopen_command_id": command_id,
        }

    def net(self, task_id: str) -> dict[str, Any]:
        handle = self.get(task_id)
        return project_registry_net(
            handle.run_dir, catalog=agent_task_catalog())


def claim_task_worker_launch(
        spec_path: Path, spec: AgentTaskSpec, *, resume: bool,
        checkpoint_version_id: str | None = None,
        reopen_command_id: str | None = None,
        reopen_reason: str | None = None,
        inherited_lock_fd: int | None = None,
):
    """Acquire this task's durable launch identity before executing it.

    The returned open file owns the advisory lock for the worker lifetime.
    ``None`` means another worker already owns the same task identity.
    """
    path = spec_path.resolve()
    root = path.parent.parent
    task_id = path.stem
    if (path.parent != (root / "specs").resolve()
            or not task_id.startswith("task-")):
        raise ValueError("task worker spec path has no task-control identity")
    lock_path = root / "launch_locks" / f"{task_id}.lock"
    if inherited_lock_fd is None:
        lock = _acquire_task_launch_lock(root, task_id)
        if lock is None:
            return None
    else:
        if (isinstance(inherited_lock_fd, bool)
                or not isinstance(inherited_lock_fd, int)
                or inherited_lock_fd < 0):
            raise ValueError("inherited launch lock descriptor is invalid")
        descriptor = os.fstat(inherited_lock_fd)
        expected = lock_path.stat()
        if ((descriptor.st_dev, descriptor.st_ino)
                != (expected.st_dev, expected.st_ino)):
            raise ValueError(
                "inherited launch lock differs from task identity")
        lock = os.fdopen(inherited_lock_fd, "a+b", closefd=True)

    try:
        manifest_path = root / "manifests" / f"{task_id}.json"
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        reopen = reopen_command_id is not None
        if reopen != (checkpoint_version_id is not None) or reopen != (
                reopen_reason is not None) or (reopen and resume):
            raise ValueError("task worker reopen intent is incomplete")
        expected_mode = "reopen" if reopen else (
            "resume" if resume else "fresh")
        if not isinstance(value, Mapping):
            raise ValueError("task worker launch has a malformed manifest")
        version = value.get("schema_version")
        if version == "rpnh/task_handle/v2":
            persisted_spec = _resolve_reference(
                root, value.get("spec_relative_path"),
                label="spec_relative_path")
            persisted_run = _resolve_reference(
                root, value.get("run_relative_path"),
                label="run_relative_path")
        elif version == "rpnh/task_handle/v1":
            persisted_spec = Path(value.get("spec_path", "")).resolve()
            persisted_run = Path(value.get("run_dir", "")).resolve()
        else:
            raise ValueError("task worker launch has an unknown manifest schema")
        if (value.get("task_id") != task_id
                or persisted_spec != path
                or persisted_run != spec.run_dir.resolve()
                or value.get("kind") != spec.kind
                or value.get("launch_state") != "pending"
                or value.get("launch_mode") != expected_mode
                or value.get("resume_checkpoint_version_id")
                != checkpoint_version_id
                or value.get("reopen_command_id") != reopen_command_id
                or value.get("reopen_reason") != reopen_reason):
            raise ValueError("task worker launch differs from durable intent")
        pid = os.getpid()
        ticks = _process_start_ticks(pid)
        if ticks is None:
            raise RuntimeError("task worker process identity is unavailable")
        value["pid"] = pid
        value["process_start_ticks"] = ticks
        value["launch_state"] = "active"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, manifest_path)
        return lock
    except Exception:
        lock.close()
        raise


__all__ = (
    "TaskControl", "TaskHandle", "claim_task_worker_launch",
    "owner_socket_path",
)
