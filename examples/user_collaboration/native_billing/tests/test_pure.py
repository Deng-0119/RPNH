"""Pure candidate checks only. Passing never claims Registry/native/Viewer success."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import unittest
from unittest.mock import patch
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.user_collaboration_source import strict_proposal_json
from examples.tool_pipeline.contracts import RULES
from examples.tool_pipeline.run import fixture
from examples.tool_pipeline import tools
from examples.user_collaboration.native_billing.business import bounded_input, contribution_document, CONTRIBUTIONS, DEFAULT_LIMIT
from examples.user_collaboration.native_billing.declarations import base_module, proposal_module
from examples.user_collaboration.native_billing.host import registration
from examples.user_collaboration.native_billing.run import collaboration_catalog


def input_envelope(kind, value=None):
    reference = {"resource_id": "pure-unregistered-input", "resource_version_id": "pure-unregistered-version"}
    result = asyncio.run((tools.read_usage if kind == "usage" else tools.read_tariff)(
        inputs={"source": {"ref": reference, "value": value or fixture(kind + ".json")}}, rules=RULES))
    return {"raw": {"ref": reference, "value": result.products["raw"]}}


def test_complete_existing_billing_topology_and_same_host_inventory():
    selected = registration()
    base = base_module(); original = base.to_dict()
    compiled = compile_module(base, selected)
    assert (len(compiled.operations), len(compiled.symbolic.places),
            len(compiled.symbolic.transitions), len(compiled.symbolic.arcs)) == (10, 18, 10, 29)
    for kind in ("usage", "tariff"):
        candidate = contribution_document(original, kind)
        assert base.to_dict() == original
        changed = [c["name"] for c, old in zip(candidate["components"], original["components"])
                   if c != old]
        assert changed == [CONTRIBUTIONS[kind]["component"]]
        assert compile_module(ModuleDeclaration.from_dict(candidate), selected).registrations == compiled.registrations
        assert strict_proposal_json(canonical_json(candidate)) == candidate
        assert len(compile_module(proposal_module(kind), selected).operations) == 1
    assert collaboration_catalog().require("user_collaboration_proposal/v1", category="object") is not None


def check_public_fixture_passes_contributed_bound(kind):
    inputs = input_envelope(kind)
    baseline = asyncio.run(bounded_input(kind=kind, inputs=inputs, rules=RULES, max_integer=DEFAULT_LIMIT))
    contribution = asyncio.run(bounded_input(kind=kind, inputs=inputs, rules=RULES,
                                             max_integer=CONTRIBUTIONS[kind]["max_integer"]))
    assert baseline == contribution and contribution.outcome == "complete"


def test_usage_limit_has_real_boundary_effect_using_original_rejection_contract():
    source = fixture("usage.json")
    source["intervals"][0]["wh"] = "100000"
    inputs = input_envelope("usage", source)
    assert asyncio.run(bounded_input(kind="usage", inputs=inputs, rules=RULES, max_integer=100000)).outcome == "complete"
    source["intervals"][0]["wh"] = "100001"
    inputs = input_envelope("usage", source)
    assert asyncio.run(bounded_input(kind="usage", inputs=inputs, rules=RULES, max_integer=DEFAULT_LIMIT)).outcome == "complete"
    bounded = asyncio.run(bounded_input(kind="usage", inputs=inputs, rules=RULES, max_integer=100000))
    assert bounded.outcome == "rejected"
    assert set(bounded.products) == {"rejection"}
    assert bounded.products["rejection"]["data"] == {
        "step": "check_usage_input", "reason": "declared per-interval wh limit exceeded"}
    assert bounded.products["rejection"]["parents"] == [inputs["raw"]["ref"]]


def test_sequential_contributor_inherits_accepted_usage_change():
    doc = contribution_document(base_module().to_dict(), "usage")
    with unittest.TestCase().assertRaisesRegex(ValueError, "unmodified target check"):
        contribution_document(doc, "usage")
    combined = contribution_document(doc, "tariff")
    operations = {c["name"]: c["operations"][0] for c in combined["components"]}
    assert operations["check_usage_input"]["config"] == {"max_integer": 100000}
    assert operations["check_tariff_input"]["config"] == {"max_integer": 1000}
    assert [c["name"] for c, old in zip(combined["components"], doc["components"]) if c != old] == ["check_tariff_input"]


def test_no_test_helper_or_fallback_imports_in_example():
    import ast
    root = Path(__file__).parents[1]
    forbidden = ("test_", "pytest", "tests.", "pipe_transport")
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(any(part.startswith(prefix) for prefix in forbidden)
                       for m in modules for part in (m, m.rsplit(".", 1)[-1]))


class PureChecks(unittest.TestCase):
    def setUp(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("pure checks must not open a Registry, bind/connect a socket or start a subprocess")
        for target in ((_RegistryCore, "__init__"), (socket.socket, "bind"), (socket.socket, "connect"), (socket.socket, "listen"), (subprocess, "Popen")):
            patcher = patch.object(*target, forbidden)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_complete_topology(self):
        test_complete_existing_billing_topology_and_same_host_inventory()

    def test_usage_fixture(self):
        check_public_fixture_passes_contributed_bound("usage")

    def test_tariff_fixture(self):
        check_public_fixture_passes_contributed_bound("tariff")

    def test_business_boundary(self):
        test_usage_limit_has_real_boundary_effect_using_original_rejection_contract()

    def test_sequential_base(self):
        test_sequential_contributor_inherits_accepted_usage_change()

    def test_same_base_coupled_contract_is_not_silently_merged(self):
        from types import SimpleNamespace
        import uuid
        from cpn.rpnh.collaboration.materials import _elements, _element_map
        from cpn.rpnh.collaboration._plain_merge_model import normalize, compare
        from cpn.rpnh.collaboration._plain_merge_resolution import resolve_atoms, rebuild_module
        base = base_module()
        selected = registration()
        ids = {p: "element:" + uuid.uuid5(uuid.NAMESPACE_URL, p).hex for p in _elements(base)}
        def model(module):
            compiled = compile_module(module, selected)
            return normalize(SimpleNamespace(module=module, compiled=compiled,
                element_map=_element_map(module, ids, None, {}),
                host_requirements={"registrations": compiled.registrations}))
        left = ModuleDeclaration.from_dict(contribution_document(base.to_dict(), "usage"))
        right = ModuleDeclaration.from_dict(contribution_document(base.to_dict(), "tariff"))
        normalized = {"base": model(base), "local": model(left), "incoming": model(right)}
        changes, conflicts = compare(normalized["base"], normalized["local"], normalized["incoming"])
        self.assertEqual(len(changes), 2)
        self.assertEqual([c["reason"] for c in conflicts], ["coupled_contract"])
        analysis = {"normalized": normalized, "conflicts": conflicts}
        with self.assertRaisesRegex(ValueError, "cover every exact conflict"):
            resolve_atoms(analysis, [])
        for side, expected in (("local", left), ("incoming", right)):
            choices = [{"conflict_id": c["conflict_id"], "choice": side,
                        "reason": "Pure regression of indivisible coupled input contract", "delete_element_ids": []}
                       for c in conflicts]
            atoms, _, _ = resolve_atoms(analysis, choices)
            result, _ = rebuild_module(atoms)
            self.assertEqual(result.to_dict(), expected.to_dict())
        # The native example therefore uses real sequential Branch advances.
        combined = contribution_document(left.to_dict(), "tariff")
        self.assertNotEqual(combined, left.to_dict())
        self.assertNotEqual(combined, right.to_dict())
        compile_module(ModuleDeclaration.from_dict(combined), selected)

    def test_import_boundary(self):
        test_no_test_helper_or_fallback_imports_in_example()


if __name__ == "__main__":
    unittest.main()
