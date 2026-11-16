"""Fixed account inputs through the real two-operation Registry/PN chain.

A publisher creates binding bytes after the true task/principal exist. Original
Success carries that exact resource to the caller. No input payload, execution
wrapper, authorization helper, or receipt is patched. Permission callbacks and
credentials are synthetic; this does not contact an account provider.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import socket
import tempfile
import unittest

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.plugins.api import (
    API_VERSION_V2, PluginDefinition, PluginOperation, canonical,
    plugin_implementation,
)
from cpn.plugins.catalog import BoundPlugin, PluginCatalog
from cpn.plugins.account_credentials import (
    AccountSelection, InstalledAccountResolver, ResolvedAccountFD,
)
from cpn.plugins.managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationConflict, ManagedPluginInvocationFailed,
    ManagedPluginInvocationService, ManagedPluginToolAdapter,
    ManagedPluginToolCatalog, ManagedToolSelector, PreparedManagedInvocation,
    execute_prepared_invocation,
)
from cpn.rpnh.collaboration.references import (
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
)
from cpn.rpnh.collaboration.schema_catalog import source_identity_schema_data
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from cpn.rpnh.run import OwnerInput, start_run


SOURCE = "account-service-fixture-source"
INPUT_SCHEMA = "application/test_account_bootstrap/v1"
BINDING_SCHEMA = "application/test_account_binding/v1"
OUTPUT_SCHEMA = "application/test_account_result/v1"
EXECUTOR = "tests/account-service-caller/v1"
TERMINAL = "tests/account-service-terminal/v1"


@plugin_implementation("tests.account_service_read", "1")
def account_read(context, arguments):
    return {
        "private_account_present": context.child_environment.get(
            "GITHUB_PERSONAL_ACCESS_TOKEN") == "synthetic-account-a",
        "ambient_secret_absent": "GITHUB_PERSONAL_ACCESS_TOKEN" not in os.environ,
        "call_id": context.call_id,
    }


def unused_executor(**_kwargs):
    raise AssertionError("focused test drives original Owner calls")


def account_world(root, *, current_use_check, credential_callback):
    operation = PluginOperation("read", "Synthetic fixed-account read",
        {"type": "object", "additionalProperties": False}, {"type": "object"},
        account_read, effect="external_read", identity_version="v2",
        declaration_revision="1")
    definition = PluginDefinition("account_fixture", "1", (operation,),
                                  api_version=API_VERSION_V2)
    selected = PluginCatalog((BoundPlugin(definition, {}, binding_revision="1"),),
                             catalog_revision="1")
    managed = ManagedPluginToolCatalog(selected, (
        ManagedToolSelector("account_read", "account_fixture/read", descriptor_revision="1"),),
        admitted_effects=("external_read",), protocol_version="v3")
    declaration = managed.declaration("account_read")
    registration = Registration()
    register_basic_components(registration)
    registration.register_schema(INPUT_SCHEMA, {"$id": INPUT_SCHEMA, "type": "object",
        "$schema": "http://json-schema.org/draft-07/schema#"})
    registration.register_schema(OUTPUT_SCHEMA, {"$id": OUTPUT_SCHEMA, "type": "object",
        "$schema": "http://json-schema.org/draft-07/schema#"})
    registration.register_schema(BINDING_SCHEMA, {
        "$id": BINDING_SCHEMA, "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"const": "rpnh/stdio_account_binding/v1"},
            "principal_ref": {"type": "object"}, "task_ref": {"type": "object"},
            "service_id": {"type": "string"}, "account_id": {"type": "string"},
            "binding_revision": {"type": "string"}, "slot": {"const": "github_pat/v1"}},
        "required": ["schema_version", "principal_ref", "task_ref", "service_id",
                     "account_id", "binding_revision", "slot"]})
    registration.register_executor(EXECUTOR, unused_executor,
        identity={"implementation_id": "tests.account_service_caller", "revision": "v1"},
        contracts={"transport": "deterministic", "input_ports": None, "output_ports": None})
    registration.register_tool(TERMINAL, dict,
        identity={"implementation_id": "tests.account_service_terminal", "revision": "v1"},
        contracts={"binding_protocol": "rpnh/module_terminal/v1"})
    ManagedPluginToolAdapter(managed).register(registration)

    def component(name, input_schema, output_schema, tools):
        return {"name": name, "key": "operation", "config_schema": CONFIG_SCHEMA_ID,
            "config": {}, "ports": [
                {"name": "request", "direction": "input", "schema": input_schema},
                {"name": "result", "direction": "output", "schema": output_schema}],
            "operations": [{"name": "run", "executor": EXECUTOR,
                "inputs": ["request"], "outputs": ["result"], "request_port": None,
                "tools": tools, "config": {},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": name, "budget_scope": name,
                                   "finalization_scope": None}}]}

    module = ModuleDeclaration.from_dict({
        "schema_version": "rpnh/module_declaration/v1", "name": "ManagedAccountBinding",
        "components": [component("publisher", INPUT_SCHEMA, BINDING_SCHEMA, []),
                       component("caller", BINDING_SCHEMA, OUTPUT_SCHEMA,
                                 [declaration.registration_key])],
        "links": [{"source": {"component": "publisher", "port": "result"},
                   "target": {"component": "caller", "port": "request"}}],
        "entry": {"request": {"component": "publisher", "port": "request"}},
        "exit": {"result": {"component": "caller", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "caller", "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, INPUT_SCHEMA, BINDING_SCHEMA, OUTPUT_SCHEMA],
        "budgets": {}, "budget_buckets": [
            {"bucket_id": name, "budget_scope": name, "finalization_scope": None,
             "max_attempts": 1} for name in ("publisher", "caller")]})
    schemas, types, paths = source_identity_schema_data()
    catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
    request = OwnerInput(INPUT_SCHEMA, b"{}", "Synthetic account bootstrap")
    owner = start_run(module, registration, run_dir=root / "run",
        task_input=request, entry_inputs={"request": request},
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 2, 0, 2, 0),
        model_condition="no-model", owner_statement="Synthetic fixed-account fixture",
        command_id="account:run", catalog=catalog)
    owner.schema_gateway.bind_source_identity(source_id=SOURCE, command_id="account:source")
    admitted = owner.admit("publisher.run", logical_tau=0, command_id="account:publisher:admit")
    publisher = owner.start(admitted, command_id="account:publisher:start")
    context = publisher.operation.canonical.context
    # Its own ref is deliberately not part of binding_document. A normal product
    # publication supplies the actual ref after these already-known scope bytes.
    selected_account = AccountSelection(
        SourceQualifiedVersionRef(SOURCE, context.principal_ref),
        SourceQualifiedVersionRef(SOURCE, context.task_ref),
        SourceQualifiedResourceRef(SOURCE, ResourceVersionRef(
            new_id("resource"), new_id("resource_version"))),
        "github_fixture", "account_a", "1")
    outputs = owner.products(publisher, outcome_id="complete",
        products={"publisher.result": (canonical(selected_account.binding_document()),)},
        command_id="account:publish-binding")
    owner.succeed(outputs, command_id="account:binding-success")
    admitted = owner.admit("caller.run", logical_tau=1, command_id="account:caller:admit")
    execution = owner.start(admitted, command_id="account:caller:start")
    actual_input, = execution.operation.inputs
    selection = replace(selected_account,
        binding_ref=SourceQualifiedResourceRef(SOURCE, actual_input.resource_ref))
    assert selection.principal_ref.ref == execution.operation.canonical.context.principal_ref
    assert selection.task_ref.ref == execution.operation.canonical.context.task_ref
    assert actual_input.consumer_payload == canonical(selection.binding_document())
    assert actual_input.resource_ref == outputs.outputs[0].resource_ref
    resolver = InstalledAccountResolver(selection, credential_callback,
                                         current_use_check=current_use_check)
    kernel, repository = owner.operation_repository()
    service = ManagedPluginInvocationService(owner, kernel, repository, managed,
                                              account_resolvers={"account_read": resolver})
    return owner, execution, selection, service, managed


class AccountOriginalServiceTests(unittest.TestCase):
    def test_original_service_success_fd_finish_and_terminal_replay(self):
        try:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            left.close()
            right.close()
        except OSError as error:
            self.skipTest(f"native SCM_RIGHTS unavailable: {type(error).__name__}/errno={error.errno}")
        calls = []
        def check(selection, scope):
            calls.append(("use", selection.account_id, scope.call_id))
            return True
        def credential(selection, scope):
            calls.append(("secret", selection.account_id, scope.call_id))
            with tempfile.TemporaryFile() as stream:
                stream.write(b"synthetic-account-a")
                stream.seek(0)
                fd = os.dup(stream.fileno())
            os.set_inheritable(fd, False)
            return ResolvedAccountFD(selection, scope, fd)
        with tempfile.TemporaryDirectory() as directory:
            owner, execution, selection, service, _managed = account_world(
                Path(directory), current_use_check=check, credential_callback=credential)
            prepared = service.prepare("account_read", execution=execution,
                                       call_id="successful-call", arguments={})
            self.assertEqual(calls, [])
            completion = execute_prepared_invocation(prepared, cancelled=lambda: False)
            self.assertIsNone(completion.error_code)
            expected_output = {"private_account_present": True,
                               "ambient_secret_absent": True,
                               "call_id": "successful-call"}
            self.assertEqual(completion.output, expected_output)
            result = service.finish(prepared, completion)
            self.assertEqual(result["outcome"], "returned")
            self.assertEqual(result["output"], expected_output)
            replay = service.prepare("account_read", execution=execution,
                                     call_id="successful-call", arguments={})
            self.assertIsInstance(replay, ManagedInvocationObservation)
            self.assertEqual(replay.future.result(), result)
            self.assertEqual(calls, [("use", "account_a", "successful-call"),
                                     ("secret", "account_a", "successful-call")])
            key, refs = service._refs(execution, "successful-call")
            for phase in ("started", "returned"):
                receipt = service._read_receipt(execution, key, refs[phase], phase)
                self.assertNotIn(b"synthetic-account-a", canonical(receipt))
                self.assertEqual(receipt["account_selection"], selection.to_dict())

    def test_actual_binding_revoke_finish_replay_and_account_conflict(self):
        calls = []
        status = {"allow": True}
        def check(selection, scope):
            calls.append(("use", selection.account_id, scope.call_id))
            return status["allow"]
        def credential(selection, scope):
            calls.append(("secret", selection.account_id, scope.call_id))
            raise AssertionError("revoked call must never open a credential")

        with tempfile.TemporaryDirectory() as directory:
            owner, execution, selection, service, managed = account_world(
                Path(directory), current_use_check=check, credential_callback=credential)
            prepared = service.prepare("account_read", execution=execution,
                                       call_id="call-a", arguments={})
            self.assertIsInstance(prepared, PreparedManagedInvocation)
            self.assertEqual(prepared.account_selection, selection)
            self.assertEqual(prepared.account_scope.execution_ref.ref,
                             execution.operation_execution_lease_ref)
            self.assertEqual(calls, [], "prepare does not query account providers or secrets")
            status["allow"] = False
            completion = execute_prepared_invocation(prepared, cancelled=lambda: False)
            self.assertEqual(completion.error_code, "account_use_denied")
            self.assertFalse(completion.may_have_executed)
            with self.assertRaises(ManagedPluginInvocationFailed) as failed:
                service.finish(prepared, completion)
            result = failed.exception.evidence
            self.assertEqual(result["outcome"], "failed")
            self.assertEqual(result["error"]["code"], "account_use_denied")
            with self.assertRaises(ManagedPluginInvocationFailed) as replay:
                service.prepare("account_read", execution=execution,
                                call_id="call-a", arguments={})
            self.assertEqual(replay.exception.evidence, result)
            self.assertEqual(calls, [("use", "account_a", "call-a")])

            # Another configured account is different receipt identity even
            # when a caller tries to reuse the original execution and call ID.
            other = replace(selection, account_id="account_b")
            resolver = InstalledAccountResolver(other, credential, current_use_check=check)
            replacement = ManagedPluginInvocationService(owner, service.kernel,
                service.repository, managed, account_resolvers={"account_read": resolver})
            with self.assertRaises(ManagedPluginInvocationConflict):
                replacement.prepare("account_read", execution=execution,
                                    call_id="call-a", arguments={})
            self.assertEqual(calls, [("use", "account_a", "call-a")])

    def test_original_binding_bytes_reject_wrong_account_before_claim(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            owner, execution, selection, service, managed = account_world(
                Path(directory), current_use_check=lambda *_: calls.append("use"),
                credential_callback=lambda *_: calls.append("secret"))
            other = replace(selection, binding_revision="2")
            resolver = InstalledAccountResolver(other,
                lambda *_: calls.append("secret"), current_use_check=lambda *_: calls.append("use"))
            replacement = ManagedPluginInvocationService(owner, service.kernel,
                service.repository, managed, account_resolvers={"account_read": resolver})
            from cpn.plugins.account_credentials import AccountCredentialError
            with self.assertRaisesRegex(AccountCredentialError, "account_binding_unavailable"):
                replacement.prepare("account_read", execution=execution,
                                    call_id="wrong-binding", arguments={})
            _key, refs = replacement._refs(execution, "wrong-binding")
            self.assertIsNone(owner._core.event_store.object_row(
                refs["started"].resource_version_id))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

