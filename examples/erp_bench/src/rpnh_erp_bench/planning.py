"""Deterministic feasibility arithmetic over solver-supplied visible facts.

This does not solve ERP-Bench, query its grader, or certify that observations
are current. Route/offer capacities and internal notes remain Odoo evidence.
"""
from decimal import Decimal


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


_REF = {"type": "string", "minLength": 1, "maxLength": 256}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0}
_NONNEGATIVE = {"type": "number", "minimum": 0}
PLAN_INPUT_SCHEMA = _object({
    "orders": {"type": "array", "minItems": 1, "maxItems": 1000, "items": _object({
        "order_ref": _REF, "quantity": _POSITIVE, "list_price": _POSITIVE,
        "budget": _NONNEGATIVE, "due_days": _NONNEGATIVE})},
    "routes": {"type": "array", "minItems": 1, "maxItems": 1000, "items": _object({
        "route_ref": _REF, "kind": {"enum": ["stock", "buy", "manufacture"]},
        "capacity": _NONNEGATIVE, "minimum_quantity": _NONNEGATIVE,
        "unit_cost": _NONNEGATIVE, "lead_days": _NONNEGATIVE})},
    "allocations": {"type": "array", "maxItems": 10000, "items": _object({
        "order_ref": _REF, "route_ref": _REF, "quantity": _POSITIVE})},
    "minimum_margin": {"type": "number", "minimum": 0, "maximum": 1},
})


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("plan quantities and amounts must be finite numbers")
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("plan quantities and amounts must be finite numbers")
    return value


def validate_plan(document):
    from cpn.plugins.api import validate
    validate(PLAN_INPUT_SCHEMA, document)
    orders = {row["order_ref"]: row for row in document["orders"]}
    routes = {row["route_ref"]: row for row in document["routes"]}
    if len(orders) != len(document["orders"]) or len(routes) != len(document["routes"]):
        raise ValueError("order and route identities must be unique")
    # Check every numeric observation, including unused routes/orders.
    for rows in (orders.values(), routes.values()):
        for row in rows:
            for key, value in row.items():
                if key not in {"order_ref", "route_ref", "kind"}:
                    _number(value)
    quantities = dict.fromkeys(orders, Decimal(0))
    used = dict.fromkeys(routes, Decimal(0))
    spend = revenue = Decimal(0)
    failures = []
    for allocation in document["allocations"]:
        order_ref, route_ref = allocation["order_ref"], allocation["route_ref"]
        if order_ref not in orders or route_ref not in routes:
            raise ValueError("allocation must reference supplied order and route identities")
        order, route = orders[order_ref], routes[route_ref]
        quantity = _number(allocation["quantity"])
        quantities[order_ref] += quantity
        used[route_ref] += quantity
        if _number(route["lead_days"]) > _number(order["due_days"]):
            failures.append({"constraint": "delivery", "order_ref": order_ref, "route_ref": route_ref})
        if route["kind"] != "stock":
            spend += quantity * _number(route["unit_cost"])
            revenue += quantity * _number(order["list_price"])
    for order_ref, row in orders.items():
        if quantities[order_ref] != _number(row["quantity"]):
            failures.append({"constraint": "coverage", "order_ref": order_ref})
        if _number(row["quantity"]) * _number(row["list_price"]) > _number(row["budget"]):
            failures.append({"constraint": "customer_budget", "order_ref": order_ref})
    for route_ref, row in routes.items():
        if used[route_ref] > _number(row["capacity"]):
            failures.append({"constraint": "horizon_capacity", "route_ref": route_ref})
        if used[route_ref] and used[route_ref] < _number(row["minimum_quantity"]):
            failures.append({"constraint": "minimum_quantity", "route_ref": route_ref})
    minimum = _number(document["minimum_margin"])
    if revenue and revenue - spend < minimum * revenue:
        failures.append({"constraint": "portfolio_new_spend_margin"})
    return {"status": "domain_infeasible" if failures else "completed",
            "new_spend": str(spend), "new_revenue": str(revenue),
            "new_spend_margin": str((revenue - spend) / revenue) if revenue else None,
            "covered_quantities": {key: str(value) for key, value in quantities.items()},
            "route_quantities": {key: str(value) for key, value in used.items()},
            "violations": failures,
            "scope": "Supplied-observation arithmetic only; not official scoring, current-world validation, or an optimality claim."}
