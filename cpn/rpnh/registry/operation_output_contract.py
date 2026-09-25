"""Mechanical selected-outcome bundle checks for registered compiled inventory."""
from __future__ import annotations

from typing import Mapping

from ..petri_contracts import OperationDeclaration


def validate_compiled_output_bundle(
        operation: OperationDeclaration, counts: Mapping[str, int], *,
        declared_outcomes: tuple[object, ...],
        selected_outcome_id: str | None = None,
) -> str:
    """Names are qualified symbolic ports, not body text or static route colours.

    Nonempty bundles without a colour may infer the sole declared outcome.
    Empty bundles always require an explicit request, even with one outcome.
    """
    outcomes = {item.name: item for item in operation.outcomes}
    if selected_outcome_id is not None and (
            not isinstance(selected_outcome_id, str) or not selected_outcome_id):
        raise ValueError("selected output outcome is malformed")
    if any(type(count) is not int or count < 0 for count in counts.values()):
        raise ValueError("output quantity is not an exact nonnegative integer")
    if set(counts) - set(operation.outputs):
        raise ValueError("output port is outside registered operation")
    colours = set()
    for colour in declared_outcomes:
        if colour is not None:
            if not isinstance(colour, str) or not colour:
                raise ValueError("output outcome descriptor is malformed")
            colours.add(colour)
    if selected_outcome_id is not None:
        colours.add(selected_outcome_id)
    if len(colours) > 1:
        raise ValueError("output bundle has contradictory outcome colours")
    if not any(counts.values()) and selected_outcome_id is None:
        raise ValueError("empty output bundle requires explicit selected outcome")
    selected = next(iter(colours), None)
    if selected is None:
        if len(outcomes) != 1:
            raise ValueError("output bundle lacks selected outcome")
        selected = next(iter(outcomes))
    if selected not in outcomes:
        raise ValueError("output outcome is outside registered declaration")
    products = {item.port: item for item in outcomes[selected].products}
    if any(count and port not in products for port, count in counts.items()):
        raise ValueError("product is outside selected outcome bundle")
    for name, product in products.items():
        if not product.minimum <= counts.get(name, 0) <= product.maximum:
            raise ValueError("selected outcome product cardinality mismatch")
    return selected
