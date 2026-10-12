"""Typed fixture evidence and one native quote operation."""
from cpn.rpnh.module import ModuleDeclaration

MODEL = "a2a-quote-offline-zero-model"
GATE = "example/a2a_quote/gate/v1"
EXECUTOR = "example/a2a_quote/quote/v1"
TERMINAL = "example/a2a_quote/terminal/v1"
STAGE = "stage1_synthetic_association_only"
STAGE2 = "BLOCKED_NOT_RUN"
SOURCE_COMMIT = "6603ba3f2c31a7ef33e70b9d8b5b5f8be42ac9a3"
FIXTURES = {"trusted25": ("fixture_eligible", 25),
            "untrusted50": ("fixture_untrusted", 0),
            "expired": ("fixture_expired", 0), "deny": ("denied", 0)}


def schema_id(name):
    return "application/a2a_quote_" + name + "/v1"


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 300}
REF = obj({"entity_type": TEXT, "logical_id": TEXT, "version_id": TEXT})
REQUEST = obj({"case_id": {"const": "quote-001"}, "hotel": {"const": "Synthetic Harbor Hotel"},
    "base_amount_minor": {"const": 20000}, "currency": {"const": "USD"},
    "assistant_task_id": TEXT, "assistant_context_id": TEXT,
    "merchant_task_id": TEXT, "merchant_context_id": TEXT,
    "proof_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
    "evidence_level": {"const": STAGE}})
ASSOCIATION = obj({"run_ref": REF, "task_ref": REF, "net_ref": REF,
    "wait_ref": REF, "request_resource_ref": REF, "transition_id": {"const": "quote.run"},
    "generation": {"type": "integer", "minimum": 1},
    "assistant_task_id": TEXT, "assistant_context_id": TEXT,
    "merchant_task_id": TEXT, "merchant_context_id": TEXT,
    "verifier_identity_ref": REF, "verifier_host_session_ref": REF,
    "verification_session_ref": TEXT, "authorization_request_ref": TEXT,
    "challenge_ref": TEXT, "expires_at_utc": TEXT})
EVIDENCE = obj({"association": ASSOCIATION, "evidence_level": {"const": STAGE},
    "fixture": {"enum": [*FIXTURES, "timeout"]},
    "verdict": {"enum": [v[0] for v in FIXTURES.values()] + ["expired"]},
    "discount_percent": {"enum": [0, 25]}})
RESULT = obj({"status": {"const": "completed"}, "evidence_level": {"const": STAGE},
    "stage2": {"const": STAGE2}, "association": ASSOCIATION,
    "evidence_ref": REF, "verdict": EVIDENCE["properties"]["verdict"],
    "discount_percent": {"enum": [0, 25]}, "base_amount_minor": {"const": 20000},
    "amount_minor": {"enum": [15000, 20000]}, "currency": {"const": "USD"},
    "hotel": {"const": "Synthetic Harbor Hotel"}, "booking": {"const": False},
    "payment": {"const": False}})
SCHEMAS = {schema_id(k): {"$id": schema_id(k), "$schema": "http://json-schema.org/draft-07/schema#", **v}
           for k, v in {"request": REQUEST, "evidence": EVIDENCE, "result": RESULT}.items()}


def evidence(association, fixture):
    verdict, discount = ("expired", 0) if fixture == "timeout" else FIXTURES[fixture]
    return {"association": association, "evidence_level": STAGE, "fixture": fixture,
            "verdict": verdict, "discount_percent": discount}


def module():
    budget = {"bucket_id": "quote", "budget_scope": "module", "finalization_scope": None}
    return ModuleDeclaration.from_dict({"schema_version": "rpnh/module_declaration/v1",
        "name": "SyntheticA2AHotelQuote", "components": [{"name": "quote", "key": GATE,
            "config_schema": "application/operation_component_config/v1", "config": {},
            "ports": [{"name": "request", "direction": "input", "schema": schema_id("request")},
                      {"name": "result", "direction": "output", "schema": schema_id("result")}],
            "operations": [{"name": "run", "executor": EXECUTOR, "inputs": ["request", "evidence"],
                "outputs": ["result"], "tools": [], "request_port": None, "config": {},
                "budget_binding": budget, "outcomes": [{"name": "complete", "products": [{"port": "result"}]}]}]}],
        "links": [], "entry": {"request": {"component": "quote", "port": "request"}},
        "exit": {"result": {"component": "quote", "port": "result"}},
        "terminal": {"key": TERMINAL, "source": {"component": "quote", "port": "result"},
            "operation": "run", "outcome": "complete", "config": {"run_outcome": "complete"}},
        "required_schemas": ["application/operation_component_config/v1", *SCHEMAS],
        "budgets": {}, "budget_buckets": [{**budget, "max_attempts": 1}]})
