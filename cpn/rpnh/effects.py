"""Declared Success effects and host-supplied candidate transformations.

Callbacks return candidate data only. Registry retains all publication,
provenance, claim, marking, capacity, and settlement authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


STRUCTURAL_REVISION_EFFECT = "structural_revision/v1"


class EffectDeclarationError(ValueError):
    pass


WITNESS_FIELDS = frozenset({
    "source_transition", "review_transition", "candidate_port", "decision_port",
    "task_port", "review_candidate_port", "review_task_port",
    "source_instruction_port", "review_instruction_port",
    "source_optional_ports", "review_optional_ports", "candidate_schema",
    "decision_schema", "receipt_schema", "receipt_port", "handoff_place",
    "receipt_place", "capacity_place", "reviewed_place", "decision_place",
    "accepted_verdict", "base_phase", "candidate_phase",
})


def structural_effect_declaration(
        effects: object, contracts: object,
) -> Mapping[str, Any] | None:
    """Select a declared effect only with immutable registered permission."""
    if effects is None:
        return None
    if not isinstance(effects, (list, tuple)):
        raise EffectDeclarationError("transition effects must be an array")
    selected = [effect for effect in effects
                if isinstance(effect, Mapping)
                and effect.get("effect_id") == STRUCTURAL_REVISION_EFFECT]
    if not selected:
        return None
    if len(selected) != 1:
        raise EffectDeclarationError("one structural effect is required")
    effect = selected[0]
    if (not isinstance(contracts, Mapping)
            or not isinstance(contracts.get("permitted_effects"), (list, tuple))
            or STRUCTURAL_REVISION_EFFECT not in contracts["permitted_effects"]):
        raise EffectDeclarationError("operation contract does not permit structural effect")
    witness = effect.get("witness")
    if (set(effect) != {"effect_id", "component_key", "witness"}
            or not isinstance(effect["component_key"], str)
            or not effect["component_key"]
            or not isinstance(witness, Mapping)
            or set(witness) != WITNESS_FIELDS):
        raise EffectDeclarationError("structural effect lacks its exact witness declaration")
    for name, value in witness.items():
        if name in {"source_optional_ports", "review_optional_ports"}:
            if (not isinstance(value, (list, tuple))
                    or any(not isinstance(item, str) or not item for item in value)
                    or len(value) != len(set(value))):
                raise EffectDeclarationError("optional witness ports must be distinct ids")
        elif not isinstance(value, str) or not value:
            raise EffectDeclarationError("witness identities must be explicit strings")
    return effect


@dataclass(frozen=True, slots=True)
class RegisteredStructuralEffect:
    """No Registry handles or authority validators are passed to callbacks."""

    key: str
    compile_candidate: Callable[..., Any]
    lower_candidate: Callable[..., Any]

    def __post_init__(self) -> None:
        if (not self.key or not callable(self.compile_candidate)
                or not callable(self.lower_candidate)):
            raise EffectDeclarationError("invalid host structural component")
