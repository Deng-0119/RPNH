"""Optional workspace, environment and numerical registered tool boundaries.

Historical growth producers remain isolated in runtime/historical_tool_executors.py;
this component contains no workflow or old runtime imports.
"""
from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cpn.rpnh.registry.resources import (
        CanonicalInvocationAuthority,
        PublishResource,
        ResourceVersionRef,
        TransitionFiringAuthority,
    )
    from cpn.rpnh.registry.models import VersionRef
    from cpn.components.agent_loop.tools import AgentToolCatalog




class RegisteredAgentActionAuthorityRequiredError(RuntimeError):
    """An agent action lacked its exact committed loop/turn authority."""


class BoundedNumericalExecutionError(ValueError):
    """The single bounded numerical worker did not return a finite result."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.provider_detail = (
            code if not detail or detail == code else f"{code}: {detail}")


class BoundedWorkspaceCommandError(ValueError):
    """The opaque workspace tool could not satisfy its mechanical bounds."""


class RuntimeEnvironmentFrameworkFault(RuntimeError):
    """The registered interpreter/environment or worker substrate is absent."""

    def __init__(
            self, code: str, *, mechanical_result: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code.split(":", 1)[0]
        self.mechanical_result = (
            dict(mechanical_result) if mechanical_result is not None else None)


RESEARCH_ENVIRONMENT_NAME = "research-exp"


@dataclass(frozen=True, slots=True)
class ExecutionEnvironmentIdentity:
    """One Registry-backed interpreter identity shared by execution tools."""

    environment_ref: "VersionRef"
    name: str
    python_executable: str
    python_prefix: str

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.models import VersionRef

        if (not isinstance(self.environment_ref, VersionRef)
                or self.name != RESEARCH_ENVIRONMENT_NAME
                or not isinstance(self.python_executable, str)
                or not os.path.isabs(self.python_executable)
                or not isinstance(self.python_prefix, str)
                or not os.path.isabs(self.python_prefix)):
            raise TypeError("execution environment identity is invalid")
        object.__setattr__(
            self, "python_executable", os.path.realpath(self.python_executable))
        object.__setattr__(self, "python_prefix", os.path.realpath(self.python_prefix))


@dataclass(frozen=True, slots=True)
class NumericalToolProfile:
    """Registry-supplied numerical boundaries; this module defines no defaults."""

    profile_ref: "VersionRef"
    environment_ref: "VersionRef"
    timeout_seconds: int | None
    memory_bytes: int
    process_limit: int
    source_size_bytes: int
    input_size_bytes: int

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.models import VersionRef

        if (not isinstance(self.profile_ref, VersionRef)
                or not isinstance(self.environment_ref, VersionRef)):
            raise TypeError("numerical profile requires exact Registry refs")
        if (self.timeout_seconds is not None
                and (isinstance(self.timeout_seconds, bool)
                     or not isinstance(self.timeout_seconds, int)
                     or self.timeout_seconds < 1)):
            raise ValueError(
                "numerical wall timeout must be null or a registered positive")
        values = (
            self.memory_bytes, self.process_limit, self.source_size_bytes,
            self.input_size_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
               for value in values):
            raise ValueError("numerical profile limits must be registered positives")

    def as_payload(self) -> dict[str, int | None]:
        """Return only the policy fields sourced by the runtime configuration."""

        return {
            "timeout_seconds": self.timeout_seconds,
            "memory_bytes": self.memory_bytes,
            "process_limit": self.process_limit,
            "source_size_bytes": self.source_size_bytes,
            "input_size_bytes": self.input_size_bytes,
        }


@dataclass(frozen=True, slots=True)
class AgentToolReadinessInputs:
    """Exact run-registered authorities needed by launcher preflight."""

    environment: ExecutionEnvironmentIdentity
    numerical_profile: NumericalToolProfile
    tool_catalog_ref: "ResourceVersionRef"
    tool_names: tuple[str, ...]
    external_resource_index_ref: "VersionRef"
    kb_package_ref: "VersionRef"
    kb_descriptor: Mapping[str, object]

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.models import VersionRef
        from cpn.rpnh.registry.resources import ResourceVersionRef

        if (not isinstance(self.environment, ExecutionEnvironmentIdentity)
                or not isinstance(self.numerical_profile, NumericalToolProfile)
                or self.numerical_profile.environment_ref
                != self.environment.environment_ref
                or not isinstance(self.tool_catalog_ref, ResourceVersionRef)
                or not isinstance(self.tool_names, tuple)
                or not self.tool_names
                or any(not isinstance(name, str) or not name
                       for name in self.tool_names)
                or len(set(self.tool_names)) != len(self.tool_names)
                or not isinstance(
                    self.external_resource_index_ref, VersionRef)
                or self.external_resource_index_ref.entity_type
                != "external_resource_index/v1"
                or not isinstance(self.kb_package_ref, VersionRef)
                or self.kb_package_ref.entity_type != "external_kb_package/v1"
                or not isinstance(self.kb_descriptor, Mapping)):
            raise TypeError("agent tool readiness inputs are invalid")


_WORKSPACE_SYSTEM_PATHS = (
    "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin",
    "/sbin", "/bin",
)
_WORKSPACE_VISIBLE_EXECUTABLES = (
    "bash", "python", "node", "npm", "npx", "git", "make", "gcc",
)


def _workspace_runtime_path(identity: ExecutionEnvironmentIdentity) -> str:
    """Expose the launch runtime without forwarding provider credentials."""

    if not isinstance(identity, ExecutionEnvironmentIdentity):
        raise RuntimeEnvironmentFrameworkFault(
            "execution_environment_identity_missing")
    entries = [os.path.dirname(identity.python_executable)]
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw or not os.path.isabs(raw) or raw.startswith("/mnt/"):
            continue
        resolved = os.path.realpath(raw)
        if os.path.isdir(resolved) and resolved not in entries:
            entries.append(resolved)
    for raw in _WORKSPACE_SYSTEM_PATHS:
        if raw not in entries:
            entries.append(raw)
    return os.pathsep.join(entries)


def workspace_runtime_capabilities(
        identity: ExecutionEnvironmentIdentity) -> dict[str, object]:
    """Return the same mechanical capability inventory used at execution."""

    runtime_path = _workspace_runtime_path(identity)
    executables = {
        name: resolved
        for name in _WORKSPACE_VISIBLE_EXECUTABLES
        for resolved in (shutil.which(name, path=runtime_path),)
        if resolved is not None
    }
    return {
        "shell": "/bin/bash --noprofile --norc -lc",
        "environment_name": identity.name,
        "environment_ref": {
            "entity_type": identity.environment_ref.entity_type,
            "logical_id": str(identity.environment_ref.entity_id),
            "version_id": str(identity.environment_ref.version_id),
        },
        "python_executable": identity.python_executable,
        "python_prefix": identity.python_prefix,
        "executables": executables,
        "working_directory": "current firing workspace root",
        "writable_scope": "current firing workspace tree",
        "agent_controls_internal_files_and_commands": True,
        "network_available": False,
        "filesystem_persists_across_turns": True,
        "revision_rule": (
            "the same live firing root persists across provider turns; incomplete "
            "work is not checkpoint permanence; exactly one full revision and "
            "changed-file inventory archive at firing settlement"),
    }


def model_visible_workspace_runtime_capabilities() -> str:
    """Render only the workspace facts needed for provider interaction."""

    return (
        "Workspace commands run in the current firing-private workspace. Files "
        "written there remain available while this responsibility is being "
        "completed; use write_file to register semantic outputs. Network access "
        "is unavailable."
    )


_LANDLOCK_WORKSPACE_BOOTSTRAP = r'''
import ctypes
import errno
import os
import resource
import sys

LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7fff0000
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_K = 0x00
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_RET = 0x06
SOCKET_SYSCALLS = (
    41,   # socket
    42,   # connect
    43,   # accept
    44,   # sendto
    45,   # recvfrom
    46,   # sendmsg
    47,   # recvmsg
    48,   # shutdown
    49,   # bind
    50,   # listen
    51,   # getsockname
    52,   # getpeername
    53,   # socketpair
    54,   # setsockopt
    55,   # getsockopt
    288,  # accept4
    299,  # recvmmsg
    307,  # sendmmsg
)

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446

LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    ]


class SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_uint16),
        ("filter", ctypes.POINTER(SockFilter)),
    ]


def checked_syscall(number, *arguments):
    result = libc.syscall(ctypes.c_long(number), *arguments)
    if result < 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    return result


def apply_workspace_write_boundary(workspace):
    abi = checked_syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        ctypes.c_void_p(), ctypes.c_size_t(0),
        ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION),
    )
    if abi < 1:
        raise OSError(errno.ENOSYS, "Landlock is unavailable")

    write_access = (
        LANDLOCK_ACCESS_FS_WRITE_FILE
        | LANDLOCK_ACCESS_FS_REMOVE_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_FILE
        | LANDLOCK_ACCESS_FS_MAKE_CHAR
        | LANDLOCK_ACCESS_FS_MAKE_DIR
        | LANDLOCK_ACCESS_FS_MAKE_REG
        | LANDLOCK_ACCESS_FS_MAKE_SOCK
        | LANDLOCK_ACCESS_FS_MAKE_FIFO
        | LANDLOCK_ACCESS_FS_MAKE_BLOCK
        | LANDLOCK_ACCESS_FS_MAKE_SYM
    )
    if abi >= 2:
        write_access |= LANDLOCK_ACCESS_FS_REFER
    if abi >= 3:
        write_access |= LANDLOCK_ACCESS_FS_TRUNCATE
    if abi >= 5:
        write_access |= LANDLOCK_ACCESS_FS_IOCTL_DEV

    ruleset_attr = RulesetAttr(write_access)
    ruleset_fd = checked_syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(ruleset_attr), ctypes.sizeof(ruleset_attr),
        ctypes.c_uint(0),
    )
    workspace_fd = -1
    null_fd = -1
    try:
        workspace_fd = os.open(
            workspace, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        path_attr = PathBeneathAttr(write_access, workspace_fd)
        checked_syscall(
            SYS_LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(path_attr), ctypes.c_uint(0),
        )
        # Common read-only developer tools still open /dev/null for writing.
        # This single device carries no project data and does not broaden the
        # writable filesystem beyond the firing workspace.
        null_fd = os.open("/dev/null", os.O_PATH | os.O_CLOEXEC)
        null_path_attr = PathBeneathAttr(
            LANDLOCK_ACCESS_FS_WRITE_FILE, null_fd)
        checked_syscall(
            SYS_LANDLOCK_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(null_path_attr), ctypes.c_uint(0),
        )
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        checked_syscall(
            SYS_LANDLOCK_RESTRICT_SELF,
            ctypes.c_int(ruleset_fd), ctypes.c_uint(0),
        )
    finally:
        if null_fd >= 0:
            os.close(null_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)
        os.close(ruleset_fd)


def apply_network_boundary():
    # The user-namespace runner already supplies a private network namespace.
    # Keep the same guarantee when the host denies unprivileged user namespaces
    # and the direct Landlock runner is used instead.
    if os.uname().machine != "x86_64":
        raise OSError(errno.ENOSYS, "workspace network boundary is unavailable")
    instructions = [
        SockFilter(BPF_LD | BPF_W | BPF_ABS, 0, 0, 0),
    ]
    for syscall_number in SOCKET_SYSCALLS:
        instructions.extend((
            SockFilter(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, syscall_number),
            SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM),
        ))
    instructions.append(SockFilter(BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW))
    filter_array = (SockFilter * len(instructions))(*instructions)
    program = SockFprog(len(instructions), filter_array)
    if libc.prctl(
            PR_SET_SECCOMP, SECCOMP_MODE_FILTER,
            ctypes.byref(program), 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
libc.prctl.restype = ctypes.c_int

start_fd = -1
try:
    (workspace_root, start_fd_text, memory_bytes_text,
     process_limit_text, opaque_script) = sys.argv[1:]
    start_fd = int(start_fd_text)
    memory_bytes = int(memory_bytes_text)
    process_limit = int(process_limit_text)
    if memory_bytes < 1 or process_limit < 1:
        raise ValueError("workspace resource limits must be positive")
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    apply_workspace_write_boundary(workspace_root)
    apply_network_boundary()
    # The parent interprets exactly one R byte, with no following F byte, as
    # proof that namespace and Landlock setup completed and Bash was entered.
    # If execve itself fails, the exception path appends F before exiting.
    os.write(start_fd, b"R")
    os.set_inheritable(start_fd, False)
    os.execve(
        "/bin/bash",
        [
            "/bin/bash", "--noprofile", "--norc", "-lc",
            opaque_script, "workspace-tool",
        ],
        os.environ,
    )
except BaseException as error:
    if start_fd >= 0:
        try:
            os.write(start_fd, b"F")
        except OSError:
            pass
    diagnostic = "workspace sandbox setup failed: {}\n".format(error)
    os.write(2, diagnostic.encode("utf-8", errors="replace"))
    os._exit(126)
'''


def _workspace_command_started(handshake: bytes) -> bool:
    """Accept only successful bootstrap-to-Bash handoff, never ``R`` + failure."""

    return handshake == b"R"


def _workspace_namespace_unavailable(stderr: bytes) -> bool:
    """Recognize the host user-namespace denial that permits the local runner."""

    return (
        b"/proc/self/uid_map" in stderr
        and b"Operation not permitted" in stderr
    )


def _workspace_process_tree_usage(root_pid: int) -> tuple[int, int]:
    """Return visible process count and RSS+swap bytes below one launcher."""

    rows: dict[int, tuple[int, int]] = {}
    try:
        entries = tuple(os.scandir("/proc"))
    except OSError:
        return 0, 0
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            pid = int(entry.name)
            fields: dict[str, str] = {}
            with open(
                    f"/proc/{pid}/status", "r", encoding="ascii",
                    errors="replace") as stream:
                for line in stream:
                    key, separator, value = line.partition(":")
                    if separator and key in {"PPid", "VmRSS", "VmSwap"}:
                        fields[key] = value.strip()
            parent_pid = int(fields["PPid"])
            resident_kib = sum(
                int(fields.get(name, "0 kB").split()[0])
                for name in ("VmRSS", "VmSwap"))
        except (KeyError, OSError, ValueError):
            continue
        rows[pid] = (parent_pid, resident_kib * 1024)

    descendants = {root_pid}
    while True:
        added = {
            pid for pid, (parent_pid, _memory) in rows.items()
            if parent_pid in descendants and pid not in descendants}
        if not added:
            break
        descendants.update(added)
    visible = tuple(pid for pid in descendants if pid in rows)
    return len(visible), sum(rows[pid][1] for pid in visible)


def execute_bounded_workspace_tool(
        *, script: str, cwd: str,
        timeout_seconds: int | None, max_output_bytes: int,
        environment: ExecutionEnvironmentIdentity,
        profile: NumericalToolProfile,
        interruption_requested: Callable[[], bool] | None = None,
        _use_user_namespace: bool = True,
) -> dict[str, object]:
    """Run an opaque Bash program within one write-confined workspace."""

    if (not isinstance(script, str)
            or not isinstance(cwd, str) or not os.path.isabs(cwd)
            or (timeout_seconds is not None
                and (isinstance(timeout_seconds, bool)
                     or not isinstance(timeout_seconds, int)
                     or timeout_seconds < 1))
            or isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or max_output_bytes < 1
            or not isinstance(profile, NumericalToolProfile)
            or profile.environment_ref != environment.environment_ref
            or (interruption_requested is not None
                and not callable(interruption_requested))):
        raise BoundedWorkspaceCommandError(
            "workspace_tool_contract_invalid")
    resolved_cwd = os.path.realpath(cwd)
    if not os.path.isdir(resolved_cwd):
        raise BoundedWorkspaceCommandError(
            "workspace_tool_cwd_invalid")

    start_read_fd, start_write_fd = os.pipe()
    try:
        runner = [
            environment.python_executable, "-I", "-S", "-c",
            _LANDLOCK_WORKSPACE_BOOTSTRAP,
            resolved_cwd, str(start_write_fd),
            str(profile.memory_bytes), str(profile.process_limit), script,
        ]
        command = (
            [
                "/usr/bin/unshare",
                "--user", "--map-root-user", "--net", "--pid", "--fork",
                "--kill-child=SIGKILL", "--",
                *runner,
            ]
            if _use_user_namespace else runner)
        process = subprocess.Popen(
            command,
            cwd=resolved_cwd,
            env={
                "HOME": resolved_cwd,
                "TMPDIR": resolved_cwd,
                "PATH": _workspace_runtime_path(environment),
                "CONDA_PREFIX": environment.python_prefix,
                "CONDA_DEFAULT_ENV": environment.name,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
            pass_fds=(start_write_fd,),
        )
    except OSError as exc:
        os.close(start_read_fd)
        os.close(start_write_fd)
        raise RuntimeEnvironmentFrameworkFault(
            f"workspace_worker_unavailable: {type(exc).__name__}: {exc}") \
            from exc
    try:
        os.close(start_write_fd)
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise BoundedWorkspaceCommandError(
                "workspace_tool_capture_unavailable")
        selector = selectors.DefaultSelector()
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
        status = "completed"
        output_truncated = False
        resource_limit_diagnostic: bytes | None = None
        try:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            deadline = (
                time.monotonic() + timeout_seconds
                if timeout_seconds is not None else None)
            while selector.get_map() or process.poll() is None:
                if (interruption_requested is not None
                        and interruption_requested()):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    status = "interrupted"
                    break
                process_count, process_memory_bytes = (
                    _workspace_process_tree_usage(process.pid))
                resource_status = (
                    "process_limited"
                    if process_count > profile.process_limit else
                    "memory_limited"
                    if process_memory_bytes > profile.memory_bytes else None)
                if resource_status is not None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    configured = (
                        profile.process_limit
                        if resource_status == "process_limited"
                        else profile.memory_bytes)
                    observed = (
                        process_count
                        if resource_status == "process_limited"
                        else process_memory_bytes)
                    resource_limit_diagnostic = (
                        "workspace resource limit exceeded: "
                        f"{resource_status}; configured={configured}; "
                        f"observed={observed}; process tree terminated\n"
                    ).encode("ascii")
                    break
                remaining = (
                    deadline - time.monotonic()
                    if deadline is not None else None)
                if remaining is not None and remaining <= 0:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    status = "timed_out"
                    break
                poll_seconds = (
                    min(remaining, 0.1) if remaining is not None else 0.1)
                for key, _mask in selector.select(poll_seconds):
                    chunk = os.read(key.fd, 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    captured_bytes = sum(
                        len(value) for value in streams.values())
                    remaining_output = max_output_bytes - captured_bytes
                    streams[key.fd].extend(chunk[:remaining_output])
                    if len(chunk) > remaining_output:
                        output_truncated = True
            if process.poll() is None:
                process.wait()
            exit_code = process.returncode
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    finally:
        try:
            os.set_blocking(start_read_fd, False)
            start_handshake = os.read(start_read_fd, 2)
        except BlockingIOError:
            start_handshake = b""
        os.close(start_read_fd)
    command_started = _workspace_command_started(start_handshake)
    stdout_bytes = bytes(streams[stdout_fd])
    stderr_bytes = bytes(streams[stderr_fd])
    if (_use_user_namespace and status != "interrupted" and not command_started
            and _workspace_namespace_unavailable(stderr_bytes)):
        return execute_bounded_workspace_tool(
            script=script,
            cwd=resolved_cwd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            environment=environment,
            profile=profile,
            interruption_requested=interruption_requested,
            _use_user_namespace=False,
        )
    if resource_limit_diagnostic is not None:
        diagnostic = resource_limit_diagnostic[:max_output_bytes]
        command_budget = max_output_bytes - len(diagnostic)
        kept_stdout = stdout_bytes[:command_budget]
        kept_stderr = stderr_bytes[
            :max(0, command_budget - len(kept_stdout))]
        if (len(kept_stdout) != len(stdout_bytes)
                or len(kept_stderr) != len(stderr_bytes)):
            output_truncated = True
        stdout_bytes = kept_stdout
        stderr_bytes = diagnostic + kept_stderr
    result = {
        "exit_code": exit_code,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "status": status,
        "output_truncated": output_truncated,
        "command_started": command_started,
    }
    if not command_started and status != "interrupted":
        detail = str(result["stderr"]).strip()
        raise RuntimeEnvironmentFrameworkFault(
            "workspace_command_not_started"
            + (f": {detail}" if detail else ""),
            mechanical_result=result)
    return result


_WORKSPACE_PREFLIGHT_LINE = "workspace-preflight-ready\n"


def preflight_workspace_execution(
        *, environment: ExecutionEnvironmentIdentity,
        profile: NumericalToolProfile,
        timeout_seconds: int | None,
) -> dict[str, object]:
    """Exercise the exact namespace/Landlock/Bash path without business data."""

    with tempfile.TemporaryDirectory(prefix="cpn-workspace-preflight-") as root:
        result = execute_bounded_workspace_tool(
            script=(
                "set -eu\n"
                "printf 'ready\\n' > .workspace-preflight\n"
                "test \"$(cat .workspace-preflight)\" = ready\n"
                "rm .workspace-preflight\n"
                "printf 'workspace-preflight-ready\\n'\n"
            ),
            cwd=root,
            timeout_seconds=timeout_seconds,
            max_output_bytes=4096,
            environment=environment,
            profile=profile,
        )
    if (result.get("status") != "completed"
            or result.get("exit_code") != 0
            or result.get("stdout") != _WORKSPACE_PREFLIGHT_LINE
            or result.get("stderr") != ""
            or result.get("output_truncated") is not False
            or result.get("command_started") is not True):
        raise RuntimeEnvironmentFrameworkFault(
            "workspace_preflight_failed: "
            f"status={result.get('status')}, exit_code={result.get('exit_code')}")
    return result


_NUMERICAL_WORKER_ERROR_CODES = frozenset({
    "numerical_attribute_error",
    "numerical_compute_absent",
    "numerical_execution_failed",
    "numerical_module_not_found",
    "numerical_non_finite",
    "numerical_source_invalid",
})

_NUMERICAL_WORKER = r'''
import builtins
import importlib.metadata as importlib_metadata
import contextlib
import json
import math
import os
import platform
import re
import resource
import socket
import sys
import traceback

# This worker captures distribution metadata only. Importing optional numerical
# libraries here incorrectly makes ordinary agent startup depend on them and
# executes third-party initialization before the worker's bounds are applied.

class WorkerDiagnostic(Exception):
    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail or code

def deny_network(*args, **kwargs):
    raise PermissionError("network disabled in bounded numerical worker")

socket.socket = deny_network
socket.create_connection = deny_network
socket.getaddrinfo = deny_network
socket.gethostbyname = deny_network

compute_started = False
import_in_progress = False

def ordinary_import(name, globals=None, locals=None, fromlist=(), level=0):
    global import_in_progress
    prior = import_in_progress
    import_in_progress = True
    try:
        return builtins.__import__(
            name, globals, locals, fromlist, level)
    finally:
        import_in_progress = prior

ordinary_builtins = dict(builtins.__dict__)
ordinary_builtins["__import__"] = ordinary_import

def audit(event, args):
    if (event.startswith("socket.") or event.startswith("subprocess.")
            or event in {"os.fork", "os.forkpty", "os.posix_spawn", "os.system"}):
        raise PermissionError("external process/network disabled")
    if (compute_started and not import_in_progress
            and event in {"open", "os.listdir", "os.scandir"}):
        raise PermissionError("host file access disabled")

sys.addaudithook(audit)

def apply_limits(limits):
    memory_bytes = limits["memory_bytes"]
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    if hasattr(resource, "RLIMIT_NPROC"):
        process_limit = limits["process_limit"]
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))

def canonical_distribution_name(name):
    return re.sub(r"[-_.]+", "-", name.casefold())

def execution_environment_inventory(environment_name):
    distributions = {}
    for distribution in importlib_metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if isinstance(name, str) and name and isinstance(version, str) and version:
            canonical = canonical_distribution_name(name)
            distributions[canonical] = {
                "kind": "python_distribution",
                "name": canonical,
                "version": version,
            }
    return {
        "kind": "execution_environment_inventory/v1",
        "environment_name": environment_name,
        "python_version": platform.python_version(),
        "inventory_complete": True,
        "installed_resources": [distributions[name]
                                for name in sorted(distributions)],
    }

try:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    limits = request["limits"]
    environment_name = request["environment_name"]
    apply_limits(limits)
    if request.get("operation") == "capture_execution_environment_inventory":
        result = {
            "status": "ok",
            "result": execution_environment_inventory(environment_name),
        }
    else:
        raise WorkerDiagnostic(
            "numerical_operation_unknown",
            "the numerical worker only captures the environment inventory")
except WorkerDiagnostic as exc:
    result = {"status": "error", "code": exc.code, "detail": exc.detail}
except SyntaxError as exc:
    result = {
        "status": "error", "code": "numerical_source_invalid",
        "detail": f"line {exc.lineno}: {exc.msg}",
    }
except ModuleNotFoundError as exc:
    result = {
        "status": "error", "code": "numerical_module_not_found",
        "detail": f"module {exc.name!r} is unavailable",
    }
except AttributeError as exc:
    result = {
        "status": "error", "code": "numerical_attribute_error",
        "detail": str(exc),
    }
except BaseException as exc:
    frames = traceback.extract_tb(exc.__traceback__)
    frame = next((item for item in reversed(frames)
                  if item.filename == "<run_numerical>"), None)
    location = f"line {frame.lineno}: " if frame is not None else ""
    message = str(exc).strip()
    result = {
        "status": "error", "code": "numerical_execution_failed",
        "detail": (f"{location}{type(exc).__name__}"
                   + (f": {message}" if message else "")),
    }
payload = json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
sys.stdout.buffer.write(payload)
'''


def _execute_numerical_worker(
        request: Mapping[str, Any], *,
        environment: ExecutionEnvironmentIdentity,
        profile: NumericalToolProfile,
        timeout_seconds: int | None = None,
        result_path: str | None = None,
) -> Any:
    """Run the sole worker with the exact registered runtime identity."""

    if (not isinstance(profile, NumericalToolProfile)
            or profile.environment_ref != environment.environment_ref):
        raise RuntimeEnvironmentFrameworkFault(
            "numerical_profile_environment_mismatch")
    result_fd: int | None = None
    temporary_path: str | None = None
    destination_path: str | None = None
    if result_path is not None:
        if not isinstance(result_path, str) or not os.path.isabs(result_path):
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_result_path_invalid")
        destination_path = os.path.realpath(result_path)
        destination_parent = os.path.dirname(destination_path)
        try:
            os.makedirs(destination_parent, exist_ok=True)
            result_fd, temporary_path = tempfile.mkstemp(
                prefix=".numerical-result-", dir=destination_parent)
        except OSError as exc:
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_result_transport_unavailable: "
                f"{type(exc).__name__}: {exc}") from exc
    closed_request = {
        **dict(request),
        "environment_name": environment.name,
        "limits": {
            "memory_bytes": profile.memory_bytes,
            "process_limit": profile.process_limit,
        },
    }
    if result_fd is not None:
        closed_request["result_fd"] = result_fd
    try:
        try:
            request_payload = json.dumps(
                closed_request, ensure_ascii=True, allow_nan=False,
                sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BoundedNumericalExecutionError(
                "numerical_input_invalid") from exc
        try:
            process = subprocess.Popen(
                [environment.python_executable, "-I", "-B", "-c",
                 _NUMERICAL_WORKER],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env={}, cwd="/", start_new_session=True,
                pass_fds=((result_fd,) if result_fd is not None else ()))
            try:
                stdout, stderr = process.communicate(
                    input=request_payload, timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    process.kill()
                process.communicate()
                raise BoundedNumericalExecutionError(
                    "numerical_timeout",
                    f"execution exceeded {timeout_seconds}s") from exc
        except OSError as exc:
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_worker_unavailable: "
                f"{type(exc).__name__}: {exc}") from exc
        finally:
            if result_fd is not None:
                try:
                    os.fsync(result_fd)
                except OSError:
                    pass
                os.close(result_fd)
    except BaseException:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
        raise
    try:
        if process.returncode != 0:
            detail = f"worker exited with status {process.returncode}"
            stderr_lines = stderr.decode("utf-8", errors="replace").splitlines()
            if stderr_lines:
                detail += f"; stderr: {stderr_lines[-1]}"
            if process.returncode < 0:
                raise BoundedNumericalExecutionError(
                    "numerical_resource_limit", detail)
            raise RuntimeEnvironmentFrameworkFault(
                f"numerical_worker_failed: {detail}")
        try:
            document = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_worker_invalid_json: "
                f"worker returned invalid JSON: {type(exc).__name__}: {exc}") from exc
        if not isinstance(document, dict):
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_worker_invalid_response")
        if document.get("status") == "error":
            code = document.get("code")
            detail = document.get("detail")
            if (set(document) != {"status", "code", "detail"}
                    or code not in _NUMERICAL_WORKER_ERROR_CODES
                    or not isinstance(detail, str) or not detail):
                raise RuntimeEnvironmentFrameworkFault(
                    "numerical_worker_invalid_error")
            raise BoundedNumericalExecutionError(code, detail)
        if result_path is None:
            if (document.get("status") != "ok"
                    or set(document) != {"status", "result"}):
                raise RuntimeEnvironmentFrameworkFault(
                    "numerical_worker_invalid_success")
            return document["result"]
        if (document.get("status") != "ok"
                or set(document) != {"status", "result_size_bytes"}
                or isinstance(document.get("result_size_bytes"), bool)
                or not isinstance(document.get("result_size_bytes"), int)
                or document["result_size_bytes"] < 1
                or temporary_path is None or destination_path is None
                or os.path.getsize(temporary_path)
                != document["result_size_bytes"]):
            raise RuntimeEnvironmentFrameworkFault(
                "numerical_worker_invalid_success")
        os.replace(temporary_path, destination_path)
        temporary_path = None
        return {
            "kind": "bounded_numerical_result/v1",
            "status": "ok",
            "result_size_bytes": document["result_size_bytes"],
        }
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _validated_execution_environment_inventory(
        inventory: Mapping[str, Any],
        environment: ExecutionEnvironmentIdentity,
) -> dict[str, Any]:
    if (not isinstance(inventory, Mapping)
            or set(inventory) != {
                "kind", "environment_name", "python_version",
                "inventory_complete", "installed_resources"}
            or inventory.get("kind") != "execution_environment_inventory/v1"
            or inventory.get("environment_name") != environment.name
            or not isinstance(inventory.get("python_version"), str)
            or not inventory["python_version"]
            or inventory.get("inventory_complete") is not True
            or not isinstance(inventory.get("installed_resources"), list)):
        raise RuntimeEnvironmentFrameworkFault(
            "execution_environment_inventory_invalid")
    installed = inventory["installed_resources"]
    if (any(not isinstance(item, Mapping)
            or set(item) != {"kind", "name", "version"}
            or item.get("kind") != "python_distribution"
            or not isinstance(item.get("name"), str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["name"])
            is None
            or item["name"] != re.sub(r"[-_.]+", "-", item["name"].casefold())
            or not isinstance(item.get("version"), str)
            or not item["version"]
            for item in installed)
            or [item["name"] for item in installed]
            != sorted(item["name"] for item in installed)
            or len({item["name"] for item in installed}) != len(installed)):
        raise RuntimeEnvironmentFrameworkFault(
            "execution_environment_inventory_invalid")
    return {
        "kind": "execution_environment_inventory/v1",
        "environment_name": environment.name,
        "python_version": inventory["python_version"],
        "inventory_complete": True,
        "installed_resources": [dict(item) for item in installed],
    }


def capture_execution_environment_inventory(
        *, environment: ExecutionEnvironmentIdentity,
        profile: NumericalToolProfile) -> dict[str, Any]:
    """Capture the registered research-exp distribution inventory once."""

    value = _execute_numerical_worker({
        "operation": "capture_execution_environment_inventory",
    }, environment=environment, profile=profile,
        timeout_seconds=profile.timeout_seconds)
    return _validated_execution_environment_inventory(value, environment)


def query_execution_environment_resources(
        *, inventory: Mapping[str, Any],
        environment: ExecutionEnvironmentIdentity,
        packages: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Filter a previously registered execution-environment inventory."""

    supplied = tuple(packages) or ("numpy", "scipy")
    if any(not isinstance(name, str)
                   or re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", name)
                   is None for name in supplied):
        raise BoundedNumericalExecutionError("numerical_input_invalid")
    requested = tuple(sorted({
        re.sub(r"[-_.]+", "-", name) for name in supplied}))
    if len(requested) > 16:
        raise BoundedNumericalExecutionError("numerical_input_invalid")
    registered = _validated_execution_environment_inventory(
        inventory, environment)
    distributions = {
        item["name"]: item for item in registered["installed_resources"]}
    installed = [distributions[name] for name in requested
                 if name in distributions]
    not_installed = [name for name in requested if name not in distributions]
    return {
        "kind": "execution_environment_resources/v1",
        "environment_name": registered["environment_name"],
        "python_version": registered["python_version"],
        "inventory_complete": False,
        "requested_packages": list(requested),
        "installed_resources": installed,
        "not_installed": not_installed,
        "allowed_by_tool": ["math", "numpy", "scipy"],
    }


















@dataclass(frozen=True, slots=True)
class PreparedRegisteredAgentAction:
    """One independently validated action, retained in tool-call order."""

    tool_call: Any
    validation: Any
    raw_arguments: str | None
    timing_evidence: Any = field(default=None, compare=False, repr=False)


def prepare_registered_agent_actions(
    *, loop_id: str, turn_sequence: int, expected_revision: int,
    tool_calls: tuple[Any, ...],
    tool_argument_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    timing_origin_ns: int | None = None,
) -> tuple[PreparedRegisteredAgentAction, ...]:
    """Validate every observed call independently without short-circuiting."""
    from cpn.components.agent_loop.models import (
        AgentActionTimingEvidence,
        record_timing_offset,
    )
    from cpn.components.agent_loop.tools import (
        AgentToolSyntaxError,
        validate_agent_tool_observation,
    )
    from cpn.rpnh.response_protocol import LLMToolCallObservation

    calls = tuple(tool_calls)
    if any(not isinstance(call, LLMToolCallObservation) for call in calls):
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent actions require LLM tool-call observations")
    prepared: list[PreparedRegisteredAgentAction] = []
    for call in calls:
        timing = AgentActionTimingEvidence(
            action_id=None,
            tool_call_ordinal=call.tool_call_ordinal,
            tool_name=call.tool_name,
        )
        record_timing_offset(
            timing, "tool_validation_start", timing_origin_ns)
        validation = validate_agent_tool_observation(
            loop_id=loop_id,
            turn_sequence=turn_sequence,
            expected_revision=expected_revision,
            observation=call,
            tool_argument_schemas=tool_argument_schemas,
        )
        record_timing_offset(
            timing, "tool_validation_finish", timing_origin_ns)
        try:
            timing.action_id = validation.action_id
            timing.validation_prevented_dispatch = isinstance(
                validation, AgentToolSyntaxError)
        except Exception:
            pass
        prepared.append(PreparedRegisteredAgentAction(
            tool_call=call,
            validation=validation,
            raw_arguments=call.raw_arguments,
            timing_evidence=timing,
        ))
    return tuple(prepared)


def execute_registered_agent_actions(
    *, registry: Any, execution: Any = None, loop: Any, turn: Any,
    tool_calls: tuple[Any, ...],
    parent_tool_catalog: "AgentToolCatalog",
    permitted_tool_names: tuple[str, ...], idempotency_key: str,
) -> tuple[Any, tuple[Any, ...]]:
    """Settle all LLM tool-call actions independently in tool-call order.

    The Registry transaction records one settlement per action.  A malformed
    action receives only its own ``ACTION_REJECTED`` settlement and observable
    tool error; valid or malformed siblings remain independently settled.
    """
    from cpn.components.agent_loop.models import (
        AgentActionRecord,
        AgentLoopSnapshot,
        AgentLoopState,
        AgentTurnRecord,
    )
    from cpn.components.agent_loop.service import AgentLoopRegistryPort
    from cpn.components.agent_loop.tools import (
        AgentToolCatalog,
        AgentToolSyntaxError,
        ValidatedAgentToolAction,
        derive_atomic_subtask_tools,
    )

    from cpn.components.operation_gateway import has_port_methods
    if not has_port_methods(registry, AgentLoopRegistryPort):
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent actions require the Registry v1 port")
    if (not isinstance(loop, AgentLoopSnapshot)
            or loop.state != AgentLoopState.TURN_STORED):
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent actions require the committed TURN_STORED snapshot")
    if (not isinstance(turn, AgentTurnRecord)
            or turn.loop_id != loop.loop_id
            or turn.sequence + 1 != loop.next_turn_sequence):
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent actions require the exact current turn")
    if not isinstance(parent_tool_catalog, AgentToolCatalog):
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent actions require the admitted parent catalog")
    global_tool_names = tuple(sorted(parent_tool_catalog.tool_names))
    atomic_subtask_tool_names = tuple(sorted(
        str(tool["name"])
        for tool in derive_atomic_subtask_tools(parent_tool_catalog)))
    if permitted_tool_names not in {
            global_tool_names, atomic_subtask_tool_names}:
        raise RegisteredAgentActionAuthorityRequiredError(
            "agent action permission differs from the closed catalog")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("agent actions require an idempotency key")
    prepared = prepare_registered_agent_actions(
        loop_id=loop.loop_id, turn_sequence=turn.sequence,
        expected_revision=loop.revision,
        tool_calls=tuple(tool_calls),
        tool_argument_schemas=parent_tool_catalog.argument_schemas)
    if not prepared:
        raise RegisteredAgentActionAuthorityRequiredError(
            "zero-call turns must use the text-only continuation path")
    settled_loop, records = registry.settle_agent_turn_actions_v1(
        loop, turn, prepared,
        permitted_tool_names=permitted_tool_names,
        idempotency_key=idempotency_key, execution=execution)
    records = tuple(records)
    if (not isinstance(settled_loop, AgentLoopSnapshot)
            or len(records) != len(prepared)
            or any(not isinstance(record, AgentActionRecord)
                   for record in records)):
        raise RegisteredAgentActionAuthorityRequiredError(
            "Registry returned an incomplete action settlement set")
    for prepared_action, record in zip(prepared, records):
        validation = prepared_action.validation
        if (record.action_id != validation.action_id
                or record.tool_call_ordinal
                != prepared_action.tool_call.tool_call_ordinal
                or record.tool_call_id
                != prepared_action.tool_call.tool_call_id
                or record.action_identity_kind
                != prepared_action.tool_call.action_identity_kind
                or record.tool_name
                != prepared_action.tool_call.tool_name):
            raise RegisteredAgentActionAuthorityRequiredError(
                "Registry changed tool-call action identity or order")
        if isinstance(validation, AgentToolSyntaxError):
            if (record.state != AgentLoopState.ACTION_REJECTED
                    or record.tool_error_ref is None
                    or record.result_refs):
                raise RegisteredAgentActionAuthorityRequiredError(
                    "malformed action lacks its own rejected settlement/error")
    return settled_loop, records


__all__ = [
    "BoundedNumericalExecutionError",
    "BoundedWorkspaceCommandError",
    "ExecutionEnvironmentIdentity",
    "NumericalToolProfile",
    "RESEARCH_ENVIRONMENT_NAME",
    "RuntimeEnvironmentFrameworkFault",
    "RegisteredAgentActionAuthorityRequiredError",
    "PreparedRegisteredAgentAction",
    "capture_execution_environment_inventory",
    "execute_registered_agent_actions",
    "execute_bounded_workspace_tool",
    "prepare_registered_agent_actions",
    "query_execution_environment_resources",
    "workspace_runtime_capabilities",
]
