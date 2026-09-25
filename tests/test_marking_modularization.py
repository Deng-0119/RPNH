from __future__ import annotations

import importlib

import pytest

import cpn.rpnh as rpnh
import cpn.rpnh.marking as marking
from cpn.rpnh._marking import delta
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef


class _GuardOnlyNet:
    place_color_sets = {}
    timed_wait_guards = ()
    token_input_arcs = ()
    registry_net_ref = None
    variable_resource_arcs = ()

    @staticmethod
    def registered_fault_transition(_transition_id: str):
        return None

    @staticmethod
    def count_predicates_of(_transition_id: str):
        return ()

    @staticmethod
    def verdict_guards_of(_transition_id: str):
        return ()

    @staticmethod
    def is_guard_only_transition(_transition_id: str) -> bool:
        return True

    @staticmethod
    def variable_resource_arc_for(_transition_id: str):
        return None


def _id(kind: str, value: int) -> TypedId:
    return TypedId(kind, f"{value:032x}")  # type: ignore[arg-type]


def _firing_ref(value: int = 1) -> VersionRef:
    return VersionRef(
        "transition_firing/v1",
        _id("transition_firing", value),
        _id("transition_firing_version", value + 100),
    )


@pytest.mark.parametrize(
    ("module_name", "method_name", "args", "kwargs"),
    (
        (
            "selection",
            "is_enabled",
            ("transition",),
            {"timed_wait_guard_states": ()},
        ),
        ("claims", "bind_active_claim", (0, object()), {}),
        ("outputs", "deposit_outputs", ("transition", []), {"claim_epoch": 0}),
        ("revisions", "bump_epoch", (), {"carry_forward_except": {"place"}}),
        ("checkpoints", "marking_snapshot", (), {}),
        ("token_state", "next_attempt", ("transition",), {}),
    ),
)
def test_team_marking_methods_are_compatible_delegates(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    method_name: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    implementation = importlib.import_module(
        f"cpn.rpnh._marking.{module_name}")
    instance = object.__new__(marking.TeamNetMarking)
    sentinel = object()
    received: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def replacement(*actual_args, **actual_kwargs):
        received.append((actual_args, actual_kwargs))
        return sentinel

    monkeypatch.setattr(implementation, method_name, replacement)

    assert getattr(instance, method_name)(*args, **kwargs) is sentinel
    assert received == [((instance, *args), kwargs)]


def test_class_and_static_marking_delegates_preserve_receivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints = importlib.import_module("cpn.rpnh._marking.checkpoints")
    legacy_timed = importlib.import_module("cpn.rpnh._marking.legacy_timed")
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        checkpoints,
        "from_authority",
        lambda *args: calls.append(args) or "restored",
    )
    monkeypatch.setattr(
        legacy_timed,
        "_bound_input",
        lambda *args: calls.append(args) or "bound",
    )

    assert marking.TeamNetMarking.from_authority("net", "authority") == "restored"
    assert marking.TeamNetMarking._bound_input("token") == "bound"
    assert calls == [
        (marking.TeamNetMarking, "net", "authority"),
        ("token",),
    ]


def test_marking_types_and_public_delta_exports_keep_exact_identity() -> None:
    modules = tuple(
        importlib.import_module(f"cpn.rpnh._marking.{name}")
        for name in (
            "selection",
            "claims",
            "outputs",
            "revisions",
            "checkpoints",
            "token_state",
            "legacy_timed",
        )
    )

    for implementation in modules:
        assert implementation.Token is marking.Token
        assert implementation.MarkingStateError is marking.MarkingStateError
    assert rpnh.PetriMarkingDelta is marking.PetriMarkingDelta is delta.PetriMarkingDelta
    assert rpnh.PetriTokenEdit is marking.PetriTokenEdit is delta.PetriTokenEdit


def test_selection_and_reservation_share_claim_arc_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selection = importlib.import_module("cpn.rpnh._marking.selection")
    instance = marking.TeamNetMarking(_GuardOnlyNet())
    calls: list[str] = []

    def claim_arcs(_marking, transition_id: str):
        calls.append(transition_id)
        return ()

    monkeypatch.setattr(selection, "_claim_input_arcs", claim_arcs)

    assert instance.is_enabled("transition") is True
    assert instance._try_reserve("transition", set()) is not None
    assert calls == ["transition", "transition", "transition"]


def test_local_claim_is_rekeyed_to_the_exact_registry_firing_ref() -> None:
    instance = marking.TeamNetMarking(_GuardOnlyNet())
    claim = marking._ActiveClaim(
        claim_id=0,
        transition_id="transition",
        epoch=0,
        token_ids=(),
        token_refs=(),
    )
    instance._active_claims[0] = claim
    firing_ref = _firing_ref()

    assert instance.bind_active_claim(0, firing_ref) is firing_ref
    assert instance._active_claims == {firing_ref: claim}


def test_historical_timed_gate_runs_before_legacy_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_timed = importlib.import_module("cpn.rpnh._marking.legacy_timed")
    instance = marking.TeamNetMarking(_GuardOnlyNet())
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(legacy_timed, "active_timed_claim", forbidden)

    with pytest.raises(marking.MarkingStateError, match="historical timed/hash"):
        instance.active_timed_claim
    assert called is False


def test_team_marking_remains_the_single_mutable_state_owner() -> None:
    instance = marking.TeamNetMarking(_GuardOnlyNet())

    assert instance.__dict__.keys() >= {
        "_tokens",
        "_epoch",
        "_attempts",
        "_active_claims",
        "_lock",
    }
