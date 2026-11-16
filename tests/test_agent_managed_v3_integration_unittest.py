"""Independent AgentLoop v3 identity integration on real Registry/PN execution.

The LLM input port is an explicit two-response script. The existing test-only
pipe wake transport leaves original owner dispatch methods in place; it does
not claim native owner-socket coverage. Installed factory selection uses the
real catalog loader. Managed admission, spawned worker, receipts, AgentLoop
action publication, PN Success and terminal inspection are not replaced.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cpn.plugins import API_VERSION_V2, PluginDefinition, PluginOperation, plugin_implementation
from cpn.plugins.catalog import load_catalog
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec, agent_task_catalog, run_agent_task
from cpn.rpnh.llm_contracts import LLMInputResponseBytes
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from multi_tool_offline_support import SessionPipeOwnerEventLoop


@plugin_implementation("tests.independent_agent_v3_double", "1")
def installed_double(context, arguments):
    return {"answer": arguments["value"] * 2, "worker_pid": os.getpid()}


def installed_factory():
    return PluginDefinition("agent_fixture", "1", (
        PluginOperation("double", "Double an integer in the real worker",
            {"type": "object", "additionalProperties": False,
             "properties": {"value": {"type": "integer"}}, "required": ["value"]},
            {"type": "object", "additionalProperties": False,
             "properties": {"answer": {"type": "integer"}, "worker_pid": {"type": "integer"}},
             "required": ["answer", "worker_pid"]},
            installed_double, identity_version="v2", declaration_revision="1"),
    ), api_version=API_VERSION_V2)


def configuration():
    return {"schema_version": "rpnh/plugins/v2", "catalog_revision": "1", "plugins": [{
        "name": "agent_fixture", "entry_point": "agent_fixture", "version": "1",
        "binding_revision": "1", "config": {}, "environment": [],
    }]}


def selected_catalog(document):
    return load_catalog(document, factories={"agent_fixture": installed_factory})


def execution_profile(root):
    adapter = root / "adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1", "adapter_kind": "local_process",
        "model_condition": "offline-managed-v3-integration",
        "argv": [sys.executable, "-c", "pass"], "probe_argv": [sys.executable, "-c", "pass"],
        "env": {}, "inherit_env": [],
    }))
    execution = root / "execution.json"
    execution.write_text(json.dumps({
        "schema_version": "llm_execution_selection/v1", "adapter_kind": "local_process",
        "model_condition": "offline-managed-v3-integration", "adapter_config_path": str(adapter),
        "timeout_seconds": 30, "max_output_tokens": 1024, "max_response_bytes": 65536,
    }))
    return execution


class TwoResponsePort:
    def __init__(self):
        self.requests = []

    def request_once(self, attempt):
        self.requests.append(json.loads(attempt.canonical_request_bytes))
        if len(self.requests) == 1:
            calls = [{"id": "managed-v3-double", "name": "double_value",
                      "arguments": json.dumps({"value": 21})}]
        elif len(self.requests) == 2:
            calls = [
                {"id": "write-v3-result", "name": "write_file", "arguments": json.dumps({
                    "path": "out/result.txt", "description": "Versioned managed result",
                    "content": json.dumps("managed result is 42"),
                    "output_port_id": "main.result", "outcome_id": "complete"})},
                {"id": "complete-v3", "name": "complete_interaction", "arguments": "{}"},
            ]
        else:
            raise AssertionError("the actual AgentLoop did not terminate after its two scripted responses")
        return LLMInputResponseBytes(json.dumps({
            "protocol": "llm_response_envelope/v1", "tool_calls": calls,
            "finish_reason": "tool_calls",
        }, sort_keys=True, separators=(",", ":")).encode(),
            status_code=None, external_request_id=None)

    def close(self):
        pass


class AgentManagedV3IntegrationTests(unittest.TestCase):
    def test_real_agent_loop_publishes_versioned_action_and_settled_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = selected_catalog(configuration())
            port = TwoResponsePort()
            spec = AgentTaskSpec(root / "run", "Use the selected integer tool once and finish.",
                (AgentStage("main", "Call double_value with 21; publish the result and finish."),),
                execution_profile(root), max_parallel_nodes=1, max_attempts_per_stage=3,
                plugin_configuration=configuration(),
                plugin_catalog_snapshot=selected.versioned_snapshot(),
                managed_bindings={"main": {"tools": {"double_value": {
                    "selector": "agent_fixture/double", "descriptor_revision": "1"}}}})
            # Exercise the actual new TaskSpec persistence branch before run.
            spec = AgentTaskSpec.from_worker_document(spec.as_worker_document())
            self.assertIsNone(spec.plugin_catalog_digest)
            with patch("cpn.plugins.catalog.load_catalog", side_effect=selected_catalog), \
                    patch("cpn.rpnh.agent_tasks.build_llm_input_port", return_value=port), \
                    patch("cpn.rpnh.agent_tasks.OwnerEventLoop", SessionPipeOwnerEventLoop):
                result = run_agent_task(spec)
            self.assertEqual(result["stop_reason"], "terminal")
            self.assertEqual(result["actual_model_call_counts"][0], 2)
            self.assertEqual(len(port.requests), 2)

            core = _RegistryCore(spec.run_dir, create=False, read_only=True,
                                 catalog=agent_task_catalog(selected))
            rows = tuple(row for row in core.event_store.object_rows()
                         if row["object_type"] in {"agent_action/v2", "agent_action/v3"})
            managed_rows = tuple(row for row in rows if row["object_type"] == "agent_action/v3")
            self.assertEqual(len(managed_rows), 1)
            self.assertEqual(sum(row["object_type"] == "agent_action/v2" for row in rows), 2)
            action = json.loads(managed_rows[0]["metadata_json"])
            core.catalog.validate_instance("agent_action/v3", category="object", instance=action)
            self.assertEqual(action["selector"], "agent_fixture/double")
            self.assertEqual(action["outcome"], "returned")
            self.assertEqual(action["binding_identity"], selected.plugins[0].binding_identity)
            self.assertEqual(action["plugin_catalog_identity"], selected.catalog_identity)
            self.assertNotIn("binding_digest", action)
            self.assertNotIn("plugin_catalog_digest", action)
            self.assertEqual(action["output"]["answer"], 42)
            self.assertNotEqual(action["output"]["worker_pid"], os.getpid())

            turn = action["agent_turn_ref"]
            turn_ref = VersionRef(turn["entity_type"], TypedId.parse(turn["logical_id"]),
                                  TypedId.parse(turn["version_id"]))
            settled = core.event_store.settled_agent_action_rows_for_turn(turn_ref)
            self.assertIn(managed_rows[0]["version_id"], {row["version_id"] for row in settled})
            terminal = action["terminal_receipt_ref"]
            resource = core.get_version(TypedId.parse(terminal["resource_version_id"]))
            receipt = json.loads(core.object_store.read_registered(resource))
            self.assertEqual(receipt["schema_version"], "rpnh/managed_native_plugin_tool_receipt/v3")
            self.assertEqual(receipt["state"], "returned")
            self.assertEqual(receipt["output"], action["output"])
            self.assertEqual(receipt["binding_identity"], action["binding_identity"])

            # Reopen a read-only original Registry view after terminal: no new
            # model request, worker dispatch or synthetic receipt reconstruction.
            reopened = _RegistryCore(spec.run_dir, create=False, read_only=True,
                                     catalog=agent_task_catalog(selected))
            reread = reopened.get_version(TypedId.parse(managed_rows[0]["version_id"]))
            self.assertEqual(reread.metadata, action)
            self.assertEqual(len(tuple(reopened.event_store.object_rows())),
                             len(tuple(core.event_store.object_rows())))


if __name__ == "__main__":
    unittest.main()

