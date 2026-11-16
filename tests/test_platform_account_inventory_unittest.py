"""Versioned plugin task documents through the platform's real inventory guard.

TaskControl writes original pending manifests/specs; only process creation is a
declared double. Inventory and runtime admission are unpatched. No worker,
model, account callback, credential, owner socket or external service is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cpn.plugins.api import API_VERSION_V2, PluginDefinition, PluginOperation, plugin_implementation
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
from cpn.rpnh.task_control import TaskAdmissionError, TaskControl, inspect_task_inventory


@plugin_implementation("tests.platform_account_inventory", "1")
def unused_account_probe(_context, _arguments):
    raise AssertionError("inventory admission cannot dispatch a tool")


class PlatformAccountInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rpnh-platform-account-inventory-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        operation = PluginOperation("read", "Undispatched account inventory fixture",
            {"type": "object"}, {"type": "object"}, unused_account_probe,
            effect="external_read", identity_version="v2", declaration_revision="1")
        definition = PluginDefinition("account_inventory", "1", (operation,), api_version=API_VERSION_V2)
        catalog = PluginCatalog((BoundPlugin(definition, {}, binding_revision="1"),), catalog_revision="1")
        self.spec = AgentTaskSpec(self.root / "child", "Inspect versioned task inventory only.",
            (AgentStage("main", "Do not dispatch."),), self.root / "execution-not-opened.json",
            plugin_configuration={"schema_version": "rpnh/plugins/v2", "catalog_revision": "1",
                "plugins": [{"name": "account_inventory", "entry_point": "account-inventory-fixture",
                    "version": "1", "config": {}, "environment": [], "binding_revision": "1"}]},
            plugin_catalog_snapshot=catalog.versioned_snapshot(), aggregate_call_cap=2,
            managed_bindings={"main": {"tools": {"account_probe": {
                "selector": "account_inventory/read", "descriptor_revision": "1"}},
                "admitted_effects": ["external_read"]}})

    def verify_format(self, *, relative):
        spawn = Mock(return_value=SimpleNamespace(pid=321, poll=lambda: None))
        control_root = self.root / "tasks"
        control = TaskControl(control_root, popen_factory=spawn)
        handle = control.start(self.spec)
        spawn.assert_called_once()
        spec_path = control_root / "specs" / f"{handle.task_id}.json"
        manifest = control_root / "manifests" / f"{handle.task_id}.json"
        if not relative:
            # Persist the supported absolute format for the same original spec.
            # No identity, input, permission, source or runtime field is changed.
            spec_path.write_text(json.dumps(handle.spec.as_worker_document()), encoding="utf-8")
        document = json.loads(spec_path.read_text())
        self.assertEqual(document["schema_version"], f"rpnh/agent_task_spec/v{17 if relative else 16}")
        self.assertNotIn("plugin_catalog_digest", document)
        self.assertNotIn("managed_account_resolver_factory", document)
        before = {path: path.read_bytes() for path in (spec_path, manifest)}
        inventory = inspect_task_inventory(control_root)
        inventory.require_write_compatible()
        inventory.assert_unchanged()
        actual, = inventory.handles
        self.assertEqual(actual.spec, handle.spec)
        self.assertEqual(actual.spec.plugin_catalog_snapshot, self.spec.plugin_catalog_snapshot)
        self.assertEqual(actual.spec.managed_bindings, self.spec.managed_bindings)
        self.assertEqual(actual.spec.aggregate_call_cap, 2)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        resumed_spawn = Mock()
        restored = TaskControl(control_root, popen_factory=resumed_spawn, recover_pending_launches=False)
        self.assertEqual(restored.get(handle.task_id).spec, handle.spec)
        resumed_spawn.assert_not_called()
        self.assertFalse(handle.run_dir.exists())
        # The process-local account hook cannot enter either persisted shape.
        document["managed_account_resolver_factory"] = {"callback": "not-code"}
        spec_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(TaskAdmissionError, "worker document is not current"):
            inspect_task_inventory(control_root)
        self.assertEqual(manifest.read_bytes(), before[manifest])
        resumed_spawn.assert_not_called()

    def test_absolute_v16_snapshot_is_admitted_without_account_callbacks(self):
        self.verify_format(relative=False)

    def test_relative_v17_snapshot_is_admitted_without_account_callbacks(self):
        self.verify_format(relative=True)


if __name__ == "__main__":
    unittest.main()
