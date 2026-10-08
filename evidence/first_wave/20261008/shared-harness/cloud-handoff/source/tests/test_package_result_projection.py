"""Local result serialization unit checks, not a native-owner execution proof."""
from types import SimpleNamespace
import hashlib

import pytest

from cpn.rpnh.collaboration.environment_cli import main as environment_main
from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError
from cpn.rpnh.collaboration.environment_host import (
    TERMINAL_RESULT_MAX_BYTES, _bounded_terminal_json, _terminal_result,
    host_request,
)


def _material(tmp_path, raw, media_type="application/json", size=None):
    path = tmp_path / "exact-result"
    path.write_bytes(raw)
    prepared = SimpleNamespace(size=len(raw) if size is None else size,
        media_type=media_type, version_id="unit-version", storage_locator="unit-locator")
    store = SimpleNamespace(validate_envelope=lambda value: None,
        locator_for_version=lambda version: "unit-locator",
        path_for_version=lambda version: path)
    from cpn.rpnh.registry.object_store import ObjectStore
    store.read_registered = lambda prepared, **kwargs: ObjectStore.read_registered(store, prepared, **kwargs)
    return SimpleNamespace(object_store=store), prepared


def test_bounded_terminal_json_serializes_exact_bytes(tmp_path):
    raw = b'{"value":5,"label":"example"}'
    core, prepared = _material(tmp_path, raw)
    assert _bounded_terminal_json(core, prepared) == {
        "status": "available", "content_sha256": hashlib.sha256(raw).hexdigest(),
        "output": {"value": 5, "label": "example"}}


@pytest.mark.parametrize("raw", [b'{"x":NaN}', b'{"x":1,"x":2}',
    b'{"x":"\\ud800"}', b'not json', b'{"x":"\xff"}', b'{"x":1e999}'])
def test_non_json_body_is_omitted_without_content(tmp_path, raw):
    core, prepared = _material(tmp_path, raw)
    assert _bounded_terminal_json(core, prepared) == {"status": "omitted_non_json"}


def test_unsupported_media_is_not_read(tmp_path):
    core, prepared = _material(tmp_path, b"private", media_type="text/plain")
    (tmp_path / "exact-result").unlink()
    assert _bounded_terminal_json(core, prepared) == {"status": "omitted_non_json"}


def test_oversize_body_is_not_read(tmp_path):
    core, prepared = _material(tmp_path, b"", size=TERMINAL_RESULT_MAX_BYTES + 1)
    (tmp_path / "exact-result").unlink()
    assert _bounded_terminal_json(core, prepared) == {"status": "omitted_oversize"}


def test_backing_size_mismatch_fails_without_returning_body(tmp_path):
    core, prepared = _material(tmp_path, b'{"value":5}', size=1)
    with pytest.raises(EnvironmentContractError, match="ENVIRONMENT_RESULT_INTEGRITY_ERROR"):
        _bounded_terminal_json(core, prepared)


def test_no_terminal_does_not_read_owner_resources():
    assert _terminal_result(object(), None, "stopped_by_owner") == {"status": "not_terminal"}


def test_cli_result_opt_in_requires_explicit_private_output(capsys):
    arguments = ["run", "--lock", "missing", "--archive", "missing",
        "--binding", "missing", "--resolved-selections", "missing", "--receipt", "missing",
        "--owner-request", "missing", "--run-dir", "absent", "--include-terminal-result"]
    with pytest.raises(SystemExit) as failure:
        environment_main(arguments)
    assert failure.value.code == 2
    assert "requires a private --output file" in capsys.readouterr().err


def test_host_request_body_is_opt_in_and_bool_only(tmp_path):
    value = SimpleNamespace(to_dict=lambda: {"entry_id": "main"})
    requirements = SimpleNamespace(target=value, package_lock=value)
    common = (requirements, tmp_path / "data.zip", (), value, value)
    default = host_request(*common, mode="run")
    assert "include_terminal_result" not in default
    explicit = host_request(*common, mode="run", include_terminal_result=True)
    assert explicit == {**default, "include_terminal_result": True}
    with pytest.raises(TypeError):
        host_request(*common, mode="run", include_terminal_result="true")
