from __future__ import annotations

import json

import pytest

from cpn.components.agent_loop.tool_catalog import (
    AGENT_TOOL_NAMES, DEFAULT_AGENT_TOOL_NAMES, TOOL_ARGUMENT_SCHEMAS,
    build_agent_tool_catalog, model_visible_agent_tool_description,
)
from cpn.components.agent_loop.tool_validation import (
    AgentToolSyntaxError, ValidatedAgentToolAction, validate_agent_tool_call,
)
from cpn.rpnh.agent_tasks import AgentStage, agent_task_registration, build_agent_task_module
from cpn.rpnh.compiler import compile_module


def validate(arguments):
    return validate_agent_tool_call(
        loop_id="agent_loop:" + "0" * 32, turn_sequence=1,
        expected_revision=0, tool_call_id="candidate", tool_name="submit_candidate",
        raw_arguments=json.dumps(arguments))


def test_candidate_catalog_is_explicit_and_sorted():
    assert AGENT_TOOL_NAMES == tuple(sorted(set(AGENT_TOOL_NAMES)))
    assert "submit_candidate" not in DEFAULT_AGENT_TOOL_NAMES
    assert "submit_candidate" not in build_agent_tool_catalog().tool_names
    catalog = build_agent_tool_catalog(tool_names=(
        "complete_interaction", "read_file", "search_text", "submit_candidate"))
    assert catalog.argument_schemas["submit_candidate"] == TOOL_ARGUMENT_SCHEMAS["submit_candidate"]
    assert "QuayDrop" not in model_visible_agent_tool_description("submit_candidate")


def test_normal_task_builder_does_not_gain_candidate_tool():
    module = build_agent_task_module((AgentStage("main", "Produce a result."),))
    compiled = compile_module(module, agent_task_registration())
    operation = next(item for item in compiled.operations
                     if item.declaration.name == "main.run")
    assert "submit_candidate" not in operation.declaration.tools
    assert "write_file" in operation.declaration.tools


@pytest.mark.parametrize("outcome", [None, "complete", "other_success"])
def test_candidate_accepts_generic_structured_bundle(outcome):
    arguments = {"products": [
        {"output_port_id": "review.analysis", "content": {"value": 1}},
        {"output_port_id": "review.plan", "content": {"items": []}},
    ]}
    if outcome is not None:
        arguments["outcome_id"] = outcome
    result = validate(arguments)
    assert isinstance(result, ValidatedAgentToolAction)


@pytest.mark.parametrize("arguments", [
    {}, {"products": []},
    {"analysis": {}, "plan": {}},
    {"products": [{"output_port_id": "result", "content": "{}"}]},
    {"products": [{"output_port_id": "result", "content": {}, "path": "x"}]},
    {"products": [{"output_port_id": "result", "content": {}},
                  {"output_port_id": "result", "content": {"different": True}}]},
])
def test_candidate_rejects_invalid_or_duplicate_ports(arguments):
    result = validate(arguments)
    assert isinstance(result, AgentToolSyntaxError)
    assert result.code == "arguments_invalid"
    assert "$arguments" in result.detail


def initialization(tool_names, output_kind="ordinary_document"):
    from cpn.components.agent_loop.context import build_agent_system_initialization
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    return build_agent_system_initialization(
        invocation_ref=VersionRef("invocation/v1", new_id("invocation"), new_id("invocation_version")),
        sponsoring_transition_firing_ref=None, role="agent",
        catalog=build_agent_tool_catalog(tool_names=tuple(sorted(tool_names))),
        located_inputs=(), output_kind=output_kind, output_contract="Required outputs.",
        llm_turn_cap=10, llm_turns_used=0, task_call_cap=None, task_calls_used=0,
        semantic_output_port_ids=("result",)).payload.decode()


@pytest.mark.parametrize("output_kind", ["ordinary_document", "finalization_review"])
@pytest.mark.parametrize("workspace", [False, True])
def test_candidate_initialization_has_no_missing_write_capability(output_kind, workspace):
    names = ["complete_interaction", "read_file", "search_text", "submit_candidate"]
    if workspace:
        names.append("workspace")
    prompt = initialization(names, output_kind)
    assert "write_file" not in prompt
    assert "submit_candidate" in prompt
    assert "JSON object content" in prompt
    assert "complete_interaction as the final ordered tool call" in prompt
    assert "accepted bundle supplies the exact registered product and check" in prompt


def test_normal_initialization_keeps_original_write_guidance():
    prompt = initialization(["complete_interaction", "write_file"])
    assert "Complete only after write_file has registered every intended output file" in prompt
    assert "every write_file call must use one of those exact ids" in prompt
    assert "submit_candidate" not in prompt


def accepted_record():
    from cpn.components.agent_loop.models import AgentActionRecord, AgentLoopState, stable_action_id
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.models import VersionRef
    from cpn.rpnh.registry.publication import _ref_payload
    from cpn.rpnh.registry.resource_service import _resource_payload
    from cpn.rpnh.registry.resources import ResourceVersionRef
    loop_id = str(new_id("agent_loop"))
    refs = [ResourceVersionRef(new_id("resource"), new_id("resource_version")) for _ in range(2)]
    check_ref = ResourceVersionRef(new_id("resource"), new_id("resource_version")).as_version_ref()
    arguments = {"products": [{"output_port_id": name, "content": {}}
                              for name in ("first", "second")]}
    return AgentActionRecord(
        action_id=stable_action_id(loop_id, 1, "candidate"), loop_id=loop_id,
        turn_sequence=1, tool_call_ordinal=0, tool_call_id="candidate",
        action_identity_kind="tool_call_id", action_identity_key="candidate",
        tool_name="submit_candidate", raw_arguments=json.dumps(arguments),
        arguments=arguments, expected_revision=0, state=AgentLoopState.ACTION_APPLIED,
        result_refs=tuple(ref.as_version_ref() for ref in refs) + (check_ref,),
        result_metadata={"kind": "content_candidate_accepted/v1",
                         "products": [{"output_port_id": name, "resource_ref": _resource_payload(ref)}
                                      for name, ref in zip(("first", "second"), refs)],
                         "check_ref": _ref_payload(check_ref)})


def action_document(record):
    from cpn.components.agent_loop.action_records import ActionRecordsMechanicsMixin
    from cpn.rpnh.registry.identities import new_id, TypedId
    from cpn.rpnh.registry.models import VersionRef
    return ActionRecordsMechanicsMixin._action_document(
        record, action_ref=VersionRef("agent_action/v2", TypedId.parse(record.action_id), new_id("agent_action_version")),
        loop_ref=VersionRef("agent_loop/v1", TypedId.parse(record.loop_id), new_id("agent_loop_version")),
        turn_ref=VersionRef("agent_turn/v4", new_id("agent_turn"), new_id("agent_turn_version")))


def action_catalog():
    from cpn.components.agent_loop.context import optional_agent_loop_schema_data
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    schemas, types = optional_agent_loop_schema_data()
    return SchemaCatalog(schemas=schemas, types=types)


def test_accepted_candidate_is_valid_original_schema_catalog_action():
    action_catalog().validate_instance("agent_action/v2", category="object",
                                       instance=action_document(accepted_record()))


@pytest.mark.parametrize("mutation", ["extra", "missing_check", "wrong_refs", "duplicate_port", "wrong_port"])
def test_candidate_action_metadata_remains_closed_and_exact(mutation):
    from copy import deepcopy
    from dataclasses import replace
    from cpn.components.agent_loop.models import AgentLoopProtocolError
    record = accepted_record()
    metadata = deepcopy(record.result_metadata)
    kwargs = {}
    if mutation == "extra":
        metadata["PASS"] = True
    elif mutation == "missing_check":
        del metadata["check_ref"]
    elif mutation == "wrong_refs":
        kwargs["result_refs"] = record.result_refs[:-1]
    elif mutation == "duplicate_port":
        metadata["products"][1]["output_port_id"] = "first"
    else:
        metadata["products"][1]["output_port_id"] = "foreign"
    with pytest.raises(AgentLoopProtocolError):
        replace(record, result_metadata=metadata, **kwargs)


def test_schema_catalog_rejects_candidate_metadata_extension():
    from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError
    document = action_document(accepted_record())
    document["result_metadata"]["PASS"] = True
    with pytest.raises(SchemaGovernanceError):
        action_catalog().validate_instance("agent_action/v2", category="object", instance=document)


def candidate_service(bound=True):
    from types import SimpleNamespace as NS
    marker = {"success_outcomes": ["complete"]} if bound else None
    operation = NS(declaration=NS(
        config={"firing_content_contract": marker} if bound else {},
        outcomes=(NS(name="complete", products=(NS(port="review.first"), NS(port="review.second"))),),
        outputs=("review.first", "review.second")))
    compiled = NS(ports=(NS(name="review.first", port_id="port_first"),
                         NS(name="review.second", port_id="port_second")))
    return NS(core=object(), _context=lambda loop: object(),
              _declared=lambda context: (None, compiled, operation)), operation


@pytest.mark.parametrize("outcome", [None, "complete"])
def test_submit_adapter_uses_core_preflight_seal_and_exact_check(monkeypatch, outcome):
    from types import SimpleNamespace as NS
    from cpn.components.agent_loop.action_execution import _submit_candidate, OPTIONAL_TOOL_BINDINGS
    from cpn.rpnh import firing_content_contract as contract
    from cpn.rpnh.registry.identities import new_id
    from cpn.rpnh.registry.publication import _resource_from_payload
    from cpn.rpnh.registry.resources import ResourceVersionRef
    from cpn.rpnh.registry.schema_catalog import canonical_json
    record = accepted_record()
    refs = [_resource_from_payload(item["resource_ref"]) for item in record.result_metadata["products"]]
    check = ResourceVersionRef(new_id("resource"), new_id("resource_version"))
    calls = []
    def preflight(core, execution, **kwargs):
        calls.append(("preflight", core, execution, kwargs))
    def seal(core, execution, **kwargs):
        calls.append(("seal", core, execution, kwargs))
        return NS(outputs=tuple(NS(port_id=port, resource_ref=ref)
                                for port, ref in zip(("port_first", "port_second"), refs)))
    monkeypatch.setattr(contract, "preflight_candidate", preflight)
    monkeypatch.setattr(contract, "seal_candidate", seal, raising=False)
    monkeypatch.setattr(contract, "get_firing_content_check", lambda *args: (check, {}), raising=False)
    service, _ = candidate_service()
    execution = object()
    arguments = {"products": [{"output_port_id": name, "content": {"value": index}}
                              for index, name in enumerate(("review.first", "review.second"))]}
    if outcome is not None:
        arguments["outcome_id"] = outcome
    result_refs, metadata = _submit_candidate(
        service=service, execution=execution, loop=object(), turn=object(),
        arguments=arguments, idempotency_key="same-action")
    assert [call[0] for call in calls] == ["preflight", "seal"]
    assert calls[0][1:3] == (service.core, execution)
    assert calls[0][3]["products"] == {
        item["output_port_id"]: canonical_json(item["content"]) for item in arguments["products"]}
    assert calls[1][3]["selected_outcome_id"] == "complete"
    assert calls[1][3]["idempotency_key"] == "same-action"
    assert result_refs == tuple(ref.as_version_ref() for ref in refs) + (check.as_version_ref(),)
    assert metadata["check_ref"]["version_id"] == str(check.resource_version_id)
    assert [item["output_port_id"] for item in metadata["products"]] == ["review.first", "review.second"]
    assert "submit_candidate" in OPTIONAL_TOOL_BINDINGS


def test_rejected_preflight_never_seals_candidate(monkeypatch):
    from cpn.components.agent_loop.action_execution import _submit_candidate
    from cpn.rpnh import firing_content_contract as contract
    def reject(*args, **kwargs):
        raise contract.FiringContentContractError("products.first.value: unsupported source")
    def unexpected(*args, **kwargs):
        pytest.fail("rejected candidate reached sealing/check retrieval")
    monkeypatch.setattr(contract, "preflight_candidate", reject)
    monkeypatch.setattr(contract, "seal_candidate", unexpected, raising=False)
    monkeypatch.setattr(contract, "get_firing_content_check", unexpected, raising=False)
    service, _ = candidate_service()
    with pytest.raises(contract.FiringContentContractError, match="unsupported source"):
        _submit_candidate(service=service, execution=object(), loop=object(), turn=object(),
                          arguments={"products": [{"output_port_id": "review.first", "content": {}}]},
                          idempotency_key="rejected-action")


def test_contract_prompt_uses_candidate_and_original_prompt_uses_write():
    from cpn.components.agent_loop.optional_host_bindings import _declared_agent_prompt
    from cpn.rpnh.llm_contracts import LLMInputTarget
    for bound in (False, True):
        _, operation = candidate_service(bound=bound)
        prompt = _declared_agent_prompt(operation, LLMInputTarget("offline", 1024, 8192))
        content = "\n".join(message["content"] for message in prompt["messages"])
        if bound:
            assert "submit_candidate" in content
            assert "write_file" not in content
            assert "review.first,review.second" in content
            assert "final tool call" in content
        else:
            assert "On every write_file call" in content
            assert "submit_candidate" not in content


def test_direct_write_rejects_contract_bound_success_port():
    from cpn.components.agent_loop.workspace import WorkspaceExecutionMixin
    service, _ = candidate_service()
    service._strict_relative_path = lambda path: path
    with pytest.raises(ValueError, match="use submit_candidate"):
        WorkspaceExecutionMixin._write_product(
            service, object(), object(), {"path": "outputs/result.json", "output_port_id": "review.first",
                                         "content": "{}", "description": "Result"}, "direct-write")
