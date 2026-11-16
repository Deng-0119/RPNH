"""Installed account factories at the original HOST service construction sites.

Both production entry paths create their real owner and managed service. A
stopper replaces only the final runner construction, before any model request;
the established pipe fixture replaces the unavailable owner command socket.
These are bootstrap/service wiring checks, not native socket or account-provider
certification. Separate account service tests cover the real PN input and FD.
"""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cpn.dsh.backend import DshBackend
from cpn.plugins.account_credentials import (
    AccountCredentialError, AccountSelection, InstalledAccountResolver,
    bind_installed_account_resolvers,
)
from cpn.plugins.api import API_VERSION_V2, PluginDefinition, PluginError, PluginOperation
from cpn.plugins.catalog import load_catalog
from cpn.plugins.managed_tools import (
    ManagedPluginInvocationService, PreparedManagedInvocation,
)
from cpn.rpnh import agent_tasks
from cpn.rpnh.agent_tasks import AgentStage, AgentTaskSpec
from cpn.rpnh.collaboration.references import (
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.run import RunOwner
from multi_tool_offline_support import SessionPipeOwnerEventLoop
from test_dsh_multi_tool_backend import BatchEffect, selection, turn_request
from test_managed_account_service_unittest import account_read, account_world


class _StopBeforeDispatch(Exception):
    pass


class _NoModelPort:
    def __init__(self, selected):
        self.execution_policy = selected.as_registry_policy()
        self.closed = False

    def request_once(self, _attempt):
        raise AssertionError("HOST factory checks cannot call a model")

    def close(self):
        self.closed = True


def _definition():
    operation = PluginOperation(
        "read", "Synthetic fixed-account metadata", {"type": "object"},
        {"type": "object"}, account_read, effect="external_read",
        identity_version="v2", declaration_revision="1")
    return PluginDefinition("account_fixture", "1", (operation,),
                            api_version=API_VERSION_V2)


def _configuration():
    return {
        "schema_version": "rpnh/plugins/v2", "catalog_revision": "1",
        "plugins": [{"name": "account_fixture", "entry_point": "account-fixture",
            "version": "1", "config": {}, "environment": [],
            "binding_revision": "1"}],
    }


def _tools():
    return {"account_read": {"selector": "account_fixture/read", "descriptor_revision": "1"}}


def _resolver_for_owner(owner, calls):
    # Non-secret metadata only. This intentionally unadmitted reference does not
    # publish a binding, grant account use or substitute for a future PN input.
    source = "host-factory-fixture-source"
    chosen = AccountSelection(
        SourceQualifiedVersionRef(source, owner.principal_ref),
        SourceQualifiedVersionRef(source, owner.identity.task_ref),
        SourceQualifiedResourceRef(source, ResourceVersionRef(
            new_id("resource"), new_id("resource_version"))),
        "synthetic-service", "explicit-account-a", "1")

    def forbidden(*_args):
        calls.append("credential-or-current-use")
        raise AssertionError("service construction cannot fetch account authority or secrets")

    return InstalledAccountResolver(chosen, forbidden, current_use_check=forbidden)


class AccountHostFactoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_original_service_accepts_fixed_factory_map_without_resolving_credentials(self):
        callbacks = []
        owner, execution, _chosen, first, catalog = account_world(
            self.root, current_use_check=lambda *_: callbacks.append("use"),
            credential_callback=lambda *_: callbacks.append("secret"))
        mapping = dict(first.account_resolvers)
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return mapping

        bound = bind_installed_account_resolvers(
            factory, owner=owner, catalog=catalog, node_id="caller")
        kernel, repository = owner.operation_repository()
        service = ManagedPluginInvocationService(
            owner, kernel, repository, catalog, account_resolvers=bound)
        prepared = service.prepare("account_read", execution=execution,
            call_id="host-factory-admitted", arguments={})
        self.assertIsInstance(prepared, PreparedManagedInvocation)
        self.assertEqual(prepared.account_selection, mapping["account_read"].selection)
        self.assertEqual(calls, [{"owner": owner, "catalog": catalog, "node_id": "caller"}])
        self.assertEqual(callbacks, [])
        mapping.clear()
        self.assertEqual(tuple(service.account_resolvers), ("account_read",))
        self.assertIsNone(bind_installed_account_resolvers(
            None, owner=owner, catalog=catalog))
        with self.assertRaises(TypeError):
            bind_installed_account_resolvers(
                lambda **_: None, owner=owner, catalog=catalog)
        with self.assertRaises(TypeError):
            bind_installed_account_resolvers(
                factory, owner=object(), catalog=catalog)

        def sensitive_failure(**_kwargs):
            raise RuntimeError("fixture-private-detail")

        with self.assertRaises(AccountCredentialError) as caught:
            bind_installed_account_resolvers(
                sensitive_failure, owner=owner, catalog=catalog)
        self.assertEqual(str(caught.exception), "account_resolver_unavailable")
        self.assertNotIn("fixture-private-detail", str(caught.exception))
        with self.assertRaises(PluginError):
            ManagedPluginInvocationService(owner, kernel, repository, catalog,
                account_resolvers={"not-selected": object()})

    def test_dsh_real_bootstrap_installs_factory_map_only_after_current_owner_exists(self):
        selected = selection(self.root)
        port = _NoModelPort(selected)
        config = (self.root / "plugins.json").resolve()
        config.write_text(json.dumps(_configuration()))
        factory_calls, callbacks, services = [], [], []
        entry = SimpleNamespace(name="account-fixture", load=lambda: _definition)

        def factory(**kwargs):
            factory_calls.append(kwargs)
            self.assertIsInstance(kwargs["owner"], RunOwner)
            self.assertIsNone(kwargs["node_id"])
            resolver = _resolver_for_owner(kwargs["owner"], callbacks)
            return {"account_read": resolver}

        with patch("cpn.plugins.catalog.metadata.entry_points", lambda *, group: [entry]), \
                patch("cpn.dsh.backend.OwnerEventLoop", SessionPipeOwnerEventLoop):
            backend = DshBackend(self.root / "sessions", "multi", BatchEffect(),
                create=True, offline=False, selection=selected,
                plugin_config_path=config, managed_tools=_tools(),
                managed_tool_admitted_effects=("external_read",),
                managed_account_resolver_factory=factory,
                input_port_factory=lambda *_args, **_kwargs: port)
            self.assertEqual(factory_calls, [])

            def stop_runner(**kwargs):
                service = backend.managed_tool_service
                self.assertIsInstance(service, ManagedPluginInvocationService)
                self.assertIs(service.owner, kwargs["owner"])
                services.append(service)
                raise _StopBeforeDispatch()

            request = turn_request(selected)
            request["policy"]["tools"] = ["account_read"]
            with patch("cpn.dsh.backend.Harness", stop_runner), \
                    self.assertRaises(_StopBeforeDispatch):
                backend.turn(request)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(tuple(services[0].account_resolvers), ("account_read",))
        self.assertIs(factory_calls[0]["catalog"], services[0].catalog)
        self.assertEqual(callbacks, [])
        self.assertTrue(port.closed)
        self.assertIsNone(backend.managed_tool_service)

    def test_agent_real_bootstrap_installs_node_map_before_any_model_request(self):
        selected = selection(self.root)
        execution = self.root / "execution.json"
        execution.write_text(json.dumps({
            "schema_version": "llm_execution_selection/v1",
            "adapter_kind": "local_process", "model_condition": "offline-multi",
            "adapter_config_path": str(selected.adapter_config_path),
            "timeout_seconds": 30, "max_output_tokens": 64,
            "max_response_bytes": 65536,
        }))
        config = _configuration()
        plugins = load_catalog(config, factories={"account-fixture": _definition})
        spec = AgentTaskSpec(self.root / "agent", "Inspect installed HOST wiring.",
            (AgentStage("main", "No model invocation in this test."),), execution,
            plugin_configuration=config, plugin_catalog_snapshot=plugins.versioned_snapshot(),
            managed_bindings={"main": {"tools": _tools(), "admitted_effects": ["external_read"]}})
        port = _NoModelPort(selected)
        calls, callbacks, services = [], [], []
        entry = SimpleNamespace(name="account-fixture", load=lambda: _definition)

        def factory(**kwargs):
            calls.append(kwargs)
            self.assertIsInstance(kwargs["owner"], RunOwner)
            self.assertEqual(kwargs["node_id"], "main")
            return {"account_read": _resolver_for_owner(kwargs["owner"], callbacks)}

        def stop_runner(**kwargs):
            installed = kwargs["prepare_dispatcher"].__self__._optional_agent_service.managed_plugin_services
            self.assertEqual(tuple(installed), ("main",))
            self.assertIsInstance(installed["main"], ManagedPluginInvocationService)
            self.assertIs(installed["main"].owner, kwargs["owner"])
            services.append(installed["main"])
            raise _StopBeforeDispatch()

        with patch("cpn.plugins.catalog.metadata.entry_points", lambda *, group: [entry]), \
                patch.object(agent_tasks, "OwnerEventLoop", SessionPipeOwnerEventLoop), \
                patch.object(agent_tasks, "build_llm_input_port", lambda *_args, **_kwargs: port), \
                patch.object(agent_tasks, "Orchestrator", stop_runner), \
                self.assertRaises(_StopBeforeDispatch):
            agent_tasks.run_agent_task(spec, managed_account_resolver_factory=factory)
        self.assertEqual(len(calls), 1)
        self.assertEqual(tuple(services[0].account_resolvers), ("account_read",))
        self.assertIs(calls[0]["catalog"], services[0].catalog)
        self.assertEqual(callbacks, [])
        self.assertTrue(port.closed)
        self.assertNotIn("managed_account_resolver_factory", {field.name for field in fields(spec)})

    def test_factories_cannot_be_supplied_without_tools_or_as_json_data(self):
        calls = []
        factory = lambda **kwargs: calls.append(kwargs)
        with self.assertRaises(TypeError):
            DshBackend(self.root / "invalid", "main", lambda _: {},
                managed_account_resolver_factory={"callable": "not-code"})
        with self.assertRaises(ValueError):
            DshBackend(self.root / "unused", "main", lambda _: {},
                managed_account_resolver_factory=factory)
        spec = AgentTaskSpec(self.root / "agent", "No tools selected.",
            (AgentStage("main", "No tools."),), self.root / "not-read.json")
        with self.assertRaises(TypeError):
            agent_tasks.run_agent_task(spec, managed_account_resolver_factory={})
        with self.assertRaises(ValueError):
            agent_tasks.run_agent_task(spec, managed_account_resolver_factory=factory)
        self.assertEqual(calls, [])
        self.assertFalse((self.root / "invalid").exists())
        self.assertFalse((self.root / "unused").exists())
        self.assertFalse(spec.run_dir.exists())

    def test_resume_and_reopen_forward_only_the_installed_process_callback(self):
        marker = object()
        factory = lambda **_: {}
        with patch.object(agent_tasks, "_execute_agent_task", return_value=marker) as execute:
            self.assertIs(agent_tasks.resume_agent_task(
                marker, managed_account_resolver_factory=factory), marker)
            execute.assert_called_once_with(marker, resume=True,
                managed_account_resolver_factory=factory)
            execute.reset_mock()
            self.assertIs(agent_tasks.reopen_agent_task(marker,
                checkpoint_version_id="checkpoint", command_id="reopen", reason="fixture",
                managed_account_resolver_factory=factory), marker)
            execute.assert_called_once_with(marker, resume=True,
                checkpoint_version_id="checkpoint", reopen_command_id="reopen",
                reopen_reason="fixture", managed_account_resolver_factory=factory)


if __name__ == "__main__":
    unittest.main()

