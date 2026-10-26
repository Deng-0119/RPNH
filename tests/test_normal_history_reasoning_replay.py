"""Offline boundary fixtures for the opt-in normal-history replay policy.

The isolated history fixture supplies immutable records, not a Registry or a
provider-quality oracle. The task fixture separately exercises real Registry
publication and delegation using a synthetic input port, without network I/O.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft7Validator, ValidationError

from cpn.components.agent_loop.compact import (
    bound_tool_result_groups, build_replacement_history, reduce_tool_messages,
)
from cpn.components.agent_loop.context import ContextExecutionMixin
from cpn.components.agent_loop.turn_records import TurnRecordsMechanicsMixin
from cpn.components.agent_loop.request_envelope_materialization import materialize_agent_request_envelope
from cpn.components.registered_host_llm import registered_host_execution_identity
from cpn.llm_adapters.config import (
    LLMExecutionConfigError, load_llm_execution_selection,
    normal_history_reasoning_replay_from_policy,
)
from cpn.llm_adapters._external_provider_protocol import (
    normalize_openai_compatible_response, provider_request_from_envelope,
)
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog, TypedReadError
from cpn.rpnh.provider_setup import build_provider_catalog
from cpn.rpnh.registry.schema_catalog import canonical_json

from test_reasoning_effort_configuration import _external_catalog


FLAG = "normal_history_reasoning_replay"
SENTINEL = "  synthetic reasoning 中 e\u0301 😀\n "


def _selection(tmp_path, value="absent", *, legacy=False):
    document = _external_catalog()
    model = document["providers"][0]["models"][0]
    if legacy:
        document["schema_version"] = "rpnh/provider_model_catalog/v2"
        model.pop("reasoning_efforts")
    if value != "absent":
        model[FLAG] = value
    path = tmp_path / "models.json"
    path.write_text(json.dumps(document))
    build_provider_catalog(path, tmp_path / "profiles")
    return load_llm_execution_selection(
        tmp_path / "profiles/execution/future-model.json")


@pytest.mark.parametrize("value,legacy", [("absent", True), ("absent", False), (False, False), (True, False)])
def test_selection_schema_binding_and_legacy_default(tmp_path, value, legacy):
    selected = _selection(tmp_path, value, legacy=legacy)
    policy = selected.as_registry_policy()
    assert selected.normal_history_reasoning_replay is (value is True)
    assert normal_history_reasoning_replay_from_policy(
        policy, model_condition=selected.input_target.model_condition) is (value is True)
    identity = registered_host_execution_identity(policy)
    assert identity.get(FLAG, False) is (value is True)
    assert registered_host_execution_identity(identity) == identity
    if value is not True:
        assert FLAG not in policy  # No default policy-byte change on recovery.
    schema = Path(__file__).parents[1] / "cpn/schemas/runtime" / (
        "llm_execution_selection.v1.schema.json" if legacy else "llm_execution_selection.v2.schema.json")
    profile = tmp_path / "profiles/execution/future-model.json"
    document = json.loads(profile.read_text())
    if legacy:
        document["adapter_config_path"] = str(selected.adapter_config_path)
    Draft7Validator(json.loads(schema.read_text())).validate(document)


def test_enabled_policy_rejects_invalid_selection_and_route(tmp_path):
    selected = _selection(tmp_path, True)
    policy = selected.as_registry_policy()
    for change in (lambda p: p.update({FLAG: 1}),
                   lambda p: p.update(adapter_kind="local_process"),
                   lambda p: p["route_provenance"][0].update(protocol="unknown/v1"),
                   lambda p: p["route_provenance"][0].update(outbound_model="other-model")):
        damaged = deepcopy(policy)
        change(damaged)
        with pytest.raises(LLMExecutionConfigError):
            normal_history_reasoning_replay_from_policy(
                damaged, model_condition=selected.input_target.model_condition)
    profile = tmp_path / "profiles/execution/future-model.json"
    data = json.loads(profile.read_text()); data[FLAG] = "true"
    profile.write_text(json.dumps(data))
    with pytest.raises(LLMExecutionConfigError):
        load_llm_execution_selection(profile)


def test_public_policy_preserves_opt_in_and_rejects_wrong_type():
    from registered_public_adapter_fixtures import public_wire
    from cpn.rpnh.public_material_contracts import validate_policy
    # Existing public-policy fixture has its own exact model/route identity.
    policy = public_wire()["policy"]
    policy[FLAG] = True
    assert validate_policy(policy)[FLAG] is True
    policy[FLAG] = "true"
    with pytest.raises(ValidationError):
        validate_policy(policy)


class _History(TurnRecordsMechanicsMixin):
    """Bounded in-memory source fixture for the existing mechanical method."""
    def __init__(self, responses, overlay=None):
        self.responses, self.overlay = responses, overlay
    def latest_context_overlay(self, loop): return self.overlay
    def turn_events(self, loop):
        return tuple(SimpleNamespace(payload={"sequence": i}) for i in range(len(self.responses)))
    def hydrate_turn_event(self, loop, event, *, read_response):
        i = event.payload["sequence"]
        # No-tool assistant turns also require replay when the host opts in.
        return SimpleNamespace(response_resource_ref=i, llm_invocation_ref=i, tool_calls=())
    def _request_owner_messages(self, invocation_ref, *, read_resource):
        # This isolated fixture contains no owner-input records.
        return ()
    def turn_ref_from_event(self, event): return event.payload["sequence"]
    def history(self, **kwargs):
        return self.history_messages(None, read_response=lambda ref: self.responses[ref],
                                     render_tool_result=lambda *a: {}, **kwargs)


def _canonical_response(reasoning="absent"):
    message = {"role": "assistant", "content": "visible"}
    if reasoning != "absent": message["reasoning_content"] = reasoning
    return normalize_openai_compatible_response(canonical_json({"choices": [{
        "message": message, "finish_reason": "stop"}]}))


def test_normal_replay_presence_original_values_recipe_wire_and_rehydration():
    responses = [_canonical_response(value) for value in (SENTINEL, "", "absent", None)]
    history = _History(responses).history(normal_history_reasoning_replay=True)
    assert history[0]["reasoning_content"] == SENTINEL
    assert history[1]["reasoning_content"] == ""
    assert all("reasoning_content" not in item for item in history[2:])
    assert _History(list(responses)).history(normal_history_reasoning_replay=True) == history
    assert _History(responses).history() == _History(responses).history(normal_history_reasoning_replay=False)
    assert all("reasoning_content" not in item for item in _History(responses).history())
    envelope = materialize_agent_request_envelope(
        model_condition="exact-model", max_output_tokens=128,
        system_content="system", prompt_messages=[{"role": "user", "content": "task"}],
        history_messages=history, tool_descriptors=[{
            "name": "complete_interaction", "description": "Finish.",
            "arguments": {"type": "object", "properties": {}}}],
        source_prompt_ref={"resource_id": "resource:" + "1" * 32,
                           "resource_version_id": "resource_version:" + "2" * 32},
        tool_catalog_ref={"resource_id": "resource:" + "3" * 32,
                          "resource_version_id": "resource_version:" + "4" * 32})
    wire = json.loads(provider_request_from_envelope(canonical_json(envelope),
        expected_model_condition="exact-model", expected_max_output_tokens=128,
        outbound_model="exact-model"))
    assert wire["messages"][-4:] == list(history)
    assert wire["stream"] is False
    # Actual Registry recipe schema, reached by both normal and compaction
    # publication. This is schema construction, not a publication claim.
    recipe = dict(envelope)
    recipe.pop("protocol")
    recipe["schema_version"] = "logical_provider_request_recipe/v1"
    recipe["model"] = recipe.pop("model_condition")
    recipe["max_tokens"] = recipe.pop("max_output_tokens")
    from cpn.rpnh.agent_tasks import agent_task_catalog
    catalog = agent_task_catalog()
    schema_id = "registry_v1/logical_provider_request_recipe/v1"
    catalog.validate_schema_ref(schema_id, recipe)
    assert recipe["messages"][-4:] == list(history)
    # Reasoning is assistant-only text; opaque and other unknown fields stay
    # closed. Presence/empty/absence above are checked at the actual schema.
    schema_path = Path(__file__).parents[1] / "cpn/schemas/registry_v1/logical_provider_request_recipe.v1.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = Draft7Validator(schema)
    for role in ("system", "user", "tool"):
        damaged = deepcopy(recipe)
        damaged["messages"][-4]["role"] = role
        with pytest.raises(ValidationError): validator.validate(damaged)
    for field, value in (("reasoning_content", None), ("reasoning_content", 1),
                         ("encrypted_content", "synthetic-opaque")):
        damaged = deepcopy(recipe)
        damaged["messages"][-4][field] = value
        with pytest.raises(ValidationError): validator.validate(damaged)
    assert FLAG not in wire  # HOST policy is not a vendor parameter.


def test_retained_groups_and_overlay_preserve_original_reasoning_and_budget():
    assistant = {"role": "assistant", "content": "", "reasoning_content": SENTINEL,
        "tool_calls": [{"id": "call", "type": "function", "function": {
            "name": "read_file", "arguments": '{ "path" : "a" }'}}]}
    history = (assistant, {"role": "tool", "tool_call_id": "call", "content": "result"})
    reduced = bound_tool_result_groups(reduce_tool_messages(history, byte_limit=128))
    replacement = build_replacement_history(reduced, "summary", retained_history_token_limit=2048,
        fact_capsule={"kind": "agent_context_fact_capsule", "schema_version": "agent_context_fact_capsule/v1"})
    retained = tuple(entry["message"] for entry in replacement[2:])
    assert retained == history
    overlay = SimpleNamespace(last_turn_sequence=1, model_visible_messages=retained)
    assert _History([], overlay).history(normal_history_reasoning_replay=True) == history
    assert "reasoning_content" not in replacement[0] and SENTINEL not in replacement[1]["content"]
    oversized = deepcopy(assistant); oversized["reasoning_content"] = "x" * 40000
    with pytest.raises(ValueError):
        bound_tool_result_groups((oversized, history[1]))


def test_registered_backend_scope_and_public_reader_do_not_export_reasoning(tmp_path):
    selected = _selection(tmp_path, True)
    backend = {"model": selected.input_target.model_condition, "selection": selected.as_registry_policy()}
    service = SimpleNamespace(_static=lambda context, role: (None, None, canonical_json(backend)))
    loop = SimpleNamespace(model_condition=backend["model"])
    assert ContextExecutionMixin._normal_history_reasoning_replay(service, object(), loop)
    from cpn.rpnh.registry.errors import ResourceIntegrityFault
    with pytest.raises(ResourceIntegrityFault):
        ContextExecutionMixin._normal_history_reasoning_replay(service, object(), SimpleNamespace(model_condition="other"))
    readers = TypedReaderCatalog()
    with pytest.raises(TypedReadError, match="UNSUPPORTED_ENTRY_TYPE"):
        readers.fields("agent_context_compaction/v3")
    fields = readers.fields("resource_version/v1")
    assert "reasoning_content" not in fields and "replacement_history" not in fields


@pytest.mark.parametrize("enabled", [False, True])
def test_offline_registry_normal_and_delegation_keep_distinct_policies(tmp_path, monkeypatch, enabled):
    from test_optional_delegate_leaf import _DelegatedLeafPort, _tool_names
    from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, run_agent_task
    from cpn.rpnh.llm_contracts import LLMInputResponseBytes
    selected = _selection(tmp_path, enabled)

    class Port(_DelegatedLeafPort):
        def request_once(self, attempt):
            envelope = json.loads(attempt.canonical_request_bytes)
            prior = [m for m in envelope["messages"] if m.get("role") == "assistant"]
            for message in prior:
                expected = enabled if "delegate_leaf" in _tool_names(envelope) else True
                assert ("reasoning_content" in message) is expected
                if expected: assert message["reasoning_content"] == SENTINEL
            result = json.loads(super().request_once(attempt))
            result["reasoning_content"] = SENTINEL
            return LLMInputResponseBytes(canonical_json(result), status_code=200, external_request_id=None)
    port = Port(); port.execution_policy = selected.as_registry_policy()
    monkeypatch.setattr("cpn.rpnh.agent_tasks.load_llm_execution_selection", lambda _path: selected)
    monkeypatch.setattr("cpn.rpnh.agent_tasks.build_llm_input_port", lambda *a, **k: port)
    result = run_agent_task(AgentTaskSpec(run_dir=tmp_path / "run", prompt="Complete one offline task.",
        stages=(AgentStage("main", "Produce the requested result."),),
        execution_config_path=tmp_path / "profiles/execution/future-model.json", max_attempts_per_stage=4))
    assert result["stop_reason"] == "terminal"
    assert result["output"] == "delegate lifecycle complete"
    assert len(port.requests) == 4
