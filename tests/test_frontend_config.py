from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import subprocess

import pytest

from cpn.rpnh import frontend_config, user_config
from cpn.rpnh.frontend_config import (
    FRONTENDS,
    FrontendPreferences,
    preferences_path,
    read_frontend_preferences,
    save_frontend_preferences,
)


@pytest.fixture(autouse=True)
def no_provider_or_host_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("frontend configuration must not invoke a provider or host")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def test_missing_preferences_are_frozen_defaults_without_creating_files(tmp_path):
    path = tmp_path / "missing" / "frontend.json"
    result = read_frontend_preferences(path)
    assert result == FrontendPreferences(frontend="auto", dsh_source=None)
    with pytest.raises(FrozenInstanceError):
        result.frontend = "dsh"
    assert not path.parent.exists()
    assert FRONTENDS == ("auto", "basic", "codex", "opencode", "dsh")


def test_default_location_tracks_user_config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RPNH_CONFIG", raising=False)
    assert preferences_path() == tmp_path / ".config" / "rpnh" / "config.json.frontend.json"
    monkeypatch.setenv("RPNH_CONFIG", "~/custom/model.json")
    assert preferences_path() == tmp_path / "custom" / "model.json.frontend.json"
    assert read_frontend_preferences() == FrontendPreferences()
    assert save_frontend_preferences("dsh") == FrontendPreferences("dsh")
    assert read_frontend_preferences() == FrontendPreferences("dsh")
    assert not user_config.config_path().exists()


def test_custom_model_config_named_frontend_cannot_be_overwritten(tmp_path, monkeypatch):
    model_config = tmp_path / "frontend.json"
    model_config.write_text('{"schema_version":"rpnh/cli_config/v4","execution_config_path":"owner-profile.json","reasoning_effort":null}\n')
    monkeypatch.setenv("RPNH_CONFIG", str(model_config))
    assert preferences_path() != model_config
    save_frontend_preferences("dsh")
    assert json.loads(model_config.read_text())["execution_config_path"] == "owner-profile.json"
    assert read_frontend_preferences().frontend == "dsh"


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_each_frontend_persists_without_a_model(tmp_path, frontend):
    path = tmp_path / "new" / "frontend.json"
    assert save_frontend_preferences(frontend, path=path) == FrontendPreferences(frontend)
    assert read_frontend_preferences(path) == FrontendPreferences(frontend)
    assert json.loads(path.read_text()) == {
        "schema_version": "rpnh/frontend_preferences/v1", "frontend": frontend,
    }


def test_source_can_be_missing_and_survives_frontend_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "frontend.json"
    source = tmp_path / "not-installed" / "dsh"
    assert save_frontend_preferences(
        "dsh", dsh_source=Path("~/not-installed/dsh"), path=path,
    ) == FrontendPreferences("dsh", source)
    assert not source.exists()
    assert save_frontend_preferences("codex", path=path) == FrontendPreferences("codex", source)
    assert read_frontend_preferences(path).dsh_source == source
    replacement = tmp_path / "replacement"
    save_frontend_preferences("basic", dsh_source=replacement, path=path)
    assert read_frontend_preferences(path) == FrontendPreferences("basic", replacement)


def test_relative_destination_and_saved_source_are_resolved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("nested/frontend.json")
    result = save_frontend_preferences("dsh", dsh_source=Path("source"), path=path)
    assert result.dsh_source == tmp_path / "source"
    path.write_text(json.dumps({
        "schema_version": "rpnh/frontend_preferences/v1",
        "frontend": "dsh", "dsh_source": "./future-source",
    }))
    assert read_frontend_preferences(path).dsh_source == tmp_path / "future-source"


@pytest.mark.parametrize("content", [
    "{", "[]", "null", "{}",
    '{"schema_version":"old","frontend":"auto"}',
    '{"schema_version":"rpnh/frontend_preferences/v1"}',
    '{"schema_version":"rpnh/frontend_preferences/v1","frontend":"invalid"}',
    '{"schema_version":"rpnh/frontend_preferences/v1","frontend":false}',
    '{"schema_version":"rpnh/frontend_preferences/v1","frontend":null}',
    '{"schema_version":"rpnh/frontend_preferences/v1","frontend":["dsh"]}',
    '{"schema_version":"rpnh/frontend_preferences/v1","frontend":"auto","extra":1}',
    *[json.dumps({
        "schema_version": "rpnh/frontend_preferences/v1",
        "frontend": "dsh", "dsh_source": value,
    }) for value in (None, 1, [], "", "   ")],
])
def test_invalid_persisted_preferences_fail_without_overwriting(tmp_path, content):
    path = tmp_path / "frontend.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="frontend|dsh_source"):
        read_frontend_preferences(path)
    with pytest.raises(ValueError, match="frontend|dsh_source"):
        save_frontend_preferences("basic", path=path)
    assert path.read_text() == content


@pytest.mark.parametrize("frontend", [None, False, 1, [], "", "DSH", "other"])
def test_invalid_frontend_argument_does_not_write(tmp_path, frontend):
    path = tmp_path / "frontend.json"
    with pytest.raises(ValueError, match="frontend"):
        save_frontend_preferences(frontend, path=path)
    assert not path.exists()


def test_invalid_source_argument_does_not_write(tmp_path):
    path = tmp_path / "frontend.json"
    with pytest.raises(ValueError, match="dsh_source"):
        save_frontend_preferences("dsh", dsh_source="", path=path)
    assert not path.exists()


def test_failed_atomic_replace_preserves_previous_preferences(tmp_path, monkeypatch):
    path = tmp_path / "frontend.json"
    save_frontend_preferences("codex", path=path)

    def fail_replace(source, destination):
        assert Path(source).parent == destination.parent == path.parent
        assert read_frontend_preferences(path).frontend == "codex"
        raise OSError("replace failed")

    monkeypatch.setattr(frontend_config.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        save_frontend_preferences("dsh", path=path)
    assert read_frontend_preferences(path).frontend == "codex"
    assert list(tmp_path.iterdir()) == [path]


def test_frontend_save_leaves_provider_config_bytes_untouched(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    monkeypatch.setenv("RPNH_CONFIG", str(config))
    original = b'{ "schema_version": "rpnh/cli_config/v4", "profile": "owner" }\n'
    config.write_bytes(original)
    save_frontend_preferences("dsh", dsh_source=tmp_path / "future-dsh")
    save_frontend_preferences("opencode")
    assert config.read_bytes() == original


def test_existing_model_selection_does_not_overwrite_preferences(tmp_path, monkeypatch):
    from cpn.rpnh.provider_setup import build_provider_catalog

    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "local", "display_name": "Local",
            "models": [{
                "profile": "offline", "model_condition": "offline-model",
                "adapter": {
                    "adapter_kind": "local_process",
                    "argv": ["not-installed-model", "{model}"],
                    "probe_argv": ["not-installed-model", "--version"],
                    "env": {}, "inherit_env": [],
                },
                "timeout_seconds": 60, "max_output_tokens": 256,
                "max_response_bytes": 4096,
            }],
        }],
    }))
    profiles = tmp_path / "profiles"
    build_provider_catalog(catalog, profiles)
    execution = profiles / "execution" / "offline.json"
    expected = save_frontend_preferences("dsh", dsh_source=tmp_path / "future-dsh")
    user_config.save_selected_path(execution, directory=execution.parent)
    assert read_frontend_preferences() == expected
    assert user_config.read_selected_path() == execution


def test_optional_binary_bindings_roundtrip_and_survive_frontend_changes(tmp_path):
    path = tmp_path / "frontend.json"
    codex, opencode = tmp_path / "codex", tmp_path / "opencode"
    result = save_frontend_preferences("codex", codex_binary=codex, path=path)
    assert result == FrontendPreferences("codex", codex_binary=codex)
    assert read_frontend_preferences(path) == result
    result = save_frontend_preferences("opencode", opencode_binary=opencode, path=path)
    assert result.codex_binary == codex and result.opencode_binary == opencode
    assert save_frontend_preferences("basic", path=path) == FrontendPreferences(
        "basic", codex_binary=codex, opencode_binary=opencode)
    assert json.loads(path.read_text()) == {
        "schema_version": "rpnh/frontend_preferences/v1", "frontend": "basic",
        "codex_binary": str(codex), "opencode_binary": str(opencode),
    }


@pytest.mark.parametrize("name", ["codex_binary", "opencode_binary"])
@pytest.mark.parametrize("value", [None, 1, [], "", "   "])
def test_invalid_optional_persisted_binary_binding_is_rejected(tmp_path, name, value):
    path = tmp_path / "frontend.json"
    original = json.dumps({"schema_version": "rpnh/frontend_preferences/v1",
                           "frontend": "basic", name: value})
    path.write_text(original)
    with pytest.raises(ValueError, match=name):
        read_frontend_preferences(path)
    with pytest.raises(ValueError, match=name):
        save_frontend_preferences("basic", path=path)
    assert path.read_text() == original


@pytest.mark.parametrize("name", ["codex_binary", "opencode_binary"])
def test_invalid_binary_argument_does_not_write(tmp_path, name):
    path = tmp_path / "frontend.json"
    with pytest.raises(ValueError, match=name):
        save_frontend_preferences("codex", **{name: "invalid"}, path=path)
    assert not path.exists()
