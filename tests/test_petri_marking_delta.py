from __future__ import annotations

import cpn.rpnh as rpnh
import cpn.rpnh.marking as marking
import cpn.rpnh._marking as private_marking
from cpn.rpnh._marking import delta
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import (
    PetriTokenAuthority,
    PetriTokenState,
    RegistryHead,
    TypedMarkingAuthority,
)


def _id(kind: str, value: int) -> TypedId:
    return TypedId(kind, f"{value:032x}")  # type: ignore[arg-type]


def _ref(entity_type: str, logical_kind: str, version_kind: str,
         value: int) -> VersionRef:
    return VersionRef(
        entity_type, _id(logical_kind, value), _id(version_kind, value + 100))


def _token(token_id: int) -> tuple[VersionRef, PetriTokenState]:
    token_ref = _ref(
        "petri_token/v1", "petri_token", "petri_token_version", token_id)
    return token_ref, PetriTokenState(
        token_ref=token_ref,
        token_id=token_id,
        place=f"place-{token_id}",
        epoch=0,
        producer=None,
        consumer=None,
        resource_ref=None,
        work_resource_ref=None,
        kind=None,
        consumed_by=None,
        override_warning=None,
        verdict=None,
        continuation=None,
    )


def _marking(
        checkpoint_value: int, net_ref: VersionRef,
        states: tuple[tuple[VersionRef, PetriTokenState], ...],
) -> TypedMarkingAuthority:
    token_refs = tuple(ref for ref, _state in states)
    head = RegistryHead(ordinal=0, writer_fencing_epoch=1,
                        task_control_sequence=0, stream_heads={})
    return TypedMarkingAuthority(
        checkpoint_ref=_ref(
            "marking_checkpoint/v1", "marking_checkpoint",
            "marking_checkpoint_version", checkpoint_value),
        net_ref=net_ref,
        team_design_root_ref=_ref(
            "team_design_root/v1", "team_design_root",
            "team_design_root_version", 90),
        epoch=0,
        next_token_id=20,
        attempts=(),
        token_refs=token_refs,
        tokens=tuple(
            PetriTokenAuthority(ref, state, head) for ref, state in states),
        previous_checkpoint_ref=None,
        settlement_delta_ref=None,
        transition_firing_refs=(),
        verified_at_head=head,
    )


def test_marking_delta_exports_are_single_objects() -> None:
    names = (
        "PetriTokenEdit",
        "PetriMarkingDelta",
        "derive_petri_marking_delta",
        "apply_petri_marking_delta",
        "verify_petri_marking_delta",
    )

    for name in names:
        assert getattr(marking, name) is getattr(delta, name)
        assert getattr(rpnh, name) is getattr(delta, name)
        assert getattr(private_marking, name) is getattr(delta, name)
    assert delta.PetriTokenEdit.__module__ == "cpn.rpnh._marking.delta"
    assert delta.PetriMarkingDelta.__module__ == "cpn.rpnh._marking.delta"


def test_marking_delta_derives_applies_and_verifies_without_mutating_markings() -> None:
    net_ref = _ref("net_instance/v1", "net_instance", "net_instance_version", 1)
    consumed = _token(2)
    retained = _token(3)
    deposited = _token(4)
    predecessor = _marking(10, net_ref, (consumed, retained))
    successor = _marking(11, net_ref, (retained, deposited))
    predecessor_tokens = predecessor.tokens
    successor_tokens = successor.tokens

    result = marking.derive_petri_marking_delta(
        predecessor, successor, (deposited,))

    assert result.consumed_token_refs == (consumed[0],)
    assert result.retained_token_refs == (retained[0],)
    assert result.deposited_token_refs == (deposited[0],)
    assert marking.apply_petri_marking_delta(predecessor, result) == (
        retained[1], deposited[1])
    assert marking.verify_petri_marking_delta(
        predecessor, successor, (deposited,), result) == (
        retained[1], deposited[1])
    assert predecessor.tokens == predecessor_tokens
    assert successor.tokens == successor_tokens
