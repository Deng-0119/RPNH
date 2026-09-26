from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from cpn.llm_adapters import (
    LLMExecutionConfigError,
    load_llm_execution_selection,
)
from cpn.llm_adapters._external_provider_credentials import (
    resolved_provider_headers,
)
from cpn.llm_adapters.codex_subscription_bridge import _validate_args
from cpn.llm_adapters.external_provider import (
    _load_config as _load_external_config,
)
from cpn.llm_adapters.local_process import _load_config as _load_local_config
from cpn.rpnh.provider_setup import (
    build_provider_catalog,
    initialize_provider_catalog,
)
from cpn.rpnh.user_config import discover_profiles


def _recovery() -> dict[str, object]:
    return {
        "strategy": "bounded_same_route_health_probe/v1",
        "max_probe_attempts": 3,
        "probe_timeout_budget_seconds": 300,
        "max_probe_success_formal_failure_cycles": 3,
    }


def test_initialization_creates_an_empty_user_owned_catalog(
        tmp_path: Path,
) -> None:
    catalog = tmp_path / "config" / "provider_models.json"
    output = tmp_path / "profiles"

    created = initialize_provider_catalog(catalog)
    assert created == {"catalog": str(catalog), "status": "created"}
    assert json.loads(catalog.read_text(encoding="utf-8")) == {
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [],
    }
    result = build_provider_catalog(catalog, output)
    assert result["profile_count"] == 0
    assert result["selection_ids"] == []
    assert discover_profiles(output / "execution") == ()
    assert initialize_provider_catalog(catalog)["status"] == "already_exists"
    assert build_provider_catalog(catalog, output, check=True)["status"] == (
        "in_sync")


def test_catalog_accepts_arbitrary_provider_and_exact_model_identifiers(
        tmp_path: Path, monkeypatch,
) -> None:
    catalog = tmp_path / "models.json"
    output = tmp_path / "generated"
    catalog.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "custom provider #42",
            "display_name": "User-defined provider",
            "models": [{
                "profile": "future-model",
                "model_condition": "vendor/future-model:v9@2026",
                "adapter": {
                    "adapter_kind": "external_provider",
                    "route_id": "primary",
                    "backend": "user backend",
                    "protocol": "openai_chat_completions/v1",
                    "endpoint": (
                        "https://api.example.invalid/v1/chat/completions"),
                    "credential": {
                        "environment": "RPNH_TEST_API_KEY",
                        "header": "X-API-Key",
                        "prefix": "",
                    },
                    "recovery": _recovery(),
                    "headers": {"X-Client": "rpnh-test"},
                },
                "timeout_seconds": 60,
                "max_output_tokens": 4096,
                "max_response_bytes": 1048576,
                "context_window_tokens": 131072,
                "context_compaction_retained_tokens": 16384,
                "runtime": {
                    "max_turns_per_node": 7,
                    "max_parallel_nodes": 2,
                    "main_history_message_limit": 8,
                    "context_pressure_trigger_ratio": 0.75,
                    "context_tool_output_byte_limit": 2048,
                    "workspace": {
                        "timeout_seconds": 45,
                        "memory_bytes": 536870912,
                        "process_limit": 8,
                        "source_size_bytes": 1048576,
                        "input_size_bytes": 2097152,
                    },
                },
            }],
        }],
    }), encoding="utf-8")

    result = build_provider_catalog(catalog, output)
    profiles = discover_profiles(output / "execution")
    selection = load_llm_execution_selection(profiles[0].path)
    adapter_config = _load_external_config(
        selection.adapter_config_path, "vendor/future-model:v9@2026")

    assert result["selection_ids"] == ["future-model"]
    assert profiles[0].selectable is True
    assert profiles[0].provider == "custom provider #42"
    assert profiles[0].required_environment == ("RPNH_TEST_API_KEY",)
    assert adapter_config.routes[0].provider == "custom provider #42"
    assert (adapter_config.routes[0].outbound_model
            == "vendor/future-model:v9@2026")
    assert adapter_config.recovery.as_document() == _recovery()
    registry_policy = selection.as_registry_policy()
    assert selection.input_target.context_window_tokens == 131072
    assert (selection.input_target.context_compaction_retained_tokens
            == 16384)
    assert registry_policy["context_window_tokens"] == 131072
    assert registry_policy["context_compaction_retained_tokens"] == 16384
    assert selection.runtime_policy.max_turns_per_node == 7
    assert selection.runtime_policy.max_parallel_nodes == 2
    assert selection.runtime_policy.context_pressure_trigger_ratio == 0.75
    assert registry_policy["runtime"] == {
        "max_turns_per_node": 7,
        "max_parallel_nodes": 2,
        "main_history_message_limit": 8,
        "context_pressure_trigger_ratio": 0.75,
        "context_tool_output_byte_limit": 2048,
        "workspace": {
            "timeout_seconds": 45,
            "memory_bytes": 536870912,
            "process_limit": 8,
            "source_size_bytes": 1048576,
            "input_size_bytes": 2097152,
        },
    }
    assert registry_policy["adapter_profile"]["recovery"] == _recovery()
    route_policy = registry_policy["route_provenance"][0]
    assert route_policy["endpoint"] == (
        "https://api.example.invalid/v1/chat/completions")
    assert route_policy["outbound_model"] == "vendor/future-model:v9@2026"
    assert route_policy["credential_binding"] == {
        "environment": "RPNH_TEST_API_KEY",
        "header": "X-API-Key",
        "prefix": "",
    }
    monkeypatch.setenv("RPNH_TEST_API_KEY", "synthetic-test-secret")
    assert "synthetic-test-secret" not in json.dumps(registry_policy)
    with resolved_provider_headers(
            adapter_config.routes[0].credential) as headers:
        assert headers["X-API-Key"] == "synthetic-test-secret"
    assert build_provider_catalog(catalog, output, check=True)["status"] == (
        "in_sync")

    adapter_bytes = selection.adapter_config_path.read_bytes()
    for field in (
            "max_probe_attempts",
            "max_probe_success_formal_failure_cycles"):
        malformed_adapter = json.loads(adapter_bytes)
        malformed_adapter["recovery"][field] = 4
        selection.adapter_config_path.write_text(
            json.dumps(malformed_adapter), encoding="utf-8")
        with pytest.raises(
                LLMExecutionConfigError,
                match=f"{field} must be at most 3"):
            selection.as_registry_policy()
    selection.adapter_config_path.write_bytes(adapter_bytes)

    (output / "execution" / "future-model.json").write_text(
        "{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="out of date"):
        build_provider_catalog(catalog, output, check=True)

    malformed_catalog = json.loads(catalog.read_text(encoding="utf-8"))
    malformed_catalog["providers"][0]["models"][0][
        "context_compaction_retained_tokens"] = 131072
    catalog.write_text(json.dumps(malformed_catalog), encoding="utf-8")
    with pytest.raises(
            ValueError,
            match="context_compaction_retained_tokens must be smaller"):
        build_provider_catalog(catalog, output)


def test_runtime_policy_rejects_partial_or_out_of_range_catalog_values(
        tmp_path: Path,
) -> None:
    catalog = tmp_path / "models.json"
    output = tmp_path / "generated"
    base = {
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "local",
            "display_name": "Local",
            "models": [{
                "profile": "local-model",
                "model_condition": "exact-local-model",
                "adapter": {
                    "adapter_kind": "local_process",
                    "argv": ["model", "{model}"],
                    "probe_argv": ["model", "--version"],
                    "env": {},
                    "inherit_env": [],
                },
                "timeout_seconds": 10,
                "max_output_tokens": 64,
                "max_response_bytes": 4096,
            }],
        }],
    }
    model = base["providers"][0]["models"][0]
    model["runtime"] = {"max_turns_per_node": 1}
    catalog.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime"):
        build_provider_catalog(catalog, output)

    model["runtime"] = {
        "max_turns_per_node": 1,
        "max_parallel_nodes": 1,
        "main_history_message_limit": 1,
        "context_pressure_trigger_ratio": 1.0,
        "context_tool_output_byte_limit": 128,
        "workspace": {
            "timeout_seconds": 1,
            "memory_bytes": 1,
            "process_limit": 1,
            "source_size_bytes": 1,
            "input_size_bytes": 1,
        },
    }
    catalog.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="context_pressure_trigger_ratio"):
        build_provider_catalog(catalog, output)


def test_catalog_accepts_loopback_http_but_rejects_remote_plaintext(
        tmp_path: Path,
) -> None:
    catalog = tmp_path / "models.json"
    output = tmp_path / "generated"

    def document(endpoint: str) -> dict[str, object]:
        return {
            "schema_version": "rpnh/provider_model_catalog/v2",
            "providers": [{
                "provider": "local server",
                "display_name": "Local server",
                "models": [{
                    "profile": "local-http",
                    "model_condition": "local/exact-model",
                    "adapter": {
                        "adapter_kind": "external_provider",
                        "route_id": "loopback",
                        "backend": "local-openai-compatible",
                        "protocol": "openai_chat_completions/v1",
                        "endpoint": endpoint,
                        "credential": None,
                        "recovery": _recovery(),
                        "headers": {},
                    },
                    "timeout_seconds": 60,
                    "max_output_tokens": 256,
                    "max_response_bytes": 1048576,
                }],
            }],
        }

    catalog.write_text(json.dumps(document(
        "http://127.0.0.1:11434/v1/chat/completions")), encoding="utf-8")
    build_provider_catalog(catalog, output)
    selection = load_llm_execution_selection(
        output / "execution" / "local-http.json")
    assert selection.as_registry_policy()["route_provenance"][0][
        "transport"] == "http"

    catalog.write_text(json.dumps(document(
        "http://models.example.invalid/v1/chat/completions")),
        encoding="utf-8")
    with pytest.raises(ValueError, match="plaintext HTTP endpoint must be loopback"):
        build_provider_catalog(catalog, output)


def test_optional_codex_bridge_does_not_whitelist_model_names(
        tmp_path: Path,
) -> None:
    executable = tmp_path / "codex"
    schema = tmp_path / "response-schema.json"
    executable.write_text("test executable placeholder\n", encoding="utf-8")
    schema.write_text("{}\n", encoding="utf-8")
    _validate_args(Namespace(
        codex=executable.resolve(),
        model="user-chosen/future-model@version",
        model_context_window=123456,
        model_max_output_tokens=7890,
        output_token_policy="user-configured",
        reasoning_effort="custom-effort",
        verbosity="custom-verbosity",
        response_schema=schema.resolve(),
    ))


def test_local_process_profile_resolves_any_available_command(
        tmp_path: Path, monkeypatch,
) -> None:
    catalog = tmp_path / "models.json"
    output = tmp_path / "generated"
    catalog.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "local user runtime",
            "display_name": "Local user runtime",
            "models": [{
                "profile": "local-future-model",
                "model_condition": "user model with spaces",
                "adapter": {
                    "adapter_kind": "local_process",
                    "argv": ["user-llm", "--model", "{model}"],
                    "probe_argv": ["user-llm", "--version"],
                    "env": {},
                    "inherit_env": [],
                },
                "timeout_seconds": 60,
                "max_output_tokens": 4096,
                "max_response_bytes": 1048576,
            }],
        }],
    }), encoding="utf-8")
    build_provider_catalog(catalog, output)
    selection = load_llm_execution_selection(
        output / "execution" / "local-future-model.json")
    monkeypatch.setattr(
        "cpn.llm_adapters.local_process.shutil.which",
        lambda command: "/opt/user/bin/user-llm"
        if command == "user-llm" else None,
    )
    argv, _environment = _load_local_config(
        selection.adapter_config_path, "user model with spaces")
    assert argv == (
        "/opt/user/bin/user-llm", "--model", "user model with spaces")


def test_same_exact_pair_can_have_multiple_user_named_routes(
        tmp_path: Path,
) -> None:
    catalog = tmp_path / "models.json"
    output = tmp_path / "generated"
    models = []
    for profile, route in (("route-one", "one"), ("route-two", "two")):
        models.append({
            "profile": profile,
            "model_condition": "same exact model",
            "adapter": {
                "adapter_kind": "external_provider",
                "route_id": route,
                "backend": "user backend",
                "protocol": "openai_chat_completions/v1",
                "endpoint": (
                    f"https://{route}.example.invalid/v1/chat/completions"),
                "credential": None,
                "recovery": _recovery(),
                "headers": {},
            },
            "timeout_seconds": 60,
            "max_output_tokens": 4096,
            "max_response_bytes": 1048576,
        })
    catalog.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "same provider",
            "display_name": "Same provider",
            "models": models,
        }],
    }), encoding="utf-8")

    result = build_provider_catalog(catalog, output)
    assert result["selection_ids"] == ["route-one", "route-two"]
