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
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agent_tasks import AgentTaskSpec, agent_task_catalog
from .control_client import ControlClient
from .inspection import project_registry_net
from .process_identity import (
    LINUX_IDENTITY, ProcessIdentityUnavailable, linux_start_ticks,
    process_identity_matches, read_process_identity, validate_process_identity,
)
from .registry._registry import _RegistryCore
from .registry.publication import _ref_payload, _version_from_payload
from .registry.resource_service import _ResourceServiceKernel
from .worker_runtime import (
    WorkerCompatibilityError, observe_worker_runtime, require_launch_runtime,
    require_worker_interoperability, validate_worker_runtime,
)


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
    launch_runtime: Mapping[str, Any] | None = None
    worker_runtime: Mapping[str, Any] | None = None
    manifest_required: bool = False
    spec_observation: tuple[int, int, int, int, int] | None = None


def _process_start_ticks(pid: int) -> int | None:
    """Retained Linux v1/v2 reader; observation failures are not process exit."""
    return linux_start_ticks(pid)


@dataclass(slots=True)
class _DetachedProcess:
    """Validated process identity for a task recovered by a later frontend."""

    pid: int
    start_ticks: int | None = None
    process_identity: object = None
    _pending: bool = False

    def poll(self):
        if self._pending:
            return 1  # Internal sentinel for a not-yet-launched pending task.
        if (isinstance(self.pid, bool) or not isinstance(self.pid, int)
                or self.pid < 1 or self.pid > 2**31 - 1):
            raise ProcessIdentityUnavailable("recorded task process PID is unavailable")
        if self.process_identity is not None:
            return None if process_identity_matches(
                self.pid, self.process_identity) else 1
        if (isinstance(self.start_ticks, bool)
                or not isinstance(self.start_ticks, int) or self.start_ticks < 0):
            raise ProcessIdentityUnavailable("recorded task process birth identity is unavailable")
        return None if _process_start_ticks(self.pid) == self.start_ticks else 1

    def send_signal(self, value):
        if self.poll() is not None:
            raise ProcessLookupError(self.pid)
        os.kill(self.pid, value)


def _detached_from_manifest(value: Mapping[str, Any]) -> _DetachedProcess:
    if value.get("launch_state", "active") == "pending":
        return _DetachedProcess(-1, -1, _pending=True)
    pid = value.get("pid")
    if value.get("schema_version") == "rpnh/task_handle/v3":
        # Missing or malformed v3 identity must not fall back to Linux ticks.
        return _DetachedProcess(pid, process_identity=value.get("process_identity"))
    return _DetachedProcess(pid, value.get("process_start_ticks"))


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


class TaskAdmissionError(ValueError):
    """A persisted task cannot be interpreted without changing its records."""


def _task_file_observation(path: Path) -> tuple[int, int, int, int, int]:
    observed = path.lstat()
    if not stat.S_ISREG(observed.st_mode):
        raise TaskAdmissionError(f"task admission requires a regular file: {path}")
    return (observed.st_dev, observed.st_ino, observed.st_size,
            observed.st_mtime_ns, observed.st_ctime_ns)


def _task_document_bytes(path: Path, *, limit: int):
    before = _task_file_observation(path)
    if before[2] > limit:
        raise TaskAdmissionError(f"task admission document exceeds its read budget: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        actual = os.fstat(source.fileno())
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != before[:2]:
            raise TaskAdmissionError(f"task admission document changed: {path}")
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise TaskAdmissionError(f"task admission document exceeds its read budget: {path}")
    if _task_file_observation(path) != before:
        raise TaskAdmissionError(f"task admission document changed while reading: {path}")
    return raw, before


def _read_task_document(path: Path, *, limit: int):
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value

    raw, observation = _task_document_bytes(path, limit=limit)
    try:
        value = json.loads(raw, object_pairs_hook=unique_fields)
    except (UnicodeError, ValueError) as exc:
        raise TaskAdmissionError(f"task admission document is invalid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise TaskAdmissionError(f"task admission document must be an object: {path}")
    return value, observation


@dataclass(frozen=True, slots=True)
class TaskInventory:
    """One read-only observation of the selected controller's task records."""

    root: Path
    handles: tuple[TaskHandle, ...]
    files: tuple[tuple[Path, tuple[int, int, int, int, int]], ...]
    manifest_paths: tuple[Path, ...]

    def assert_unchanged(self) -> None:
        current = _task_manifest_paths(self.root)
        if current != self.manifest_paths:
            raise TaskAdmissionError("task admission manifest inventory changed")
        for path, observation in self.files:
            if _task_file_observation(path) != observation:
                raise TaskAdmissionError(f"task admission document changed: {path}")

    def require_write_compatible(self) -> None:
        for handle in self.handles:
            label = f"task {handle.task_id}"
            if handle.launch_state == "pending":
                require_launch_runtime(handle.launch_runtime, label=label)
            elif handle.process.poll() is None:
                require_worker_interoperability(handle.worker_runtime, label=label)


def _task_manifest_paths(root: Path) -> tuple[Path, ...]:
    directory = root / "manifests"
    if directory.is_symlink():
        raise TaskAdmissionError(f"task manifest directory must not be a symlink: {directory}")
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise TaskAdmissionError(f"task manifest directory is not a directory: {directory}")
    # A future or malformed final record must not disappear because its name
    # no longer matches task-*.json. Atomic-write .tmp files are not final records.
    return tuple(sorted(directory.glob("*.json")))


def _read_task_record(root: Path, path: Path, *, cached: TaskHandle | None = None):
    """Parse one manifest and its exact spec without observing process liveness."""
    try:
        value, manifest_observation = _read_task_document(path, limit=65536)
        task_id = value["task_id"]
        version = value.get("schema_version")
        common = {"schema_version", "task_id", "kind", "pid", "launch_state",
                  "launch_mode", "resume_checkpoint_version_id", "reopen_command_id",
                  "reopen_reason", "launch_runtime", "worker_runtime"}
        if version in {"rpnh/task_handle/v2", "rpnh/task_handle/v3"}:
            path_fields = {"spec_relative_path", "run_relative_path",
                           "socket_relative_path", "log_relative_path"}
            spec_path = _resolve_reference(root, value.get("spec_relative_path"), label="spec_relative_path")
            run_dir = _resolve_reference(root, value.get("run_relative_path"), label="run_relative_path")
            socket_path = _resolve_reference(run_dir, value.get("socket_relative_path"), label="socket_relative_path")
            log_path = _resolve_reference(root, value.get("log_relative_path"), label="log_relative_path")
        elif version == "rpnh/task_handle/v1":
            path_fields = {"spec_path", "run_dir", "socket_path", "log_path"}
            if any(not isinstance(value.get(key), str) or not Path(value[key]).is_absolute()
                   for key in path_fields):
                raise ValueError("legacy manifest paths must be absolute")
            spec_path, run_dir = Path(value["spec_path"]).resolve(), Path(value["run_dir"]).resolve()
            socket_path, log_path = Path(value["socket_path"]).resolve(), Path(value["log_path"]).resolve()
        else:
            raise ValueError("unsupported task manifest schema")
        birth_field = "process_identity" if version == "rpnh/task_handle/v3" else "process_start_ticks"
        if set(value) - (common | path_fields | {birth_field}):
            raise ValueError("unknown task manifest field")
        if (not isinstance(task_id, str) or not task_id.startswith("task-")
                or path.name != f"{task_id}.json"
                or spec_path != (root / "specs" / f"{task_id}.json").resolve()
                or log_path != (root / "logs" / f"{task_id}.log").resolve()
                or (root / "specs").is_symlink()
                or (root / "specs" / f"{task_id}.json").is_symlink()):
            raise ValueError("task manifest identity or spec source differs")
        spec_observation = _task_file_observation(spec_path)
        if cached is not None and cached.task_id == task_id and cached.spec_observation == spec_observation:
            spec = cached.spec
        else:
            document, spec_observation = _read_task_document(spec_path, limit=16 * 1024 * 1024)
            spec = AgentTaskSpec.from_worker_document(document, document_root=spec_path.parent)
        launch_state, launch_mode = value.get("launch_state", "active"), value.get("launch_mode", "fresh")
        reopen_values = tuple(value.get(key) for key in (
            "resume_checkpoint_version_id", "reopen_command_id", "reopen_reason"))
        if (launch_state not in {"pending", "active"}
                or launch_mode not in {"fresh", "resume", "reopen"}
                or any(item is not None and (not isinstance(item, str) or not item)
                       for item in reopen_values)
                or ((launch_mode == "reopen") != all(item is not None for item in reopen_values))
                or (launch_mode != "reopen" and any(item is not None for item in reopen_values))):
            raise ValueError("task manifest launch lifecycle is invalid")
        runtime = {key: (None if value.get(key) is None else validate_worker_runtime(value[key]))
                   for key in ("launch_runtime", "worker_runtime")}
        if launch_state == "pending" and (
                value.get("pid") is not None or value.get(birth_field) is not None
                or runtime["worker_runtime"] is not None):
            raise ValueError("pending task manifest retains an active process observation")
        handle = TaskHandle(
            task_id=task_id, kind=value["kind"], run_dir=run_dir,
            socket_path=socket_path, log_path=log_path, spec=spec,
            process=_detached_from_manifest(value), launch_state=launch_state,
            launch_mode=launch_mode, resume_checkpoint_version_id=reopen_values[0],
            reopen_command_id=reopen_values[1], reopen_reason=reopen_values[2],
            launch_runtime=runtime["launch_runtime"], worker_runtime=runtime["worker_runtime"],
            manifest_required=True, spec_observation=spec_observation)
        if (handle.kind != spec.kind or handle.run_dir != spec.run_dir.resolve()
                or handle.socket_path != (spec.owner_socket_path if spec.owner_socket_path is not None
                                          else handle.run_dir / "owner.sock")):
            raise ValueError("task manifest differs from its exact spec")
        return handle, ((path, manifest_observation), (spec_path, spec_observation)), value
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise TaskAdmissionError(f"task admission rejected {path}: {exc}") from exc


def inspect_task_inventory(root: Path) -> TaskInventory:
    """Parse all final task records without a writer, launch lock or spawn."""
    if not isinstance(root, Path):
        raise TypeError("task inventory requires pathlib.Path")
    root = root.resolve()
    paths = _task_manifest_paths(root)
    handles, files = [], []
    for path in paths:
        handle, observed_files, _document = _read_task_record(root, path)
        handles.append(handle)
        files.extend(observed_files)
    return TaskInventory(root, tuple(handles), tuple(files), paths)


class TaskControl:
    """Create and control independent RPNH task objects."""

    def __init__(
            self, root: Path, *,
            popen_factory: Callable[..., Any] = subprocess.Popen,
            channel_ready: Callable[[Path], bool] | None = None,
            recover_pending_launches: bool = True,
    ) -> None:
        from cpn.update_context import reject_candidate_runtime
        reject_candidate_runtime()
        if not isinstance(root, Path):
            raise TypeError("TaskControl root requires pathlib.Path")
        if not isinstance(recover_pending_launches, bool):
            raise TypeError("recover_pending_launches requires bool")
        self.root = root.resolve()
        self._popen = popen_factory
        self._channel_ready = channel_ready or (lambda path: path.is_socket())
        self._tasks: dict[str, TaskHandle] = {}
        inventory = self._load_persisted_tasks()
        if recover_pending_launches:
            self._recover_pending_launches(inventory=inventory)

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
        handle.spec_observation = _task_file_observation(spec_path)
        return spec_path

    def _write_manifest(
            self, handle: TaskHandle, *, launch_state: str,
            launch_mode: str, pid: int | None = None,
            process_start_ticks: int | None = None,
            process_identity: Mapping[str, Any] | None = None,
    ) -> None:
        if launch_state not in {"pending", "active"}:
            raise ValueError("task launch state is invalid")
        if launch_mode not in {"fresh", "resume", "reopen"}:
            raise ValueError("task launch mode is invalid")
        if launch_state == "pending":
            pid = process_start_ticks = None
            process_identity = None
            handle.launch_runtime = observe_worker_runtime()
            handle.worker_runtime = None
        else:
            if (isinstance(pid, bool) or not isinstance(pid, int)
                    or pid < 1 or pid > 2**31 - 1):
                raise ValueError("active task manifest requires an exact process PID")
            if process_identity is not None:
                process_identity = validate_process_identity(process_identity)
                if process_start_ticks is not None:
                    raise ValueError("active task manifest requires only one birth identity")
            elif (isinstance(process_start_ticks, bool)
                  or not isinstance(process_start_ticks, int)
                  or process_start_ticks < 0):
                raise ValueError("active task manifest requires exact process identity")
        self._manifest_root.mkdir(parents=True, exist_ok=True)
        value = {
            "schema_version": ("rpnh/task_handle/v3" if process_identity is not None
                               else "rpnh/task_handle/v2"),
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
            "launch_runtime": handle.launch_runtime,
            "worker_runtime": handle.worker_runtime,
        }
        if process_identity is not None:
            value.pop("process_start_ticks")
            value["process_identity"] = process_identity
        path = self._manifest_root / f"{handle.task_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        os.replace(temporary, path)
        handle.launch_state = launch_state
        handle.launch_mode = launch_mode
        handle.manifest_required = True

    def _refresh_handle_process(self, handle: TaskHandle) -> None:
        path = self._manifest_root / f"{handle.task_id}.json"
        if not path.exists() and not path.is_symlink() and not handle.manifest_required:
            return  # Existing in-memory test/embedding handles have no durable record.
        try:
            observed, _files, _document = _read_task_record(self.root, path, cached=handle)
        except (OSError, TypeError, ValueError) as exc:
            raise TaskAdmissionError(f"task manifest cannot be refreshed: {path}: {exc}") from exc
        if (observed.task_id != handle.task_id or observed.kind != handle.kind
                or observed.run_dir != handle.run_dir or observed.socket_path != handle.socket_path
                or observed.log_path != handle.log_path or observed.spec != handle.spec):
            raise TaskAdmissionError(f"task manifest source/spec binding changed: {path}")
        for field in ("launch_state", "launch_mode", "resume_checkpoint_version_id",
                      "reopen_command_id", "reopen_reason", "launch_runtime", "worker_runtime",
                      "spec_observation"):
            setattr(handle, field, getattr(observed, field))
        if observed.launch_state == "active":
            current_pid = getattr(handle.process, "pid", None)
            if current_pid == observed.process.pid and not isinstance(handle.process, _DetachedProcess):
                # Preserve a local Popen's known exit code after OS process removal.
                return
            handle.process = observed.process

    def _load_persisted_tasks(self) -> TaskInventory:
        inventory = inspect_task_inventory(self.root)
        self._tasks = {handle.task_id: handle for handle in inventory.handles}
        return inventory

    def _recover_pending_launches(self, *, inventory: TaskInventory | None = None) -> None:
        """Compensate durable intents without creating another task identity."""
        # Validate the complete selected controller before touching any intent.
        # A malformed neighbour must not be silently skipped while another task
        # is spawned, and an unknown live worker is not a compatibility grant.
        if inventory is None:
            inventory = inspect_task_inventory(self.root)
        inventory.require_write_compatible()
        inventory.assert_unchanged()
        for handle in self._tasks.values():
            if handle.launch_state == "pending":
                self._require_worker_account_provider(handle.spec)
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
            try:
                self._refresh_handle_process(handle)
                if handle.launch_state == "active":
                    launch_lock.close()
                    continue
                require_launch_runtime(handle.launch_runtime, label=f"task {handle.task_id}")
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
        from .update_install import worker_lease
        # Acquire before spawn; the worker retains its own lifetime lease.
        update_lease = None
        log = None
        try:
            update_lease = worker_lease()
            log = log_path.open("ab", buffering=0)
            return self._popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(self.root),
                env=environment,
                pass_fds=(() if launch_lock is None else (launch_lock.fileno(),))
                         + (() if update_lease is None else (update_lease.fileno(),)),
                # Linux workers remain in the caller's supervised process group.
                start_new_session=not sys.platform.startswith("linux"),
            )
        finally:
            if log is not None:
                log.close()
            if update_lease is not None:
                update_lease.close()  # Never LOCK_UN an inherited worker lease.
            if launch_lock is not None:
                launch_lock.close()

    @staticmethod
    def _require_worker_account_provider(spec: AgentTaskSpec) -> None:
        if spec.managed_accounts is not None and spec.managed_account_provider is None:
            raise ValueError("account task workers require an explicitly installed account provider")

    def start(self, spec: AgentTaskSpec) -> TaskHandle:
        if not isinstance(spec, AgentTaskSpec):
            raise TypeError("TaskControl.start requires AgentTaskSpec")
        self._require_worker_account_provider(spec)
        inventory = inspect_task_inventory(self.root)
        inventory.require_write_compatible()
        inventory.assert_unchanged()
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
            process=_DetachedProcess(-1, -1, _pending=True),
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
        identity_error = None
        try:
            observed_return_code = handle.process.poll()
        except ProcessIdentityUnavailable as exc:
            observed_return_code = None
            identity_error = str(exc)
        return_code = (
            None if isinstance(handle.process, _DetachedProcess)
            else observed_return_code)
        process_status = (
            "EXITED" if registry_status is not None
            and registry_status.get("execution_status") == "terminal" else
            "STOPPED" if registry_status is not None
            and registry_status.get("execution_status") == "stopped_by_owner" else
            "STARTING" if handle.launch_state == "pending" else
            "UNKNOWN" if identity_error is not None else
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
        if identity_error is not None:
            result["process_identity_error"] = identity_error
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
        from .registry.run_authority import read_run_execution
        read = read_run_execution(
            core, _ResourceServiceKernel(core))
        terminal = core.event_store.canonical_object_rows(
            object_type="run_terminal_evidence/v1",
            through_ordinal=read.cut.view.through_ordinal)
        final = core.event_store.canonical_object_rows(
            object_type="final_result_index/v1",
            through_ordinal=read.cut.view.through_ordinal)
        result = {
            "task_ref": str(core.task_id),
            "execution_status": read.status,
            "checkpoint_ref": _ref_payload(read.checkpoint_ref),
            "execution_generation": read.execution_generation,
            "terminal_evidence_count": len(terminal),
            "final_result_index_count": len(final),
            "actual_model_call_counts": list(
                core.event_store.actual_model_call_counts()),
        }
        # Accounting remains cumulative; reject any advance during its queries.
        read.cut.assert_unchanged(core)
        return result

    def result_evidence(self, task_id: str) -> Mapping[str, Any]:
        """Inspect existing registered result/request facts without starting an owner."""
        from .agent_result_inspection import project_registry_agent_results
        return project_registry_agent_results(
            self.get(task_id).run_dir, catalog=agent_task_catalog())

    def snapshot(self, task_id: str) -> Mapping[str, Any]:
        handle = self.get(task_id)
        require_worker_interoperability(handle.worker_runtime, label=f"task {task_id}")
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
        from .registry.run_authority import (
            read_run_execution, read_run_terminal_bytes,
        )
        read = read_run_execution(
            core, _ResourceServiceKernel(core))
        terminal = read.terminal
        if terminal is None:
            raise RuntimeError("task has no registered terminal result")
        raw = read_run_terminal_bytes(
            core, read, max_bytes=terminal.result.size)
        result = {
            "task_id": task_id,
            "kind": handle.kind,
            "terminal_evidence_ref": _ref_payload(terminal.evidence_ref),
            "terminal_result_ref": _ref_payload(terminal.result_ref),
            "run_outcome": terminal.run_outcome,
            "execution_generation": read.execution_generation,
            "output": json.loads(raw),
            "actual_model_call_counts": list(
                core.event_store.actual_model_call_counts()),
        }
        read.cut.assert_unchanged(core)
        return result

    def message(
            self, task_id: str, body: str, *, target: str | None = None,
    ) -> Mapping[str, Any]:
        handle = self.get(task_id)
        require_worker_interoperability(handle.worker_runtime, label=f"task {task_id}")
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
        observation = (handle.launch_runtime if startup_safe and handle.launch_state == "pending"
                       and not isinstance(handle.process, _DetachedProcess) else handle.worker_runtime)
        require_worker_interoperability(observation, label=f"task {task_id}")
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
        self._require_worker_account_provider(handle.spec)
        inventory = inspect_task_inventory(self.root)
        inventory.require_write_compatible()
        inventory.assert_unchanged()
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
        self._require_worker_account_provider(handle.spec)
        inventory = inspect_task_inventory(self.root)
        inventory.require_write_compatible()
        inventory.assert_unchanged()
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
    from cpn.update_context import reject_candidate_runtime
    reject_candidate_runtime()
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
        record, _files, value = _read_task_record(root, manifest_path)
        if record.spec != spec:
            raise TaskAdmissionError("task worker spec changed before its launch claim")
        reopen = reopen_command_id is not None
        if reopen != (checkpoint_version_id is not None) or reopen != (
                reopen_reason is not None) or (reopen and resume):
            raise ValueError("task worker reopen intent is incomplete")
        expected_mode = "reopen" if reopen else ("resume" if resume else "fresh")
        if (record.task_id != task_id or record.launch_state != "pending"
                or record.launch_mode != expected_mode
                or record.resume_checkpoint_version_id != checkpoint_version_id
                or record.reopen_command_id != reopen_command_id
                or record.reopen_reason != reopen_reason):
            raise ValueError("task worker launch differs from durable intent")
        # Direct task_worker entry must obey the same unknown-pending gate as
        # parent recovery. Old stopped tasks may still be explicitly resumed;
        # an unqualified durable pending intent is never silently rebound here.
        actual_runtime = require_launch_runtime(record.launch_runtime, label=f"task {task_id}")
        version = value["schema_version"]
        persisted_run = record.run_dir
        pid = os.getpid()
        if sys.platform.startswith("linux"):
            ticks = _process_start_ticks(pid)
            identity = (None if ticks is None else {
                "kind": LINUX_IDENTITY, "start_ticks": ticks})
        else:
            identity = read_process_identity(pid)
        if identity is None:
            raise ProcessIdentityUnavailable("task worker process identity is unavailable")
        value["pid"] = pid
        if identity["kind"] == LINUX_IDENTITY and version != "rpnh/task_handle/v3":
            # Existing Linux runs keep their v1/v2 process metadata contract.
            value["process_start_ticks"] = identity["start_ticks"]
        else:
            if version == "rpnh/task_handle/v1":
                for old, new, base in (
                        ("spec_path", "spec_relative_path", root),
                        ("run_dir", "run_relative_path", root),
                        ("log_path", "log_relative_path", root),
                        ("socket_path", "socket_relative_path", persisted_run)):
                    value[new] = _relative_reference(Path(value.pop(old)).resolve(), base)
            value["schema_version"] = "rpnh/task_handle/v3"
            value.pop("process_start_ticks", None)
            value["process_identity"] = validate_process_identity(identity)
        value["launch_state"] = "active"
        value["worker_runtime"] = actual_runtime
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
    "owner_socket_path", "TaskAdmissionError", "TaskInventory", "inspect_task_inventory",
)
