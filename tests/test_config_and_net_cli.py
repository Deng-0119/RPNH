from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.cli import _filter_projection, main
from cpn.llm_adapters import load_llm_execution_selection
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
        "max_probe_attempts": 3,
        "probe_timeout_budget_seconds": 300,
        "max_probe_success_formal_failure_cycles": 3,
    }


def _catalog(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": "rpnh/provider_model_catalog/v2",
        "providers": [{
            "provider": "user provider",
            "display_name": "User provider",
            "models": [{
                "profile": "user-model",
                "model_condition": "vendor/model:future@1",
                "adapter": {
                    "adapter_kind": "external_provider",
                    "route_id": "primary",
                    "backend": "user backend",
                    "protocol": "openai_chat_completions/v1",
                    "endpoint": (
                        "https://api.example.invalid/v1/chat/completions"),
                    "credential": None,
                    "recovery": _recovery(),
                    "headers": {},
                },
                "timeout_seconds": 60,
                "max_output_tokens": 4096,
                "max_response_bytes": 1048576,
                "context_window_tokens": 131072,
                "context_compaction_retained_tokens": 16384,
            }],
        }],
    }), encoding="utf-8")


def test_installation_has_no_preselected_provider_or_model(
        tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    assert discover_profiles() == ()


def test_user_can_build_and_select_any_exact_provider_model_pair(
        tmp_path: Path, monkeypatch, capsys,
) -> None:
    catalog = tmp_path / "provider_models.json"
    output = tmp_path / "profiles"
    _catalog(catalog)
    build_provider_catalog(catalog, output)
    monkeypatch.setenv("RPNH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("RPNH_PROFILE_DIR", str(output / "execution"))

    selected = select_profile("user provider", "vendor/model:future@1")
    assert selected.selection_id == "user-model"
    assert selected.provider == "user provider"
    assert selected.model_condition == "vendor/model:future@1"
    assert read_selected_path() == selected.path
    selection = load_llm_execution_selection(selected.path)
    assert selection.adapter_config_path.is_absolute()
    assert selection.adapter_config_path.is_file()
    assert selection.adapter_config_path.parent.name == "adapters"
    assert selection.as_registry_policy()["adapter_profile"]["recovery"] == (
        _recovery())
    assert selection.input_target.context_window_tokens == 131072
    assert (selection.input_target.context_compaction_retained_tokens
            == 16384)
    assert selected.as_public_dict()["recovery"] == _recovery()
    assert rpnh_main(["config", "show"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["recovery"] == _recovery()
    assert shown["context_window_tokens"] == 131072
    assert shown["context_compaction_retained_tokens"] == 16384
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": "rpnh/cli_config/v3",
        "profile": "user-model",
        "provider": "user provider",
        "model_condition": "vendor/model:future@1",
    }


def test_net_command_requires_an_existing_run_source() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["net", "show", "--format", "json"])
    assert exc_info.value.code == 2


def test_resource_places_and_edges_are_hidden_until_requested() -> None:
    projection = {
        "schema_version": "rpnh/net_view/v1",
        "source": {"mode": "registry_run"},
        "nodes": [
            {
                "id": "request", "kind": "place", "category": "place",
                "hidden_by_default": False,
            },
            {
                "id": "worker.run", "kind": "transition",
                "category": "execution", "hidden_by_default": False,
            },
            {
                "id": "worker.capacity", "kind": "place",
                "category": "resource", "hidden_by_default": True,
            },
        ],
        "edges": [
            {
                "id": "request-edge", "source": "request",
                "target": "worker.run", "resource": False,
                "hidden_by_default": False,
            },
            {
                "id": "capacity-borrow", "source": "worker.capacity",
                "target": "worker.run", "resource": True,
                "hidden_by_default": True,
            },
            {
                "id": "capacity-return", "source": "worker.run",
                "target": "worker.capacity", "resource": True,
                "hidden_by_default": True,
            },
        ],
    }
    ordinary = _filter_projection(
        projection, show_resources=False, resources_only=False, node_id=None)
    assert {node["id"] for node in ordinary["nodes"]} == {
        "request", "worker.run"}
    assert [edge["id"] for edge in ordinary["edges"]] == ["request-edge"]

    expanded = _filter_projection(
        projection, show_resources=True, resources_only=False, node_id=None)
    assert {node["id"] for node in expanded["nodes"]} == {
        "request", "worker.run", "worker.capacity"}
    assert expanded["summary"]["resource_place_count"] == 1
    assert expanded["summary"]["resource_edge_count"] == 2
