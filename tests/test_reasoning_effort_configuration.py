from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from cpn.llm_adapters import (
    build_llm_input_port,
    LLMExecutionConfigError,
    load_llm_execution_selection,
)
from cpn.llm_adapters._external_provider_protocol import (
    provider_request_from_envelope,
)
from cpn.llm_adapters.local_process import _load_config as _load_local_config
from cpn.rpnh.frontend_application import RegistryFrontendApplication
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh_cli import main as rpnh_main
from cpn.rpnh.user_config import (
    discover_profiles,
    read_selected_path,
    select_profile,
)


def _recovery() -> dict[str, object]:
    return {
        "strategy": "bounded_same_route_health_probe/v1",
        "max_probe_attempts": 1,
        "probe_timeout_budget_seconds": 3,
        "max_probe_success_formal_failure_cycles": 1,
    }


def _external_catalog() -> dict[str, object]:
    return {
        "schema_version": "rpnh/provider_model_catalog/v3",
        "providers": [{
            "provider": "user provider",
            "display_name": "User Provider",
            "models": [{
                "profile": "future-model",
                "model_condition": "vendor/future:model@2027",
                "reasoning_efforts": {
                    "supported": ["none", "low", "medium", "high", "max"],
                    "default": "medium",
                },
                "adapter": {
                    "adapter_kind": "external_provider",
                    "route_id": "primary",
                    "backend": "user backend",
                    "protocol": "openai_chat_completions/v1",
                    "endpoint": "https://api.example.invalid/v1/chat/completions",
                    "credential": None,
                    "recovery": _recovery(),
                    "headers": {},
                },
                "timeout_seconds": 60,
                "max_output_tokens": 4096,
                "max_response_bytes": 1048576,
            }],
        }],
    }


def _validate_runtime_document(
        document_path: Path, schema_name: str,
) -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "cpn" / "schemas" / "runtime" / schema_name)
    validator = Draft7Validator(json.loads(schema_path.read_text()))
    validator.validate(json.loads(document_path.read_text()))


def test_catalog_generates_groupable_immutable_effort_variants(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    catalog = tmp_path / "catalog.json"
    output = tmp_path / "profiles"
    catalog.write_text(json.dumps(_external_catalog()), encoding="utf-8")

    result = build_provider_catalog(catalog, output)
    profiles = discover_profiles(output / "execution")
    by_effort = {profile.reasoning_effort: profile for profile in profiles}

    assert result["profile_count"] == 5
    assert result["selection_ids"] == ["future-model"]
    assert set(by_effort) == {"none", "low", "medium", "high", "max"}
    assert by_effort["medium"].name == "future-model"
    assert by_effort["medium"].path.name == "future-model.json"
    assert by_effort["high"].name == "future-model--effort-high"
    assert {profile.selection_id for profile in profiles} == {"future-model"}
    assert {profile.supported_reasoning_efforts for profile in profiles} == {
        ("none", "low", "medium", "high", "max")}
    assert {profile.default_reasoning_effort for profile in profiles} == {
        "medium"}

    frontend = RegistryFrontendApplication(
        tmp_path / "frontend-session", by_effort["high"].path)
    configuration = frontend.configuration()
    assert configuration["default_reasoning_effort"] == "high"
    assert configuration["profiles"][0][
        "supported_reasoning_efforts"] == [
            "none", "low", "medium", "high", "max"]
    frontend.close()

    selected = load_llm_execution_selection(by_effort["high"].path)
    assert selected.physical_profile == "future-model--effort-high"
    assert selected.logical_selection_id == "future-model"
    assert selected.reasoning_effort == "high"
    assert selected.supported_reasoning_efforts == (
        "none", "low", "medium", "high", "max")
    assert selected.default_reasoning_effort == "medium"
    assert selected.as_registry_policy()["reasoning_effort"] == {
        "selected": "high",
        "supported": ["none", "low", "medium", "high", "max"],
        "default": "medium",
    }
    adapter = json.loads(selected.adapter_config_path.read_text())
    assert adapter["schema_version"] == "external_provider_adapter_config/v3"
    assert adapter["reasoning_effort"] == "high"
    _validate_runtime_document(
        catalog, "provider_model_catalog.v3.schema.json")
    _validate_runtime_document(
        output / "profiles.json", "provider_profiles.v3.schema.json")
    _validate_runtime_document(
        by_effort["high"].path, "llm_execution_selection.v2.schema.json")
    _validate_runtime_document(
        selected.adapter_config_path,
        "external_provider_adapter_config.v3.schema.json")

    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "selected.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(output / "execution"))
    default_profile = select_profile(
        "user provider", "vendor/future:model@2027")
    assert default_profile.reasoning_effort == "medium"
    high_profile = select_profile(
        "user provider", "vendor/future:model@2027",
        reasoning_effort="high")
    assert read_selected_path() == high_profile.path
    assert select_profile(
        "future-model", reasoning_effort="low").reasoning_effort == "low"
    select_profile(
        "user provider", "vendor/future:model@2027",
        reasoning_effort="high")
    saved = json.loads((tmp_path / "selected.json").read_text())
    assert saved == {
        "schema_version": "rpnh/cli_config/v4",
        "profile": "future-model",
        "provider": "user provider",
        "model_condition": "vendor/future:model@2027",
        "reasoning_effort": "high",
    }
    assert rpnh_main(["config", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0]["selection_id"] == "future-model"
    assert listed[0]["reasoning_effort"] == "high"
    assert listed[0]["selected"] is True

    (tmp_path / "selected.json").write_text(json.dumps({
        "schema_version": "rpnh/cli_config/v3",
        "profile": "future-model",
        "provider": "user provider",
        "model_condition": "vendor/future:model@2027",
    }), encoding="utf-8")
    assert read_selected_path() == default_profile.path


def test_local_effort_requires_and_substitutes_formal_argv_placeholder(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = tmp_path / "catalog.json"
    output = tmp_path / "profiles"
    document = {
        "schema_version": "rpnh/provider_model_catalog/v3",
        "providers": [{
            "provider": "local",
            "display_name": "Local",
            "models": [{
                "profile": "local-model",
                "model_condition": "exact local model",
                "reasoning_efforts": {
                    "supported": ["low", "medium"], "default": "medium"},
                "adapter": {
                    "adapter_kind": "local_process",
                    "argv": [
                        "user-llm", "--model", "{model}",
                        "--reasoning-effort", "{reasoning_effort}"],
                    "probe_argv": ["user-llm", "--version"],
                    "env": {},
                    "inherit_env": [],
                },
                "timeout_seconds": 60,
                "max_output_tokens": 256,
                "max_response_bytes": 4096,
            }],
        }],
    }
    catalog.write_text(json.dumps(document), encoding="utf-8")
    build_provider_catalog(catalog, output)
    selection = load_llm_execution_selection(
        output / "execution" / "local-model--effort-low.json")
    monkeypatch.setattr(
        "cpn.llm_adapters.local_process.shutil.which",
        lambda command: "/opt/user/bin/user-llm"
        if command == "user-llm" else None)

    argv, _environment = _load_local_config(
        selection.adapter_config_path, "exact local model", "low")
    assert argv == (
        "/opt/user/bin/user-llm", "--model", "exact local model",
        "--reasoning-effort", "low")
    port = build_llm_input_port(
        selection, destination_run_root=tmp_path / "run")
    assert port.execution_policy["reasoning_effort"]["selected"] == "low"
    port.close()
    _validate_runtime_document(
        selection.adapter_config_path,
        "local_process_adapter_config.v2.schema.json")

    document["providers"][0]["models"][0]["adapter"]["argv"] = [
        "user-llm", "--model", "{model}"]
    catalog.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=r"must contain \{reasoning_effort\}"):
        build_provider_catalog(catalog, output)


def test_catalog_rejects_invalid_effort_metadata(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    document = _external_catalog()
    efforts = document["providers"][0]["models"][0]["reasoning_efforts"]
    efforts["default"] = "unsupported"
    catalog.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="default reasoning effort"):
        build_provider_catalog(catalog, tmp_path / "profiles")


def test_current_catalog_without_efforts_generates_one_no_effort_variant(
        tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.json"
    output = tmp_path / "profiles"
    document = _external_catalog()
    document["providers"][0]["models"][0].pop("reasoning_efforts")
    catalog.write_text(json.dumps(document), encoding="utf-8")

    result = build_provider_catalog(catalog, output)
    profiles = discover_profiles(output / "execution")
    selection = load_llm_execution_selection(profiles[0].path)

    assert result["profile_count"] == 1
    assert profiles[0].name == "future-model"
    assert profiles[0].reasoning_effort is None
    assert profiles[0].supported_reasoning_efforts == ()
    assert profiles[0].default_reasoning_effort is None
    adapter = json.loads(selection.adapter_config_path.read_text())
    assert adapter["schema_version"] == "external_provider_adapter_config/v3"
    assert adapter["reasoning_effort"] is None


def test_external_wire_omits_or_includes_only_selected_effort() -> None:
    request = json.dumps({
        "protocol": "llm_request_envelope/v1",
        "model_condition": "exact-model",
        "max_output_tokens": 16,
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [],
        "tool_choice": "none",
    }, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode()

    legacy = provider_request_from_envelope(
        request,
        expected_model_condition="exact-model",
        expected_max_output_tokens=16,
        outbound_model="exact-model")
    selected = provider_request_from_envelope(
        request,
        expected_model_condition="exact-model",
        expected_max_output_tokens=16,
        outbound_model="exact-model",
        reasoning_effort="custom-max")

    assert legacy == (
        b'{"model":"exact-model","max_tokens":16,'
        b'"messages":[{"content":"hello","role":"user"}],'
        b'"tools":[],"tool_choice":"none","stream":false}')
    assert json.loads(selected)["reasoning_effort"] == "custom-max"


def test_current_execution_selection_rejects_arbitrary_relative_adapter_path(
        tmp_path: Path,
) -> None:
    current = tmp_path / "current.json"
    document = {
        "schema_version": "llm_execution_selection/v2",
        "adapter_kind": "local_process",
        "model_condition": "exact-model",
        "adapter_config_path": "arbitrary.json",
        "timeout_seconds": 10,
        "max_output_tokens": 16,
        "max_response_bytes": 4096,
        "physical_profile": "profile",
        "logical_selection_id": "profile",
        "reasoning_effort": None,
        "supported_reasoning_efforts": [],
        "default_reasoning_effort": None,
    }
    current.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(
            LLMExecutionConfigError, match="relative layout is invalid"):
        load_llm_execution_selection(current)

    legacy = tmp_path / "legacy.json"
    document = {
        key: value for key, value in document.items()
        if key not in {
            "physical_profile", "logical_selection_id", "reasoning_effort",
            "supported_reasoning_efforts", "default_reasoning_effort"}
    }
    document["schema_version"] = "llm_execution_selection/v1"
    legacy.write_text(json.dumps(document), encoding="utf-8")
    assert load_llm_execution_selection(legacy).adapter_config_path == (
        tmp_path / "arbitrary.json").resolve()
