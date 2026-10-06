"""Pure normal-root allocation contracts over fixed, in-memory DTO fixtures.

These tests do not create a Registry, publish objects, or replay historical
evidence. The historical flag cases exercise only the selector's compatibility
rule; their constructed objects are not a genuine pre-change transaction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import uuid

from jsonschema import Draft7Validator
import pytest

from cpn.rpnh.collaboration.worksets import record_ref
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.execution_child_closure import child_seal_ref
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import PreparedObject, VersionRef
from cpn.rpnh.registry.normal_root_token_allocation import (
    NORMAL_ROOT_TOKEN_SCHEME,
    normal_root_allocation_scheme,
    normal_root_token_ref,
    validate_normal_root_token_delta,
)


_MARKER = "ordinary_token_ref_scheme"
_ROOT = "collaboration_root_terminal/v2"
_SEAL = "execution_child_seal/v1"
_PROFILE = "execution-v1-normal-only"
_SOURCE = "allocation-unit-source"
_SUCCESS_ARGUMENT = {
    "operation_result/v1": "result",
    "marking_delta/v1": "delta",
    "firing_completion/v2": "completion",
    "marking_checkpoint/v1": "checkpoint",
}


def _id(kind: str, number: int) -> TypedId:
    return TypedId(kind, f"{number:032x}")  # type: ignore[arg-type]


def _ref(kind: str, number: int, *, entity_type: str | None = None) -> VersionRef:
    return VersionRef(
        entity_type or kind + "/v1", _id(kind, number),
        _id(kind + "_version", number + 100),
    )


def _payload(ref: VersionRef) -> dict[str, str]:
    return {"entity_type": ref.entity_type, "logical_id": str(ref.entity_id),
            "version_id": str(ref.version_id)}


def _qualified(ref: VersionRef) -> dict:
    return {"schema_version": "rpnh/collaboration/source_version_ref/v1",
            "source_id": _SOURCE, "ref": _payload(ref)}


def _prepared(ref: VersionRef, metadata: dict, producer: TypedId) -> PreparedObject:
    # This descriptor exists only in memory; no object-store write is implied.
    raw = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    return PreparedObject(
        object_type=ref.entity_type, logical_id=ref.entity_id,
        version_id=ref.version_id, size=len(raw), media_type="application/json",
        schema_ref="registry_v1/" + ref.entity_type,
        producer_invocation_id=producer,
        storage_locator="unit-fixture/" + str(ref.version_id),
        metadata=deepcopy(metadata),
    )


def _normal_batch() -> dict:
    """One exact structural Success chain, with no persisted authority claim."""
    task = _ref("task", 1)
    net = _ref("net_instance", 2)
    firing = _ref("transition_firing", 3)
    invocation = _ref("invocation", 4)
    binding = _ref("operation_binding", 5)
    admission = _ref("marking_checkpoint", 6)
    result = _ref("operation_result", 7)
    delta = _ref("marking_delta", 8)
    completion = _ref("firing_completion", 9, entity_type="firing_completion/v2")
    checkpoint = _ref("marking_checkpoint", 10)
    token = _ref("petri_token", 11)
    output = _ref("resource", 12, entity_type="resource_version/v1")
    run = _ref("run", 13, entity_type="native_run_identity/v1")
    command = "allocation-unit-normal-root"
    transaction = TypedId("transaction", uuid.uuid5(
        uuid.NAMESPACE_URL, f"d1-c:transaction:{task.entity_id}:{command}").hex)
    root = record_ref(_ROOT, task.entity_id, command, command)
    seal = child_seal_ref(task.entity_id, command)

    firing_data = {
        "transition_firing_ref": _payload(firing), "task_ref": _payload(task),
        "net_instance_ref": _payload(net), "operation_binding_ref": _payload(binding),
        "admission_marking_checkpoint_ref": _payload(admission),
    }
    invocation_data = {
        "invocation_ref": _payload(invocation), "task_ref": _payload(task),
        "own_transition_firing_ref": _payload(firing),
        "net_instance_ref": _payload(net), "operation_binding_ref": _payload(binding),
        "admission_marking_checkpoint_ref": _payload(admission),
    }
    result_data = {
        "operation_result_ref": _payload(result), "invocation_ref": _payload(invocation),
        "transition_firing_ref": _payload(firing), "business_outcome": "completed",
        "output_resource_refs": [_payload(output)],
    }
    delta_data = {
        "marking_delta_ref": _payload(delta), "net_instance_ref": _payload(net),
        "transition_firing_refs": [_payload(firing)],
        "operation_binding_refs": [_payload(binding)], "phase": "settlement",
        "consumed_refs": [], "deposited_refs": [_payload(token)],
        _MARKER: NORMAL_ROOT_TOKEN_SCHEME,
    }
    completion_data = {
        "firing_completion_ref": _payload(completion),
        "transition_firing_ref": _payload(firing), "invocation_ref": _payload(invocation),
        "operation_result_ref": _payload(result), "marking_delta_ref": _payload(delta),
        "successor_checkpoint_ref": _payload(checkpoint), "business_outcome": "completed",
    }
    checkpoint_data = {
        "marking_checkpoint_ref": _payload(checkpoint), "net_instance_ref": _payload(net),
        "transition_firing_refs": [_payload(firing)], "settlement_delta_ref": _payload(delta),
        "previous_checkpoint_ref": _payload(admission), "token_refs": [_payload(token)],
    }
    root_data = {
        "schema_version": "registry_v1/" + _ROOT, "record_ref": _qualified(root),
        "owner_task_ref": _qualified(task), "command_id": command,
        "body": {
            "closure_profile": _PROFILE, "required_child_seal_ref": _qualified(seal),
            "firing_ref": _payload(firing), "invocation_ref": _payload(invocation),
            "completion_ref": _payload(completion), "checkpoint_ref": _payload(checkpoint),
            "occurrence_ref": _payload(token), "output_resource_ref": _payload(output),
        },
    }
    seal_data = {
        "execution_child_seal_ref": _payload(seal), "source_id": _SOURCE,
        "task_ref": _payload(task), "run_ref": _payload(run), "closure_profile": _PROFILE,
        "parent_business_firing_ref": _payload(firing), "parent_invocation_ref": _payload(invocation),
        "parent_business_net_ref": _payload(net), "parent_business_checkpoint_ref": _payload(admission),
        "operation_result_ref": _payload(result), "successor_business_checkpoint_ref": _payload(checkpoint),
        "success_command_id": command, "success_transaction_id": str(transaction),
    }
    token_data = {
        "petri_token_ref": _payload(token), "net_instance_ref": _payload(net),
        "resource_ref": {"resource_id": str(output.entity_id),
                         "resource_version_id": str(output.version_id)},
    }
    objects = tuple(_prepared(ref, data, invocation.entity_id) for ref, data in (
        (root, root_data), (seal, seal_data), (result, result_data),
        (delta, delta_data), (completion, completion_data),
        (checkpoint, checkpoint_data), (token, token_data),
    ))
    return {
        "objects": objects, "task_id": task.entity_id, "transaction_id": transaction,
        "firing": firing_data, "invocation": invocation_data, "result": result_data,
        "delta": delta_data, "completion": completion_data, "checkpoint": checkpoint_data,
    }


def _member(batch: dict, object_type: str) -> PreparedObject:
    matches = [obj for obj in batch["objects"] if obj.object_type == object_type]
    assert len(matches) == 1
    return matches[0]


def _replace_document(batch: dict, object_type: str, document: dict) -> None:
    original = _member(batch, object_type)
    assert original.producer_invocation_id is not None
    ref = VersionRef(original.object_type, original.logical_id, original.version_id)
    replacement = _prepared(ref, document, original.producer_invocation_id)
    batch["objects"] = tuple(replacement if obj is original else obj for obj in batch["objects"])
    argument = _SUCCESS_ARGUMENT.get(object_type)
    if argument is not None:
        batch[argument] = deepcopy(document)


@pytest.fixture
def delta_schema() -> dict:
    path = (Path(__file__).resolve().parents[1] / "cpn" / "schemas" /
            "registry_v1" / "marking_delta.v1.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_normal_ref_is_repeatable_and_has_separate_typed_id_domains() -> None:
    assert NORMAL_ROOT_TOKEN_SCHEME == "normal_root_firing_scoped/v1"
    net, firing = _ref("net_instance", 2), _ref("transition_firing", 3)
    first = normal_root_token_ref(net, firing, 0)
    assert first == normal_root_token_ref(net, firing, 0)
    # Frozen wire-identity vector, computed independently with stdlib UUID5.
    # A change in the versioned recipe must not silently remint stored refs.
    assert first == VersionRef(
        "petri_token/v1",
        TypedId("petri_token", "35f2d68394135cf2bc68e9f4f89ec44a"),
        TypedId("petri_token_version", "9e19ff3059bc558d9891df83342e2d72"),
    )
    assert first.entity_type == "petri_token/v1"
    assert first.entity_id.kind == "petri_token"
    assert first.version_id.kind == "petri_token_version"
    assert first.entity_id.value != first.version_id.value


@pytest.mark.parametrize("changed", (
    "net_logical", "net_version", "firing_logical", "firing_version", "ordinal",
))
def test_normal_ref_binds_each_exact_scope_component(changed: str) -> None:
    net, firing, ordinal = _ref("net_instance", 2), _ref("transition_firing", 3), 0
    original = normal_root_token_ref(net, firing, ordinal)
    if changed == "net_logical":
        net = replace(net, entity_id=_id("net_instance", 40))
    elif changed == "net_version":
        net = replace(net, version_id=_id("net_instance_version", 40))
    elif changed == "firing_logical":
        firing = replace(firing, entity_id=_id("transition_firing", 40))
    elif changed == "firing_version":
        firing = replace(firing, version_id=_id("transition_firing_version", 40))
    else:
        ordinal = 1
    changed_ref = normal_root_token_ref(net, firing, ordinal)
    assert changed_ref.entity_id != original.entity_id
    assert changed_ref.version_id != original.version_id


def test_normal_ref_is_outside_the_legacy_net_ordinal_namespace() -> None:
    net, firing = _ref("net_instance", 2), _ref("transition_firing", 3)
    # Freeze the pre-existing legacy recipe as an independent compatibility
    # oracle, not as an alternate formula accepted by the new selector.
    legacy_logical = uuid.uuid5(
        uuid.NAMESPACE_URL, f"d1-c:petri_token:{net.version_id}:0").hex
    legacy_version = uuid.uuid5(
        uuid.NAMESPACE_URL, f"d1-c:petri_token_version:{net.version_id}:0").hex
    normal = normal_root_token_ref(net, firing, 0)
    assert normal.entity_id.value != legacy_logical
    assert normal.version_id.value != legacy_version


@pytest.mark.parametrize("ordinal", (-1, True, False, 1.0, "1", None))
def test_normal_ref_rejects_non_integer_or_negative_ordinal(ordinal) -> None:
    with pytest.raises(RegistryConflict):
        normal_root_token_ref(_ref("net_instance", 2), _ref("transition_firing", 3), ordinal)


@pytest.mark.parametrize("changed", ("net_type", "net_id_kind", "firing_type", "firing_version_kind"))
def test_normal_ref_rejects_mistyped_exact_scope(changed: str) -> None:
    net, firing = _ref("net_instance", 2), _ref("transition_firing", 3)
    if changed == "net_type":
        net = replace(net, entity_type="execution_instance/v1")
    elif changed == "net_id_kind":
        net = replace(net, entity_id=_id("task", 2))
    elif changed == "firing_type":
        firing = replace(firing, entity_type="execution_transition_firing/v1")
    else:
        firing = replace(firing, version_id=_id("invocation_version", 103))
    with pytest.raises(RegistryConflict):
        normal_root_token_ref(net, firing, 0)


def test_schema_accepts_tagged_settlement_and_historical_field_absence(delta_schema: dict) -> None:
    delta = _normal_batch()["delta"]
    validator = Draft7Validator(delta_schema)
    validator.validate(delta)
    validate_normal_root_token_delta(delta_schema, delta)
    legacy = {key: value for key, value in delta.items() if key != _MARKER}
    validator.validate(legacy)
    with pytest.raises(RegistryConflict):
        validate_normal_root_token_delta(delta_schema, legacy)


@pytest.mark.parametrize("marker", (None, "normal_root_firing_scoped/v2", "", False, 1, {}, []))
def test_schema_rejects_present_null_unknown_or_malformed_marker(delta_schema: dict, marker) -> None:
    delta = _normal_batch()["delta"]
    delta[_MARKER] = marker
    assert not Draft7Validator(delta_schema).is_valid(delta)
    with pytest.raises(RegistryConflict):
        validate_normal_root_token_delta(delta_schema, delta)


@pytest.mark.parametrize("case", ("claim", "zero_firings", "two_firings"))
def test_schema_requires_tagged_settlement_with_one_original_firing(delta_schema: dict, case: str) -> None:
    delta = _normal_batch()["delta"]
    if case == "claim":
        delta["phase"] = "claim"
    elif case == "zero_firings":
        delta["transition_firing_refs"] = []
    else:
        delta["transition_firing_refs"].append(_payload(_ref("transition_firing", 70)))
    assert not Draft7Validator(delta_schema).is_valid(delta)
    with pytest.raises(RegistryConflict):
        validate_normal_root_token_delta(delta_schema, delta)


def test_schema_validation_uses_the_supplied_schema_without_host_fallback(delta_schema: dict) -> None:
    legacy_schema = deepcopy(delta_schema)
    del legacy_schema["properties"][_MARKER]
    del legacy_schema["allOf"]
    delta = _normal_batch()["delta"]
    with pytest.raises(RegistryConflict):
        validate_normal_root_token_delta(legacy_schema, delta)
    validate_normal_root_token_delta(delta_schema, delta)


@pytest.mark.parametrize("historical", (False, True))
def test_selector_accepts_exact_normal_batch_with_known_marker(historical: bool) -> None:
    batch = _normal_batch()
    before = deepcopy(batch)
    assert normal_root_allocation_scheme(**batch, historical=historical) == NORMAL_ROOT_TOKEN_SCHEME
    assert batch == before


def test_selector_restricts_absent_normal_marker_to_historical_mode() -> None:
    batch = _normal_batch()
    untagged = {key: value for key, value in batch["delta"].items() if key != _MARKER}
    _replace_document(batch, "marking_delta/v1", untagged)
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)
    assert normal_root_allocation_scheme(**batch, historical=True) is None


@pytest.mark.parametrize("historical", (False, True))
@pytest.mark.parametrize("marker", (None, "normal_root_firing_scoped/v2", False, {}))
def test_selector_never_treats_present_invalid_marker_as_legacy_absence(historical: bool, marker) -> None:
    batch = _normal_batch()
    _replace_document(batch, "marking_delta/v1", {**batch["delta"], _MARKER: marker})
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch, historical=historical)


@pytest.mark.parametrize(("kind", "field", "wrong_ref"), (
    ("firing_completion/v2", "marking_delta_ref", _ref("marking_delta", 80)),
    ("marking_delta/v1", "net_instance_ref", _ref("net_instance", 80)),
    ("marking_checkpoint/v1", "settlement_delta_ref", _ref("marking_delta", 80)),
    (_SEAL, "parent_business_firing_ref", _ref("transition_firing", 80)),
))
def test_selector_rejects_exact_refs_bound_to_another_success(kind: str, field: str, wrong_ref: VersionRef) -> None:
    batch = _normal_batch()
    document = deepcopy(_member(batch, kind).metadata)
    document[field] = _payload(wrong_ref)
    _replace_document(batch, kind, document)
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)


def test_selector_rejects_delta_argument_detached_from_its_actual_prepared_object() -> None:
    batch = _normal_batch()
    batch["delta"] = {**batch["delta"], "deposited_refs": []}
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)


@pytest.mark.parametrize("kind", (_ROOT, _SEAL, "operation_result/v1", "marking_delta/v1"))
def test_selector_rejects_a_member_owned_by_another_producer(kind: str) -> None:
    batch = _normal_batch()
    original = _member(batch, kind)
    batch["objects"] = tuple(
        replace(obj, producer_invocation_id=_id("invocation", 90)) if obj is original else obj
        for obj in batch["objects"])
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)


@pytest.mark.parametrize("kind", (_ROOT, _SEAL, "firing_completion/v2", "marking_delta/v1"))
def test_selector_rejects_missing_or_duplicate_success_members(kind: str) -> None:
    batch = _normal_batch()
    member = _member(batch, kind)
    missing = {**batch, "objects": tuple(obj for obj in batch["objects"] if obj is not member)}
    duplicate = {**batch, "objects": batch["objects"] + (member,)}
    for malformed in (missing, duplicate):
        with pytest.raises(RegistryConflict):
            normal_root_allocation_scheme(**malformed)


@pytest.mark.parametrize("mismatch", ("task", "transaction", "root_source", "profile"))
def test_selector_rejects_foreign_owner_transaction_source_or_profile(mismatch: str) -> None:
    batch = _normal_batch()
    if mismatch == "task":
        batch["task_id"] = _id("task", 90)
    elif mismatch == "transaction":
        batch["transaction_id"] = _id("transaction", 90)
    else:
        root = deepcopy(_member(batch, _ROOT).metadata)
        if mismatch == "root_source":
            root["record_ref"]["source_id"] = "another-source"
        else:
            root["body"]["closure_profile"] = "execution-v1-unknown"
        _replace_document(batch, _ROOT, root)
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)


@pytest.mark.parametrize("other_kind", (
    None, "collaboration_root_terminal/v1", "collaboration_result_export/v1",
    "collaboration_acceptance/v1", "collaboration_contribution/v1",
))
def test_selector_rejects_tag_on_plain_or_other_collaboration_success(other_kind: str | None) -> None:
    batch = _normal_batch()
    batch["objects"] = tuple(obj for obj in batch["objects"] if obj.object_type not in {_ROOT, _SEAL})
    if other_kind is not None:
        ref = _ref("resource", 90, entity_type=other_kind)
        batch["objects"] += (_prepared(ref, {}, _id("invocation", 4)),)
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)
    untagged = {key: value for key, value in batch["delta"].items() if key != _MARKER}
    _replace_document(batch, "marking_delta/v1", untagged)
    assert normal_root_allocation_scheme(**batch) is None


def test_selector_rejects_claim_marker_even_inside_an_exact_normal_batch() -> None:
    batch = _normal_batch()
    _replace_document(batch, "marking_delta/v1", {**batch["delta"], "phase": "claim"})
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)


def test_selector_rejects_normal_root_with_declared_revision_effects() -> None:
    batch = _normal_batch()
    _replace_document(batch, "marking_delta/v1", {
        **batch["delta"], "declared_effects": {
            "selected_outcome_id": "revise", "effects": [{"effect_kind": "structural_revision"}],
        },
    })
    with pytest.raises(RegistryConflict):
        normal_root_allocation_scheme(**batch)
