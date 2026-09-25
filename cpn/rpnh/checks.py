"""Core exact checkpoint-local Petri state equation; no global liveness proof."""
from __future__ import annotations
from typing import Any,Callable,Collection,Mapping

class CheckpointFiringStepError(ValueError):
    """One selected checkpoint-local Petri step is mechanically impossible."""


def analyze_checkpoint_firing_step(
    net: Any,
    *,
    claim_epoch: int,
    tokens: Collection[Any],
    selected_firing_ids: tuple[str, ...],
    claimed_token_refs: Mapping[object, tuple[Any, ...]],
    expected_consumed_place_counts: Mapping[object, Mapping[str, int]],
    enabledness: Callable[[str], bool],
    selected_occurrence_keys: tuple[object, ...] | None = None,
) -> Mapping[str, int]:
    """Purely validate one already-selected checkpoint-local firing step.

    ``selected_firing_ids`` is the actual randomized allocation order and may
    repeat a transition id for distinct exact binding occurrences.
    ``expected_consumed_place_counts`` is the marking's explicit projection of
    the same claim contract that selected the exact refs; this analyzer does not
    reconstruct consume semantics from the net's static arc views.
    ``enabledness`` is the current marking's prevalidated enabledness predicate:
    its caller incorporates timed evidence before this function is called,
    keeping this module independent of marking and timing implementation
    details.  Token and resource payloads are deliberately opaque.

    This performs no mutation and does not predict payloads, global liveness,
    or deadlock.  Its return value is the structural successor-place count
    projection for this selected step if its firings later publish successfully:
    declared ordinary outputs, plus the marking's declared read and
    reusable-resource consume-return inscriptions.  The checkpoint-local Petri
    invariant is that this effective state equation stays within the declared
    place set, never goes negative, and preserves every reusable capacity/lease
    place.  It raises only for a mechanically invalid selected claim or a breach
    of that local invariant in the supplied checkpoint token view.
    """
    token_rows = tuple(tokens)
    occurrence_keys = (
        selected_firing_ids
        if selected_occurrence_keys is None else selected_occurrence_keys)
    if (len(occurrence_keys) != len(selected_firing_ids)
            or len(set(occurrence_keys)) != len(occurrence_keys)
            or set(claimed_token_refs) != set(occurrence_keys)
            or set(expected_consumed_place_counts) != set(occurrence_keys)):
        raise CheckpointFiringStepError(
            "selected step lacks one exact consume projection")
    selected_refs: list[Any] = []
    claimed_by_occurrence: dict[object, tuple[Any, ...]] = {}
    for occurrence_key, firing_id in zip(
            occurrence_keys, selected_firing_ids, strict=True):
        if not enabledness(firing_id):
            raise CheckpointFiringStepError(
                f"selected transition {firing_id!r} is no longer enabled")
        refs = claimed_token_refs.get(occurrence_key)
        if refs is None:
            raise CheckpointFiringStepError(
                f"selected transition {firing_id!r} lacks exact token claims")
        if (occurrence_key in claimed_by_occurrence
                or len(set(refs)) != len(refs)):
            raise CheckpointFiringStepError(
                f"selected transition {firing_id!r} repeats a claim token")
        claimed_by_occurrence[occurrence_key] = refs
        selected_refs.extend(refs)
    if len(set(selected_refs)) != len(selected_refs):
        raise CheckpointFiringStepError("selected firings overlap on one consume token")

    token_by_ref = {getattr(token, "token_ref", None): token for token in token_rows}
    if len(token_by_ref) != len(token_rows) or None in token_by_ref:
        raise CheckpointFiringStepError("checkpoint tokens lack unique exact references")

    structural_places = set(net.places) | set(net.registered_fault_places)
    current_counts: dict[str, int] = {}
    for token in token_rows:
        if (getattr(token, "epoch", None) == claim_epoch
                and getattr(token, "consumed_by", None) is None
                and not getattr(token, "faulted", False)):
            place = getattr(token, "place", None)
            if place not in structural_places:
                raise CheckpointFiringStepError(
                    "checkpoint marking contains a token outside the declared places")
            current_counts[place] = current_counts.get(place, 0) + 1

    consumed_counts: dict[str, int] = {}
    returned_counts: dict[str, int] = {}
    ordinary_output_counts: dict[str, int] = {}
    for occurrence_key, firing_id in zip(
            occurrence_keys, selected_firing_ids, strict=True):
        claimed: list[Any] = []
        for token_ref in claimed_by_occurrence[occurrence_key]:
            token = token_by_ref.get(token_ref)
            if (token is None
                    or getattr(token, "epoch", None) != claim_epoch
                    or getattr(token, "consumed_by", None) is not None
                    or getattr(token, "faulted", False)
                    or getattr(token, "consumer", None) not in {None, firing_id}):
                raise CheckpointFiringStepError(
                    f"selected transition {firing_id!r} has a missing or nonfresh token")
            claimed.append(token)

        expected_by_place = dict(
            expected_consumed_place_counts[occurrence_key])
        if any(
                not isinstance(place, str)
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count <= 0
                for place, count in expected_by_place.items()):
            raise CheckpointFiringStepError(
                f"selected transition {firing_id!r} has an invalid consume projection")

        actual_by_place: dict[str, int] = {}
        for token in claimed:
            place = getattr(token, "place", None)
            actual_by_place[place] = actual_by_place.get(place, 0) + 1
            consumed_counts[place] = consumed_counts.get(place, 0) + 1
        if actual_by_place != expected_by_place:
            raise CheckpointFiringStepError(
                f"selected transition {firing_id!r} differs from exact consume projection")

        declared_agent_returns = {
            place for place in net.agent_resource_places()
            if net.output_arc_weight(firing_id, place) > 0
        }
        for place in (
                net.agent_resource_places().intersection(actual_by_place)
                | declared_agent_returns):
            returned = net.output_arc_weight(firing_id, place)
            consumed = actual_by_place.get(place, 0)
            if (consumed == 0
                    or net.output_emit_for(firing_id, place) != "content_less"
                    or returned != consumed):
                raise CheckpointFiringStepError(
                    f"agent resource {place!r} has no exact content-less return")
            returned_counts[place] = returned_counts.get(place, 0) + returned

        variable_resource = net.variable_resource_arc_for(firing_id)
        lease_places = net.resource_lease_places().intersection(actual_by_place)
        declared_lease_outputs = {
            place for place in net.resource_lease_places()
            if net.output_arc_weight(firing_id, place) > 0
        }
        invalid_lease_outputs = {
            place for place in declared_lease_outputs
            if net.output_emit_for(firing_id, place) != "lease_mint"
        }
        if invalid_lease_outputs:
            raise CheckpointFiringStepError(
                "resource lease output requires lease_mint or the exact variable return inscription")
        if lease_places:
            if (variable_resource is None
                    or lease_places != {variable_resource.lease_pool_place}):
                raise CheckpointFiringStepError(
                    "resource lease has no exact variable consume-return inscription")
            lease_place = variable_resource.lease_pool_place
            returned_counts[lease_place] = (
                returned_counts.get(lease_place, 0)
                + actual_by_place[lease_place])

        read_return_places = {
            place for place, scope in net.read_scope_of(firing_id)
            if scope == "read" and place not in net.reusable_resource_places()
        }
        for place in read_return_places.intersection(actual_by_place):
            returned_counts[place] = (
                returned_counts.get(place, 0) + actual_by_place[place])

        decision_colors = {
            getattr(token, "verdict", None) for token in claimed
            if getattr(token, "verdict", None) is not None
        }
        decision_color = (
            next(iter(decision_colors)) if len(decision_colors) == 1 else None)
        for transition_id, place, weight in net.output_arcs:
            if transition_id != firing_id:
                continue
            # Resource output arcs declare the agent-resource return, which was
            # counted above from the exact consumed multiset.  Lease returns are
            # variable inscriptions and deliberately have no ordinary arc.
            if place in net.reusable_resource_places():
                continue
            expected_color = net.output_guard_for(firing_id, place)
            if expected_color is not None:
                matches = decision_color is not None and (
                    decision_color == expected_color
                    if isinstance(expected_color, str)
                    else decision_color is expected_color)
                if not matches:
                    continue
            ordinary_output_counts[place] = (
                ordinary_output_counts.get(place, 0) + weight)
        for place in net.registered_fault_outputs_of(firing_id):
            ordinary_output_counts[place] = (
                ordinary_output_counts.get(place, 0) + 1)

    projected = dict(current_counts)
    for place, count in consumed_counts.items():
        projected[place] = projected.get(place, 0) - count
    for place, count in returned_counts.items():
        projected[place] = projected.get(place, 0) + count
    for place, count in ordinary_output_counts.items():
        projected[place] = projected.get(place, 0) + count
    if any(count < 0 for count in projected.values()):
        raise CheckpointFiringStepError("selected step projects a negative marking")
    if set(projected) - structural_places:
        raise CheckpointFiringStepError(
            "selected step projects a token outside the declared places")
    for place in net.reusable_resource_places():
        if projected.get(place, 0) != current_counts.get(place, 0):
            raise CheckpointFiringStepError(
                f"selected step violates the reusable-resource invariant at {place!r}")
    return {place: count for place, count in projected.items() if count}
