"""Reuse the public complete ten-operation electricity billing declaration."""
from cpn.components.basic import CONFIG_SCHEMA_ID
from cpn.rpnh.agent_tasks import TEXT_SCHEMA
from cpn.rpnh.module import ModuleDeclaration
from examples.tool_pipeline.run import module as billing_module
from .business import DEFAULT_LIMIT
from .host import PREFIX, CONFIG, PROPOSAL_CONFIG, TERMINAL


def base_module():
    document = billing_module().to_dict()
    document["name"] = "SharedSyntheticBilling"
    document["required_schemas"].append(CONFIG)
    for kind in ("usage", "tariff"):
        component = next(c for c in document["components"] if c["name"] == "check_" + kind + "_input")
        operation, = component["operations"]
        operation.update(executor=PREFIX + "check_" + kind + "/v1", tools=[PREFIX + "bounded_input/v1"],
                         config={"max_integer": DEFAULT_LIMIT})
    return ModuleDeclaration.from_dict(document)


def proposal_module(kind):
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1",
        "name": "IndependentBillingProposal", "components": [{"name": "proposal", "key": "operation",
            "config_schema": CONFIG_SCHEMA_ID, "config": {},
            "ports": [{"name": "base", "direction": "input", "schema": TEXT_SCHEMA},
                      {"name": "result", "direction": "output", "schema": TEXT_SCHEMA}],
            "operations": [{"name": "derive", "executor": PREFIX + "propose/v1", "inputs": ["base"],
                "outputs": ["result"], "tools": [], "config": {"kind": kind},
                "outcomes": [{"name": "complete", "products": [{"port": "result"}]}],
                "budget_binding": {"bucket_id": "proposal", "budget_scope": "module", "finalization_scope": None}}]}],
        "links": [], "entry": {"base": {"component": "proposal", "port": "base"}},
        "exit": {"result": {"component": "proposal", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "proposal", "port": "result"},
                     "operation": "derive", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": [CONFIG_SCHEMA_ID, TEXT_SCHEMA, PROPOSAL_CONFIG], "budgets": {},
        "budget_buckets": [{"bucket_id": "proposal", "budget_scope": "module", "finalization_scope": None, "max_attempts": 1}]})
