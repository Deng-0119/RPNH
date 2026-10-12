"""Small example-local port contracts; no new Registry authority or token types."""
PREFIX = "application/query_validation_"
RULES = {"id": "read-only-query-observation/v1", "original_verifier": "not_run"}


def schema_id(name):
    return PREFIX + name + "/v1"


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}


TEXT = {"type": "string", "minLength": 1}
SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
REF = obj({"resource_id": TEXT, "resource_version_id": TEXT})
DATABASE = obj({"path": TEXT, "sha256": SHA,
    "condition": {"enum": ["synthetic_wiring_smoke", "tb2_pinned_database"]},
    "query_timeout_s": {"type": "number", "exclusiveMinimum": 0, "maximum": 1800}})
SQL = obj({"sql": TEXT, "sha256": SHA})


def envelope(stage):
    return obj({"stage": {"const": stage}, "rules_id": {"const": RULES["id"]},
        "parents": {"type": "array", "minItems": 1, "items": REF},
        "data": {"type": "object"}})


SCHEMAS = {"database": DATABASE, "original_sql": SQL, "candidate_sql": SQL,
           **{name: envelope(name) for name in ("request", "original_result", "candidate_result", "report", "final_sql")}}
SCHEMAS = {schema_id(name): {"$id": schema_id(name),
    "$schema": "http://json-schema.org/draft-07/schema#", **value}
    for name, value in SCHEMAS.items()}

STEPS = {
    "inspect_inputs": ({"database": "database", "original_sql": "original_sql", "candidate_sql": "candidate_sql"}, {"request": "request"}),
    "execute_queries": ({"request": "request"}, {"original_result": "original_result", "candidate_result": "candidate_result"}),
    "compare_results": ({"original_result": "original_result", "candidate_result": "candidate_result"}, {"report": "report", "final_sql": "final_sql"}),
}
