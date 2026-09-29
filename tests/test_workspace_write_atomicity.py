from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop import workspace
from cpn.rpnh.registry.errors import ResourcePayloadSchemaViolation
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef


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
    def __init__(self, root: Path, publish, *, preflight=None):
        self.root = root
        self.core = object()
        self.kernel = _Kernel(publish, preflight)
        port = SimpleNamespace(
            name="worker.result",
            port_id="port-result",
            schema="rpnh/test_result/v1",
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

    def write(self):
        return self._write_product(
            self.execution,
            self.loop,
            {
                "path": "outputs/result.json",
                "description": "Atomic write fixture.",
                "content": '{"candidate":true}',
                "output_port_id": "worker.result",
                "outcome_id": "complete",
            },
            "atomic-write-fixture",
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
