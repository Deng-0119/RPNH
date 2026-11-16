"""Actual registered HOST authorization after inventory normalization.

New tests use the ordinary Registry and immutable operation authority. A copied
spec is untrusted test input, never patched into the repository. No worker or
business call is needed to exercise the exact HOST resolution boundary.
"""
from __future__ import annotations

import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import cpn.components.execution_services as host_services
from cpn.plugins.api import json_copy
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.operations import OperationAuthorityError
import multi_tool_core_matrix_support as fixture


class RegisteredToolInventoryTests(unittest.TestCase):
    def test_unsorted_registered_inventory_keeps_exact_authority_and_rejections(self):
        temporary = tempfile.TemporaryDirectory(prefix="rpnh-host-inventory-")
        self.addCleanup(temporary.cleanup)
        original_module = fixture._module

        def unsorted_module(managed, names):
            document = original_module(managed, names).to_dict()
            for component in document["components"]:
                for operation in component["operations"]:
                    operation["tools"] = sorted(operation["tools"], reverse=True)
            return ModuleDeclaration.from_dict(document)

        with patch.object(fixture, "_module", unsorted_module):
            host = fixture.MatrixHost(Path(temporary.name) / "host")
        self.addCleanup(host.close)
        execution = host.executions[0]
        _compiled, declared = host.repository.registered_compiled_operation(execution.operation)
        declaration = host.managed.declaration("good_value")
        arguments = dict(identity=json_copy(declaration.identity),
                         contracts=json_copy(declaration.contracts))
        registered_order = tuple(declared.declaration.tools)
        admitted_order = execution.operation.spec.allowed_tool_ids
        self.assertNotEqual(registered_order, admitted_order)
        self.assertEqual(tuple(sorted(registered_order)), admitted_order)
        before_events = len(host.owner._core.event_store.list_events())
        resolved = host_services.resolve_registered_tool(
            host.owner, host.kernel, host.repository, execution,
            declaration.registration_key, **arguments)
        self.assertIs(resolved, host.owner.registration.resolve("tool", declaration.registration_key))
        rejections = {}

        # When an exact baseline is supplied, demonstrate that the original
        # ordinary HOST function rejected this same valid non-sorted inventory.
        baseline = os.environ.get("RPNH_BASELINE_SOURCE")
        if baseline:
            baseline_path = Path(baseline) / "cpn/components/execution_services.py"
            tree = ast.parse(baseline_path.read_text())
            original, = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                         and node.name == "invoke_registered_tool"]
            namespace = dict(vars(host_services))
            exec(compile(ast.Module(body=[original], type_ignores=[]),
                         str(baseline_path), "exec"), namespace)
            with self.assertRaisesRegex(OperationAuthorityError, "tool inventory differs") as denied:
                namespace["invoke_registered_tool"](
                    host.owner, host.kernel, host.repository, execution,
                    declaration.registration_key, **arguments)
            rejections["baseline_non_sorted_valid_inventory"] = str(denied.exception)

        with self.assertRaisesRegex(OperationAuthorityError, "unique sorted") as denied:
            replace(execution.operation.spec,
                    allowed_tool_ids=tuple(sorted((*admitted_order, admitted_order[0]))))
        rejections["duplicate_inventory"] = str(denied.exception)

        for label, inventory in (
                ("missing_declared_tool", tuple(key for key in admitted_order
                                               if key != host.key("read_value"))),
                ("extra_registered_tool", tuple(sorted((*admitted_order, fixture.TERMINAL))))):
            spec = replace(execution.operation.spec, allowed_tool_ids=inventory)
            plan = replace(execution.operation.input_plan, spec=spec)
            operation = replace(execution.operation, spec=spec, input_plan=plan)
            copied_execution = replace(execution, operation=operation)
            with self.subTest(label=label), self.assertRaises(
                    (OperationAuthorityError, ResourceIntegrityFault)) as denied:
                host_services.resolve_registered_tool(
                    host.owner, host.kernel, host.repository, copied_execution,
                    declaration.registration_key, **arguments)
            rejections[label] = str(denied.exception)

        with self.assertRaisesRegex(OperationAuthorityError, "outside.*declared tools") as denied:
            host_services.resolve_registered_tool(
                host.owner, host.kernel, host.repository, execution,
                fixture.TERMINAL, **arguments)
        rejections["undeclared_requested_tool"] = str(denied.exception)
        with self.assertRaisesRegex(OperationAuthorityError, "identity/contracts") as denied:
            host_services.resolve_registered_tool(
                host.owner, host.kernel, host.repository, execution,
                declaration.registration_key, identity={"changed": True},
                contracts=arguments["contracts"])
        rejections["changed_host_identity"] = str(denied.exception)

        self.assertEqual(host.receipts(), [])
        self.assertEqual(list(host.gate.iterdir()), [])
        self.assertEqual(len(host.owner._core.event_store.list_events()), before_events)
        target = os.environ.get("RPNH_HOST_INVENTORY_EVIDENCE_DIR")
        if target:
            destination = Path(target) / self._testMethodName
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "facts.json").write_text(json.dumps({
                "method": self.id(), "registered_order": registered_order,
                "admitted_sorted_order": admitted_order,
                "candidate_resolves_exact_registered_callable": True,
                "rejections": rejections, "managed_receipts": 0,
                "new_events": 0, "worker_processes_dispatched": 0,
                "scope": "Real Registry/Owner/compiled declarations and original execution verification; resolution only",
            }, indent=2) + "\n")
            shutil.copytree(host.root / "run", destination / "registered-run", dirs_exist_ok=True)


if __name__ == "__main__":
    unittest.main()

