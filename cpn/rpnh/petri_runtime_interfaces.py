"""Structural inputs to the marking; no default loader or execution owner.

These protocols describe members actually read by the existing marking. They
do not confer Registry authority: concrete Core DTOs, exact refs and the normal
Registry checkpoint/publication validation remain mandatory.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .registry.models import VersionRef
from .registry.resources import ExecutableNetAuthority


class DeclaredExactRef(Protocol):
    entity_type: str
    logical_id: str
    version_id: str


class MarkingTransition(Protocol):
    config: Mapping[str, Any]


class MarkingLeaseClaim(Protocol):
    lease_identity_ref: DeclaredExactRef
    expected_resource_ref: DeclaredExactRef | None
    access_mode: str


class MarkingVariableResourceArc(Protocol):
    transition_id: str
    claim_token_place: str
    lease_pool_place: str
    initial_claims: tuple[MarkingLeaseClaim, ...]


class MarkingLeasePool(Protocol):
    lease_pool_place: str
    initial_resource_refs: tuple[DeclaredExactRef, ...]
    initial_logical_slot_refs: tuple[DeclaredExactRef, ...]


class MarkingLogicalArtifact(Protocol):
    slot_ref: DeclaredExactRef
    producer_transition_id: str
    output_port_id: str
    candidate_place: str
    published_place: str


class MarkingTimedWaitGuard(Protocol):
    transition_id: str


@runtime_checkable
class MarkingNetStructure(Protocol):
    """Complete net surface consumed by the marking, not a workflow identity.

    Retired fault/ignition and disabled timed data are described only because
    retained mechanical checks inspect them; this does not enable legacy paths.
    Config values and colour/projector declarations retain their existing meaning.
    """
    places: tuple[str, ...]
    transitions: Mapping[str, MarkingTransition]
    initial_marking: Mapping[str, int]
    sources: tuple[str, ...]
    registry_read_arcs: tuple[tuple[str, str, int], ...]
    token_input_arcs: tuple[tuple[str, str, int], ...]
    output_arcs: tuple[tuple[str, str, int], ...]
    synthetic_ignition_arcs: tuple[tuple[str, str, int], ...]
    timed_wait_guards: tuple[MarkingTimedWaitGuard, ...]
    place_color_sets: Mapping[str, tuple[str, ...]]
    resource_lease_pool_bindings: tuple[MarkingLeasePool, ...]
    variable_resource_arcs: tuple[MarkingVariableResourceArc, ...]
    logical_artifact_bindings: tuple[MarkingLogicalArtifact, ...]
    registry_net_ref: VersionRef | None
    registered_fault_transition_routes: Mapping[str, tuple[object, object]]
    registered_fault_places: tuple[str, ...]

    def producers_of(self, place: str) -> tuple[str, ...]: ...
    def consumers_of(self, place: str) -> tuple[str, ...]: ...
    def registered_fault_transition(
        self, transition_id: str,
    ) -> tuple[object, object] | None: ...
    def registered_fault_consumers_of(self, place: str) -> tuple[str, ...]: ...
    def registered_fault_outputs_of(self, transition_id: str) -> tuple[str, ...]: ...
    def place_kind(self, place: str) -> str: ...
    def agent_resource_places(self) -> frozenset[str]: ...
    def resource_lease_places(self) -> frozenset[str]: ...
    def reusable_resource_places(self) -> frozenset[str]: ...
    def variable_resource_arc_for(
        self, transition_id: str,
    ) -> MarkingVariableResourceArc | None: ...
    def logical_document_input_places(
        self, transition_id: str,
    ) -> frozenset[str]: ...
    def count_predicates_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, int, str, str], ...]: ...
    def forward_source_place(
        self, transition_id: str, output_place: str,
    ) -> str | None: ...
    def inputs_of(self, transition_id: str) -> tuple[str, ...]: ...
    def token_inputs_of(self, transition_id: str) -> tuple[str, ...]: ...
    def reference_inputs_of(self, transition_id: str) -> tuple[str, ...]: ...
    def verdict_guards_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, bool | str], ...]: ...
    def output_guards_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, bool | str], ...]: ...
    def output_guard_for(
        self, transition_id: str, place: str,
    ) -> bool | str | None: ...
    def output_emit_of(self, transition_id: str) -> tuple[tuple[str, str], ...]: ...
    def output_emit_for(self, transition_id: str, place: str) -> str | None: ...
    def output_color_expression_for(
        self, transition_id: str, place: str,
    ) -> Mapping[str, Any] | None: ...
    def output_color_expressions_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, Mapping[str, Any]], ...]: ...
    def output_lease_claims_for(
        self, transition_id: str, place: str,
    ) -> tuple[object, ...]: ...
    def output_lease_claim_exclusions_for(
        self, transition_id: str, place: str,
    ) -> tuple[str, ...]: ...
    def output_lease_claim_set_for(
        self, transition_id: str, place: str,
    ) -> object | None: ...
    def selected_output_arcs_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, str], ...]: ...
    def selected_output_place_for_selector(
        self, transition_id: str, selector: str,
    ) -> str | None: ...
    def output_effect_selector_for(
        self, transition_id: str, place: str,
    ) -> str | None: ...
    def lease_pool_place_for(self, qualified_name: str) -> str: ...
    def reset_arcs_of(
        self, transition_id: str,
    ) -> tuple[tuple[str, str], ...]: ...
    def reset_place_for_selector(
        self, transition_id: str, selector: str,
    ) -> str | None: ...
    def lease_identity_ref(self, qualified_name: str) -> object: ...
    def project_output_color(
        self, transition_id: str, place: str,
        claimed_colors: Sequence[tuple[str, bool | str | None]],
    ) -> str | None: ...
    def read_scope_of(self, transition_id: str) -> tuple[tuple[str, str], ...]: ...
    def is_guard_only_transition(self, transition_id: str) -> bool: ...
    def outputs_of(self, transition_id: str) -> tuple[str, ...]: ...
    def input_arc_weight(self, place: str, transition_id: str) -> int: ...
    def output_arc_weight(self, transition_id: str, place: str) -> int: ...


@runtime_checkable
class RegistryTokenAllocator(Protocol):
    """Owner's exact token-ref allocation surface used by typed_snapshot.

    Returned values still pass the Core petri_token/v1 identity gate; allocating
    a ref is never proof of publication, admission, settlement or a checkpoint.
    """
    def petri_token_ref(
        self, executable: ExecutableNetAuthority, token_id: int,
    ) -> VersionRef: ...


__all__ = ["MarkingNetStructure", "RegistryTokenAllocator"]
