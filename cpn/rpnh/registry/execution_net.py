"""Strict pure-data contracts for a same-Registry execution Petri net."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import VersionRef


RecoveryMode = Literal["pure", "idempotent_materialization"]
ExecutionCheckpointStatus = Literal["running", "map_ready"]
_NAME = re.compile(r"^[a-z][a-z0-9_.-]*$")


class ExecutionNetError(ValueError):
    """Execution-net data or authority is malformed or inconsistent."""


def _name(label: str, value: object) -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ExecutionNetError(f"{label} must be a lowercase lexical id")
    return value


@dataclass(frozen=True, slots=True, order=True)
class ExecutionTransition:
    transition_id: str
    recovery_mode: RecoveryMode = "pure"

    def __post_init__(self) -> None:
        _name("execution transition", self.transition_id)
        if self.recovery_mode not in {"pure", "idempotent_materialization"}:
            raise ExecutionNetError("execution recovery mode is unsupported")


@dataclass(frozen=True, slots=True, order=True)
class ExecutionInputArc:
    place: str
    transition_id: str
    weight: int = 1

    def __post_init__(self) -> None:
        _name("execution input place", self.place)
        _name("execution input transition", self.transition_id)
        if (isinstance(self.weight, bool) or not isinstance(self.weight, int)
                or self.weight < 1):
            raise ExecutionNetError("execution input weight must be positive")


@dataclass(frozen=True, slots=True, order=True)
class ExecutionOutputArc:
    transition_id: str
    place: str
    weight: int = 1

    def __post_init__(self) -> None:
        _name("execution output transition", self.transition_id)
        _name("execution output place", self.place)
        if (isinstance(self.weight, bool) or not isinstance(self.weight, int)
                or self.weight < 1):
            raise ExecutionNetError("execution output weight must be positive")


@dataclass(frozen=True, slots=True)
class ExecutionNetDefinition:
    definition_key: str
    places: tuple[str, ...]
    transitions: tuple[ExecutionTransition, ...]
    input_arcs: tuple[ExecutionInputArc, ...]
    output_arcs: tuple[ExecutionOutputArc, ...]
    initial_place: str
    initial_tokens: int
    terminal_places: tuple[str, ...]

    def __post_init__(self) -> None:
        _name("execution definition key", self.definition_key)
        if (not isinstance(self.places, tuple) or not self.places
                or any(not isinstance(value, str)
                       or _NAME.fullmatch(value) is None
                       for value in self.places)
                or self.places != tuple(sorted(self.places))
                or len(set(self.places)) != len(self.places)):
            raise ExecutionNetError(
                "execution places must be nonempty, unique, and sorted")
        if (not isinstance(self.transitions, tuple) or not self.transitions
                or any(not isinstance(value, ExecutionTransition)
                       for value in self.transitions)
                or self.transitions != tuple(sorted(self.transitions))
                or len({value.transition_id for value in self.transitions})
                != len(self.transitions)):
            raise ExecutionNetError(
                "execution transitions must be nonempty, unique, and sorted")
        if (not isinstance(self.input_arcs, tuple) or not self.input_arcs
                or any(not isinstance(value, ExecutionInputArc)
                       for value in self.input_arcs)
                or self.input_arcs != tuple(sorted(self.input_arcs))
                or len({(value.place, value.transition_id)
                        for value in self.input_arcs}) != len(self.input_arcs)):
            raise ExecutionNetError(
                "execution input arcs must be nonempty, unique, and sorted")
        if (not isinstance(self.output_arcs, tuple) or not self.output_arcs
                or any(not isinstance(value, ExecutionOutputArc)
                       for value in self.output_arcs)
                or self.output_arcs != tuple(sorted(self.output_arcs))
                or len({(value.transition_id, value.place)
                        for value in self.output_arcs}) != len(self.output_arcs)):
            raise ExecutionNetError(
                "execution output arcs must be nonempty, unique, and sorted")
        places = set(self.places)
        transition_ids = {value.transition_id for value in self.transitions}
        _name("execution initial place", self.initial_place)
        if self.initial_place not in places:
            raise ExecutionNetError("execution initial place is undeclared")
        if (isinstance(self.initial_tokens, bool)
                or not isinstance(self.initial_tokens, int)
                or self.initial_tokens < 1):
            raise ExecutionNetError("execution initial token count must be positive")
        if (not isinstance(self.terminal_places, tuple)
                or not self.terminal_places
                or any(not isinstance(value, str)
                       or _NAME.fullmatch(value) is None
                       for value in self.terminal_places)
                or self.terminal_places != tuple(sorted(self.terminal_places))
                or len(set(self.terminal_places)) != len(self.terminal_places)
                or not set(self.terminal_places).issubset(places)
                or self.initial_place in self.terminal_places):
            raise ExecutionNetError(
                "execution terminal places must be declared, unique, and sorted")
        if any(arc.place not in places or arc.transition_id not in transition_ids
               for arc in self.input_arcs):
            raise ExecutionNetError("execution input arc has an unknown endpoint")
        if any(arc.place not in places or arc.transition_id not in transition_ids
               for arc in self.output_arcs):
            raise ExecutionNetError("execution output arc has an unknown endpoint")
        if ({arc.transition_id for arc in self.input_arcs} != transition_ids
                or {arc.transition_id for arc in self.output_arcs}
                != transition_ids):
            raise ExecutionNetError(
                "every execution transition requires input and output arcs")
        if any(arc.place in self.terminal_places for arc in self.input_arcs):
            raise ExecutionNetError("execution terminal places cannot be consumed")
        if any(arc.place == self.initial_place for arc in self.output_arcs):
            raise ExecutionNetError("execution initial place cannot be produced")

    def transition(self, transition_id: str) -> ExecutionTransition:
        matches = tuple(
            item for item in self.transitions
            if item.transition_id == transition_id)
        if len(matches) != 1:
            raise ExecutionNetError(
                f"unknown execution transition: {transition_id!r}")
        return matches[0]

    def inputs_for(self, transition_id: str) -> tuple[ExecutionInputArc, ...]:
        self.transition(transition_id)
        return tuple(
            item for item in self.input_arcs
            if item.transition_id == transition_id)

    def outputs_for(self, transition_id: str) -> tuple[ExecutionOutputArc, ...]:
        self.transition(transition_id)
        return tuple(
            item for item in self.output_arcs
            if item.transition_id == transition_id)


@dataclass(frozen=True, slots=True)
class ExecutionParentAuthority:
    invocation_ref: VersionRef
    business_firing_ref: VersionRef
    business_net_ref: VersionRef
    business_checkpoint_ref: VersionRef

    def __post_init__(self) -> None:
        expected = (
            (self.invocation_ref, "invocation/v1"),
            (self.business_firing_ref, "transition_firing/v1"),
            (self.business_net_ref, "net_instance/v1"),
            (self.business_checkpoint_ref, "marking_checkpoint/v1"),
        )
        if any(not isinstance(ref, VersionRef) or ref.entity_type != kind
               for ref, kind in expected):
            raise ExecutionNetError(
                "execution parent requires exact business Registry refs")


def execution_child_stream_id(parent: ExecutionParentAuthority) -> str:
    """Return the CAS stream that seals one firing's execution child set."""

    if not isinstance(parent, ExecutionParentAuthority):
        raise TypeError("execution child stream requires parent authority")
    return f"execution-children:{parent.business_firing_ref.version_id}"


@dataclass(frozen=True, slots=True)
class ExecutionToken:
    token_ref: VersionRef
    place: str
    ordinal: int
    produced_by_firing_ref: VersionRef | None


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    checkpoint_ref: VersionRef
    sequence: int
    previous_checkpoint_ref: VersionRef | None
    transition_firing_ref: VersionRef | None
    tokens: tuple[ExecutionToken, ...]
    active_firing_refs: tuple[VersionRef, ...]
    evidence_refs: tuple[VersionRef, ...]
    next_token_ordinal: int
    status: ExecutionCheckpointStatus

    @property
    def marking(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for token in self.tokens:
            result[token.place] = result.get(token.place, 0) + 1
        return dict(sorted(result.items()))

    @property
    def map_ready(self) -> bool:
        return self.status == "map_ready"


@dataclass(frozen=True, slots=True)
class ExecutionState:
    instance_ref: VersionRef
    definition_ref: VersionRef
    parent: ExecutionParentAuthority
    definition: ExecutionNetDefinition
    checkpoint: ExecutionCheckpoint

    @property
    def map_ready_checkpoint_ref(self) -> VersionRef | None:
        return (self.checkpoint.checkpoint_ref
                if self.checkpoint.map_ready else None)


@dataclass(frozen=True, slots=True)
class ExecutionRecovery:
    firing_ref: VersionRef
    transition_id: str
    recovery_mode: RecoveryMode
    action: Literal["replay_pure", "retry_same_materialization"]
    materialization_key: str | None


__all__ = (
    "ExecutionCheckpoint",
    "ExecutionCheckpointStatus",
    "ExecutionInputArc",
    "ExecutionNetDefinition",
    "ExecutionNetError",
    "ExecutionOutputArc",
    "ExecutionParentAuthority",
    "ExecutionRecovery",
    "ExecutionState",
    "ExecutionToken",
    "ExecutionTransition",
    "RecoveryMode",
    "execution_child_stream_id",
)
