from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.optional_execution import (
    OPTIONAL_TOOL_BINDINGS,
    OptionalAgentLoopRegistryService,
)
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity,
    NumericalToolProfile,
    execute_bounded_workspace_tool,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef


def test_rpnh_workspace_tool_exposes_only_supported_sync_mode() -> None:
    _implementation, declaration = OPTIONAL_TOOL_BINDINGS["workspace"]
    contract = declaration["contracts"]

    assert contract["arguments"]["properties"]["execution_mode"]["enum"] == [
        "sync"]
    assert "monitored" not in json.dumps(
        contract, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("raw_payload", (
    b'{"text":"line1\nline2"}',
    b'"quoted workspace text"',
))
def test_schema_less_workspace_json_text_rebuilds_settled_read_history(
        raw_payload: bytes) -> None:
    resource_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    context = object()

    class _Kernel:
        @staticmethod
        def _read_firing_registered(_context, ref):
            assert _context is context
            assert ref == resource_ref
            return raw_payload

        @staticmethod
        def _firing_prepared(_context, ref):
            assert _context is context
            assert ref == resource_ref
            return SimpleNamespace(metadata={
                "origin_kind": "workspace_write",
                "media_type": "application/json",
                "content_schema_ref": None,
            })

    service = object.__new__(OptionalAgentLoopRegistryService)
    service.kernel = _Kernel()

    assert service._decoded_read(
        context, resource_ref, {"offset_chars": 0, "max_chars": 32768},
    ) == raw_payload.decode("utf-8")


def test_workspace_read_rejects_non_utf8_before_delivery() -> None:
    resource_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    context = object()
    delivered = False

    class _Kernel:
        @staticmethod
        def _read_firing_registered(_context, ref):
            assert _context is context
            assert ref == resource_ref
            return b"\xff\xfe"

        @staticmethod
        def _firing_prepared(_context, ref):
            assert _context is context
            assert ref == resource_ref
            return SimpleNamespace(metadata={
                "origin_kind": "workspace_write",
                "media_type": "application/octet-stream",
                "content_schema_ref": None,
            })

    service = object.__new__(OptionalAgentLoopRegistryService)
    service.kernel = _Kernel()
    service._context = lambda _loop: context
    service._project_workspace = lambda _execution, _loop: (
        SimpleNamespace(
            sandbox_path="outputs/binary.dat",
            resource_ref=resource_ref),
    )

    def unexpected_delivery(*_args):
        nonlocal delivered
        delivered = True
        raise AssertionError("invalid text must not be delivered")

    service._deliver = unexpected_delivery

    with pytest.raises(
            ValueError, match="read_file requires registered UTF-8 text"):
        service._read_input(
            object(), object(), {"path": "outputs/binary.dat"}, "read")

    assert delivered is False


def test_workspace_owner_interruption_terminates_the_process_group(
        tmp_path: Path,
) -> None:
    environment_ref = VersionRef(
        "execution_environment_identity/v1",
        new_id("execution_environment"),
        new_id("execution_environment_version"),
    )
    profile_ref = VersionRef(
        "numerical_tool_profile/v1",
        new_id("numerical_tool_profile"),
        new_id("numerical_tool_profile_version"),
    )
    environment = ExecutionEnvironmentIdentity(
        environment_ref, "research-exp", sys.executable, sys.prefix)
    profile = NumericalToolProfile(
        profile_ref, environment_ref, 30,
        512 * 1024 * 1024, 16, 1024 * 1024, 1024 * 1024)
    interrupted = threading.Event()
    timer = threading.Timer(0.2, interrupted.set)
    timer.start()
    started = time.monotonic()
    try:
        result = execute_bounded_workspace_tool(
            script="sleep 30",
            cwd=str(tmp_path),
            timeout_seconds=30,
            max_output_bytes=4096,
            environment=environment,
            profile=profile,
            interruption_requested=interrupted.is_set,
            _use_user_namespace=False,
        )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 3
    assert result["status"] == "interrupted"
    assert result["exit_code"] != 0
