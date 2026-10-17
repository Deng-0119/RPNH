"""Registered-read history never infers full provider delivery from a locator."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from cpn.components.agent_loop.compact import (
    prior_tool_result_reference, reduce_tool_messages,
)
from cpn.components.agent_loop.compaction import CompactionExecutionMixin
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef


@pytest.mark.parametrize("body_size", [30, 11030])
def test_normal_and_bounded_reads_have_truthful_compaction_locators(body_size):
    metadata = {
        "kind": "registered_file_read/v1",
        "path": "registered_resources/exact/content",
        "resource_ref": {"resource_id": "resource:" + "a" * 32,
                         "resource_version_id": "resource_version:" + "b" * 32},
        "use_receipt_ref": {"entity_type": "resource_use_receipt/v1",
                            "logical_id": "resource_use_receipt:" + "c" * 32,
                            "version_id": "resource_use_receipt_version:" + "d" * 32},
    }
    action_ref = VersionRef("agent_action/v2", TypedId.parse("agent_action:" + "e" * 32),
                            TypedId.parse("agent_action_version:" + "f" * 32))
    action = SimpleNamespace(result_metadata=metadata, tool_error_ref=None)
    original = deepcopy(metadata)
    immediate = reduce_tool_messages([{
        "role": "tool", "tool_call_id": "read-1",
        "content": json.dumps(dict(metadata, content="x" * body_size)),
    }], byte_limit=10000)[0]["content"]
    assert ("bytes omitted" in immediate) == (body_size > 10000)
    captured = []

    def history(loop, *, render_tool_result, **kwargs):
        projected = render_tool_result(action, action_ref)
        captured.append(projected)
        return (
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "read-1", "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps({"path": metadata["path"]})}}]},
            {"role": "tool", "tool_call_id": "read-1", "content": json.dumps(projected)},
        )

    class HistoryCaptured(Exception):
        pass

    def stop(context):
        raise HistoryCaptured

    catalog_ref = object()
    host = SimpleNamespace(
        _context=lambda loop: None,
        _static=lambda context, name: (catalog_ref, None, b'{"messages":[]}'),
        _target=stop,
        prepare_agent_system_initialization_v1=lambda *args: SimpleNamespace(payload=b"system", located_inputs=()),
        mechanical_lifecycle=SimpleNamespace(history_messages=history),
    )
    with pytest.raises(HistoryCaptured):
        CompactionExecutionMixin._compaction_envelope(
            host, object(), SimpleNamespace(tool_catalog_ref=catalog_ref),
            SimpleNamespace(tool_names=("read_file",)),
            SimpleNamespace(tool_output_byte_limit=10000))
    projected = captured[0]
    assert projected == prior_tool_result_reference(metadata, {
        "entity_type": action_ref.entity_type,
        "logical_id": str(action_ref.entity_id), "version_id": str(action_ref.version_id)})
    assert projected["path"] == metadata["path"]
    assert projected["resource_ref"] == metadata["resource_ref"]
    assert projected["use_receipt_ref"] == metadata["use_receipt_ref"]
    assert projected["body_replayed"] is False
    assert projected["model_visible_history"] == "reference_only"
    notice = projected["history_notice"]
    assert "complete registered file page was delivered" not in notice
    assert "does not establish" in notice
    assert all(word in notice for word in ("read_file", "offset_chars", "max_chars"))
    assert metadata == original
