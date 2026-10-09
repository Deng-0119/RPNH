"""Original legacy selection/factory/bound-port construction, policy and close.

Only task-owned synthetic configuration is read. Requests, credential resolution
and physical transport are forbidden; no Registry or native worker is launched.
"""
import json
import sys

import pytest

from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.llm_adapters.factory import (
    BoundLLMInputPort, bound_llm_execution_policy, build_llm_input_port,
)
from cpn.rpnh.llm_contracts import LLMInputTarget
from registered_agent_owner_fixtures import install_owner_test_boundaries


@pytest.mark.parametrize("kind", ["local_process", "external_provider"])
def test_original_legacy_factory_policy_and_close(tmp_path, monkeypatch, record_property, kind):
    boundaries = install_owner_test_boundaries(monkeypatch)
    from cpn.llm_adapters import external_provider, local_process
    from cpn.llm_adapters import _external_provider_credentials as credentials
    for cls in (external_provider.ExternalProviderInputPort, local_process.LocalProcessInputPort):
        for name in ("request_once", "request_once_interruptible", "_request_once"):
            monkeypatch.setattr(cls, name, boundaries.forbid(cls.__name__ + "." + name))
    monkeypatch.setattr(external_provider, "_credential_headers", boundaries.forbid("credential_resolution"))
    monkeypatch.setattr(credentials, "resolved_provider_headers", boundaries.forbid("resolved_provider_headers"))
    if kind == "local_process":
        config = {
            "schema_version": "local_process_adapter_config/v1", "adapter_kind": kind,
            "model_condition": "combined-offline-model",
            "argv": [sys.executable, "-c", "raise SystemExit(91)"],
            "probe_argv": [sys.executable, "-c", "raise SystemExit(92)"],
            "env": {}, "inherit_env": [],
        }
        port_type = local_process.LocalProcessInputPort
    else:
        config = {
            "schema_version": "external_provider_adapter_config/v2", "adapter_kind": kind,
            "model_condition": "combined-offline-model",
            "recovery": {"strategy": "bounded_same_route_health_probe/v1",
                         "max_probe_attempts": 3, "probe_timeout_budget_seconds": 300,
                         "max_probe_success_formal_failure_cycles": 3},
            "routes": [{"route_id": "synthetic", "provider": "offline-provider",
                        "backend": "offline-backend", "protocol": "openai_chat_completions/v1",
                        "endpoint": "https://synthetic.example.invalid/v1/chat/completions",
                        "outbound_model": "combined-offline-model", "credential": None, "headers": {}}],
        }
        port_type = external_provider.ExternalProviderInputPort
    config_path = tmp_path / "synthetic-adapter.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    selection = LLMExecutionSelection(LLMInputTarget("combined-offline-model", 64, 65536),
                                      kind, config_path.resolve(), 30)
    policy = selection.as_registry_policy()
    closed = []
    original_close = port_type.close

    def close(original_port):
        closed.append(original_port)
        return original_close(original_port)

    monkeypatch.setattr(port_type, "close", close)
    destination = tmp_path / "unused-run"
    port = build_llm_input_port(selection, destination_run_root=destination)
    try:
        assert type(selection) is LLMExecutionSelection
        assert type(port) is BoundLLMInputPort and type(port._port) is port_type
        assert not port._port._closed
        assert port.execution_policy == bound_llm_execution_policy(port) == policy
        assert policy["adapter_kind"] == kind and policy["max_output_tokens"] == 64
        assert policy["max_response_bytes"] == 65536 and policy["timeout_seconds"] == 30
        assert policy["route_provenance"][0]["transport"] == (
            "subprocess" if kind == "local_process" else "https")
        assert str(config_path) not in json.dumps(policy)
        copy = port.execution_policy
        copy["route_provenance"][0]["transport"] = "changed-copy"
        copy["timeout_seconds"] = 999
        assert port.execution_policy == policy, "bound legacy policy must return an isolated copy"
        with pytest.raises(ValueError, match="legacy wrapper has no public identity"):
            _ = port.public_identity
    finally:
        port.close()
        assert port._port._closed and closed == [port._port]
        # Exercise close idempotence through the original wrapper/adapter pair.
        port.close()
        assert closed == [port._port, port._port] and port._port._closed
        boundaries.assert_no_external_calls()
    assert not destination.exists(), "construction/close must not create runtime/audit material"
    assert sorted(p.name for p in tmp_path.iterdir()) == [config_path.name]
    record_property("legacy_factory_evidence", json.dumps({
        "adapter_kind": kind, "wrapper": type(port).__name__, "adapter": port_type.__name__,
        "policy": policy, "original_close_calls": len(closed),
        "boundary_counts": dict(boundaries.counts), "runtime_material_created": False}, sort_keys=True))
