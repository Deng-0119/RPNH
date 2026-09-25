"""Bounded memoization of *schema syntax*, never Registry or instance authority.

The key is a complete canonical JSON document, not an ID, path, hash, run or
writer epoch. A caller still reads/verifies its current registered resource,
identity, references and instance on every use. Only successful Draft7
meta-schema checks are cached. No mutable schema or validator is returned.
"""
from __future__ import annotations

from functools import lru_cache
import json
from typing import Any

from jsonschema import Draft7Validator

_MAX_SCHEMA_CHARS = 65_536  # ensure_ascii keys: at most 16 MiB for 256 entries


@lru_cache(maxsize=256)
def _check_canonical_schema(canonical: str) -> None:
    Draft7Validator.check_schema(json.loads(canonical))


def _plain_json(value: Any) -> bool:
    if value is None or type(value) in (str, bool, int, float):
        return True
    if type(value) is list:
        return all(_plain_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _plain_json(item)
                   for key, item in value.items())
    return False


def check_draft7_schema(schema: Any) -> None:
    """Reuse only a pure successful meta-check for identical finite JSON."""
    if not _plain_json(schema):
        Draft7Validator.check_schema(schema)
        return
    try:
        canonical = json.dumps(schema, ensure_ascii=True, allow_nan=False,
                               sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        # Preserve the underlying validator's behavior for non-JSON callers;
        # normal registered inputs are already finite JSON before this point.
        Draft7Validator.check_schema(schema)
        return
    if len(canonical) > _MAX_SCHEMA_CHARS:
        Draft7Validator.check_schema(schema)
    else:
        _check_canonical_schema(canonical)
