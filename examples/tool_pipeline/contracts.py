"""Versioned application schemas and the frozen, synthetic billing policy."""
from __future__ import annotations

PREFIX = "application/tool_pipeline_"
RULES = {
    "id": "synthetic-electricity/v1",
    "usage_unit": "Wh", "normalized_usage_unit": "kWh", "wh_per_kwh": 1000,
    "tariff_unit": "fen/kWh", "normalized_tariff_unit": "CNY/kWh",
    "currency": "CNY", "fen_per_cny": 100,
    "rounding": "ROUND_HALF_UP", "round_each_interval": True,
    "amount_places": 2, "usage_places": 3, "rate_places": 2,
    "decimal_precision": 50, "max_rows": 1000, "max_input_integer": 1000000000,
}


def object_schema(properties, required=None):
    return {"type": "object", "additionalProperties": False,
            "properties": properties,
            "required": list(properties if required is None else required)}


TEXT = {"type": "string", "minLength": 1}
INTEGER_TEXT = {"type": "string", "pattern": r"^(0|[1-9][0-9]{0,9})$"}
REF = object_schema({"resource_id": TEXT, "resource_version_id": TEXT})
INTERVAL = {"interval_id": TEXT, "start": TEXT, "end": TEXT}
ROWS = {"type": "array", "minItems": 1, "maxItems": RULES["max_rows"]}


def source_document(kind):
    value, unit = ("wh", "Wh") if kind == "usage" else ("fen_per_kwh", "fen/kWh")
    fields = {"schema_version": {"const": f"tool_pipeline/{kind}/v1"},
              "timezone": {"const": "UTC"}, "unit": {"const": unit},
              "intervals": {**ROWS, "items": object_schema({**INTERVAL, value: INTEGER_TEXT})}}
    if kind == "tariff":
        fields["currency"] = {"const": "CNY"}
    return object_schema(fields)


def normalized_document(kind):
    field, places, unit = (("kwh", 3, "kWh") if kind == "usage"
                           else ("cny_per_kwh", 2, "CNY/kWh"))
    return object_schema({"unit": {"const": unit}, "intervals": {**ROWS,
        "items": object_schema({**INTERVAL, field: decimal_text(places)})}})


def decimal_text(places):
    return {"type": "string", "pattern": rf"^(0|[1-9][0-9]*)\.[0-9]{{{places}}}$"}


JOINED = object_schema({"intervals": {**ROWS, "items": object_schema({**INTERVAL,
    "kwh": decimal_text(3), "cny_per_kwh": decimal_text(2)})}})
REPORT = object_schema({"currency": {"const": "CNY"}, "usage_unit": {"const": "kWh"},
    "rounding": {"const": "ROUND_HALF_UP"}, "round_each_interval": {"const": True},
    "intervals": {**ROWS, "items": object_schema({**INTERVAL,
        "kwh": decimal_text(3), "cny_per_kwh": decimal_text(2), "amount_cny": decimal_text(2)})},
    "total_kwh": decimal_text(3), "total_cny": decimal_text(2)})


def schema_id(name):
    return PREFIX + name + "/v1"


def envelope(stage, data):
    return object_schema({"stage": {"const": stage}, "rules_id": {"const": RULES["id"]},
        "source_refs": {"type": "object", "additionalProperties": False,
                        "properties": {"usage": REF, "tariff": REF}},
        "parents": {"type": "array", "minItems": 1, "items": REF}, "data": data})


SCHEMAS = {
    "source_usage": {"type": "object"}, "source_tariff": {"type": "object"},
    "usage_raw": envelope("usage_raw", {"type": "object"}),
    "tariff_raw": envelope("tariff_raw", {"type": "object"}),
    "usage_validation_source": envelope("usage_validation_source", {"type": "object"}),
    "tariff_validation_source": envelope("tariff_validation_source", {"type": "object"}),
    "usage_checked": envelope("usage_checked", source_document("usage")),
    "tariff_checked": envelope("tariff_checked", source_document("tariff")),
    "usage_normalized": envelope("usage_normalized", normalized_document("usage")),
    "tariff_normalized": envelope("tariff_normalized", normalized_document("tariff")),
    "joined": envelope("joined", JOINED),
    "candidate": envelope("candidate", REPORT),
    "validated": envelope("validated", object_schema({"report": REPORT,
        "candidate_ref": REF, "oracle": {"const": "integer-fen-half-up/v1"}})),
    "final": envelope("final", object_schema({"report": REPORT,
        "candidate_ref": REF, "validation_ref": REF,
        "oracle": {"const": "integer-fen-half-up/v1"}})),
    "rejection": envelope("rejection", object_schema({"step": TEXT, "reason": TEXT})),
}
SCHEMAS = {schema_id(name): {"$id": schema_id(name),
    "$schema": "http://json-schema.org/draft-07/schema#", **body}
    for name, body in SCHEMAS.items()}
