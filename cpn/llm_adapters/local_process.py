"""Private subprocess implementation of the source-neutral input port."""

from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping

from cpn.rpnh.llm_contracts import (
    LLMCallAttempt,
    LLMInputPortFailure,
    LLMInputPortInterrupted,
    LLMInputResponseBytes,
)

from ._audit import PrivateAttemptAudit
from ._common import (
    AdapterConfigError,
    ResponseEnvelopeError,
    require_canonical_request_envelope,
    require_canonical_response_envelope,
)


SCHEMA_VERSION = "local_process_adapter_config/v1"
_CONFIG_FIELDS = {
    "schema_version", "adapter_kind", "model_condition", "argv",
    "probe_argv", "env", "inherit_env",
}
_IO_CHUNK_BYTES = 64 * 1024
_STDERR_TAIL_BYTES = 16 * 1024
_STDERR_EXCERPT_CHARS = 2 * 1024
_BRIDGE_FAILURE_PREFIX = "bridge: local_bridge_failure: "


def _local_failure_category(
        failure: str, *, stderr_excerpt: str | None = None,
) -> str:
    """Keep process/bridge faults distinct from response-protocol faults."""
    if failure == "response_protocol_invalid":
        return failure
    if (failure.startswith("bridge:")
            or (stderr_excerpt is not None
                and stderr_excerpt.startswith("bridge: local_bridge_failure:"))):
        return "local_bridge_failure"
    return "local_process_failure"


def _bridge_failure_code(stderr_excerpt: str | None) -> str | None:
    """Extract only the bridge's stable code, keeping its detail as evidence."""
    if (not isinstance(stderr_excerpt, str)
            or not stderr_excerpt.startswith(_BRIDGE_FAILURE_PREFIX)):
        return None
    payload = stderr_excerpt[len(_BRIDGE_FAILURE_PREFIX):]
    code, separator, _detail = payload.partition(" | ")
    if (not separator or not code or code != code.strip()
            or any(character.isspace() for character in code)):
        return None
    return code


def _local_failure_disposition(
        failure: str | None, *, request_submitted: bool,
) -> tuple[str, str]:
    """Classify a local provider result without losing submission evidence.

    A bridge process can exit before returning bytes even after its request was
    written to the subprocess.  The provider boundary treats that transient
    bridge failure as retryable; the submission state remains unknown so the
    audit does not claim that the external request was never sent.
    """
    if failure == "response_too_large":
        return "provider_failure", "response_observed"
    if failure in {None, "local_process_error", "timeout"}:
        return (
            "provider_retryable",
            "submission_unknown" if request_submitted else "not_submitted",
        )
    if request_submitted:
        return "submission_unknown", "submission_unknown"
    return "provider_retryable", "not_submitted"


def _text(value: object, *, label: str, allow_empty: bool = False) -> str:
    if (not isinstance(value, str) or "\x00" in value
            or (not allow_empty and (not value or value != value.strip()))):
        raise AdapterConfigError(f"{label} is invalid")
    return value


def _runtime_token(value: str) -> str:
    if value == "{python}":
        return str(Path(sys.executable).resolve())
    if value == "{codex}":
        resolved = shutil.which("codex")
        if resolved is None:
            raise AdapterConfigError(
                "Codex executable is unavailable; install codex-cli 0.155.0")
        return str(Path(resolved).resolve())
    if value == "{codex_response_schema}":
        return str(Path(__file__).with_name(
            "codex_subscription_response_format.v1.schema.json").resolve())
    return value


def _argv(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AdapterConfigError(f"{label} must be a nonempty array")
    result = tuple(_runtime_token(
        _text(item, label=f"{label} item", allow_empty=True))
        for item in value)
    if not Path(result[0]).is_absolute():
        resolved = shutil.which(result[0])
        if resolved is None:
            raise AdapterConfigError(
                f"{label}[0] is neither an absolute path nor an available command")
        result = (str(Path(resolved).resolve()), *result[1:])
    return result


def _env(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise AdapterConfigError("local process env must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = _text(raw_name, label="environment variable name")
        env_value = _text(
            raw_value, label=f"environment variable {name}",
            allow_empty=True)
        if (not name.replace("_", "a").isalnum()
                or name[0].isdigit() or "=" in name):
            raise AdapterConfigError("environment variable name is invalid")
        result[name] = env_value
    return result


def _inherited_env(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AdapterConfigError("inherit_env must be an array")
    result: list[str] = []
    for raw_name in value:
        name = _text(raw_name, label="inherited environment variable name")
        if (not name.replace("_", "a").isalnum()
                or name[0].isdigit() or "=" in name):
            raise AdapterConfigError(
                "inherited environment variable name is invalid")
        if name in result:
            raise AdapterConfigError(
                "inherit_env contains a duplicate environment variable")
        result.append(name)
    return tuple(result)


def _load_config(
        path: Path, model_condition: str,
) -> tuple[tuple[str, ...], dict[str, str]]:
    try:
        document: Any = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterConfigError(
            "local adapter config is unavailable or invalid") from exc
    if not isinstance(document, Mapping) or set(document) != _CONFIG_FIELDS:
        raise AdapterConfigError("local adapter config fields are not current")
    if (document.get("schema_version") != SCHEMA_VERSION
            or document.get("adapter_kind") != "local_process"
            or document.get("model_condition") != model_condition):
        raise AdapterConfigError("local adapter identity differs from selection")
    # probe_argv remains adapter-private config input but is not executable in
    # the source-neutral current path.
    _argv(document.get("probe_argv"), label="probe_argv")
    env = _env(document.get("env"))
    inherited_names = _inherited_env(document.get("inherit_env"))
    overlap = set(env).intersection(inherited_names)
    if overlap:
        raise AdapterConfigError(
            "env and inherit_env must not declare the same variable")
    for name in inherited_names:
        if name in os.environ:
            env[name] = os.environ[name]
    return _argv(document.get("argv"), label="argv"), env


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _wait_status_without_reaping(
        process: subprocess.Popen[bytes], *, deadline: float,
        interruption_requested: Callable[[], bool] | None = None,
) -> str:
    while True:
        if (interruption_requested is not None
                and interruption_requested()):
            return "interrupted"
        status = os.waitid(
            os.P_PID, process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if status is not None:
            return (
                "success"
                if status.si_code == os.CLD_EXITED and status.si_status == 0
                else "failure")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout"
        time.sleep(min(0.01, remaining))


def _run_bounded(
        argv: tuple[str, ...], env: Mapping[str, str], request_bytes: bytes,
        *, cwd: Path, timeout_seconds: int, max_stdout_bytes: int,
        interruption_requested: Callable[[], bool] | None = None,
) -> tuple[int | None, bytes | None, str | None, bytes, bool]:
    if interruption_requested is not None and interruption_requested():
        return None, None, "owner_interrupted", b"", False
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=dict(env), close_fds=True,
            cwd=str(cwd), shell=False, bufsize=0, start_new_session=True)
    except (OSError, ValueError):
        return None, None, "local_process_unavailable", b"", False
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _terminate(process)
        return None, None, "local_process_unavailable", b"", False
    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr_tail = bytearray()
    request_offset = 0
    request_submitted = False
    streams = (process.stdin, process.stdout, process.stderr)
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdout.fileno(), selectors.EVENT_READ, "stdout")
    selector.register(process.stderr.fileno(), selectors.EVENT_READ, "stderr")
    selector.register(process.stdin.fileno(), selectors.EVENT_WRITE, "stdin")
    deadline = time.monotonic() + timeout_seconds
    failure: str | None = None
    try:
        while selector.get_map():
            if (interruption_requested is not None
                    and interruption_requested()):
                failure = "owner_interrupted"
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            events = selector.select(min(remaining, 0.1))
            if not events:
                continue
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        written = os.write(
                            key.fd, request_bytes[request_offset:])
                        request_offset += written
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        request_offset = len(request_bytes)
                    if request_offset == len(request_bytes):
                        request_submitted = True
                        selector.unregister(key.fd)
                        process.stdin.close()
                else:
                    read_size = _IO_CHUNK_BYTES
                    if key.data == "stdout":
                        read_size = min(
                            read_size,
                            max_stdout_bytes + 1 - len(stdout))
                    if read_size <= 0:
                        failure = "response_too_large"
                        break
                    try:
                        chunk = os.read(key.fd, read_size)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fd)
                    elif key.data == "stdout":
                        stdout.extend(chunk)
                        if len(stdout) > max_stdout_bytes:
                            failure = "response_too_large"
                            break
                    else:
                        stderr_tail.extend(chunk)
                        if len(stderr_tail) > _STDERR_TAIL_BYTES:
                            del stderr_tail[:-_STDERR_TAIL_BYTES]
            if failure is not None:
                break
        if failure is not None:
            _terminate(process)
            return (
                process.returncode, None, failure, bytes(stderr_tail),
                request_submitted,
            )
        exit_status = _wait_status_without_reaping(
            process, deadline=deadline,
            interruption_requested=interruption_requested)
        if exit_status in {"timeout", "interrupted"}:
            _terminate(process)
            return (
                process.returncode, None,
                ("timeout" if exit_status == "timeout"
                 else "owner_interrupted"),
                bytes(stderr_tail),
                request_submitted,
            )
        if exit_status != "success":
            _terminate(process)
            return (
                process.returncode, None, "local_process_error",
                bytes(stderr_tail), request_submitted,
            )
        return_code = process.wait()
        return (
            return_code, bytes(stdout), None, bytes(stderr_tail),
            request_submitted,
        )
    except OSError:
        _terminate(process)
        return (
            process.returncode, None, "local_process_error",
            bytes(stderr_tail), request_submitted,
        )
    finally:
        selector.close()
        for stream in streams:
            try:
                if not stream.closed:
                    stream.close()
            except OSError:
                pass
        if process.returncode is None:
            _terminate(process)


def _stderr_excerpt(stderr_tail: bytes) -> str | None:
    text = stderr_tail.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return text[-_STDERR_EXCERPT_CHARS:]


class LocalProcessInputPort:
    def __init__(
            self, *, model_condition: str, max_output_tokens: int,
            timeout_seconds: int, max_response_bytes: int,
            config_path: Path, destination_run_root: Path,
    ) -> None:
        self._model_condition = model_condition
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._argv, self._env = _load_config(config_path, model_condition)
        self._destination_run_root = destination_run_root.resolve()
        self._audit = PrivateAttemptAudit(self._destination_run_root)
        self._lock = threading.Lock()
        self._closed = False

    def request_once(self, attempt: LLMCallAttempt) -> LLMInputResponseBytes:
        return self._request_once(attempt, interruption_requested=None)

    def request_once_interruptible(
            self, attempt: LLMCallAttempt, *,
            interruption_requested: Callable[[], bool],
    ) -> LLMInputResponseBytes:
        if not callable(interruption_requested):
            raise TypeError("local interruption probe must be callable")
        return self._request_once(
            attempt, interruption_requested=interruption_requested)

    def _request_once(
            self, attempt: LLMCallAttempt, *,
            interruption_requested: Callable[[], bool] | None,
    ) -> LLMInputResponseBytes:
        if not isinstance(attempt, LLMCallAttempt):
            raise TypeError("local input port requires LLMCallAttempt")
        with self._lock:
            if self._closed or attempt.model_condition != self._model_condition:
                self._audit.finish(
                    attempt,
                    route={"route_id": "local_process", "provider": "local_process", "backend": "subprocess", "outbound_model": self._model_condition},
                    outcome="adapter_not_submitted",
                    detail={"failure_category": "adapter_not_submitted", "retry_eligible": False, "recovery_disposition": "block_no_retry"})
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="local_adapter_closed_or_model_mismatch")
            try:
                require_canonical_request_envelope(
                    attempt.canonical_request_bytes,
                    expected_model_condition=self._model_condition,
                    expected_max_output_tokens=self._max_output_tokens)
            except ResponseEnvelopeError:
                self._audit.finish(
                    attempt,
                    route={"route_id": "local_process", "provider": "local_process", "backend": "subprocess", "outbound_model": self._model_condition},
                    outcome="adapter_not_submitted",
                    detail={"failure_category": "adapter_not_submitted", "retry_eligible": False, "recovery_disposition": "block_no_retry"})
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="local_request_protocol_invalid")
            audit_identity = {
                "route_id": "local_process",
                "provider": "local_process",
                "backend": "subprocess",
                "outbound_model": self._model_condition,
            }
            if not self._audit.reserve_submission(
                    attempt, route=audit_identity):
                raise LLMInputPortFailure(
                    "adapter_not_submitted", submission_state="not_submitted",
                    failure_code="local_duplicate_submission_prevented")
            return_code, stdout, failure, stderr_tail, request_submitted = (
                _run_bounded(
                self._argv, self._env, attempt.canonical_request_bytes,
                cwd=self._destination_run_root,
                timeout_seconds=self._timeout_seconds,
                max_stdout_bytes=min(
                    self._max_response_bytes, attempt.max_response_bytes),
                interruption_requested=interruption_requested))
            detail: dict[str, object] = {}
            if return_code is not None:
                detail["exit_code"] = return_code
            stderr_excerpt = _stderr_excerpt(stderr_tail)
            if stderr_excerpt is not None:
                detail["stderr_excerpt"] = stderr_excerpt
            if failure is not None or not stdout:
                if failure == "owner_interrupted":
                    submission_state = (
                        "submission_unknown"
                        if request_submitted else "not_submitted")
                    self._audit.finish(
                        attempt, route=audit_identity,
                        outcome="owner_interrupted",
                        detail={
                            **detail,
                            "failure_category": "owner_interrupted",
                            "submission_state": submission_state,
                            "failure_code": "owner_interrupted",
                            "retry_eligible": False,
                            "recovery_disposition": "owner_checkpoint",
                        })
                    raise LLMInputPortInterrupted(
                        submission_state=submission_state)
                category = _local_failure_category(
                    failure or "no_response", stderr_excerpt=stderr_excerpt)
                disposition, submission_state = _local_failure_disposition(
                    failure, request_submitted=request_submitted)
                bridge_code = _bridge_failure_code(stderr_excerpt)
                failure_code = (
                    f"{category}:{bridge_code}"
                    if bridge_code is not None
                    else f"{category}:{failure or 'no_response'}")
                self._audit.finish(
                    attempt, route=audit_identity,
                    outcome=failure or "no_response",
                    detail={**detail, "failure_category": category,
                            "submission_state": submission_state,
                            "failure_code": failure_code,
                            "retry_eligible": disposition == "provider_retryable",
                            "recovery_disposition": (
                                "provider_retry"
                                if disposition == "provider_retryable"
                                else "block_no_retry")})
                raise LLMInputPortFailure(
                    disposition, submission_state=submission_state,
                    failure_code=failure_code)
            try:
                require_canonical_response_envelope(stdout)
            except ResponseEnvelopeError as exc:
                self._audit.finish(
                    attempt, route=audit_identity,
                    outcome="protocol_failure",
                    detail={**detail, "failure_category": "response_protocol_invalid",
                            "failure_code": type(exc).__name__,
                            "submission_state": "response_observed",
                            "retry_eligible": False,
                            "recovery_disposition": "block_no_retry"})
                raise LLMInputPortFailure(
                    "protocol_rejected", submission_state="response_observed",
                    failure_code=type(exc).__name__)
            self._audit.finish(
                attempt, route=audit_identity,
                outcome="response_bytes_returned", detail=detail or None)
            return LLMInputResponseBytes(
                stdout, status_code=None, external_request_id=None)

    def close(self) -> None:
        with self._lock:
            self._closed = True


__all__ = ["AdapterConfigError", "LocalProcessInputPort", "SCHEMA_VERSION"]
