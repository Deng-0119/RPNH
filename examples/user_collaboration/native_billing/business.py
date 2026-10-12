"""Two bounded input checks over the unchanged public electricity tools.

The limits are per-interval business validation, never execution permissions.
Arithmetic, normalization, independent integer validation and source fixtures are
those in examples.tool_pipeline. No Registry or scheduling code lives here.
"""
from copy import deepcopy

from examples.tool_pipeline import tools
from examples.tool_pipeline.contracts import RULES

DEFAULT_LIMIT = RULES["max_input_integer"]
CONTRIBUTIONS = {
    "usage": {"component": "check_usage_input", "max_integer": 100000},
    "tariff": {"component": "check_tariff_input", "max_integer": 1000},
}


async def bounded_input(*, kind, inputs, rules, max_integer):
    if kind not in CONTRIBUTIONS or type(max_integer) is not int or not 1 <= max_integer <= DEFAULT_LIMIT:
        raise ValueError("invalid declared business bound")
    original = tools.check_usage_input if kind == "usage" else tools.check_tariff_input
    result = await original(inputs=inputs, rules=rules)
    if result.outcome != "complete":
        return result
    field = "wh" if kind == "usage" else "fen_per_kwh"
    if any(int(row[field]) > max_integer for row in inputs["raw"]["value"]["data"]["intervals"]):
        return tools._reject("check_" + kind + "_input",
            "declared per-interval " + field + " limit exceeded", inputs, rules)
    return result


def contribution_document(base, kind):
    """One definition-only branch from its exact admitted accepted base."""
    from cpn.rpnh.module import ModuleDeclaration
    from cpn.rpnh.net_operations import ExtractPlan, branch_module
    selection = CONTRIBUTIONS[kind]
    copied = branch_module(ModuleDeclaration.from_dict(base), ExtractPlan(),
                           output_mode="definition_only").definition.to_dict()
    document = deepcopy(copied)
    found = [c for c in document["components"] if c["name"] == selection["component"]]
    if len(found) != 1 or len(found[0]["operations"]) != 1:
        raise ValueError("contribution requires the exact billing input check")
    operation = found[0]["operations"][0]
    if operation["config"] != {"max_integer": DEFAULT_LIMIT}:
        raise ValueError("contributor must change an unmodified target check")
    operation["config"] = {"max_integer": selection["max_integer"]}
    return document
