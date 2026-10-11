from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from cpn.llm_adapters import LLMExecutionConfigError
from cpn.rpnh import onboarding, user_config
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh_cli import main


def _unexpected(*args, **kwargs):
    pytest.fail("unexpected lower-priority lookup, interactive setup, or execution")


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(tmp_path / "profiles/execution"))
    monkeypatch.setenv("RPNH_PROVIDER_CATALOG", str(tmp_path / "catalog.json"))
    monkeypatch.delenv("RPNH_EXECUTION_CONFIG", raising=False)
    monkeypatch.setenv("FIXTURE_API_KEY", "synthetic-credential-value")
    monkeypatch.setattr(socket.socket, "connect", _unexpected)
    monkeypatch.setattr(socket.socket, "connect_ex", _unexpected)
    monkeypatch.setattr(socket, "getaddrinfo", _unexpected)
    monkeypatch.setattr(subprocess, "Popen", _unexpected)
    monkeypatch.setattr(os, "system", _unexpected)
    monkeypatch.setattr("cpn.llm_adapters.build_llm_input_port", _unexpected)
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", _unexpected)
    monkeypatch.setattr(user_config, "interactive_setup", _unexpected)


def _build_profiles(tmp_path, kind="external_provider"):
    catalog = {
        "schema_version": "rpnh/provider_model_catalog/v3",
        "providers": [{
            "provider": "fixture-provider", "display_name": "Fixture provider",
            "models": [{
                "profile": "fixture-model", "model_condition": "fixture-model-id",
                "reasoning_efforts": {"supported": ["none", "medium", "high"], "default": "medium"},
                "adapter": {
                    "adapter_kind": "external_provider", "route_id": "primary",
                    "backend": "fixture-backend", "protocol": "openai_chat_completions/v1",
                    "endpoint": "https://fixture-endpoint.invalid/v1/chat/completions",
                    "credential": {"environment": "FIXTURE_API_KEY", "header": "Authorization", "prefix": "Bearer "},
                    "headers": {},
                    "recovery": {"strategy": "bounded_same_route_health_probe/v1",
                                 "max_probe_attempts": 1, "probe_timeout_budget_seconds": 3,
                                 "max_probe_success_formal_failure_cycles": 1},
                },
                "timeout_seconds": 60, "max_output_tokens": 4096,
                "max_response_bytes": 1048576,
            }],
        }],
    }
    model = catalog["providers"][0]["models"][0]
    if kind == "local_process":
        model["adapter"] = {
            "adapter_kind": "local_process",
            "argv": [sys.executable, "--fixture-only-argument", "{model}", "{reasoning_effort}"],
            "probe_argv": [sys.executable, "--fixture-only-probe"],
            "env": {}, "inherit_env": [],
        }
    plain = dict(model)
    plain.update(profile="plain-model", model_condition="plain-model-id")
    del plain["reasoning_efforts"]
    if kind == "local_process":
        plain["adapter"] = {**model["adapter"], "argv": [sys.executable, "--fixture-only-argument", "{model}"]}
    catalog["providers"][0]["models"].append(plain)
    (tmp_path / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    build_provider_catalog()
    return {p.reasoning_effort: p for p in user_config.discover_profiles()}


@pytest.fixture
def profiles(tmp_path):
    return _build_profiles(tmp_path)


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@contextmanager
def _read_only():
    active = True

    def audit(event, args):
        if not active:
            return
        if event == "open":
            _, mode, flags = args
            assert not (mode and any(c in mode for c in "wax+")), "doctor attempted a file write"
            assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        assert event not in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.chmod",
                             "subprocess.Popen", "os.system", "socket.connect", "socket.getaddrinfo",
                             "sqlite3.connect"}, f"doctor attempted {event}"

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active = False


def test_explicit_short_circuits_environment_and_invalid_saved_config(profiles, monkeypatch):
    user_config.config_path().write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv("RPNH_EXECUTION_CONFIG", "invalid-environment-selection")

    class UnreadExecutionEnvironment(dict):
        def get(self, key, default=None):
            if key == "RPNH_EXECUTION_CONFIG":
                pytest.fail("explicit selection read the lower-priority environment selector")
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", UnreadExecutionEnvironment(os.environ))
    monkeypatch.setattr(user_config, "read_selected_path", _unexpected)
    selected = profiles["high"].path
    result = user_config._resolve_execution_selection(selected, save_default=False)
    assert result.path == selected
    assert result.selection_source is user_config._ExecutionSelectionSource.EXPLICIT
    assert user_config.resolve_execution_path(selected, save_default=False) == selected


def test_environment_short_circuits_invalid_saved_config(profiles, monkeypatch):
    user_config.config_path().write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv("RPNH_EXECUTION_CONFIG", str(profiles["high"].path))
    monkeypatch.setattr(user_config, "read_selected_path", _unexpected)
    result = user_config._resolve_execution_selection(None, save_default=False)
    assert result.path == profiles["high"].path
    assert result.selection_source is user_config._ExecutionSelectionSource.ENVIRONMENT


@pytest.mark.parametrize("source", ["explicit", "env"])
@pytest.mark.parametrize("malformed", [False, True])
def test_invalid_winner_never_falls_back(profiles, tmp_path, monkeypatch, source, malformed):
    user_config.save_selected_path(profiles["medium"].path)
    invalid = tmp_path / "invalid.json"
    if malformed:
        invalid.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RPNH_EXECUTION_CONFIG", str(invalid if source == "env" else profiles["medium"].path))
    monkeypatch.setattr(user_config, "read_selected_path", _unexpected)
    explicit = invalid if source == "explicit" else None
    error = LLMExecutionConfigError if malformed else ValueError
    with pytest.raises(error):
        user_config._resolve_execution_selection(explicit, save_default=False, allow_interactive_setup=True)
    with pytest.raises(error):
        user_config.resolve_execution_path(explicit, save_default=False, allow_interactive_setup=True)
    report = onboarding.configuration_report(explicit)
    assert not report["ready"] and report["network_checked"] is False
    assert not any(c["check"] == "execution selection source" for c in report["checks"])


@pytest.mark.parametrize("version", ["v3", "v4"])
@pytest.mark.parametrize("identity", [False, True])
def test_saved_compatibility_and_empty_environment(profiles, monkeypatch, version, identity):
    chosen = profiles["medium" if version == "v3" and identity else "high"]
    saved = {"schema_version": "rpnh/cli_config/" + version}
    if identity:
        saved.update(profile=chosen.selection_id, provider=chosen.provider, model_condition=chosen.model_condition)
    else:
        saved["execution_config_path"] = str(chosen.path)
    if version == "v4":
        saved["reasoning_effort"] = chosen.reasoning_effort
    config = user_config.config_path()
    config.write_text(json.dumps(saved), encoding="utf-8")
    before = config.read_bytes()
    monkeypatch.setenv("RPNH_EXECUTION_CONFIG", "")
    result = user_config._resolve_execution_selection(None, save_default=False)
    assert result.path == user_config.resolve_execution_path(None, save_default=False) == chosen.path
    assert result.selection_source is user_config._ExecutionSelectionSource.SAVED
    assert config.read_bytes() == before


def test_saved_v4_effort_mismatch_is_not_repaired_or_replaced(profiles):
    config = user_config.config_path()
    config.write_text(json.dumps({"schema_version": "rpnh/cli_config/v4",
                                 "execution_config_path": str(profiles["high"].path),
                                 "reasoning_effort": "medium"}), encoding="utf-8")
    before = config.read_bytes()
    with pytest.raises(ValueError, match="differs from its reasoning effort"):
        user_config.resolve_execution_path(None, save_default=False, allow_interactive_setup=True)
    assert config.read_bytes() == before


def test_save_default_and_interactive_path_api_are_preserved(profiles, monkeypatch):
    selected = profiles["high"]
    result = user_config.resolve_execution_path(selected.path, save_default=True)
    assert isinstance(result, Path) and result == selected.path
    assert json.loads(user_config.config_path().read_text()) == {
        "schema_version": "rpnh/cli_config/v4", "profile": selected.selection_id,
        "provider": selected.provider, "model_condition": selected.model_condition,
        "reasoning_effort": "high",
    }
    user_config.config_path().unlink()
    calls = []

    def setup():
        calls.append("setup")
        user_config.save_selected_path(selected.path)
        return selected

    monkeypatch.setattr(user_config, "interactive_setup", setup)
    result = user_config._resolve_execution_selection(None, save_default=False, allow_interactive_setup=True)
    assert result.path == selected.path
    assert result.selection_source is user_config._ExecutionSelectionSource.INTERACTIVE
    assert calls == ["setup"] and user_config.read_selected_path() == selected.path


def test_save_default_error_precedes_all_lookups(monkeypatch):
    monkeypatch.setattr(user_config, "read_selected_path", _unexpected)
    monkeypatch.setattr(user_config, "profile_for_path", _unexpected)
    with pytest.raises(ValueError, match="^--save-default requires --execution$"):
        user_config.resolve_execution_path(None, save_default=True, allow_interactive_setup=True)


def test_path_api_delegates_once_with_unchanged_arguments(tmp_path, monkeypatch):
    selected = tmp_path / "unused.json"
    calls = []

    def resolve(explicit, **kwargs):
        calls.append((explicit, kwargs))
        return user_config._ResolvedExecutionSelection(selected, user_config._ExecutionSelectionSource.EXPLICIT)

    monkeypatch.setattr(user_config, "_resolve_execution_selection", resolve)
    assert user_config.resolve_execution_path(selected, save_default=True, allow_interactive_setup=True) == selected
    assert calls == [(selected, {"save_default": True, "allow_interactive_setup": True})]


@pytest.mark.parametrize("source,detail", [
    ("explicit", "Explicit --execution option."),
    ("env", "RPNH_EXECUTION_CONFIG environment variable."),
    ("saved", "Saved profile selection."),
])
def test_doctor_uses_one_resolution_and_only_fixed_source_label(profiles, tmp_path, monkeypatch, capsys, source, detail):
    user_config.save_selected_path(profiles["medium"].path)
    if source != "saved":
        monkeypatch.setenv("RPNH_EXECUTION_CONFIG", str(profiles["high"].path))
    selected = profiles["high"].path if source == "explicit" else None
    original = user_config._resolve_execution_selection
    calls = []

    def resolve(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(onboarding, "_resolve_execution_selection", resolve)
    before = _snapshot(tmp_path)
    with _read_only():
        report = onboarding.configuration_report(selected)
    assert report["ready"] and report["network_checked"] is False, report
    assert calls == [((selected,), {"save_default": False})]
    source_check = [c for c in report["checks"] if c["check"] == "execution selection source"]
    assert source_check == [{"check": "execution selection source", "status": "ok", "detail": detail}]
    serialized = json.dumps(report)
    for value in (str(tmp_path), "synthetic-credential-value", "fixture-endpoint.invalid", "Authorization", "--fixture-only-argument"):
        assert value not in serialized
    assert _snapshot(tmp_path) == before
    selector = ["--execution", str(selected)] if selected is not None else []
    with _read_only():
        assert main(["doctor", *selector]) == 0
    assert "OK: execution selection source: " + detail in capsys.readouterr().out
    with _read_only():
        assert main(["config", "doctor", "--json", *selector]) == 0
    assert json.loads(capsys.readouterr().out) == report
    assert len(calls) == 3 and _snapshot(tmp_path) == before


def test_doctor_missing_selection_stays_read_only(tmp_path):
    before = _snapshot(tmp_path)
    with _read_only():
        report = onboarding.configuration_report()
    assert not report["ready"] and report["network_checked"] is False
    assert not any(c["check"] == "execution selection source" for c in report["checks"])
    assert "no model configured" in report["checks"][-1]["detail"]
    assert _snapshot(tmp_path) == before


def test_config_show_still_uses_saved_selection(profiles, monkeypatch, capsys):
    user_config.save_selected_path(profiles["medium"].path)
    monkeypatch.setenv("RPNH_EXECUTION_CONFIG", str(profiles["high"].path))
    with _read_only():
        assert main(["config", "show"]) == 0
    assert json.loads(capsys.readouterr().out)["reasoning_effort"] == "medium"


@pytest.mark.parametrize("kind", ["external_provider", "local_process"])
@pytest.mark.parametrize("effort", ["high", "none", None])
def test_doctor_validates_exact_selected_effort(tmp_path, monkeypatch, kind, effort):
    profiles = _build_profiles(tmp_path, kind)
    selected = profiles[effort]
    user_config.save_selected_path(selected.path)
    from importlib import import_module
    adapter = import_module("cpn.llm_adapters." + kind)
    original = adapter._load_config
    calls = []

    def load(path, model, reasoning_effort=None):
        calls.append(reasoning_effort)
        return original(path, model, reasoning_effort)

    monkeypatch.setattr(adapter, "_load_config", load)
    before = _snapshot(tmp_path)
    with _read_only():
        report = onboarding.configuration_report()
    assert report["ready"] and report["network_checked"] is False, report
    assert calls == [effort]
    assert selected.reasoning_effort == effort
    assert _snapshot(tmp_path) == before
    serialized = json.dumps(report)
    for value in (str(tmp_path), sys.executable, "--fixture-only-argument",
                  "--fixture-only-probe", "synthetic-credential-value", "fixture-endpoint.invalid"):
        assert value not in serialized


@pytest.mark.parametrize("kind", ["external_provider", "local_process"])
@pytest.mark.parametrize("selected_effort,adapter_effort", [("high", "medium"), ("none", None), (None, "none")])
def test_doctor_rejects_mismatched_adapter_effort(tmp_path, kind, selected_effort, adapter_effort):
    profiles = _build_profiles(tmp_path, kind)
    selected = profiles[selected_effort]
    user_config.save_selected_path(selected.path)
    from cpn.llm_adapters import load_llm_execution_selection
    path = load_llm_execution_selection(selected.path).adapter_config_path
    adapter = json.loads(path.read_text())
    adapter["reasoning_effort"] = adapter_effort
    path.write_text(json.dumps(adapter), encoding="utf-8")
    before = _snapshot(tmp_path)
    with _read_only():
        report = onboarding.configuration_report()
    assert not report["ready"] and report["network_checked"] is False
    assert any("reasoning effort differs from selection" in c["detail"]
               for c in report["checks"] if c["status"] == "error")
    assert _snapshot(tmp_path) == before
