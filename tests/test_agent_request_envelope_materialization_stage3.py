from cpn.components.agent_loop.request_envelope_materialization import (
    materialize_agent_request_envelope,
)


def _values():
    prompt_message = {"role": "user", "content": "prompt"}
    history_message = {
        "role": "tool", "tool_call_id": "call-1", "content": "result"}
    first_parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    second_parameters = {"type": "object", "properties": {}}
    tool_descriptors = [
        {"name": "write_file", "description": "Write a file.",
         "arguments": first_parameters},
        {"name": "complete_interaction", "description": "Finish.",
         "arguments": second_parameters},
    ]
    prompt_ref = {"resource_id": "prompt", "version_id": "prompt-v1"}
    catalog_ref = {"resource_id": "catalog", "version_id": "catalog-v1"}
    return (prompt_message, history_message, tool_descriptors,
            prompt_ref, catalog_ref)


def test_materializes_normal_request_envelope_without_checkpoint() -> None:
    prompt_message, history_message, tools, prompt_ref, catalog_ref = _values()

    envelope = materialize_agent_request_envelope(
        model_condition="offline", max_output_tokens=512,
        system_content="system", prompt_messages=[prompt_message],
        history_messages=[history_message], tool_descriptors=tools,
        source_prompt_ref=prompt_ref, tool_catalog_ref=catalog_ref)

    assert envelope == {
        "protocol": "llm_request_envelope/v1",
        "model_condition": "offline",
        "max_output_tokens": 512,
        "messages": [
            {"role": "system", "content": "system"}, prompt_message,
            history_message,
        ],
        "tools": [
            {"type": "function", "function": {
                "name": "write_file", "description": "Write a file.",
                "parameters": tools[0]["arguments"]}},
            {"type": "function", "function": {
                "name": "complete_interaction", "description": "Finish.",
                "parameters": tools[1]["arguments"]}},
        ],
        "tool_choice": "auto",
        "source_prompt_ref": prompt_ref,
        "tool_catalog_ref": catalog_ref,
        "placeholders": [],
    }
    assert envelope["messages"][1] is prompt_message
    assert envelope["messages"][2] is history_message
    assert envelope["tools"][0]["function"]["parameters"] is tools[0]["arguments"]
    assert envelope["source_prompt_ref"] is prompt_ref
    assert envelope["tool_catalog_ref"] is catalog_ref


def test_materializes_checkpoint_after_history_without_reordering_tools() -> None:
    prompt_message, history_message, tools, prompt_ref, catalog_ref = _values()

    envelope = materialize_agent_request_envelope(
        model_condition="offline", max_output_tokens=512,
        system_content="system", prompt_messages=[prompt_message],
        history_messages=[history_message], tool_descriptors=tools,
        source_prompt_ref=prompt_ref, tool_catalog_ref=catalog_ref,
        checkpoint_prompt="checkpoint")

    assert envelope["messages"] == [
        {"role": "system", "content": "system"}, prompt_message,
        history_message, {"role": "system", "content": "checkpoint"},
    ]
    assert [item["function"]["name"] for item in envelope["tools"]] == [
        "write_file", "complete_interaction"]
