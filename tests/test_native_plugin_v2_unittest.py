"""Original Registry/PN and spawned-worker coverage for explicit identities."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cpn.plugins.api import PluginError, canonical
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.plugins.host import NativePluginHost, _operation_descriptor, schema_key
from cpn.plugins.runtime import build_plugin_module, plugin_registration
from cpn.plugins.worker import WorkerFailure, execute_worker
from cpn.rpnh.registry.operations import register_operation_outputs
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.run import OwnerInput, start_run
from test_plugin_identity_v2_unittest import configured, mcp_configured


def native_world(root, catalog, selector, arguments):
    module = build_plugin_module(catalog, selector)
    request = OwnerInput(schema_key(catalog, selector, "input"), canonical(arguments), "native v2 request")
    owner = start_run(module, plugin_registration(catalog), run_dir=Path(root) / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 1, 0, 1, 0),
        model_condition="no-model", owner_statement="native v2 offline fixture", command_id="v2:fresh")
    admitted = owner.admit("plugin.run", logical_tau=0, command_id="v2:admit")
    execution = owner.start(admitted, command_id="v2:start")
    host = NativePluginHost(owner, *owner.operation_repository())
    plugin, operation = catalog.resolve(selector)
    return owner, execution, host, plugin, operation


def native_receipts(owner):
    rows = owner._core.event_store.object_rows_by_type("resource_version/v1")
    return [json.loads(owner._core.object_store.read_registered(owner._core.get_version(row["version_id"])))
            for row in rows if "native_plugin_execution" in json.loads(row["metadata_json"]).get("descriptors", {})]


class NativePluginV2Tests(unittest.TestCase):
    def test_native_v2_spawns_publishes_and_settles_through_original_owner(self):
        with tempfile.TemporaryDirectory() as root:
            _, catalog = configured()
            owner, execution, host, plugin, operation = native_world(root, catalog, "echo/echo", {"value": 7})
            changed, _ = configured(value=True)
            with self.assertRaises(PluginError):
                host.prepare(execution, _operation_descriptor(changed, changed.definition.operations[0]))
            self.assertEqual(native_receipts(owner), [])
            prepared = host.prepare(execution, _operation_descriptor(plugin, operation))
            result = execute_worker(operation.handler, prepared["packet"], environment_names=(),
                timeout_seconds=5, cancelled=lambda: False)
            self.assertEqual(result, {"value": 7})
            with self.assertRaises(PluginError):
                NativePluginHost(owner, *owner.operation_repository()).prepare(
                    execution, _operation_descriptor(plugin, operation))
            artifacts = host.products(execution, prepared["attempt_ref"], result)
            outputs = register_operation_outputs(host.repository, execution, artifacts,
                idempotency_key="v2:close", selected_outcome_id="complete")
            owner.succeed(outputs, command_id="v2:success")
            settled = owner._core.event_store.list_events_by_type(("transition_firing_settled/v1",))
            self.assertEqual(len(settled), 1)
            receipts = native_receipts(owner)
            self.assertEqual({row["phase"] for row in receipts}, {"started", "returned"})
            self.assertTrue(all(row["schema_version"] == "rpnh/native_plugin_receipt/v2" for row in receipts))
            self.assertIn("binding_identity", next(row for row in receipts if row["phase"] == "started"))
            from cpn.rpnh.inspection import project_registry_net
            projection = project_registry_net(Path(root) / "run", catalog=owner._core.catalog)
            transitions = [node for node in projection["nodes"] if node["kind"] == "transition"]
            self.assertEqual(len(transitions), 1)

    def test_supplied_input_projection_cannot_replace_original_native_request(self):
        with tempfile.TemporaryDirectory() as root:
            bound, _ = configured()
            definition = replace(bound.definition, operations=(replace(bound.definition.operations[0], effect="external_read"),))
            catalog = PluginCatalog((BoundPlugin(definition, dict(bound.config), binding_revision="binding1"),),
                                    catalog_revision="catalog1")
            owner, execution, host, plugin, operation = native_world(root, catalog, "echo/echo", {"value": 7})
            inputs = tuple(replace(item, projected_payload=canonical({"value": 999}))
                           if item.artifact.payload == canonical({"value": 7}) else item
                           for item in execution.operation.inputs)
            forged = replace(execution, operation=replace(execution.operation, inputs=inputs))
            prepared = host.prepare(forged, _operation_descriptor(plugin, operation))
            self.assertEqual(prepared["packet"]["arguments"], {"value": 7})
            host.failed(execution, prepared["attempt_ref"], "mcp_response_unknown",
                        may_have_executed=True, observation_lost=True)
            failed = next(row for row in native_receipts(owner) if row["phase"] == "failed")
            self.assertTrue(failed["outcome_unknown"])
            self.assertTrue(failed["observation_lost"])
            self.assertEqual(owner._core.event_store.list_events_by_type(("transition_firing_settled/v1",)), ())

    def test_actual_mcp_worker_guard_records_no_dispatch(self):
        from importlib import metadata
        try:
            installed = metadata.version("mcp")
        except metadata.PackageNotFoundError:
            installed = None
        if installed == "1.30.0":
            self.skipTest("pinned SDK available; fixed-protocol stdio suite owns transport validation")
        with tempfile.TemporaryDirectory() as root:
            definition, config = mcp_configured()
            catalog = PluginCatalog((BoundPlugin(definition, config, binding_revision="r1"),), catalog_revision="r1")
            owner, execution, host, plugin, operation = native_world(root, catalog, "research/search", {"query": "registry"})
            prepared = host.prepare(execution, _operation_descriptor(plugin, operation))
            with self.assertRaises(WorkerFailure) as caught:
                execute_worker(operation.handler, prepared["packet"], environment_names=(),
                    timeout_seconds=5, cancelled=lambda: False)
            failure = caught.exception
            self.assertIn(failure.code, {"mcp_sdk_missing", "mcp_sdk_version_mismatch"})
            self.assertFalse(failure.may_have_executed)
            host.failed(execution, prepared["attempt_ref"], failure.code,
                        may_have_executed=failure.may_have_executed,
                        observation_lost=failure.observation_lost, protocol_code=failure.protocol_code)
            failed = next(row for row in native_receipts(owner) if row["phase"] == "failed")
            self.assertFalse(failed["outcome_unknown"])
            self.assertFalse(failed["may_have_executed"])


if __name__ == "__main__":
    unittest.main()

