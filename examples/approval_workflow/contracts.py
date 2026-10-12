"""Refund data and declared PN; these fields never grant client authority."""
from cpn.rpnh.module import ModuleDeclaration

PREFIX = "application/approval_workflow_"
TERMINAL = "example/approval_workflow/terminal/v1"
MODEL = "approval-workflow-offline-zero-model"
REQUEST_WAIT = "approval:request-wait"
DECISION_WAIT = "approval:decision-wait"
POLICY = {"id": "synthetic-refund-policy-v1", "currency": "USD", "max_amount_minor": 10000,
          "effect": "registered_mock_output_only",
          "source_commit": "4ca41b2135991ac744c3729bbb7d42bf731a8185",
          "source": "Mastra customer-refund interaction contract"}
ORDER = {"id": "synthetic-order-001", "paid_minor": 10000, "currency": "USD", "refundable": True}


def schema_id(name):
    return PREFIX + name + "/v1"


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
REF = obj({"entity_type": TEXT, "logical_id": TEXT, "version_id": TEXT})
TARGET = obj({"task_ref": REF, "run_ref": REF, "net_ref": REF,
              "transition_id": {"const": "refund.run"}})
BUSINESS = {"case_id": TEXT, "amount_minor": {"type": "integer", "minimum": 1, "maximum": 10000},
            "currency": {"const": "USD"}, "reason": {"type": "string", "minLength": 1, "maxLength": 2000}}
CANDIDATE = obj({**BUSINESS, "order": {"const": ORDER["id"]}, "target": TARGET, "policy_ref": REF,
    "source_refs": {"type": "array", "items": REF, "minItems": 2}, "command_ref": REF,
    "requester_session_ref": REF, "wait_ref": REF})
DECISION = obj({"decision": {"enum": ["approve", "deny", "expired"]},
                "candidate_ref": REF, "candidate": CANDIDATE})
SCHEMAS = {
    "policy": obj({"case_id": TEXT, "policy": {"const": POLICY}, "order": {"const": ORDER},
                   "decision_seconds": {"type": "integer", "minimum": 1, "maximum": 86400}}),
    "request": obj({**BUSINESS, "target": TARGET, "requester_session_ref": REF, "wait_ref": REF}),
    "candidate": CANDIDATE,
    "decision": DECISION,
    "result": obj({"status": {"enum": ["completed", "denied", "expired"]},
        "mock_refund": {"type": "boolean"}, "amount_minor": BUSINESS["amount_minor"],
        "currency": {"const": "USD"}, "case_id": TEXT, "order": {"const": ORDER["id"]}, "target": TARGET,
        "candidate_ref": REF, "decision_ref": REF, "wait_ref": REF}),
}
SCHEMAS = {schema_id(k): {"$id": schema_id(k), "$schema": "http://json-schema.org/draft-07/schema#", **v}
           for k, v in SCHEMAS.items()}


def executor_key(stage):
    return "example/approval_workflow/" + stage + "/v1"


def module():
    budget = {"bucket_id": "approval", "budget_scope": "module", "finalization_scope": None}
    components = []
    for name, inputs, outputs in (
        ("candidate", {"policy": "policy", "request": "request"}, {"candidate": "candidate"}),
        ("refund", {"candidate": "candidate", "decision": "decision"}, {"result": "result"}),
    ):
        components.append({"name": name, "key": "example/approval_workflow/gate/v1",
            "config_schema": "application/operation_component_config/v1", "config": {},
            "ports": [{"name": port, "direction": direction, "schema": schema_id(schema)}
                      for direction, items in (("input", inputs), ("output", outputs))
                      for port, schema in items.items() if port not in {"request", "decision"}],
            "operations": [{"name": "run", "executor": executor_key(name), "inputs": list(inputs),
                "outputs": list(outputs), "tools": [], "request_port": None, "config": {},
                "budget_binding": budget, "outcomes": [{"name": "complete", "products": [{"port": p} for p in outputs]}]}]})
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1",
        "name": "ControlledMockRefund", "components": components,
        "links": [{"source": {"component": "candidate", "port": "candidate"},
                   "target": {"component": "refund", "port": "candidate"}}],
        "entry": {"policy": {"component": "candidate", "port": "policy"}},
        "exit": {"result": {"component": "refund", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "refund", "port": "result"},
                     "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": ["application/operation_component_config/v1", *SCHEMAS],
        "budgets": {}, "budget_buckets": [{**budget, "max_attempts": 2}]})
