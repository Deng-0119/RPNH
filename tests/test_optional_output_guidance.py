from __future__ import annotations

from types import SimpleNamespace

from jsonschema import Draft7Validator

from cpn.components.agent_loop.optional_host_bindings import _declared_agent_prompt
from cpn.components.agent_loop.request_envelope_materialization import (
    materialize_agent_request_envelope,
)
from cpn.rpnh.agent_tasks import (
    AgentStage, agent_task_registration, build_agent_task_module,
)
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.llm_contracts import LLMInputTarget


def test_declared_prompt_preserves_exact_outcomes_and_schema_aware_content() -> None:
    operation = SimpleNamespace(declaration=SimpleNamespace(
        config={"node_synopsis": "Produce the declared result."},
        outcomes=tuple(SimpleNamespace(
            name=name, products=(SimpleNamespace(port=port),),
        ) for name, port in (
            ("accepted", "worker.result"),
            ("needs_revision", "worker.feedback"),
            ("interrupted", "worker.checkpoint"),
        )),
    ))

    prompt = _declared_agent_prompt(
        operation, LLMInputTarget("offline", 1024, 8192))
    instruction = prompt["messages"][1]["content"]

    assert prompt["messages"][0]["content"] == "Produce the declared result."
    assert "accepted=worker.result; needs_revision=worker.feedback" in instruction
    assert "interrupted=" not in instruction
    assert "outcome_id to the exact name before '='" in instruction
    assert "never substitute internal port_* handles" in instruction
    assert "application/rpnh_agent_text/v1" in instruction
    assert "content is direct text" in instruction
    assert "content is one JSON document" in instruction
    assert "validated against the declared schema" in instruction
    assert "source_resource_ref" in instruction
    assert "never both" in instruction
    assert "workspace bytes are never an implicit write source" in instruction
    assert "text result must include JSON string quotes" not in instruction
    assert "content field carries serialized JSON bytes" not in instruction
    assert "timed-out or nonzero-exit workspace action remains immutable" in instruction
    assert instruction.endswith("as the final tool call.")


def test_materialized_optional_catalog_and_prompt_agree_on_write_content() -> None:
    module = build_agent_task_module((AgentStage("main", "Produce a result."),))
    compiled = compile_module(module, agent_task_registration())
    operation = next(item for item in compiled.operations
                     if item.declaration.name == "main.run")
    prompt = _declared_agent_prompt(
        operation, LLMInputTarget("offline", 1024, 8192))
    descriptors = [
        {"name": name, **{
            field: compiled.registrations["tool"][name]["contracts"][field]
            for field in ("description", "arguments")
        }}
        for name in operation.declaration.tools
    ]
    envelope = materialize_agent_request_envelope(
        model_condition=prompt["model_condition"],
        max_output_tokens=prompt["max_output_tokens"],
        system_content="Follow the exact declared tool contracts.",
        prompt_messages=prompt["messages"], history_messages=(),
        tool_descriptors=descriptors,
        source_prompt_ref={"fixture": "prompt"},
        tool_catalog_ref={"fixture": "catalog"},
    )
    write = next(item["function"] for item in envelope["tools"]
                 if item["function"]["name"] == "write_file")
    instructions = "\n".join(item["content"] for item in envelope["messages"])

    for guidance in ("content is direct text", "content is one JSON document"):
        assert guidance in write["description"]
        assert guidance in instructions
    assert "one exact visible source_resource_ref" in write["description"]
    assert "text result must include JSON string quotes" not in instructions

    validator = Draft7Validator(write["parameters"])
    base = {"path": "outputs/result.txt", "description": "Result."}
    source = {
        "resource_id": "resource:" + "0" * 32,
        "resource_version_id": "resource_version:" + "1" * 32,
    }
    assert validator.is_valid({**base, "content": "direct text"})
    assert validator.is_valid({**base, "source_resource_ref": source})
    for invalid in (
        base,
        {**base, "content": "direct text", "source_resource_ref": source},
        {**base, "source_resource_ref": {"path": "outputs/result.txt"}},
    ):
        assert not validator.is_valid(invalid)
