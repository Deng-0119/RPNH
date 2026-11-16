"""Versioned managed tools through original Registry admission and workers."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cpn.plugins import (
    API_VERSION_V2, BoundPlugin, PluginCatalog, PluginDefinition, PluginOperation,
    ManagedPluginInvocationService, ManagedPluginToolCatalog, ManagedToolSelector,
    plugin_implementation,
)
from cpn.plugins.api import PluginError, canonical, json_copy
from cpn.plugins.managed_tools import (
    ManagedPluginInvocationConflict, ManagedInvocationObservation,
    execute_prepared_invocation,
)
from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy
from cpn.rpnh.registry.operations import OperationAuthorityError
from managed_service_offline_support import prepared_invocation, invoke, receipts


@plugin_implementation("tests.versioned_managed_double", "1")
def versioned_double(context, arguments):
    return arguments["value"] * 2


def versioned_catalog(mode=1):
    operation = PluginOperation("double", "Versioned integer doubling",
        {"type": "object", "additionalProperties": False,
         "properties": {"value": {"type": "integer"}}, "required": ["value"]},
        {"type": "integer"}, versioned_double,
        identity_version="v2", declaration_revision="operation1")
    definition = PluginDefinition("synthetic", "1", (operation,),
        config_schema={"type": "object", "additionalProperties": False,
            "properties": {"mode": {}}, "required": ["mode"]}, api_version=API_VERSION_V2)
    catalog = PluginCatalog((BoundPlugin(definition, {"mode": mode},
        binding_revision="binding1"),), catalog_revision="catalog1")
    managed = ManagedPluginToolCatalog(catalog, (
        ManagedToolSelector("double_value", "synthetic/double",
            descriptor_revision="descriptor1"),), protocol_version="v3")
    return catalog, managed


class ManagedVersionedIdentityTests(unittest.TestCase):
    def test_new_catalog_construction_does_not_enter_legacy_identity_path(self):
        with patch("hashlib.sha256", side_effect=AssertionError("legacy identity path")):
            catalog, managed = versioned_catalog()
            declaration = managed.declaration("double_value")
            ManagedSchedulerPolicy().validate_declaration(declaration)
            document = managed.document()
            self.assertEqual(document["schema_version"],
                "rpnh/managed_native_plugin_tool_catalog/v3")
            self.assertNotIn("plugin_catalog_digest", document)
            self.assertNotIn("binding_digest", declaration.identity)
            self.assertEqual(canonical(declaration.contracts["managed_plugin"]["binding_descriptor"]),
                             canonical(catalog.plugins[0].descriptor()))

    def test_registry_worker_return_receipt_and_original_id_replay(self):
        _, managed = versioned_catalog()
        with tempfile.TemporaryDirectory() as directory:
            fixture = prepared_invocation(Path(directory), managed_catalog=managed)
            first = invoke(fixture)
            self.assertEqual(first["output"], 8)
            with patch("cpn.plugins.worker.execute_worker",
                       side_effect=AssertionError("terminal must not dispatch")):
                self.assertEqual(invoke(fixture), first)
            documents = receipts(fixture["owner"]._core.run_dir)
            self.assertEqual({d["state"] for d in documents}, {"started", "returned"})
            self.assertTrue(all(d["schema_version"] ==
                "rpnh/managed_native_plugin_tool_receipt/v3" for d in documents))
            self.assertTrue(all("binding_identity" in d and "binding_digest" not in d
                                and "plugin_catalog_digest" not in d for d in documents))
            with self.assertRaises(ManagedPluginInvocationConflict):
                invoke(fixture, arguments={"value": 5})

    def test_same_revision_config_change_is_rejected_by_full_registration(self):
        _, original = versioned_catalog(mode=1)
        _, changed = versioned_catalog(mode=True)
        self.assertEqual(original.plugin_catalog_identity, changed.plugin_catalog_identity)
        with tempfile.TemporaryDirectory() as directory:
            fixture = prepared_invocation(Path(directory), managed_catalog=original)
            replacement = ManagedPluginInvocationService(fixture["owner"], fixture["kernel"],
                fixture["repository"], changed)
            with self.assertRaises(OperationAuthorityError):
                replacement.prepare("double_value", execution=fixture["execution"],
                                    call_id="changed", arguments={"value": 4})
            self.assertEqual(receipts(fixture["owner"]._core.run_dir), [])

    def test_supplied_projection_is_not_used_for_the_prepared_context(self):
        _, managed = versioned_catalog()
        with tempfile.TemporaryDirectory() as directory:
            fixture = prepared_invocation(Path(directory), managed_catalog=managed)
            execution = fixture["execution"]
            operation = execution.operation
            first = operation.inputs[0]
            # The original API validates against the persisted input plan.
            # Its returned wrapper is rehydrated before the worker packet.
            supplied = replace(execution, operation=replace(operation,
                inputs=(replace(first, projected_payload=b'{"forged":true}'),)))
            prepared = fixture["service"].prepare("double_value", execution=supplied,
                call_id="projection", arguments={"value": 4})
            result = fixture["service"].finish(prepared,
                execute_prepared_invocation(prepared, cancelled=lambda: False))
            self.assertEqual(result["output"], 8)

    def test_legacy_service_still_reads_and_replays_its_original_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = prepared_invocation(Path(directory))
            result = invoke(fixture)
            self.assertEqual(invoke(fixture), result)
            self.assertTrue(all(d["schema_version"] ==
                "rpnh/managed_native_plugin_tool_receipt/v2"
                and "binding_digest" in d for d in receipts(fixture["owner"]._core.run_dir)))


if __name__ == "__main__":
    unittest.main()

