"""Closed-shape compatibility for original AgentLoop action/binding codecs.

The kernel view in the hydration check is an explicit metadata-only fixture;
these tests do not claim Registry authorization or a full model/tool run.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from cpn.components.agent_loop.action_records import ActionRecordsMechanicsMixin
from cpn.components.agent_loop.context import ContextExecutionMixin
from cpn.components.agent_loop.managed_identity import managed_identity_fields
from cpn.components.agent_loop.models import AgentActionRecord, AgentLoopProtocolError, AgentLoopState, stable_action_id
from cpn.plugins.api import canonical
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.registry.schema_catalog import SchemaGovernanceError


def identity(versioned=True):
    if not versioned:
        return {"binding_digest": "a" * 64, "plugin_catalog_digest": "b" * 64}
    binding = {"schema_version": "rpnh/plugin_binding/v2", "plugin_name": "tool",
               "plugin_version": "1", "binding_revision": "r1"}
    return {"binding_identity": binding, "plugin_catalog_identity": {
        "schema_version": "rpnh/plugin_catalog/v2", "revision": "r1", "bindings": [binding]}}


def record(versioned=True):
    loop = new_id("agent_loop")
    receipt = lambda: {"resource_id": str(new_id("resource")), "resource_version_id": str(new_id("resource_version"))}
    started, terminal = receipt(), receipt()
    output = {"answer": 42}
    managed = {"provider_name": "tool_call", "registration_key": "tests/tool/v1",
        "selector": "tool/call", **identity(versioned), "effect": "external_read", "outcome": "returned",
        "request_admission_receipt_ref": started, "admitted_at_utc": "2026-11-14T00:00:00Z",
        "started_receipt_ref": started, "terminal_receipt_ref": terminal, "model_visible_result_ref": None,
        "non_delivery_reason": "provider_delivery_not_recorded", "output": output, "error": None,
        "max_result_bytes": 4096}
    result = AgentActionRecord(stable_action_id(str(loop), 0, "call-1"), str(loop), 0, 0, "call-1",
        "tool_call_id", "call-1", "tool_call", "{}", {}, 1, AgentLoopState.ACTION_APPLIED,
        result_metadata={"kind": "managed_native_plugin_result/v1", "output": output,
                         "terminal_receipt_ref": terminal}, managed_action=managed)
    return result


def document(item):
    action = VersionRef("agent_action/v3", new_id("agent_action"), new_id("agent_action_version"))
    from cpn.rpnh.registry.identities import TypedId
    action = replace(action, entity_id=TypedId.parse(item.action_id))
    data = ActionRecordsMechanicsMixin._action_document(item, action_ref=action,
        loop_ref=VersionRef("agent_loop/v1", TypedId.parse(item.loop_id), new_id("agent_loop_version")),
        turn_ref=VersionRef("agent_turn/v1", new_id("agent_turn"), new_id("agent_turn_version")))
    return action, data


class AgentManagedIdentityTests(unittest.TestCase):
    def test_legacy_projection_does_not_add_identity_format_checks(self):
        value = {"binding_digest": "existing-binding", "plugin_catalog_digest": "existing-catalog"}
        self.assertEqual(managed_identity_fields(value), value)

    def test_legacy_and_versioned_actions_keep_existing_schema_and_roundtrip(self):
        for versioned in (False, True):
            with self.subTest(versioned=versioned):
                item = record(versioned)
                action, data = document(item)
                agent_task_catalog().validate_instance("agent_action/v3", category="object", instance=data)
                metadata_view = SimpleNamespace(kernel=SimpleNamespace(
                    _exact_object=lambda *args, **kwargs: SimpleNamespace(metadata=data)))
                restored = ActionRecordsMechanicsMixin.hydrate_action(metadata_view, action)
                self.assertEqual(restored, item)
                self.assertEqual(set(managed_identity_fields(data)), set(identity(versioned)))

    def test_mixed_partial_and_foreign_catalog_actions_are_rejected(self):
        original = record()
        candidates = []
        mixed = deepcopy(original.managed_action)
        mixed.update(identity(False))
        candidates.append(mixed)
        partial = deepcopy(original.managed_action)
        del partial["binding_identity"]
        candidates.append(partial)
        foreign = deepcopy(original.managed_action)
        foreign["plugin_catalog_identity"]["bindings"] = []
        candidates.append(foreign)
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(AgentLoopProtocolError):
                replace(original, managed_action=candidate)

    def test_schema_rejects_mixed_identity_fields_and_unknown_nested_fields(self):
        _, data = document(record())
        for altered in (
            {**data, **identity(False)},
            {**data, "binding_identity": {**data["binding_identity"], "account_id": "not-an-identity-field"}},
        ):
            with self.subTest(altered=altered), self.assertRaises(SchemaGovernanceError):
                agent_task_catalog().validate_instance("agent_action/v3", category="object", instance=altered)

    def test_node_binding_decoder_supports_exact_v1_and_v2_shapes(self):
        for versioned in (False, True):
            keys = identity(versioned)
            catalog_key = "plugin_catalog_identity" if versioned else "plugin_catalog_digest"
            binding_key = "binding_identity" if versioned else "binding_digest"
            data = {"schema_version": "rpnh/agent_loop_managed_tool_bindings/" + ("v2" if versioned else "v1"),
                "semantic_node_id": "node", catalog_key: keys[catalog_key],
                "bindings": {"tool_call": {"registration_key": "tests/tool/v1", "selector": "tool/call",
                    binding_key: keys[binding_key], "effect": "external_read", "max_result_bytes": 4096}}}
            view = SimpleNamespace(_static=lambda *args: (None, None, canonical(data)))
            result = ContextExecutionMixin._managed_tool_bindings(view, None)
            self.assertEqual(managed_identity_fields(result["tool_call"]), keys)
            data["bindings"]["tool_call"]["max_result_bytes"] = True
            with self.assertRaises(ResourceIntegrityFault):
                ContextExecutionMixin._managed_tool_bindings(view, None)


if __name__ == "__main__":
    unittest.main()

