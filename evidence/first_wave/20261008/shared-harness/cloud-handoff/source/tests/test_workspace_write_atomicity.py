from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

from cpn.components.agent_loop import workspace
from cpn.rpnh.registry.errors import (
    ResourceIntegrityFault,
    ResourcePayloadSchemaViolation,
    UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json


def _ref(entity_type: str, entity_kind: str, version_kind: str) -> VersionRef:
    return VersionRef(
        entity_type, new_id(entity_kind), new_id(version_kind))


class _Kernel:
    def __init__(self, publish, preflight=None):
        self.publish_bytes = publish
        self.preflight = preflight or (lambda _context, _command: None)

    def _preflight_publish(self, context, command):
        return self.preflight(context, command)

    def _firing_prepared(self, _context, _ref):
        raise AssertionError("the fixture has no previously written resources")

    def _firing_header(self, _context, _ref):
        raise AssertionError("the fixture has no previously written resources")


class _WriteProductHarness(workspace.WorkspaceExecutionMixin):
    def __init__(self, root: Path, publish, *, preflight=None,
                 schema="rpnh/test_result/v1"):
        self.root = root
        self.core = object()
        self.kernel = _Kernel(publish, preflight)
        port = SimpleNamespace(
            name="worker.result",
            port_id="port-result",
            schema=schema,
        )
        outcome = SimpleNamespace(
            name="complete",
            products=(SimpleNamespace(port=port.name),),
        )
        self.compiled = SimpleNamespace(ports=(port,))
        self.operation = SimpleNamespace(
            declaration=SimpleNamespace(
                outputs=(port.name,), outcomes=(outcome,)),
        )
        self.context = SimpleNamespace(
            activation_ref=None,
            invocation_ref=_ref(
                "invocation/v1", "invocation", "invocation_version"),
        )
        output_binding = SimpleNamespace(
            port_id=port.port_id,
            output_binding_ref=_ref(
                "output_binding/v1", "output_binding",
                "output_binding_version"),
            place="result",
        )
        self.execution = SimpleNamespace(operation=SimpleNamespace(
            operation_binding=SimpleNamespace(
                output_port_bindings=(output_binding,)),
            canonical=object(),
        ))
        self.loop = SimpleNamespace(written_resource_refs=())

    def _context(self, _loop):
        return self.context

    def _declared(self, _context):
        return object(), self.compiled, self.operation

    def _workspace_root(self, _loop):
        return self.root

    def _execute_file_materialization(
            self, _context, _identity_key, materialize):
        ref, = materialize(None)
        return (ref,)

    def write(
            self, *, path: str = "outputs/result.json",
            key: str = "atomic-write-fixture",
            content: str = '{"candidate":true}',
    ):
        return self._write_product(
            self.execution,
            self.loop,
            {
                "path": path,
                "description": "Atomic write fixture.",
                "content": content,
                "output_port_id": "worker.result",
                "outcome_id": "complete",
            },
            key,
        )


@pytest.mark.parametrize("prior", [b'\n{"prior":true}\n', None])
def test_schema_rejection_preserves_or_omits_target(
        tmp_path: Path, prior: bytes | None,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    if prior is not None:
        target.parent.mkdir(parents=True)
        target.write_bytes(prior)

    def reject(_context, _command):
        raise ResourcePayloadSchemaViolation("fixture schema rejection")

    service = _WriteProductHarness(
        tmp_path,
        lambda *_args: pytest.fail(
            "schema-rejected publication must not reach materialization"),
        preflight=reject,
    )
    with pytest.raises(ResourcePayloadSchemaViolation):
        service.write()

    if prior is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == prior
    assert service._workspace_files(tmp_path) == (
        (() if prior is None else ("outputs/result.json",)))
    assert not tuple(tmp_path.rglob(".rpnh-write-stage-*.tmp"))


def test_registry_publication_failure_preserves_prior_target(
        tmp_path: Path,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prior publication")

    def fail_publication(_context, _command):
        raise RuntimeError("simulated Registry publication failure")

    service = _WriteProductHarness(tmp_path, fail_publication)
    with pytest.raises(RuntimeError, match="Registry publication failure"):
        service.write()

    assert target.read_bytes() == b"prior publication"
    assert service._workspace_files(tmp_path) == ("outputs/result.json",)
    assert not tuple(tmp_path.rglob(".rpnh-write-stage-*.tmp"))


@pytest.mark.parametrize("destination_kind", ("directory", "fifo"))
def test_correctable_destination_type_rejection_precedes_execution_child(
        tmp_path: Path, destination_kind: str,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    target.parent.mkdir(parents=True)
    if destination_kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)

    service = _WriteProductHarness(
        tmp_path,
        lambda *_args: pytest.fail("rejected path must not publish"),
    )
    service._execute_file_materialization = lambda *_args, **_kwargs: (
        pytest.fail("rejected path must not attach an execution child"))

    with pytest.raises(
            ValueError, match="destination must be one regular file"):
        service.write()


def test_nul_path_rejection_precedes_execution_child(tmp_path: Path) -> None:
    service = _WriteProductHarness(
        tmp_path,
        lambda *_args: pytest.fail("rejected path must not publish"),
    )
    service._execute_file_materialization = lambda *_args, **_kwargs: (
        pytest.fail("rejected path must not attach an execution child"))

    with pytest.raises(ValueError, match="without NUL"):
        service.write(path="outputs/bad\x00.txt")


def test_corrected_destination_can_materialize_after_preflight_rejection(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected = tmp_path / "outputs" / "result.json"
    rejected.parent.mkdir(parents=True)
    rejected.mkdir()
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    service = _WriteProductHarness(
        tmp_path, lambda *_args: published_ref)
    calls = []
    original_execute = service._execute_file_materialization

    def execute(*args, **kwargs):
        calls.append(kwargs.get(
            "identity_key", args[1] if len(args) > 1 else None))
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(service, "_execute_file_materialization", execute)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)

    with pytest.raises(ValueError):
        service.write(key="rejected-directory")
    refs, result = service.write(
        path="outputs/good.json", key="corrected-file")

    assert calls == ["semantic-write:corrected-file"]
    assert refs == (published_ref.as_version_ref(),)
    assert result["path"] == "outputs/good.json"
    assert (tmp_path / "outputs" / "good.json").read_bytes() == (
        b'{"candidate":true}')


def test_destination_change_after_preflight_is_not_model_correctable(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    target.parent.mkdir(parents=True)
    target.mkdir()
    service = _WriteProductHarness(
        tmp_path,
        lambda *_args: pytest.fail("changed path must not publish"),
    )
    monkeypatch.setattr(
        service, "_preflight_workspace_destination",
        lambda *_args: None)

    with pytest.raises(
            ResourceIntegrityFault,
            match="changed after materialization admission"):
        service.write()


def test_success_publishes_before_atomic_replace_and_cleans_staging(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prior bytes")
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    observed_commands = []

    service = None

    def publish(_context, command):
        observed_commands.append(command)
        assert target.read_bytes() == b"prior bytes"
        assert service is not None
        assert service._workspace_files(tmp_path) == ("outputs/result.json",)
        staging = tuple((tmp_path / "registered_resources").glob(
            ".rpnh-write-stage-*.tmp"))
        assert len(staging) == 1
        return published_ref

    service = _WriteProductHarness(tmp_path, publish)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)

    refs, result = service.write()

    assert refs == (published_ref.as_version_ref(),)
    assert result["resource_ref"] == {
        "resource_id": str(published_ref.resource_id),
        "resource_version_id": str(published_ref.resource_version_id),
    }
    assert target.read_bytes() == b'{"candidate":true}'
    assert len(observed_commands) == 1
    assert observed_commands[0].payload == b'{"candidate":true}'
    assert not tuple(tmp_path.rglob(".rpnh-write-stage-*.tmp"))

    collision = target.parent / (
        workspace._WORKSPACE_STAGING_PREFIX + "0" * 32 + ".tmp")
    collision.write_bytes(b"user content")
    assert service._workspace_files(tmp_path) == (
        "outputs/.rpnh-write-stage-00000000000000000000000000000000.tmp",
        "outputs/result.json",
    )
    assert collision.read_bytes() == b"user content"


def test_atomic_writer_flushes_payload_before_publish_and_directories_after(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    events = []
    original_fsync = workspace.os.fsync
    original_replace = workspace.os.replace

    def observed_fsync(descriptor):
        kind = (
            "file-fsync"
            if workspace.stat.S_ISREG(workspace.os.fstat(descriptor).st_mode)
            else "directory-fsync")
        events.append(kind)
        return original_fsync(descriptor)

    def observed_replace(*args, **kwargs):
        events.append("replace")
        return original_replace(*args, **kwargs)

    def publish(_context, _command):
        events.append("publish")
        return published_ref

    monkeypatch.setattr(workspace.os, "fsync", observed_fsync)
    monkeypatch.setattr(workspace.os, "replace", observed_replace)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)

    _WriteProductHarness(tmp_path, publish).write()

    assert events[0] == "file-fsync"
    assert events.index("file-fsync") < events.index("publish")
    assert events.index("publish") < events.index("replace")
    assert "directory-fsync" in events[events.index("replace") + 1:]


def test_publish_success_replace_failure_can_replay_same_materialization(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "outputs" / "result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prior bytes")
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    publish_calls = []
    original_replace = workspace.os.replace

    def publish(_context, command):
        publish_calls.append(command.payload)
        return published_ref

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated atomic replace interruption")

    service = _WriteProductHarness(tmp_path, publish)
    monkeypatch.setattr(workspace.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace interruption"):
        service.write()
    assert target.read_bytes() == b"prior bytes"
    assert publish_calls == [b'{"candidate":true}']
    assert not tuple(tmp_path.rglob(".rpnh-write-stage-*.tmp"))

    monkeypatch.setattr(workspace.os, "replace", original_replace)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)
    refs, _result = service.write()
    assert refs == (published_ref.as_version_ref(),)
    assert target.read_bytes() == b'{"candidate":true}'
    assert publish_calls == [b'{"candidate":true}', b'{"candidate":true}']


def test_atomic_writer_keeps_traversal_and_symlink_rejections(
        tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    with pytest.raises(ValueError):
        workspace.WorkspaceExecutionMixin._write_workspace_bytes(
            root, "../escape.txt", b"rejected")

    (root / "parent-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        workspace.WorkspaceExecutionMixin._write_workspace_bytes(
            root, "parent-link/escape.txt", b"rejected")

    outside_target = outside / "target.txt"
    outside_target.write_bytes(b"outside")
    (root / "target-link").symlink_to(outside_target)
    with pytest.raises(OSError):
        workspace.WorkspaceExecutionMixin._write_workspace_bytes(
            root, "target-link", b"rejected")

    assert not (outside / "escape.txt").exists()
    assert outside_target.read_bytes() == b"outside"
    assert (root / "target-link").is_symlink()


@pytest.mark.parametrize("schema,content,expected_value,expected_workspace", (
    ("application/rpnh_agent_text/v1", "Plain text.\n第二行", "Plain text.\n第二行",
     "Plain text.\n第二行".encode("utf-8")),
    ("application/rpnh_agent_text/v1", '"Legacy quoted text."', "Legacy quoted text.",
     b"Legacy quoted text."),
    ("application/rpnh_agent_text/v1", '{"answer":true}', '{"answer":true}',
     b'{"answer":true}'),
    ("application/rpnh_agent_text/v1", '[1,"two"]', '[1,"two"]', b'[1,"two"]'),
    ("application/rpnh_agent_text/v1", "true", "true", b"true"),
    ("application/rpnh_agent_text/v1", "null", "null", b"null"),
    ("application/rpnh_agent_text/v1", "42", "42", b"42"),
    ("rpnh/test_result/v1", ' {"answer": true}\n', {"answer": True},
     b' {"answer": true}\n'),
    ("rpnh/test_result/v1", '[1,"two"]', [1, "two"], b'[1,"two"]'),
    ("rpnh/test_result/v1", '"Structured string."', "Structured string.",
     b"Structured string."),
))
def test_content_encoding_preserves_existing_schema_boundary(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        schema: str, content: str, expected_value, expected_workspace: bytes,
) -> None:
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    commands = []

    def publish(_context, command):
        commands.append(command)
        return published_ref

    service = _WriteProductHarness(tmp_path, publish, schema=schema)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)

    refs, result = service.write(content=content)

    assert refs == (published_ref.as_version_ref(),)
    assert result["registered"] is True
    assert (tmp_path / "outputs/result.json").read_bytes() == expected_workspace
    command, = commands
    assert command.content_schema_ref == schema
    assert json.loads(command.payload) == expected_value
    assert command.payload == (
        canonical_json(expected_value)
        if schema == "application/rpnh_agent_text/v1" else content.encode("utf-8"))
    assert command.derived_from == ()


@pytest.mark.parametrize("content", ("plain text", '{"unfinished":', '{} {}'))
def test_nontext_invalid_json_rejected_before_file_execution(
        tmp_path: Path, content: str,
) -> None:
    service = _WriteProductHarness(
        tmp_path, lambda *_args: pytest.fail("invalid JSON must not publish"),
        preflight=lambda *_args: pytest.fail("invalid JSON must not reach preflight"))
    service._execute_file_materialization = lambda *_args: pytest.fail(
        "invalid JSON must not attach or execute a file-write child")

    with pytest.raises(json.JSONDecodeError):
        service.write(content=content)

    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("content", ('{"answer":true}', '{"answer":"yes"}'))
def test_nontext_valid_json_still_obeys_declared_schema_before_file_execution(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    validator = Draft7Validator({
        "type": "object", "additionalProperties": False,
        "properties": {"answer": {"type": "boolean"}}, "required": ["answer"],
    })
    preflight_payloads = []

    def preflight(_context, command):
        preflight_payloads.append(command.payload)
        try:
            validator.validate(json.loads(command.payload))
        except ValidationError as exc:
            raise ResourcePayloadSchemaViolation("fixture schema rejection") from exc

    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    service = _WriteProductHarness(
        tmp_path, lambda *_args: published_ref, preflight=preflight)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)
    if isinstance(json.loads(content)["answer"], bool):
        service.write(content=content)
        assert (tmp_path / "outputs/result.json").read_text() == content
    else:
        service._execute_file_materialization = lambda *_args: pytest.fail(
            "schema rejection must not attach or execute a file-write child")
        with pytest.raises(ResourcePayloadSchemaViolation):
            service.write(content=content)
        assert not tuple(tmp_path.iterdir())
    assert preflight_payloads == [content.encode("utf-8")]


@pytest.mark.parametrize("authorized", (True, False))
def test_resource_source_requires_exact_delivery_without_workspace_fallback(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authorized: bool,
) -> None:
    source_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    published_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    target = tmp_path / "outputs/result.json"
    target.parent.mkdir()
    target.write_bytes(b'{"workspace":"not a source"}')
    commands = []
    deliveries = []

    def publish(_context, command):
        commands.append(command)
        return published_ref

    service = _WriteProductHarness(tmp_path, publish)

    def deliver(context, ref, purpose, key):
        deliveries.append((context, ref, purpose, key))
        if not authorized:
            raise UnauthorizedResourceDelivery("fixture source denied")
        return None, None, b'{"registered":"exact source"}'

    monkeypatch.setattr(service, "_deliver", deliver, raising=False)
    monkeypatch.setattr(workspace, "verify_resource", lambda *_args: None)
    arguments = {
        "path": "outputs/result.json", "description": "Resource source fixture.",
        "source_resource_ref": {
            "resource_id": str(source_ref.resource_id),
            "resource_version_id": str(source_ref.resource_version_id),
        },
        "output_port_id": "worker.result", "outcome_id": "complete",
    }
    if authorized:
        service._write_product(service.execution, service.loop, arguments, "source-write")
        assert target.read_bytes() == b'{"registered":"exact source"}'
        command, = commands
        assert command.payload == b'{"registered":"exact source"}'
        assert command.derived_from == (source_ref,)
    else:
        service._execute_file_materialization = lambda *_args: pytest.fail(
            "denied source must not attach or execute a file-write child")
        with pytest.raises(UnauthorizedResourceDelivery):
            service._write_product(
                service.execution, service.loop, arguments, "source-write")
        assert target.read_bytes() == b'{"workspace":"not a source"}'
        assert commands == []
    assert deliveries == [(service.context, source_ref, "tool_result", "source-write:source")]
