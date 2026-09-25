"""Pure formal Petri-structure primitives owned by the Harness.

External modules may compose a candidate ``SymbolicNet``.  They do not mutate
the running net directly.  This module derives the exact elementary edits from
the registered predecessor and verifies that applying those edits reconstructs
the candidate Petri structure.

Registry publication, marking migration and workflow interpretation are
deliberately outside this pure layer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Callable

from .module import SymbolicNet
from .petri_contracts import (
    ArcDeclaration,
    DeclarationError,
    LeaseIdentityDeclaration,
    LogicalSlotBinding,
    OperationDeclaration,
    PlaceDeclaration,
    ResetArcDeclaration,
    ResourceLeasePoolBinding,
    TransitionDeclaration,
    VariableResourceArc,
)


class PetriStructureDeltaError(DeclarationError):
    """A structural edit is not an exact operation over its predecessor."""


@dataclass(frozen=True, slots=True)
class PetriBindingDeclaration:
    """One named symbolic interface-to-Petri binding."""

    name: str
    target: str


@dataclass(frozen=True, slots=True)
class PetriStructureSnapshot:
    """The executable Petri structure, excluding workflow/Registry metadata."""

    places: tuple[PlaceDeclaration, ...]
    transitions: tuple[TransitionDeclaration, ...]
    operations: tuple[OperationDeclaration, ...]
    arcs: tuple[ArcDeclaration, ...]
    port_place_bindings: tuple[PetriBindingDeclaration, ...]
    entry_bindings: tuple[PetriBindingDeclaration, ...]
    exit_bindings: tuple[PetriBindingDeclaration, ...]
    lease_identities: tuple[LeaseIdentityDeclaration, ...]
    lease_pools: tuple[ResourceLeasePoolBinding, ...]
    variable_resource_arcs: tuple[VariableResourceArc, ...]
    logical_slots: tuple[LogicalSlotBinding, ...]
    reset_arcs: tuple[ResetArcDeclaration, ...]

    @classmethod
    def from_symbolic(cls, net: SymbolicNet) -> "PetriStructureSnapshot":
        if not isinstance(net, SymbolicNet):
            raise TypeError("Petri structure requires an exact SymbolicNet")
        return cls(
            places=_ordered_unique("place", net.places, lambda value: value.name),
            transitions=_ordered_unique("transition", net.transitions, lambda value: value.name),
            operations=_ordered_unique("operation", net.operations, lambda value: value.name),
            arcs=_ordered_unique("arc", net.arcs, _arc_identity),
            port_place_bindings=_bindings(net.port_places),
            entry_bindings=_bindings(net.entry),
            exit_bindings=_bindings(net.exit),
            lease_identities=_ordered_unique(
                "lease_identity", net.lease_identities, lambda value: value.name),
            lease_pools=_ordered_unique("lease_pool", net.lease_pools, lambda value: value.name),
            variable_resource_arcs=_ordered_unique(
                "variable_resource_arc", net.variable_resource_arcs,
                lambda value: value.transition),
            logical_slots=_ordered_unique("logical_slot", net.logical_slots, lambda value: value.name),
            reset_arcs=_ordered_unique(
                "reset_arc", net.reset_arcs, _reset_arc_identity),
        )


@dataclass(frozen=True, slots=True)
class PetriStructureEdit:
    """One exact add/remove/replace over a typed Petri element identity."""

    element_kind: str
    action: str
    identity: str | tuple[str, str, str, str | None]
    before: Any | None
    after: Any | None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "element_kind": self.element_kind,
            "action": self.action,
            "identity": list(self.identity) if isinstance(self.identity, tuple) else self.identity,
            "before": None if self.before is None else asdict(self.before),
            "after": None if self.after is None else asdict(self.after),
        }
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


@dataclass(frozen=True, slots=True)
class PetriStructureDelta:
    """A deterministic atomic bundle of elementary Petri structure edits."""

    edits: tuple[PetriStructureEdit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"edits": [edit.to_dict() for edit in self.edits]}


@dataclass(frozen=True, slots=True)
class _ElementSpec:
    field: str
    value_type: type
    identity: Callable[[Any], Any]


def _arc_identity(value: ArcDeclaration):
    return value.place, value.transition, value.direction, value.outcome


def _reset_arc_identity(value: ResetArcDeclaration):
    return value.place, value.transition, value.selector, value.outcome


_ELEMENTS = {
    "place": _ElementSpec("places", PlaceDeclaration, lambda value: value.name),
    "transition": _ElementSpec("transitions", TransitionDeclaration, lambda value: value.name),
    "operation": _ElementSpec("operations", OperationDeclaration, lambda value: value.name),
    "arc": _ElementSpec("arcs", ArcDeclaration, _arc_identity),
    "reset_arc": _ElementSpec(
        "reset_arcs", ResetArcDeclaration, _reset_arc_identity),
    "port_place_binding": _ElementSpec(
        "port_place_bindings", PetriBindingDeclaration, lambda value: value.name),
    "entry_binding": _ElementSpec(
        "entry_bindings", PetriBindingDeclaration, lambda value: value.name),
    "exit_binding": _ElementSpec(
        "exit_bindings", PetriBindingDeclaration, lambda value: value.name),
    "lease_identity": _ElementSpec(
        "lease_identities", LeaseIdentityDeclaration, lambda value: value.name),
    "lease_pool": _ElementSpec(
        "lease_pools", ResourceLeasePoolBinding, lambda value: value.name),
    "variable_resource_arc": _ElementSpec(
        "variable_resource_arcs", VariableResourceArc, lambda value: value.transition),
    "logical_slot": _ElementSpec(
        "logical_slots", LogicalSlotBinding, lambda value: value.name),
}
_ACTIONS = frozenset({"add", "remove", "replace"})


def _identity_order(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _ordered_unique(label: str, values: tuple[Any, ...], identity: Callable[[Any], Any]):
    result = {}
    for value in values:
        key = identity(value)
        if key in result:
            raise PetriStructureDeltaError(f"Duplicate {label} identity in Petri structure")
        result[key] = value
    return tuple(result[key] for key in sorted(result, key=_identity_order))


def _bindings(values) -> tuple[PetriBindingDeclaration, ...]:
    if any(type(name) is not str or type(target) is not str for name, target in values.items()):
        raise PetriStructureDeltaError("Petri bindings require exact string identities")
    return tuple(PetriBindingDeclaration(name, values[name]) for name in sorted(values))


def _inventory(snapshot: PetriStructureSnapshot, spec: _ElementSpec):
    result = {}
    for value in getattr(snapshot, spec.field):
        if not isinstance(value, spec.value_type):
            raise PetriStructureDeltaError(
                f"Petri structure field {spec.field} requires {spec.value_type.__name__}")
        key = spec.identity(value)
        if key in result:
            raise PetriStructureDeltaError(
                f"Duplicate {spec.field} identity in Petri structure")
        result[key] = value
    return result


def derive_petri_structure_delta(
    predecessor: SymbolicNet | PetriStructureSnapshot,
    candidate: SymbolicNet | PetriStructureSnapshot,
) -> PetriStructureDelta:
    """Derive the sole deterministic elementary edit bundle between two nets."""

    before = (PetriStructureSnapshot.from_symbolic(predecessor)
              if isinstance(predecessor, SymbolicNet) else predecessor)
    after = (PetriStructureSnapshot.from_symbolic(candidate)
             if isinstance(candidate, SymbolicNet) else candidate)
    if not isinstance(before, PetriStructureSnapshot) or not isinstance(after, PetriStructureSnapshot):
        raise TypeError("Petri delta derivation requires SymbolicNet or PetriStructureSnapshot")
    edits = []
    for kind, spec in _ELEMENTS.items():
        old = _inventory(before, spec)
        new = _inventory(after, spec)
        for identity in sorted(old.keys() - new.keys(), key=_identity_order):
            edits.append(PetriStructureEdit(kind, "remove", identity, old[identity], None))
        for identity in sorted(old.keys() & new.keys(), key=_identity_order):
            if old[identity] != new[identity]:
                edits.append(PetriStructureEdit(
                    kind, "replace", identity, old[identity], new[identity]))
        for identity in sorted(new.keys() - old.keys(), key=_identity_order):
            edits.append(PetriStructureEdit(kind, "add", identity, None, new[identity]))
    return PetriStructureDelta(tuple(edits))


def apply_petri_structure_delta(
    predecessor: SymbolicNet | PetriStructureSnapshot,
    delta: PetriStructureDelta,
) -> PetriStructureSnapshot:
    """Atomically apply exact edits, rejecting stale or fabricated before state."""

    before = (PetriStructureSnapshot.from_symbolic(predecessor)
              if isinstance(predecessor, SymbolicNet) else predecessor)
    if not isinstance(before, PetriStructureSnapshot) or not isinstance(delta, PetriStructureDelta):
        raise TypeError("Petri delta application requires typed predecessor and delta")
    inventories = {kind: _inventory(before, spec) for kind, spec in _ELEMENTS.items()}
    seen = set()
    for edit in delta.edits:
        if not isinstance(edit, PetriStructureEdit):
            raise PetriStructureDeltaError("Petri delta contains an untyped edit")
        spec = _ELEMENTS.get(edit.element_kind)
        if spec is None or edit.action not in _ACTIONS:
            raise PetriStructureDeltaError("Unknown Petri element edit")
        marker = edit.element_kind, edit.identity
        if marker in seen:
            raise PetriStructureDeltaError("Petri delta repeats one element identity")
        seen.add(marker)
        current = inventories[edit.element_kind]
        if edit.action == "add":
            if edit.before is not None or not isinstance(edit.after, spec.value_type):
                raise PetriStructureDeltaError("Petri add requires only a typed after value")
            if spec.identity(edit.after) != edit.identity or edit.identity in current:
                raise PetriStructureDeltaError("Petri add identity is not absent and exact")
            current[edit.identity] = edit.after
        elif edit.action == "remove":
            if not isinstance(edit.before, spec.value_type) or edit.after is not None:
                raise PetriStructureDeltaError("Petri remove requires only a typed before value")
            if spec.identity(edit.before) != edit.identity or current.get(edit.identity) != edit.before:
                raise PetriStructureDeltaError("Petri remove differs from exact predecessor")
            del current[edit.identity]
        else:
            if (not isinstance(edit.before, spec.value_type)
                    or not isinstance(edit.after, spec.value_type)):
                raise PetriStructureDeltaError("Petri replace requires typed before and after values")
            if (spec.identity(edit.before) != edit.identity
                    or spec.identity(edit.after) != edit.identity
                    or current.get(edit.identity) != edit.before):
                raise PetriStructureDeltaError("Petri replace differs from exact predecessor")
            current[edit.identity] = edit.after
    values = {}
    for kind, spec in _ELEMENTS.items():
        inventory = inventories[kind]
        values[spec.field] = tuple(
            inventory[key] for key in sorted(inventory, key=_identity_order))
    return PetriStructureSnapshot(**values)


def verify_petri_structure_delta(
    predecessor: SymbolicNet | PetriStructureSnapshot,
    candidate: SymbolicNet | PetriStructureSnapshot,
    delta: PetriStructureDelta,
) -> PetriStructureSnapshot:
    """Require the canonical delta and its pure application to equal candidate."""

    expected = derive_petri_structure_delta(predecessor, candidate)
    if delta != expected:
        raise PetriStructureDeltaError("Petri delta is not the canonical predecessor/candidate change")
    applied = apply_petri_structure_delta(predecessor, delta)
    target = (PetriStructureSnapshot.from_symbolic(candidate)
              if isinstance(candidate, SymbolicNet) else candidate)
    if applied != target:
        raise PetriStructureDeltaError("Petri delta does not reconstruct the exact candidate structure")
    return applied


__all__ = (
    "PetriBindingDeclaration",
    "PetriStructureDelta",
    "PetriStructureDeltaError",
    "PetriStructureEdit",
    "PetriStructureSnapshot",
    "apply_petri_structure_delta",
    "derive_petri_structure_delta",
    "verify_petri_structure_delta",
)
