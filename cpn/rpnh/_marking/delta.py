"""Pure exact-occurrence Petri marking delta operations."""
from __future__ import annotations

from dataclasses import dataclass

from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.marking import MarkingStateError, _version_ref_key


@dataclass(frozen=True, slots=True)
class PetriTokenEdit:
    """One immutable occurrence consume/deposit primitive.

    A token is never moved, recoloured or rewritten in place.  Such a CPN
    change is represented by consuming the old occurrence and depositing one
    new typed occurrence.
    """

    action: str
    token_ref: VersionRef
    before: object | None
    after: object | None

    def __post_init__(self) -> None:
        from cpn.rpnh.registry.resources import PetriTokenState

        if (self.action not in {"consume", "deposit"}
                or not isinstance(self.token_ref, VersionRef)
                or self.token_ref.entity_type != "petri_token/v1"):
            raise MarkingStateError("Petri token edit has no exact primitive identity")
        if self.action == "consume":
            valid = (isinstance(self.before, PetriTokenState)
                     and self.before.token_ref == self.token_ref
                     and self.after is None)
        else:
            valid = (self.before is None
                     and isinstance(self.after, PetriTokenState)
                     and self.after.token_ref == self.token_ref)
        if not valid:
            raise MarkingStateError(
                "Petri token edit must consume before or deposit after, never rewrite")


@dataclass(frozen=True, slots=True)
class PetriMarkingDelta:
    """Canonical pure token change between two typed PN markings.

    This is calculated before a Registry success transaction stages any object
    or event.  It records only formal occurrence changes; resource and workflow
    meaning remain outside the Petri runtime.
    """

    net_ref: VersionRef
    predecessor_checkpoint_ref: VersionRef
    predecessor_token_refs: tuple[VersionRef, ...]
    consumed_token_refs: tuple[VersionRef, ...]
    retained_token_refs: tuple[VersionRef, ...]
    deposited_token_refs: tuple[VersionRef, ...]
    successor_token_refs: tuple[VersionRef, ...]
    edits: tuple[PetriTokenEdit, ...]


def derive_petri_marking_delta(predecessor, successor, new_tokens) -> PetriMarkingDelta:
    """Derive and validate the sole exact occurrence delta for a successor."""

    from cpn.rpnh.registry.resources import (
        PetriTokenState, TypedMarkingAuthority)

    if (not isinstance(predecessor, TypedMarkingAuthority)
            or not isinstance(successor, TypedMarkingAuthority)
            or predecessor.net_ref != successor.net_ref
            or not isinstance(new_tokens, tuple)
            or any(not isinstance(ref, VersionRef)
                   or not isinstance(state, PetriTokenState)
                   or state.token_ref != ref
                   for ref, state in new_tokens)):
        raise MarkingStateError(
            "Petri marking delta requires exact typed predecessor/successor occurrences")
    before = {item.token_ref: item.state for item in predecessor.tokens}
    after = {item.token_ref: item.state for item in successor.tokens}
    if (len(before) != len(predecessor.tokens)
            or len(after) != len(successor.tokens)
            or set(before) != set(predecessor.token_refs)
            or set(after) != set(successor.token_refs)):
        raise MarkingStateError("Petri marking delta contains repeated token authority")
    retained = set(before) & set(after)
    if any(before[ref] != after[ref] for ref in retained):
        raise MarkingStateError("Petri marking delta rewrites a retained occurrence")
    deposited = set(after) - set(before)
    supplied = {ref: state for ref, state in new_tokens}
    if (len(supplied) != len(new_tokens)
            or set(supplied) != deposited
            or any(supplied[ref] != after[ref] for ref in deposited)):
        raise MarkingStateError(
            "Petri marking delta deposited occurrences differ from the successor")

    ordered = lambda refs: tuple(sorted(refs, key=_version_ref_key))
    consumed_refs = ordered(set(before) - set(after))
    deposited_refs = ordered(deposited)
    edits = tuple(
        PetriTokenEdit("consume", ref, before[ref], None)
        for ref in consumed_refs) + tuple(
        PetriTokenEdit("deposit", ref, None, after[ref])
        for ref in deposited_refs)
    return PetriMarkingDelta(
        net_ref=predecessor.net_ref,
        predecessor_checkpoint_ref=predecessor.checkpoint_ref,
        predecessor_token_refs=ordered(before),
        consumed_token_refs=consumed_refs,
        retained_token_refs=ordered(retained),
        deposited_token_refs=deposited_refs,
        successor_token_refs=ordered(after),
        edits=edits,
    )


def apply_petri_marking_delta(predecessor, delta: PetriMarkingDelta):
    """Apply exact token primitives and return the formal successor multiset."""

    from cpn.rpnh.registry.resources import (
        PetriTokenState, TypedMarkingAuthority)

    if (not isinstance(predecessor, TypedMarkingAuthority)
            or not isinstance(delta, PetriMarkingDelta)
            or delta.net_ref != predecessor.net_ref
            or delta.predecessor_checkpoint_ref != predecessor.checkpoint_ref):
        raise MarkingStateError(
            "Petri marking delta application requires its exact predecessor")
    current = {item.token_ref: item.state for item in predecessor.tokens}
    if (len(current) != len(predecessor.tokens)
            or tuple(sorted(current, key=_version_ref_key))
            != delta.predecessor_token_refs):
        raise MarkingStateError(
            "Petri marking delta predecessor multiset is stale or repeated")
    seen: set[VersionRef] = set()
    consumed: list[VersionRef] = []
    deposited: list[VersionRef] = []
    for edit in delta.edits:
        if not isinstance(edit, PetriTokenEdit) or edit.token_ref in seen:
            raise MarkingStateError(
                "Petri marking delta repeats or mistypes one token edit")
        seen.add(edit.token_ref)
        if edit.action == "consume":
            if current.get(edit.token_ref) != edit.before:
                raise MarkingStateError(
                    "Petri consume differs from its exact predecessor occurrence")
            del current[edit.token_ref]
            consumed.append(edit.token_ref)
        else:
            if (edit.token_ref in current
                    or not isinstance(edit.after, PetriTokenState)):
                raise MarkingStateError(
                    "Petri deposit does not create one fresh typed occurrence")
            current[edit.token_ref] = edit.after
            deposited.append(edit.token_ref)
    ordered = lambda refs: tuple(sorted(refs, key=_version_ref_key))
    if (ordered(consumed) != delta.consumed_token_refs
            or ordered(deposited) != delta.deposited_token_refs
            or ordered(set(predecessor.token_refs) - set(consumed))
            != delta.retained_token_refs
            or ordered(current) != delta.successor_token_refs):
        raise MarkingStateError(
            "Petri token edits do not reconstruct the declared successor multiset")
    return tuple(current[ref] for ref in ordered(current))


def verify_petri_marking_delta(
        predecessor, successor, new_tokens,
        delta: PetriMarkingDelta):
    """Require the canonical token-edit bundle and exact successor states."""

    expected = derive_petri_marking_delta(
        predecessor, successor, new_tokens)
    if delta != expected:
        raise MarkingStateError(
            "Petri marking delta is not the canonical predecessor/successor change")
    applied = apply_petri_marking_delta(predecessor, delta)
    target = tuple(
        item.state for item in sorted(
            successor.tokens,
            key=lambda item: _version_ref_key(item.token_ref)))
    if applied != target:
        raise MarkingStateError(
            "Petri token edits do not reconstruct exact successor states")
    return applied


__all__ = (
    "PetriMarkingDelta",
    "PetriTokenEdit",
    "apply_petri_marking_delta",
    "derive_petri_marking_delta",
    "verify_petri_marking_delta",
)
