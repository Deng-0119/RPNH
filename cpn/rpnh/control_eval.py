"""Typed finite AST evaluation over explicit snapshot data, never callbacks.

This module records observations. It cannot read a Registry, claim occurrences,
check live authority, or grant dispatch. A caller must separately own admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any

from .control_types import (
    BOOL, INT, TEXT, ControlIRError, canonical_key, data, exact_ref, fail,
    fields, rational, same_type, text, type_spec, typed_value,
)


@dataclass(frozen=True)
class EvalError:
    code: str
    path: str
    binding_ref: dict | None
    detail: str = ""


@dataclass(frozen=True)
class ReadSet:
    heads: tuple[dict, ...] = ()
    immutable_refs: tuple[dict, ...] = ()

    def stale_heads(self, current_heads: list[dict]) -> tuple[dict, ...]:
        """Compare every observed head with caller-supplied data; no admission."""
        current = {}
        for index, head in enumerate(data(current_heads)):
            _origin(head, f"$.current_heads[{index}]")
            if head["kind"] != "head":
                fail("expected_head", f"$.current_heads[{index}]")
            key = _head_key(head)
            if key in current:
                fail("duplicate_head", f"$.current_heads[{index}]")
            current[key] = head
        return tuple(head for head in self.heads if current.get(_head_key(head)) != head)


@dataclass(frozen=True)
class EvalResult:
    type: dict
    value: Any
    error: EvalError | None
    read_set: ReadSet


def _head_key(head):
    return head["source"], head["stream"], head["identity"]


def _origin(origin, path):
    if type(origin) is not dict:
        fail("missing_read_origin", path)
    if origin.get("kind") == "head":
        fields(origin, ("kind", "source", "stream", "identity", "version"), path=path)
        for name in ("source", "stream", "identity"):
            text(origin[name], path + "." + name)
        if type(origin["version"]) is not int or origin["version"] < 0:
            fail("invalid_head_version", path + ".version")
    elif origin.get("kind") == "immutable":
        fields(origin, ("kind", "ref"), path=path)
        exact_ref(origin["ref"], path + ".ref")
    elif origin.get("kind") == "parameter":
        fields(origin, ("kind", "ref"), path=path)
        exact_ref(origin["ref"], path + ".ref")
    else:
        fail("unknown_read_origin", path)


def _environment(bindings):
    if type(bindings) is not dict:
        fail("invalid_bindings", "$.bindings")
    for name, binding in bindings.items():
        text(name, "$.bindings")
        path = "$.bindings." + name
        fields(binding, ("type", "value", "origin"), path=path)
        type_spec(binding["type"], path + ".type")
        _origin(binding["origin"], path + ".origin")


def _project_type(spec, path, where):
    if type(path) is not list or any(type(part) is not str for part in path):
        fail("invalid_field_path", where)
    for part in path:
        if spec["kind"] != "Record" or part not in spec["fields"]:
            fail("unknown_field_type", where, part)
        spec = spec["fields"][part]
    return spec


def _numeric(left, right, op, path):
    if left["kind"] not in ("Int", "Rational") or right["kind"] not in ("Int", "Rational"):
        fail("numeric_type_required", path)
    a, b = left.get("unit", {}), right.get("unit", {})
    if op in ("add", "sub", "eq", "lt", "le", "gt", "ge"):
        if a != b:
            fail("unit_mismatch", path)
        unit = a
    else:
        unit = dict(a)
        for name, power in b.items():
            unit[name] = unit.get(name, 0) + (power if op == "mul" else -power)
        unit = {name: power for name, power in unit.items() if power}
    kind = "Int" if op == "floor_div" else (
        "Rational" if op == "div" or "Rational" in (left["kind"], right["kind"]) else "Int")
    return {"kind": kind, **({"unit": unit} if unit else {})}


def _infer_expression(expression, bindings, *, _locals=None, _path="$.expression"):
    """Check both branches without reading values; no source access occurs."""
    local = {} if _locals is None else _locals
    path = _path
    if type(expression) is not dict or type(expression.get("op")) is not str:
        fail("invalid_expression", path)
    op = expression["op"]
    infer = lambda child, suffix, env=local: _infer_expression(
        child, bindings, _locals=env, _path=path + suffix)
    if op == "literal":
        fields(expression, ("op", "type", "value"), path=path)
        spec = type_spec(expression["type"], path + ".type")
        typed_value(expression["value"], spec, path + ".value")
        return spec
    if op in ("read", "parameter", "local"):
        fields(expression, ("op", "name"), ("path",), path)
        name = text(expression["name"], path + ".name")
        source = local if op == "local" else bindings
        if name not in source:
            fail("unknown_binding", path + ".name", name)
        if op == "local":
            spec = source[name]
        else:
            binding = source[name]
            if op == "parameter" and binding["origin"]["kind"] != "parameter":
                fail("parameter_origin_required", path)
            spec = binding["type"]
        return _project_type(spec, expression.get("path", []), path + ".path")
    if op == "match_known":
        fields(expression, ("op", "value", "name", "known", "unknown"), path=path)
        source = infer(expression["value"], ".value")
        if source["kind"] != "Maybe":
            fail("known_union_required", path)
        name = text(expression["name"], path + ".name")
        if name in local:
            fail("shadowed_local", path + ".name")
        known = infer(expression["known"], ".known", dict(local, **{name: source["item"]}))
        unknown = infer(expression["unknown"], ".unknown")
        if not same_type(known, unknown):
            fail("branch_type_mismatch", path)
        return known
    if op in ("sequence", "set"):
        fields(expression, ("op", "item_type", "items"), path=path)
        item_type = type_spec(expression["item_type"], path + ".item_type")
        if type(expression["items"]) is not list:
            fail("expected_sequence", path + ".items")
        for index, child in enumerate(expression["items"]):
            if not same_type(infer(child, f".items[{index}]"), item_type):
                fail("collection_item_type_mismatch", f"{path}.items[{index}]")
        return {"kind": "Sequence" if op == "sequence" else "Set", "item": item_type}
    if op == "record":
        fields(expression, ("op", "fields"), path=path)
        if type(expression["fields"]) is not dict:
            fail("expected_record", path + ".fields")
        for name in expression["fields"]:
            text(name, path + ".fields")
        return {"kind": "Record", "fields": {name: infer(child, ".fields." + name)
                for name, child in expression["fields"].items()}}
    if op == "sort":
        fields(expression, ("op", "value", "keys", "tie_break"), path=path)
        source = infer(expression["value"], ".value")
        if source["kind"] not in ("Sequence", "Set") or source["item"]["kind"] != "Record":
            fail("record_collection_required", path)
        if type(expression["keys"]) is not list:
            fail("invalid_sort_keys", path)
        keys = [*expression["keys"], expression["tie_break"]]
        if not keys or any(type(key) is not list or not key for key in keys):
            fail("explicit_tie_break_required", path)
        if len({canonical_key(key) for key in keys}) != len(keys):
            fail("duplicate_sort_key", path)
        for key in keys:
            spec = _project_type(source["item"], key, path + ".keys")
            if spec["kind"] not in ("Int", "Rational", "Text", "Enum", "Bool"):
                fail("unordered_sort_key", path)
        return {"kind": "Sequence", "item": source["item"]}
    unary = {"not", "floor", "rational", "min", "max", "count", "absence"}
    binary = {"add", "sub", "mul", "div", "floor_div", "eq", "lt", "le", "gt", "ge", "member"}
    if op not in unary | binary | {"and", "or"}:
        fail("unknown_operator", path, op)
    fields(expression, ("op", "args"), path=path)
    args = expression["args"]
    if type(args) is not list or (op in unary and len(args) != 1) or (
        op in binary and len(args) != 2) or (op in {"and", "or"} and not args):
        fail("invalid_arity", path)
    specs = [infer(arg, f".args[{i}]") for i, arg in enumerate(args)]
    a = specs[0]
    if op in ("not", "and", "or"):
        if any(spec["kind"] != "Bool" for spec in specs):
            fail("bool_required", path)
        return BOOL
    if op == "rational":
        if a["kind"] not in ("Text", "Int", "Rational"):
            fail("numeric_type_required", path)
        return {"kind": "Rational", **({"unit": a["unit"]} if a.get("unit") else {})}
    if op == "floor":
        if a["kind"] not in ("Int", "Rational"):
            fail("numeric_type_required", path)
        return {"kind": "Int", **({"unit": a["unit"]} if a.get("unit") else {})}
    if op in ("count", "absence", "min", "max"):
        if a["kind"] not in ("Sequence", "Set"):
            fail("finite_collection_required", path)
        if op in ("min", "max") and a["item"]["kind"] not in ("Int", "Rational", "Text"):
            fail("ordered_type_required", path)
        return BOOL if op == "absence" else (INT if op == "count" else a["item"])
    b = specs[1]
    if op == "member":
        if b["kind"] not in ("Sequence", "Set") or not same_type(a, b["item"]):
            fail("membership_type_mismatch", path)
        return BOOL
    if op in ("eq", "lt", "le", "gt", "ge"):
        if a["kind"] in ("Int", "Rational"):
            _numeric(a, b, op, path)
        elif not same_type(a, b) or (op != "eq" and a["kind"] not in ("Text", "Enum")):
            fail("comparison_type_mismatch", path)
        return BOOL
    return _numeric(a, b, op, path)


def infer_expression(expression, bindings):
    """Infer a data-only expression's type without reading binding values."""
    expression, bindings = data(expression), data(bindings)
    _environment(bindings)
    return _infer_expression(expression, bindings)


def evaluate_expression(expression, bindings) -> EvalResult:
    """Value or EvalError plus the exact observations actually executed.

    Missing fields are errors, even for a Maybe field. Unknown must be explicit
    data. Short-circuit and match_known never read the skipped branch.
    """
    expression, bindings = data(expression), data(bindings)
    _environment(bindings)
    result_type = infer_expression(expression, bindings)
    heads, refs = {}, {}
    current_origin = None

    def observe(origin):
        nonlocal current_origin
        current_origin = origin
        if origin["kind"] == "head":
            key = _head_key(origin)
            if key in heads and heads[key] != origin:
                fail("inconsistent_snapshot", "$.bindings", repr(key))
            heads[key] = origin
        else:
            refs[canonical_key(origin["ref"])] = origin["ref"]

    def project(value, parts, path):
        for part in parts:
            if type(value) is not dict or part not in value:
                fail("missing_field", path + "." + part)
            value = value[part]
        return value

    def run(expr, local, local_types, path):
        op = expr["op"]
        spec = _infer_expression(expr, bindings, _locals=local_types, _path=path)
        sub = lambda child, suffix: run(child, local, local_types, path + suffix)
        if op == "literal":
            return typed_value(expr["value"], spec, path + ".value")
        if op in ("read", "parameter", "local"):
            if op == "local":
                value = local[expr["name"]]
            else:
                binding = bindings[expr["name"]]
                observe(binding["origin"])
                value = binding["value"]
            return typed_value(project(value, expr.get("path", []), path), spec, path)
        if op == "match_known":
            value = sub(expr["value"], ".value")
            if value["tag"] == "unknown":
                return sub(expr["unknown"], ".unknown")
            item_type = _infer_expression(expr["value"], bindings, _locals=local_types)["item"]
            return run(expr["known"], dict(local, **{expr["name"]: value["value"]}),
                       dict(local_types, **{expr["name"]: item_type}), path + ".known")
        if op in ("sequence", "set"):
            return typed_value([sub(child, f".items[{index}]") for index, child in enumerate(expr["items"])], spec, path)
        if op == "record":
            return {name: sub(child, ".fields." + name) for name, child in expr["fields"].items()}
        if op == "sort":
            values = sub(expr["value"], ".value")
            source_type = _infer_expression(expr["value"], bindings, _locals=local_types)
            keys = [*expr["keys"], expr["tie_break"]]
            key_types = [_project_type(source_type["item"], key, path) for key in keys]
            def key_for(value):
                raw = [project(value, key, path) for key in keys]
                return tuple(rational(item, path) if t["kind"] == "Rational" else item
                             for item, t in zip(raw, key_types))
            seen = set()
            for value in values:
                key = key_for(value)
                if key in seen:
                    fail("nonunique_sort_tie_break", path)
                seen.add(key)
            return sorted(values, key=key_for)
        if op in ("and", "or"):
            for i, arg in enumerate(expr["args"]):
                value = sub(arg, f".args[{i}]")
                if (op == "and" and not value) or (op == "or" and value):
                    return value
            return op == "and"
        values = [sub(arg, f".args[{i}]") for i, arg in enumerate(expr["args"])]
        specs = [_infer_expression(arg, bindings, _locals=local_types) for arg in expr["args"]]
        a = values[0]
        if op == "not": return not a
        if op == "rational": return str(rational(a, path))
        if op == "floor": return math.floor(rational(a, path))
        if op == "count": return len(a)
        if op == "absence": return len(a) == 0
        if op in ("min", "max"):
            if not a: fail("empty_collection", path)
            key = (lambda v: rational(v, path)) if specs[0]["item"]["kind"] == "Rational" else None
            return (min if op == "min" else max)(a, key=key)
        b = values[1]
        if op == "member": return any(canonical_key(a) == canonical_key(v) for v in b)
        if specs[0]["kind"] in ("Int", "Rational"):
            a, b = rational(a, path), rational(b, path)
        if op == "eq": return a == b
        if op == "lt": return a < b
        if op == "le": return a <= b
        if op == "gt": return a > b
        if op == "ge": return a >= b
        if op in ("div", "floor_div") and b == 0: fail("division_by_zero", path)
        if op == "add": value = a + b
        elif op == "sub": value = a - b
        elif op == "mul": value = a * b
        elif op == "div": value = a / b
        else: value = a // b
        return str(value) if spec["kind"] == "Rational" else int(value)

    try:
        value = run(expression, {}, {}, "$.expression")
        error = None
    except ControlIRError as exc:
        value, error = None, EvalError(exc.code, exc.path, current_origin, exc.detail)
    read_set = ReadSet(tuple(heads[key] for key in sorted(heads)),
                       tuple(refs[key] for key in sorted(refs)))
    return EvalResult(result_type, value, error, read_set)


__all__ = ("EvalError", "EvalResult", "ReadSet", "infer_expression", "evaluate_expression")
