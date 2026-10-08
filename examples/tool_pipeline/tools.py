"""Ten independent async HOST tools; no Registry handle or pipeline scheduler.

The existing HOST tool gateway verifies the exact registration and constructs
one coroutine. Its admitted Harness worker awaits it. A tool receives only the
bytes/refs already delivered to this occurrence and a frozen policy value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import copy
import re

from jsonschema import Draft7Validator
from .contracts import source_document


@dataclass(frozen=True)
class ToolResult:
    outcome: str
    products: dict


def _value(stage, data, inputs, rules, sources=None):
    if sources is None:
        sources = {}
        for item in inputs.values():
            for name, ref in item["value"].get("source_refs", {}).items():
                if name in sources and sources[name] != ref:
                    raise ValueError("inconsistent exact source lineage")
                sources[name] = ref
    return {"stage": stage, "rules_id": rules["id"],
            "source_refs": copy.deepcopy(sources),
            "parents": [copy.deepcopy(item["ref"]) for item in inputs.values()],
            "data": copy.deepcopy(data)}


def _reject(step, reason, inputs, rules):
    return ToolResult("rejected", {"rejection": _value("rejection",
        {"step": step, "reason": reason}, inputs, rules, sources={})})


def check_document(value, kind, rules):
    """Business input middleware; malformed units are ordinary rejection data."""
    errors = sorted(Draft7Validator(source_document(kind)).iter_errors(value),
                    key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError("input schema: " + errors[0].message)
    seen, spans = set(), []
    field = "wh" if kind == "usage" else "fen_per_kwh"
    for row in value["intervals"]:
        if row["interval_id"] in seen:
            raise ValueError("duplicate interval ID")
        seen.add(row["interval_id"])
        if int(row[field]) > rules["max_input_integer"]:
            raise ValueError("input integer exceeds frozen bound")
        times = []
        for key in ("start", "end"):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", row[key]):
                raise ValueError("interval boundary must be canonical UTC")
            try:
                times.append(datetime.strptime(row[key], "%Y-%m-%dT%H:%M:%SZ"))
            except ValueError as exc:
                raise ValueError("invalid UTC date") from exc
        if times[0] >= times[1]:
            raise ValueError("interval must have positive duration")
        spans.append(tuple(times))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError("overlapping intervals")


async def read_usage(*, inputs, rules):
    source = inputs["source"]
    value = _value("usage_raw", source["value"], inputs, rules, {"usage": source["ref"]})
    # Explicit two-product fan-out: distinct consuming tokens, same exact source.
    return ToolResult("complete", {"raw": value, "validation_source": dict(copy.deepcopy(value), stage="usage_validation_source")})


async def read_tariff(*, inputs, rules):
    source = inputs["source"]
    value = _value("tariff_raw", source["value"], inputs, rules, {"tariff": source["ref"]})
    return ToolResult("complete", {"raw": value, "validation_source": dict(copy.deepcopy(value), stage="tariff_validation_source")})


async def check_usage_input(*, inputs, rules):
    data = inputs["raw"]["value"]["data"]
    try:
        check_document(data, "usage", rules)
    except ValueError as exc:
        return _reject("check_usage_input", str(exc), inputs, rules)
    return ToolResult("complete", {"checked": _value("usage_checked", data, inputs, rules)})


async def check_tariff_input(*, inputs, rules):
    data = inputs["raw"]["value"]["data"]
    try:
        check_document(data, "tariff", rules)
    except ValueError as exc:
        return _reject("check_tariff_input", str(exc), inputs, rules)
    return ToolResult("complete", {"checked": _value("tariff_checked", data, inputs, rules)})


async def normalize_usage(*, inputs, rules):
    rows = inputs["checked"]["value"]["data"]["intervals"]
    with localcontext() as context:
        context.prec = rules["decimal_precision"]
        result = [{k: row[k] for k in ("interval_id", "start", "end")} |
                  {"kwh": format(Decimal(row["wh"]) / Decimal(rules["wh_per_kwh"]), ".3f")}
                  for row in rows]
    return ToolResult("complete", {"normalized": _value("usage_normalized",
        {"unit": "kWh", "intervals": result}, inputs, rules)})


async def normalize_tariff(*, inputs, rules):
    rows = inputs["checked"]["value"]["data"]["intervals"]
    with localcontext() as context:
        context.prec = rules["decimal_precision"]
        result = [{k: row[k] for k in ("interval_id", "start", "end")} |
                  {"cny_per_kwh": format(Decimal(row["fen_per_kwh"]) / Decimal(rules["fen_per_cny"]), ".2f")}
                  for row in rows]
    return ToolResult("complete", {"normalized": _value("tariff_normalized",
        {"unit": "CNY/kWh", "intervals": result}, inputs, rules)})


async def join_intervals(*, inputs, rules):
    usage = inputs["usage"]["value"]["data"]["intervals"]
    tariff = inputs["tariff"]["value"]["data"]["intervals"]
    u = {row["interval_id"]: row for row in usage}
    t = {row["interval_id"]: row for row in tariff}
    if len(u) != len(usage) or len(t) != len(tariff) or u.keys() != t.keys():
        return _reject("join_intervals", "interval coverage differs", inputs, rules)
    if any((row["start"], row["end"]) != (t[key]["start"], t[key]["end"]) for key, row in u.items()):
        return _reject("join_intervals", "interval boundaries differ", inputs, rules)
    rows = [dict(row, cny_per_kwh=t[row["interval_id"]]["cny_per_kwh"])
            for row in sorted(usage, key=lambda x: (x["start"], x["interval_id"]))]
    return ToolResult("complete", {"joined": _value("joined", {"intervals": rows}, inputs, rules)})


async def compute_cost(*, inputs, rules):
    rows = inputs["joined"]["value"]["data"]["intervals"]
    with localcontext() as context:
        context.prec = rules["decimal_precision"]
        result = [dict(row, amount_cny=format((Decimal(row["kwh"]) * Decimal(row["cny_per_kwh"]))
                    .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")) for row in rows]
        report = {"currency": rules["currency"], "usage_unit": "kWh", "rounding": rules["rounding"],
            "round_each_interval": True, "intervals": result,
            "total_kwh": format(sum((Decimal(row["kwh"]) for row in rows), Decimal(0)), ".3f"),
            "total_cny": format(sum((Decimal(row["amount_cny"]) for row in result), Decimal(0)), ".2f")}
    return ToolResult("complete", {"candidate": _value("candidate", report, inputs, rules)})


def _integer_decimal(value, scale):
    """Independent integer formatting: the validator never calls compute/Decimal."""
    base = 10 ** scale
    return f"{value // base}.{value % base:0{scale}d}"


async def validate_report(*, inputs, rules):
    candidate = inputs["candidate"]["value"]
    usage = inputs["usage_source"]["value"]
    tariff = inputs["tariff_source"]["value"]
    try:
        # Revalidate original bytes independently of the normalized data path.
        check_document(usage["data"], "usage", rules)
        check_document(tariff["data"], "tariff", rules)
        expected_sources = {"usage": usage["source_refs"]["usage"], "tariff": tariff["source_refs"]["tariff"]}
        if candidate["source_refs"] != expected_sources or candidate["rules_id"] != rules["id"]:
            raise ValueError("candidate source versions or policy differ")
        u = {r["interval_id"]: r for r in usage["data"]["intervals"]}
        t = {r["interval_id"]: r for r in tariff["data"]["intervals"]}
        if u.keys() != t.keys():
            raise ValueError("oracle interval coverage differs")
        rows, total_wh, total_fen = [], 0, 0
        for key, row in sorted(u.items(), key=lambda x: (x[1]["start"], x[0])):
            rate = t[key]
            if (row["start"], row["end"]) != (rate["start"], rate["end"]):
                raise ValueError("oracle interval boundaries differ")
            wh, fen_rate = int(row["wh"]), int(rate["fen_per_kwh"])
            # All accepted values are nonnegative. Round each exact n/1000 fen
            # half-up using integer division; sum rounded rows, not rounded sum.
            fen = (wh * fen_rate + rules["wh_per_kwh"] // 2) // rules["wh_per_kwh"]
            rows.append({"interval_id": key, "start": row["start"], "end": row["end"],
                         "kwh": _integer_decimal(wh, 3), "cny_per_kwh": _integer_decimal(fen_rate, 2),
                         "amount_cny": _integer_decimal(fen, 2)})
            total_wh += wh
            total_fen += fen
        expected = {"currency": "CNY", "usage_unit": "kWh", "rounding": "ROUND_HALF_UP",
                    "round_each_interval": True, "intervals": rows,
                    "total_kwh": _integer_decimal(total_wh, 3), "total_cny": _integer_decimal(total_fen, 2)}
        if candidate["data"] != expected:
            raise ValueError("candidate differs from independent integer oracle")
    except (ValueError, KeyError) as exc:
        return _reject("validate_report", str(exc), inputs, rules)
    return ToolResult("complete", {"validated": _value("validated", {
        "report": candidate["data"], "candidate_ref": inputs["candidate"]["ref"],
        "oracle": "integer-fen-half-up/v1"}, inputs, rules)})


async def publish_report(*, inputs, rules):
    verified = inputs["validated"]
    data = dict(verified["value"]["data"], validation_ref=verified["ref"])
    return ToolResult("complete", {"final": _value("final", data, inputs, rules)})


TOOLS = {function.__name__: function for function in (
    read_usage, read_tariff, check_usage_input, check_tariff_input,
    normalize_usage, normalize_tariff, join_intervals, compute_cost,
    validate_report, publish_report)}
