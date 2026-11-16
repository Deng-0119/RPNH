"""No-network contracts for explicit plugin identities and MCP HOST binding."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from cpn.plugins.api import (
    API_VERSION_V2, PluginContext, PluginDefinition, PluginError, PluginOperation,
    PluginResource, canonical, implementation_identity, plugin_implementation,
)
from cpn.plugins.catalog import BoundPlugin, PluginCatalog, load_catalog
from cpn.plugins.host import _operation_descriptor, capability_value, operation_config
from cpn.plugins.mcp import MCPToolBinding, mcp_discovery_plugin, mcp_plugin, invoke_mcp_tool
from cpn.plugins.mcp_contracts import SelectedTool
from cpn.plugins.mcp_stdio import StdioDeployment
from cpn.plugins.worker import WorkerFailure


@plugin_implementation("tests.versioned_echo", "1")
def versioned_echo(context, arguments):
    return arguments


def legacy_echo(context, arguments):
    return arguments


def configured(*, value=1, payload=b"original"):
    operation = PluginOperation("echo", "Echo the admitted object", {"type": "object"},
        {"type": "object"}, versioned_echo, resources=("manual",), identity_version="v2",
        declaration_revision="op1")
    definition = PluginDefinition("echo", "1", (operation,),
        config_schema={"type": "object", "properties": {"value": {}}, "required": ["value"]},
        resources=(PluginResource("manual", payload, revision="manual1"),), api_version=API_VERSION_V2)
    bound = BoundPlugin(definition, {"value": value}, binding_revision="binding1")
    return bound, PluginCatalog((bound,), catalog_revision="catalog1")


def mcp_configured():
    descriptor = {"name": "remote.search", "inputSchema": {"type": "object",
        "properties": {"query": {"type": "string"}}, "required": ["query"],
        "additionalProperties": False}, "annotations": {"readOnlyHint": True}}
    return mcp_plugin(name="research", plugin_version="1",
        deployment=StdioDeployment("/explicit/mcp-server"),
        tools=(MCPToolBinding(SelectedTool("search", descriptor), "search1", "external_read"),))


class PluginIdentityV2Tests(unittest.TestCase):
    def test_complete_versioned_assembly_never_uses_legacy_digest(self):
        with patch("hashlib.sha256", side_effect=AssertionError("legacy digest path reached")):
            bound, catalog = configured()
            descriptor = _operation_descriptor(bound, bound.definition.operations[0])
            self.assertEqual(catalog.describe()[0]["binding_identity"], bound.binding_identity)
            self.assertEqual(operation_config(catalog, "echo/echo")["native_plugin"]["capability"],
                             descriptor["capability"])
            self.assertIn("/binding1/echo/op1", catalog.operation_key("echo/echo"))
            self.assertNotIn("binding_digest", descriptor)
            self.assertNotIn("sha256", canonical(descriptor).decode())

    def test_v2_does_not_silently_construct_a_legacy_handler(self):
        with patch("hashlib.sha256", side_effect=AssertionError("legacy digest path reached")):
            with self.assertRaises(PluginError):
                PluginOperation("echo", "Echo", {"type": "object"}, {"type": "object"},
                    legacy_echo, identity_version="v2", declaration_revision="r1")
            with self.assertRaises(PluginError):
                implementation_identity(versioned_echo,
                    expected={"schema_version": "rpnh/plugin_implementation/unknown"})

    def test_full_capability_distinguishes_same_revision_config_and_resource_changes(self):
        original, _ = configured(value=1, payload=b"original")
        bool_value, _ = configured(value=True, payload=b"original")
        new_bytes, _ = configured(value=1, payload=b"modified")
        op = original.definition.operations[0]
        expected = canonical(capability_value(original, op))
        self.assertNotEqual(expected, canonical(capability_value(bool_value, op)))
        self.assertNotEqual(expected, canonical(capability_value(new_bytes, op)))
        self.assertEqual(original.binding_identity, bool_value.binding_identity)

    def test_legacy_catalog_and_versioned_catalog_are_explicitly_distinct(self):
        legacy = PluginDefinition("legacy", "1", (PluginOperation("echo", "Echo",
            {"type": "object"}, {"type": "object"}, legacy_echo),))
        bound = BoundPlugin(legacy, {})
        catalog = PluginCatalog((bound,))
        self.assertIn("binding_digest", catalog.describe()[0])
        self.assertNotIn("identity_version", legacy.operations[0].descriptor())
        current, _ = configured()
        with self.assertRaises(PluginError):
            PluginCatalog((bound,), catalog_revision="r1")
        with self.assertRaises(PluginError):
            PluginCatalog((current,))
        with self.assertRaises(PluginError):
            _ = current.digest

    def test_config_revisions_checked_before_loading_installed_factory(self):
        calls = []
        def factory():
            calls.append("loaded")
            return configured()[0].definition
        document = {"schema_version": "rpnh/plugins/v2", "catalog_revision": "r1", "plugins": [
            {"name": "echo", "entry_point": "echo", "version": "1", "config": {"value": 1},
             "environment": [], "binding_revision": "r1"}]}
        good = load_catalog(document, factories={"echo": factory})
        self.assertTrue(good.versioned)
        calls.clear()
        document["plugins"][0]["binding_revision"] = ""
        with self.assertRaises(PluginError):
            load_catalog(document, factories={"echo": factory})
        self.assertEqual(calls, [])

    def test_worker_identity_compares_installed_explicit_revision(self):
        expected = implementation_identity(versioned_echo, identity_version="v2")
        with patch("hashlib.sha256", side_effect=AssertionError("legacy digest path reached")):
            actual = implementation_identity(versioned_echo, expected=expected)
        self.assertEqual(actual, expected)
        modified = {**expected, "revision": "2"}
        self.assertNotEqual(implementation_identity(versioned_echo, expected=modified), modified)

    def test_private_context_environment_is_readonly_and_cleared_with_owner_scope(self):
        environment = {"EXAMPLE_TOKEN": "synthetic-only"}
        context = PluginContext(config={}, resources=(), operation_id="x/y", invocation_id="i",
            firing_id="f", call_id="c", cancel=threading.Event(), deadline=time.monotonic()+5,
            private_environment=environment)
        with self.assertRaises(TypeError):
            context.child_environment["OTHER"] = "forbidden"
        self.assertEqual(context.config, {})
        environment.clear()
        self.assertEqual(dict(context.child_environment), {})

    def test_mcp_selection_and_private_environment_are_fixed_before_business_arguments(self):
        definition, config = mcp_configured()
        environment = {"EXAMPLE_TOKEN": "synthetic-only"}
        context = PluginContext(config=config, resources=(), operation_id="research/search",
            invocation_id="i", firing_id="f", call_id="c", cancel=threading.Event(),
            deadline=time.monotonic()+5, private_environment=environment)
        with patch("cpn.plugins.mcp.invoke_selected", return_value={"fixture": True}) as transport:
            result = invoke_mcp_tool(context, {"query": "registry"})
        self.assertEqual(result, {"fixture": True})
        self.assertEqual(transport.call_args.args[1].remote_name, "remote.search")
        self.assertEqual(dict(transport.call_args.kwargs["environment"]), environment)
        self.assertNotIn("synthetic-only", canonical(definition.descriptor()).decode())
        self.assertNotIn("synthetic-only", canonical(config).decode())
        with self.assertRaises(PluginError):
            SelectedTool("search", config["tools"]["search"]).arguments(
                {"query": "registry", "remote_name": "arbitrary"})

    def test_mcp_guard_rejects_current_missing_or_unpinned_sdk_before_dispatch(self):
        from importlib import metadata
        try:
            installed = metadata.version("mcp")
        except metadata.PackageNotFoundError:
            installed = None
        if installed == "1.30.0":
            self.skipTest("the pinned SDK is present; use the real stdio protocol suite")
        _, config = mcp_configured()
        context = PluginContext(config=config, resources=(), operation_id="research/search",
            invocation_id="i", firing_id="f", call_id="c", cancel=threading.Event(),
            deadline=time.monotonic()+5, private_environment={})
        with self.assertRaises(WorkerFailure) as caught:
            invoke_mcp_tool(context, {"query": "registry"})
        self.assertIn(caught.exception.code, {"mcp_sdk_missing", "mcp_sdk_version_mismatch"})
        self.assertFalse(caught.exception.may_have_executed)

    def test_discovery_does_not_admit_discovered_tools_and_annotations_do_not_select_effect(self):
        definition, config = mcp_discovery_plugin(name="discovery", plugin_version="1",
            declaration_revision="r1", deployment=StdioDeployment("/explicit/mcp-server"))
        self.assertEqual([op.name for op in definition.operations], ["discover"])
        self.assertEqual(definition.operations[0].effect, "external_read")
        self.assertNotIn("tools", config)
        selected = SelectedTool("read", {"name": "remote.read", "inputSchema": {"type": "object"},
                                          "annotations": {"readOnlyHint": True}})
        with self.assertRaises(PluginError):
            MCPToolBinding(selected, "r1", "pure")


if __name__ == "__main__":
    unittest.main()

