"""Seven complete static call contracts; no attach/return implementation.

The catalog preserves the frozen compatible-profile sources, modes, defaults,
omission rules, payload requirements and parent updates. Nominal call types
describe author projections; they do not resolve a live ref or grant authority.
"""
from __future__ import annotations

import json
from pathlib import Path

from .control_types import canonical_key, data, fail, fields, text


_SCHEMA = Path(__file__).resolve().parent.parent / "schemas/rpnh/typed_call.v1.schema.json"


def _catalog():
    return json.loads(_SCHEMA.read_text(encoding="utf-8"))


def call_contract(site: str) -> dict:
    """Return a fresh complete declaration for one fixed compatible call-site."""
    text(site, "$.site")
    for entry in _catalog()["oneOf"]:
        if entry["const"]["site"] == site:
            return entry["const"]
    fail("unknown_call_site", "$.site", str(site))


def call_sites() -> tuple[str, ...]:
    return tuple(entry["const"]["site"] for entry in _catalog()["oneOf"])


def _same(actual, expected, path, code):
    # JSON equality must never accept True in place of an integer literal.
    if canonical_key(actual) != canonical_key(expected):
        if type(actual) is dict and type(expected) is dict:
            fields(actual, expected, path=path)
            for key in expected:
                _same(actual[key], expected[key], path + "." + key, code)
        else:
            fail(code, path, "does not match the frozen call contract")


def validate_call_contract(value, path="$.call") -> dict:
    value = data(value, path)
    if type(value) is not dict or "site" not in value:
        fail("call_site_missing", path)
    expected = call_contract(value["site"])
    fields(value, expected, path=path)
    for name in expected.keys() - {"inputs", "returns"}:
        _same(value[name], expected[name], path + "." + name, "call_contract_mismatch")
    for collection, identity, code in (("inputs", "port", "call_input_mismatch"),
                                        ("returns", "outcome", "call_return_mismatch")):
        if type(value[collection]) is not list:
            fail(code, path + "." + collection)
        actual = {}
        for i, item in enumerate(value[collection]):
            item_path = f"{path}.{collection}[{i}]"
            if type(item) is not dict or identity not in item:
                fail(code, item_path)
            key = item[identity]
            if type(key) is not str or key in actual:
                fail("duplicate_" + identity, item_path)
            actual[key] = item
        wanted = {item[identity]: item for item in expected[collection]}
        if actual.keys() != wanted.keys():
            fail("call_" + collection + "_incomplete", path + "." + collection,
                 f"missing={sorted(wanted.keys() - actual.keys())}, unexpected={sorted(actual.keys() - wanted.keys())}")
        for key, item in wanted.items():
            _same(actual[key], item, path + "." + collection + "." + key, code)
    # Declaration order is incidental for ports/tags; return one canonical form.
    return expected


__all__ = ("call_contract", "call_sites", "validate_call_contract")
