"""Mechanical H7 origin composition in the ordinary Module compiler.

This declaration adds a required PN resource, never permission or a token.
Only the existing protected Registry producer may supply that exact resource.
Ordinary Modules without this opt-in keep their original composition.
"""
from __future__ import annotations

from dataclasses import replace

from .module import ModuleDeclaration
from .petri_contracts import (
    ArcDeclaration, DeclarationError, LeaseIdentityDeclaration,
    PlaceDeclaration, ResourceLeasePoolBinding,
)


ORIGIN_CONSTRAINT = "rpnh_parent_bound_origin_v1"
ORIGIN_SYMBOL = "rpnhOrigin"
ORIGIN_SCHEMA = "rpnh/parent_origin_capability/v1"


def has_bound_origin(source):
    # Preserve the ordinary Module JSON normalization boundary (e.g. numeric
    # legacy Python constraint keys), rather than inspecting raw Python keys.
    constraints = source.to_dict()["designer_constraints"]
    keys = [key for key in constraints if key.startswith("rpnh_parent_bound_origin_")]
    if not keys:
        return False
    if (keys != [ORIGIN_CONSTRAINT]
            or constraints[ORIGIN_CONSTRAINT] != {"resource_input": ORIGIN_SYMBOL}):
        raise DeclarationError("unsupported protected-origin lowering contract")
    if ORIGIN_SCHEMA not in source.required_schemas:
        raise DeclarationError("protected-origin lowering requires its registered schema")
    if any(key.startswith("rpnh_control_ir_") for key in constraints):
        raise DeclarationError("bound ControlIR lowering needs a separate proof contract")
    return True


def with_bound_origin(module):
    """Return a detached Module requesting one compiler-owned origin input.

    No Registration changes, files, factories, Registry writes or code imports
    from declaration strings. An existing compatible request is idempotent.
    """
    if type(module) is not ModuleDeclaration:
        raise TypeError("bound lowering requires an ordinary ModuleDeclaration")
    document = module.to_dict()
    constraints = document["designer_constraints"]
    if any(key.startswith("rpnh_parent_bound_origin_") for key in constraints):
        has_bound_origin(module)
    else:
        constraints[ORIGIN_CONSTRAINT] = {"resource_input": ORIGIN_SYMBOL}
    document["required_schemas"] = sorted(set(document["required_schemas"]) | {ORIGIN_SCHEMA})
    result = ModuleDeclaration.from_dict(document)
    has_bound_origin(result)
    return result


def compose_bound_origin(source, symbolic, aliases):
    """Apply the same fixed structural transform in compiler and wire reader."""
    if not has_bound_origin(source):
        return symbolic, aliases
    if any(op.executor == "rpnh/parent_child_launch/v1" for op in symbolic.operations):
        raise DeclarationError("H7a child does not support recursive native launch")
    if (not symbolic.transitions
            or any(p.schema == ORIGIN_SCHEMA for p in symbolic.places)
            or any(item.name == ORIGIN_SYMBOL for items in (
                symbolic.places, symbolic.transitions, symbolic.operations,
                symbolic.lease_identities, symbolic.lease_pools, symbolic.logical_slots)
                for item in items)
            or ORIGIN_SYMBOL in aliases or ORIGIN_SYMBOL in symbolic.port_places):
        raise DeclarationError("protected-origin mechanical namespace is already occupied")
    origin = PlaceDeclaration(ORIGIN_SYMBOL, ORIGIN_SCHEMA,
        token_kind="resource_lease", capacity=1, reusable=True)
    return replace(symbolic,
        places=(*symbolic.places, origin),
        arcs=(*symbolic.arcs, *(ArcDeclaration(ORIGIN_SYMBOL, t.name,
            "input", mode="read") for t in symbolic.transitions)),
        lease_identities=(*symbolic.lease_identities, LeaseIdentityDeclaration(ORIGIN_SYMBOL)),
        lease_pools=(*symbolic.lease_pools, ResourceLeasePoolBinding(
            ORIGIN_SYMBOL, ORIGIN_SYMBOL, (ORIGIN_SYMBOL,)))), {**aliases, ORIGIN_SYMBOL: ORIGIN_SYMBOL}


__all__ = ("with_bound_origin", "ORIGIN_SYMBOL", "ORIGIN_SCHEMA")
