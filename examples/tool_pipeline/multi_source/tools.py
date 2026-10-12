"""In-memory tools consuming admitted byte resources, never ambient input paths."""
from __future__ import annotations

import base64
import copy
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import io
import json
import re

from .contracts import RULES


@dataclass(frozen=True)
class ToolResult:
    products: dict
    outcome: str = "complete"


def pack_blob(data, filename, media_type):
    return {"filename": filename, "media_type": media_type,
            "sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data),
            "encoding": "base64", "content": base64.b64encode(data).decode("ascii")}


def unpack_blob(blob, *, limit=None):
    if blob["encoding"] != "base64":
        raise ValueError("only explicitly encoded base64 bytes are supported")
    if limit is not None and (blob["byte_length"] > limit or len(blob["content"]) > 4 * ((limit + 2) // 3)):
        raise ValueError("input exceeds the declared byte limit")
    data = base64.b64decode(blob["content"], validate=True)
    if len(data) != blob["byte_length"] or hashlib.sha256(data).hexdigest() != blob["sha256"]:
        raise ValueError("byte resource integrity check failed")
    return data


def _envelope(stage, inputs, data):
    refs = {}
    for item in inputs.values():
        value = item["value"]
        if "source_refs" in value:
            refs.update(value["source_refs"])
        else:
            refs[value["source"]] = item["ref"]
    return {"stage": stage, "rules_id": RULES["id"],
            "parents": copy.deepcopy([item["ref"] for item in inputs.values()]),
            "source_refs": copy.deepcopy(refs), "data": data}


FIELD_ALIASES = {"user_id": ("user_id", "id", "userId"),
                 "name": ("full_name", "name", "userName"),
                 "email": ("email", "email_address"),
                 "created_date": ("registration_date", "created_at", "joined"),
                 "status": ("status", "is_active", "active")}


def normalize_records(rows):
    """Keep row and original-field locators next to normalized values."""
    if not isinstance(rows, list) or len(rows) > RULES["max_rows"]:
        raise ValueError("expected a bounded list of records")
    normalized, seen = [], set()
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("each source record must be an object")
        values, fields = {}, {}
        for target, names in FIELD_ALIASES.items():
            found = [(name, row[name]) for name in names if name in row and row[name] not in (None, "")]
            if not found:
                if target != "status":
                    raise ValueError(f"row {row_index} has no {target}")
                continue
            if len(found) != 1:
                raise ValueError(f"row {row_index} has ambiguous {target} aliases")
            key, value = found[0]
            if target == "user_id":
                if isinstance(value, bool) or not (isinstance(value, int) or
                    isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]*)", value)):
                    raise ValueError("user_id must be an integer")
                value = int(value)
            elif target == "created_date":
                if not isinstance(value, str):
                    raise ValueError("date must be YYYY-MM-DD text")
                value = date.fromisoformat(value).isoformat()
            elif target == "status" and key in {"is_active", "active"}:
                if isinstance(value, str) and value.lower() in {"true", "false"}:
                    value = value.lower() == "true"
                if not isinstance(value, bool):
                    raise ValueError("active status must be boolean")
                value = "active" if value else "inactive"
            elif not isinstance(value, str):
                raise ValueError(f"{target} must be text")
            values[target], fields[target] = value, key
        if values["user_id"] in seen:
            raise ValueError("duplicate user_id within one source")
        seen.add(values["user_id"])
        normalized.append({"values": values, "row_index": row_index, "original_fields": fields})
    return normalized


def _parse_parquet(data):
    # No fixture conversion, magic fallback, package installation, or model call.
    import pandas as pd
    frame = pd.read_parquet(io.BytesIO(data))
    return frame.astype(object).where(pd.notnull(frame), None).to_dict(orient="records")


async def _read(source, inputs, rules):
    if rules != RULES or inputs["source"]["value"]["source"] != source:
        raise ValueError("tool rules or source identity mismatch")
    try:
        payload = inputs["source"]["value"]["blob"]
        data = unpack_blob(payload, limit=RULES["max_input_bytes"])
        if source == "source_a":
            rows = json.loads(data.decode("utf-8"))
        elif source == "source_b":
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
        else:
            rows = _parse_parquet(data)
        records = normalize_records(rows)
    except ImportError as exc:
        return ToolResult({"blocked": _envelope("blocked", inputs,
            {"step": "read_" + source, "reason": str(exc)})}, "blocked")
    except (ValueError, UnicodeError, TypeError) as exc:
        return ToolResult({"rejected": _envelope("rejected", inputs,
            {"step": "read_" + source, "reason": str(exc)})}, "rejected")
    return ToolResult({"parsed": _envelope(source + "_parsed", inputs,
        {"source": source, "sha256": payload["sha256"], "records": records})})


async def read_source_a(*, inputs, rules):
    return await _read("source_a", inputs, rules)


async def read_source_b(*, inputs, rules):
    return await _read("source_b", inputs, rules)


async def read_source_c(*, inputs, rules):
    return await _read("source_c", inputs, rules)


async def merge_users(*, inputs, rules):
    if rules != RULES or set(inputs) != set(RULES["priority"]):
        raise ValueError("merge requires the three exact admitted inputs")
    by_id = {}
    for source in RULES["priority"]:
        for record in inputs[source]["value"]["data"]["records"]:
            by_id.setdefault(record["values"]["user_id"], {})[source] = record
    rows, conflicts, decisions = [], [], []
    for user_id, records in sorted(by_id.items()):
        merged = {"user_id": user_id}
        for field in ("name", "email", "created_date", "status"):
            values = {source: records[source]["values"][field] for source in RULES["priority"]
                      if source in records and field in records[source]["values"]}
            if not values:
                continue
            selected_source = next(iter(values))
            merged[field] = values[selected_source]
            if len(set(values.values())) > 1:
                conflicts.append({"user_id": user_id, "field": field,
                                  "values": values, "selected": merged[field]})
                origins = {source: {"input_ref": inputs[source]["value"]["source_refs"][source],
                    "parsed_result_ref": inputs[source]["ref"],
                    "row_index": records[source]["row_index"],
                    "original_field": records[source]["original_fields"][field]}
                    for source in values}
                decisions.append({"conflict_index": len(conflicts) - 1,
                    "user_id": user_id, "field": field, "selected_source": selected_source,
                    "selected": merged[field], "origins": origins})
        rows.append(merged)
    return ToolResult({
        "merged": _envelope("merged", inputs, {"rows": rows, "decisions": decisions}),
        "conflicts": _envelope("conflicts", inputs,
            {"report": {"total_conflicts": len(conflicts), "conflicts": conflicts},
             "decisions": decisions}),
    })


async def encode_parquet(*, inputs, rules):
    if rules != RULES:
        raise ValueError("rules mismatch")
    data = inputs["merged"]["value"]["data"]
    try:
        import pandas as pd
        frame = pd.DataFrame(data["rows"])
        frame["user_id"] = frame["user_id"].astype("int64")
        for column in frame.columns:
            if column != "user_id":
                frame[column] = frame[column].astype("string")
        raw = frame.to_parquet(index=False)
        # Actual bytes must round-trip before they can become an output resource.
        recovered = pd.read_parquet(io.BytesIO(raw))
        pd.testing.assert_frame_equal(recovered, frame)
    except ImportError as exc:
        return ToolResult({"blocked": _envelope("blocked", inputs,
            {"step": "encode_parquet", "reason": str(exc)})}, "blocked")
    return ToolResult({"artifact": _envelope("parquet_artifact", inputs,
        {"blob": pack_blob(raw, "merged_users.parquet", "application/vnd.apache.parquet"),
         "decisions": data["decisions"]})})


async def encode_conflicts(*, inputs, rules):
    if rules != RULES:
        raise ValueError("rules mismatch")
    data = inputs["conflicts"]["value"]["data"]
    raw = (json.dumps(data["report"], indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return ToolResult({"artifact": _envelope("conflict_artifact", inputs,
        {"blob": pack_blob(raw, "conflicts.json", "application/json"),
         "decisions": data["decisions"]})})


async def finish(*, inputs, rules):
    if rules != RULES:
        raise ValueError("rules mismatch")
    for item in inputs.values():
        unpack_blob(item["value"]["data"]["blob"])
    return ToolResult({"final": _envelope("final", inputs,
        {"artifacts": {"merged_users.parquet": inputs["parquet"]["ref"],
                       "conflicts.json": inputs["conflicts"]["ref"]},
         "original_verifier": "not_run"})})


TOOLS = {name: globals()[name] for name in ("read_source_a", "read_source_b", "read_source_c",
    "merge_users", "encode_parquet", "encode_conflicts", "finish")}
