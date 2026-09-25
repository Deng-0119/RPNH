from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from cpn.components.execution_services import _RegisteredOptionalInputBinding
from cpn.llm_adapters.local_process import _run_bounded
from cpn.rpnh.llm_contracts import (
    LLMInputPortInterrupted,
    LLMInputResponseBytes,
)


@dataclass(frozen=True)
class _AttemptRef:
    version_id: str = "attempt-version"


@dataclass(frozen=True)
class _Attempt:
    attempt_ref: _AttemptRef = _AttemptRef()


class _Port:
    def __init__(self, response: LLMInputResponseBytes) -> None:
        self.response = response

    def request_once(self, attempt):
        return self.response


class _Gateway:
    def __init__(self) -> None:
        self.registered = None
        self.interrupted = None

    def prepare_optional_input_submission(self, execution, attempt):
        return None

    def register_optional_input_return(
            self, execution, attempt, response, *, status_code,
            external_request_id,
    ):
        self.registered = (
            execution, attempt, response, status_code, external_request_id)

    def record_optional_input_owner_interruption(
            self, execution, attempt, submission_state):
        self.interrupted = (execution, attempt, submission_state)


def test_registered_input_preserves_http_transport_observation() -> None:
    response = LLMInputResponseBytes(
        b'{"schema_version":"llm_response_envelope/v1"}',
        status_code=202,
        external_request_id="provider-request-1",
    )
    gateway = _Gateway()
    attempt = _Attempt()
    binding = _RegisteredOptionalInputBinding(
        gateway, _Port(response), "execution")

    returned = binding.request_once(attempt)

    assert returned is response
    assert gateway.registered == (
        "execution", attempt, bytes(response), 202, "provider-request-1")
    assert response.status_code == 202
    assert response.external_request_id == "provider-request-1"


def test_input_response_rejects_invalid_http_status() -> None:
    with pytest.raises(TypeError, match="metadata"):
        LLMInputResponseBytes(
            b"{}", status_code=99, external_request_id=None)


def test_registered_input_forwards_owner_interruption_without_raw_return() -> None:
    class _InterruptiblePort:
        def request_once(self, _attempt):
            raise AssertionError("interruptible port used noninterruptible path")

        @staticmethod
        def request_once_interruptible(
                _attempt, *, interruption_requested):
            assert interruption_requested() is True
            raise LLMInputPortInterrupted(submission_state="not_submitted")

    gateway = _Gateway()
    binding = _RegisteredOptionalInputBinding(
        gateway, _InterruptiblePort(), "execution", lambda: True)

    attempt = _Attempt()
    with pytest.raises(LLMInputPortInterrupted):
        binding.request_once(attempt)

    assert gateway.registered is None
    assert gateway.interrupted == ("execution", attempt, "not_submitted")


def test_local_process_input_stops_its_process_group_on_owner_interrupt(
        tmp_path: Path,
) -> None:
    interrupted = threading.Event()
    timer = threading.Timer(0.2, interrupted.set)
    timer.start()
    started = time.monotonic()
    try:
        return_code, response, failure, _stderr, submitted = _run_bounded(
            (
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.buffer.read(); time.sleep(30)",
            ),
            dict(os.environ),
            b"request",
            cwd=tmp_path,
            timeout_seconds=30,
            max_stdout_bytes=4096,
            interruption_requested=interrupted.is_set,
        )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 3
    assert response is None
    assert failure == "owner_interrupted"
    assert submitted is True
    assert return_code is not None


def test_local_process_runs_from_the_selected_registry_root(
        tmp_path: Path,
) -> None:
    return_code, response, failure, stderr, submitted = _run_bounded(
        (
            sys.executable,
            "-c",
            "import os,sys; sys.stdin.buffer.read(); print(os.getcwd())",
        ),
        dict(os.environ),
        b"request",
        cwd=tmp_path,
        timeout_seconds=5,
        max_stdout_bytes=4096,
    )

    assert return_code == 0
    assert failure is None
    assert stderr == b""
    assert submitted is True
    assert response is not None
    assert response.decode().strip() == str(tmp_path)
