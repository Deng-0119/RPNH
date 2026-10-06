"""Caller-selected author outcomes; unresolved selections never publish a result."""
from copy import deepcopy
import uuid

import pytest

import test_collaboration_plain_merge as support
from test_collaboration_plain_merge_result import fixture
from cpn.rpnh.collaboration import UnresolvedPlainMerge, validate_closed_revision
from cpn.rpnh.collaboration._plain_merge_resolution import rebuild_module, resolve_atoms
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json


def choices_for(analysis, side):
    return [{"conflict_id": row["conflict_id"], "choice": side,
             "reason": "Explicit fixture caller selects " + side, "delete_element_ids": []}
            for row in analysis.document["conflicts"]]


def conflicting(inputs, *, coupled=False):
    base = inputs[5]
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"][1]["operations"][0]["config"] = {"value": True}
    right_doc["components"][1]["operations"][0]["config"] = {"value": 1}
    if coupled:
        left_doc["budgets"] = {"units": 1}
    left = support.publish(inputs, left_doc, "left")
    right = support.publish(inputs, right_doc, "right")
    analysis = inputs[4].analyze(**support.request(inputs, left, right))
    return analysis, left, right


@pytest.mark.parametrize("side", ["base", "local", "incoming"])
def test_explicit_side_choice_preserves_exact_scalar_type(fixture, side):
    inputs, author = fixture
    analysis, left, right = conflicting(inputs)
    choices = choices_for(analysis, side)
    assert choices
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="resolve:" + side)
    expected = {"base": inputs[5], "local": left, "incoming": right}[side]
    assert canonical_json(result.module.to_dict()) == canonical_json(expected.module.to_dict())
    assert canonical_json(validate_closed_revision(inputs[0], result.revision.revision_ref,
        support.registration()).module.to_dict()) == canonical_json(expected.module.to_dict())
    support.assert_inert(inputs[0], 4)


def test_missing_duplicate_extra_and_contradictory_choices_write_nothing(fixture):
    inputs, author = fixture
    analysis, _, _ = conflicting(inputs, coupled=True)
    assert {c["reason"] for c in analysis.document["conflicts"]} >= {"divergent_atom", "coupled_contract"}
    valid = choices_for(analysis, "local")
    extra = deepcopy(valid)
    extra[0]["conflict_id"] = "conflict:" + "f" * 64
    contradictory = deepcopy(valid)
    divergent = next(c["conflict_id"] for c in analysis.document["conflicts"] if c["reason"] == "divergent_atom")
    next(c for c in contradictory if c["conflict_id"] == divergent)["choice"] = "incoming"
    before = support.counts(inputs[0])
    for index, choices in enumerate((valid[:-1], valid + valid[:1], extra, contradictory)):
        with pytest.raises((ValueError, UnresolvedPlainMerge)):
            author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="invalid:" + str(index))
        assert support.counts(inputs[0]) == before
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=valid, command_id="compatible")
    assert result.module.budgets == {"units": 1}
    assert result.module.components[1].operations[0].config == {"value": True}


def test_overlapping_different_labels_accept_identical_selected_states(fixture):
    inputs, author = fixture
    doc = inputs[5].module.to_dict()
    doc["components"][1]["key"] = "test/multiple/v1"
    joint = support.publish(inputs, doc, "joint-base")
    left_doc, right_doc = deepcopy(doc), deepcopy(doc)
    ids = {**inputs[6], "/components/b/operations/again": "element:" + "e" * 32}
    for body, chosen in ((left_doc, True), (right_doc, 1)):
        operation = deepcopy(body["components"][1]["operations"][0])
        operation["name"] = "again"
        body["components"][1]["operations"].append(operation)
        body["components"][1]["operations"][0]["config"] = {"value": chosen}
    left = support.publish(inputs, left_doc, "left", ids, joint.revision.revision_ref)
    right = support.publish(inputs, right_doc, "right", ids, joint.revision.revision_ref)
    analysis = inputs[4].analyze(local_ref=left.revision.revision_ref, incoming_ref=right.revision.revision_ref,
        base_ref=joint.revision.revision_ref, command_id="overlap")
    concurrent = next(c for c in analysis.document["conflicts"] if c["reason"] == "concurrent_identity_introduction")
    coupled = next(c for c in analysis.document["conflicts"] if c["reason"] == "coupled_contract")
    assert set(concurrent["subjects"]) & set(coupled["subjects"])
    choices = choices_for(analysis, "local")
    next(c for c in choices if c["conflict_id"] == concurrent["conflict_id"])["choice"] = "incoming"
    value = author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="compatible-labels")
    assert canonical_json(value.module.to_dict()) == canonical_json(left_doc)


def test_delete_structural_children_requires_no_business_cascade(fixture):
    inputs, author = fixture
    core, _, _, _, analyzer, base, ids = inputs
    left_doc, right_doc = deepcopy(base.module.to_dict()), deepcopy(base.module.to_dict())
    left_doc["components"].pop()
    left_doc["entry"].pop("second")
    left_ids = {p: i for p, i in ids.items() if not p.startswith("/components/b") and p != "/entry/second"}
    right_doc["components"][1]["operations"][0]["config"] = {"value": "caller deletes this changed region"}
    left = support.publish(inputs, left_doc, "left", left_ids)
    right = support.publish(inputs, right_doc, "right")
    analysis = analyzer.analyze(**support.request(inputs, left, right))
    choices = []
    for conflict in analysis.document["conflicts"]:
        # The fixture caller explicitly deletes b and its second entry whenever
        # that conflict covers either element. Structural children belong to b.
        targets = []
        if any(k.startswith(ids["/components/b"] + "/") or
               any(k.startswith(i + "/") for p, i in ids.items() if p.startswith("/components/b/"))
               for k in conflict["subjects"]):
            targets.append(ids["/components/b"])
        if any(k.startswith(ids["/entry/second"] + "/") for k in conflict["subjects"]):
            targets.append(ids["/entry/second"])
        choices.append({"conflict_id": conflict["conflict_id"], "choice": "delete",
                        "reason": "Caller explicitly removes the second region", "delete_element_ids": targets})
    assert choices and all(c["delete_element_ids"] for c in choices)
    result = author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="explicit-delete")
    assert result.module.to_dict() == left_doc
    assert {row["locator"]: row["element_id"] for row in result.element_map["elements"]} == left_ids
    support.assert_inert(core, 4)


def test_deleting_changed_operation_does_not_delete_its_component_or_boundary(fixture):
    inputs, author = fixture
    analysis, _, _ = conflicting(inputs)
    op = inputs[6]["/components/b/operations/run"]
    choices = [{"conflict_id": c["conflict_id"], "choice": "delete",
                "reason": "Caller deletes only this operation", "delete_element_ids": [op]}
               for c in analysis.document["conflicts"]]
    before = support.counts(inputs[0])
    # The ordinary operation component requires one operation. No automatic
    # component/entry deletion is allowed to repair the caller's incomplete model.
    with pytest.raises(ValueError):
        author.publish(analysis_ref=analysis.analysis_ref, choices=choices, command_id="dangling-delete")
    assert support.counts(inputs[0]) == before


def test_concurrent_membership_requires_an_explicit_expressible_order(fixture):
    inputs, author = fixture
    base, ids = inputs[5:]
    heads = []
    for name in ("left_added", "right_added"):
        doc = deepcopy(base.module.to_dict())
        component = deepcopy(doc["components"][1]); component["name"] = name
        component["ports"] = component["ports"][1:]
        component["operations"][0]["inputs"] = []
        doc["components"].append(component)
        selected_ids = {p: ids.get(p, "element:" + uuid.uuid5(uuid.NAMESPACE_URL, p).hex)
                        for p in _elements(ModuleDeclaration.from_dict(doc))}
        heads.append(support.publish(inputs, doc, name, selected_ids))
    analysis = inputs[4].analyze(**support.request(inputs, *heads))
    order_subject = ids["/"] + "/components_order"
    assert any(order_subject in c["subjects"] for c in analysis.document["conflicts"])
    assert len(analysis.document["conflicts"]) == 1
    before = support.counts(inputs[0])
    for side in ("base", "local", "incoming"):
        # Both additions are nonconflicting. A selected existing order must not
        # silently drop the other region or invent an order for both additions.
        with pytest.raises(UnresolvedPlainMerge, match="order/membership"):
            author.publish(analysis_ref=analysis.analysis_ref,
                choices=choices_for(analysis, side), command_id="missing-order:" + side)
        assert support.counts(inputs[0]) == before


def test_same_exact_heads_and_unrelated_analysis_reject_before_result_writes(fixture):
    inputs, author = fixture
    core, _, _, ordinary, analyzer, base, ids = inputs
    same = analyzer.analyze(**support.request(inputs, base, base))
    other = ordinary.publish(module=base.module, element_ids=ids, command_id="other-root")
    unrelated = analyzer.analyze(local_ref=base.revision.revision_ref,
        incoming_ref=other.revision.revision_ref, command_id="unrelated")
    before = support.counts(core)
    for analysis in (same, unrelated):
        with pytest.raises(UnresolvedPlainMerge):
            author.publish(analysis_ref=analysis.analysis_ref, choices=[], command_id="not-a-result")
        assert support.counts(core) == before


def test_wrong_component_same_name_port_cannot_silently_rebind(fixture):
    inputs, _ = fixture
    analysis, _, _ = conflicting(inputs)
    atoms, _, _ = resolve_atoms(analysis.document, choices_for(analysis, "local"))
    ids = inputs[6]
    atoms[ids["/components/b/operations/run"] + "/value"]["inputs"] = [ids["/components/a/ports/request"]]
    with pytest.raises(UnresolvedPlainMerge, match="wrong-component port"):
        rebuild_module(atoms)
