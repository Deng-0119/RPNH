"""Finite, data-only author types and exact values (no runtime authority)."""
from __future__ import annotations

from fractions import Fraction
import json
import math
from typing import Any

from .petri_contracts import DeclarationError


class ControlIRError(DeclarationError):
    """Stable author diagnostic with a precise field/expression path."""

    def __init__(self, code: str, path: str, detail: str = ""):
        self.code, self.path, self.detail = code, path, detail
        super().__init__(f"{code} at {path}" + (f": {detail}" if detail else ""))


def fail(code, path, detail=""):
    raise ControlIRError(code, path, detail)


def data(value: Any, path: str = "$") -> Any:
    """Reject Python objects, non-string keys, cycles and nonfinite numbers."""
    def check(item, where, active):
        if type(item) in (dict, list):
            if id(item) in active:
                fail("cyclic_data", where)
            active.add(id(item))
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        fail("non_json_data", where)
                    check(child, where + "." + key, active)
            else:
                for index, child in enumerate(item):
                    check(child, f"{where}[{index}]", active)
            active.remove(id(item))
        elif item is not None and type(item) not in (str, bool, int, float):
            fail("non_json_data", where)
        elif type(item) is float and not math.isfinite(item):
            fail("nonfinite_number", where)
    try:
        check(value, path, set())
        return json.loads(json.dumps(value, allow_nan=False))
    except RecursionError as exc:
        raise ControlIRError("representation_not_supported", path, "Python nesting capacity") from exc


def fields(value, required, optional=(), path="$"):
    if type(value) is not dict:
        fail("expected_record", path)
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing:
        fail("missing_field", path + "." + sorted(missing)[0])
    if extra:
        fail("unknown_field", path + "." + sorted(extra)[0])


def text(value, path):
    if type(value) is not str or not value:
        fail("expected_nonempty_text", path)
    return value


def type_spec(value, path="$.type"):
    if type(value) is not dict or "kind" not in value:
        fail("invalid_type", path)
    kind = value["kind"]
    if kind in ("Bool", "Text"):
        fields(value, ("kind",), path=path)
    elif kind in ("Int", "Rational"):
        fields(value, ("kind",), ("unit",), path)
        unit = value.get("unit", {})
        if type(unit) is not dict:
            fail("invalid_unit", path + ".unit")
        for name, power in unit.items():
            text(name, path + ".unit")
            if type(power) is not int or power == 0:
                fail("invalid_unit", path + ".unit." + name)
    elif kind == "Enum":
        fields(value, ("kind", "values"), path=path)
        vals = value["values"]
        if type(vals) is not list or not vals or any(type(v) is not str for v in vals) or len(set(vals)) != len(vals):
            fail("invalid_enum", path)
    elif kind == "ExactRef":
        fields(value, ("kind", "schema"), path=path)
        text(value["schema"], path + ".schema")
    elif kind == "Record":
        fields(value, ("kind", "fields"), path=path)
        if type(value["fields"]) is not dict:
            fail("invalid_record_type", path)
        for name, child in value["fields"].items():
            text(name, path + ".fields")
            type_spec(child, path + ".fields." + name)
    elif kind in ("Sequence", "Set", "Maybe"):
        fields(value, ("kind", "item"), path=path)
        type_spec(value["item"], path + ".item")
    else:
        fail("unknown_type", path, str(kind))
    return value


def same_type(left, right):
    # Units are dimension maps; their omitted form means dimensionless.
    if left["kind"] != right["kind"]:
        return False
    if left["kind"] in ("Int", "Rational") and right["kind"] == left["kind"]:
        return left.get("unit", {}) == right.get("unit", {})
    if left["kind"] in ("Sequence", "Set", "Maybe"):
        return same_type(left["item"], right["item"])
    if left["kind"] == "Record":
        return left["fields"].keys() == right["fields"].keys() and all(
            same_type(spec, right["fields"][name]) for name, spec in left["fields"].items())
    return left == right


def exact_ref(value, path="$.ref"):
    fields(value, ("schema", "source", "identity", "version"), path=path)
    for key in ("schema", "source", "identity"):
        text(value[key], path + "." + key)
    if type(value["version"]) is not int or value["version"] < 0:
        fail("invalid_ref_version", path + ".version")
    return value


def rational(value, path):
    if type(value) not in (str, int, float):
        fail("type_mismatch", path, "Rational requires a decimal/rational encoding")
    if type(value) is float and not math.isfinite(value):
        fail("nonfinite_number", path)
    try:
        # Preserve the existing ratio convention; never Fraction(float).
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError) as exc:
        raise ControlIRError("invalid_rational", path) from exc


def canonical_key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def typed_value(value, spec, path="$.value"):
    """Validate and normalize a finite JSON value; Rational uses canonical text."""
    kind = spec["kind"]
    if kind == "Bool":
        if type(value) is not bool:
            fail("type_mismatch", path, "Bool")
    elif kind == "Int":
        if type(value) is not int:
            fail("type_mismatch", path, "Int")
    elif kind == "Rational":
        value = str(rational(value, path))
    elif kind == "Text":
        if type(value) is not str:
            fail("type_mismatch", path, "Text")
    elif kind == "Enum":
        if type(value) is not str or value not in spec["values"]:
            fail("type_mismatch", path, "Enum")
    elif kind == "ExactRef":
        exact_ref(value, path)
        if value["schema"] != spec["schema"]:
            fail("ref_schema_mismatch", path)
    elif kind == "Record":
        fields(value, spec["fields"], path=path)
        value = {name: typed_value(value[name], child, path + "." + name)
                 for name, child in spec["fields"].items()}
    elif kind in ("Sequence", "Set"):
        if type(value) is not list:
            fail("type_mismatch", path, kind)
        value = [typed_value(child, spec["item"], f"{path}[{i}]") for i, child in enumerate(value)]
        if kind == "Set":
            keys = [canonical_key(item) for item in value]
            if len(set(keys)) != len(keys):
                fail("duplicate_set_member", path)
            value = [item for _, item in sorted(zip(keys, value), key=lambda pair: pair[0])]
    elif kind == "Maybe":
        if type(value) is not dict:
            fail("type_mismatch", path, "Known/Unknown")
        if value.get("tag") == "known":
            fields(value, ("tag", "value"), path=path)
            value = {"tag": "known", "value": typed_value(value["value"], spec["item"], path + ".value")}
        elif value.get("tag") == "unknown":
            fields(value, ("tag", "reason"), path=path)
            text(value["reason"], path + ".reason")
        else:
            fail("invalid_known_tag", path)
    else:
        fail("unknown_type", path, str(kind))
    return value


BOOL = {"kind": "Bool"}
INT = {"kind": "Int"}
TEXT = {"kind": "Text"}
