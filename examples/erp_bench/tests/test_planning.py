import copy
import math

import pytest

from rpnh_erp_bench.planning import validate_plan
from cpn.plugins.api import PluginError


def plan():
    return {"orders": [{"order_ref": "visible-order", "quantity": 4, "list_price": 10,
                        "budget": 40, "due_days": 5}],
            "routes": [{"route_ref": "visible-offer", "kind": "buy", "capacity": 4,
                        "minimum_quantity": 2, "unit_cost": 6, "lead_days": 3}],
            "allocations": [{"order_ref": "visible-order", "route_ref": "visible-offer", "quantity": 4}],
            "minimum_margin": 0.4}


def test_exact_margin_boundary_and_no_grader_or_reuse_claim():
    result = validate_plan(plan())
    assert result["status"] == "completed" and result["new_spend_margin"] == "0.4"
    assert result["new_spend"] == "24" and result["new_revenue"] == "40"


@pytest.mark.parametrize("mutation,constraint", [
    (lambda p: p["orders"][0].update(budget=39), "customer_budget"),
    (lambda p: p["routes"][0].update(capacity=3), "horizon_capacity"),
    (lambda p: p["routes"][0].update(minimum_quantity=5), "minimum_quantity"),
    (lambda p: p["routes"][0].update(lead_days=6), "delivery"),
    (lambda p: p.update(minimum_margin=0.40001), "portfolio_new_spend_margin"),
    (lambda p: p["allocations"][0].update(quantity=3), "coverage"),
])
def test_domain_infeasibility_is_a_returned_calculation(mutation, constraint):
    value = plan(); mutation(value)
    result = validate_plan(value)
    assert result["status"] == "domain_infeasible"
    assert constraint in [row["constraint"] for row in result["violations"]]


def test_shared_capacity_aggregates_across_orders():
    value = plan()
    value["orders"].append(dict(value["orders"][0], order_ref="second"))
    value["allocations"].append(dict(value["allocations"][0], order_ref="second"))
    assert any(row["constraint"] == "horizon_capacity" for row in validate_plan(value)["violations"])


def test_finished_stock_does_not_invent_new_spend_margin():
    value = plan(); value["routes"][0]["kind"] = "stock"
    result = validate_plan(value)
    assert result["new_spend_margin"] is None and result["new_spend"] == "0"


@pytest.mark.parametrize("number", [True, math.inf, -math.inf, math.nan])
def test_nonfinite_or_boolean_observations_rejected(number):
    value = plan(); value["routes"][0]["unit_cost"] = number
    with pytest.raises((ValueError, PluginError)):
        validate_plan(value)


def test_foreign_or_duplicate_identities_are_not_accepted():
    value = plan(); value["allocations"][0]["order_ref"] = "foreign"
    with pytest.raises(ValueError, match="reference"):
        validate_plan(value)
    value = plan(); value["routes"].append(copy.deepcopy(value["routes"][0]))
    with pytest.raises(ValueError, match="unique"):
        validate_plan(value)
