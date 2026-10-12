"""Application-only ABI. No new Registry, scheduling, or permission semantics."""
from __future__ import annotations

PREFIX = "application/tool_pipeline_multi_source_"
RULES = {
    "id": "terminal-bench/multi-source-data-merger/v1",
    "priority": ["source_a", "source_b", "source_c"],
    "upstream_commit": "2fd12b88aafdd04a52c298e3940bcb189f9766d6",
    "max_input_bytes": 8 * 1024 * 1024,
    "max_rows": 100000,
}


def schema_id(name):
    return PREFIX + name + "/v1"


def obj(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties if required is None else required)}


TEXT = {"type": "string", "minLength": 1}
REF = obj({"resource_id": TEXT, "resource_version_id": TEXT})
BLOB = obj({"filename": TEXT, "media_type": TEXT,
            "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "byte_length": {"type": "integer", "minimum": 0},
            "encoding": {"const": "base64"}, "content": {"type": "string"}})


def envelope(stage, data):
    return obj({"stage": {"const": stage}, "rules_id": {"const": RULES["id"]},
                "parents": {"type": "array", "minItems": 1, "items": REF},
                "source_refs": {"type": "object", "additionalProperties": False,
                                "properties": {s: REF for s in RULES["priority"]}},
                "data": data})


SCHEMAS = {}
for source in RULES["priority"]:
    SCHEMAS[source + "_bytes"] = obj({"source": {"const": source},
        "upstream_url": TEXT, "blob": BLOB})
    SCHEMAS[source + "_parsed"] = envelope(source + "_parsed", obj({
        "source": {"const": source}, "sha256": TEXT,
        "records": {"type": "array", "maxItems": RULES["max_rows"],
                    "items": {"type": "object"}}}))
SCHEMAS.update({
    "merged": envelope("merged", obj({"rows": {"type": "array"},
                                       "decisions": {"type": "array"}})),
    "conflicts": envelope("conflicts", obj({"report": {"type": "object"},
                                              "decisions": {"type": "array"}})),
    "parquet_artifact": envelope("parquet_artifact", obj({"blob": BLOB,
                                                           "decisions": {"type": "array"}})),
    "conflict_artifact": envelope("conflict_artifact", obj({"blob": BLOB,
                                                            "decisions": {"type": "array"}})),
    "final": envelope("final", obj({"artifacts": obj({"merged_users.parquet": REF,
                                                      "conflicts.json": REF}),
                                     "original_verifier": {"const": "not_run"}})),
    "blocked": envelope("blocked", obj({"step": TEXT, "reason": TEXT})),
    "rejected": envelope("rejected", obj({"step": TEXT, "reason": TEXT})),
})
SCHEMAS = {schema_id(name): {"$id": schema_id(name),
    "$schema": "http://json-schema.org/draft-07/schema#", **body}
    for name, body in SCHEMAS.items()}

# Distinct schema IDs make the existing schema-to-role ABI unambiguous.
STEPS = {
    **{f"read_{s}": ({"source": s + "_bytes"}, {"parsed": s + "_parsed",
        "blocked": "blocked", "rejected": "rejected"}) for s in RULES["priority"]},
    "merge_users": ({s: s + "_parsed" for s in RULES["priority"]},
                    {"merged": "merged", "conflicts": "conflicts"}),
    "encode_parquet": ({"merged": "merged"},
                       {"artifact": "parquet_artifact", "blocked": "blocked"}),
    "encode_conflicts": ({"conflicts": "conflicts"}, {"artifact": "conflict_artifact"}),
    "finish": ({"parquet": "parquet_artifact", "conflicts": "conflict_artifact"},
               {"final": "final"}),
}
