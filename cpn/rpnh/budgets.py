"""Data-only budget-bucket contracts; no role policy or accounting counters.

The execution owner publishes ``budget_buckets`` in the recovery manifest and
``budget_bucket_id`` in each operation binding. IDs are exact manifest-local
keys, not role selectors. The nullable finalization scope is an opaque config
tag. Bounds describe the registered accounting contract; this validator does
not reserve calls or replace Registry cap/turn/claim checks.
"""
from __future__ import annotations

from typing import Any, Mapping


class BudgetContractError(ValueError):
    """A declaration or binding disagrees with the registered bucket contract."""


def validate_budget_scopes(budget_scope: Any, finalization_scope: Any) -> None:
    """Check DTO syntax only; registration is checked at the authority boundary."""
    if not isinstance(budget_scope, str) or not budget_scope:
        raise BudgetContractError("budget scope must be a nonempty string")
    if (finalization_scope is not None
            and (not isinstance(finalization_scope, str) or not finalization_scope)):
        raise BudgetContractError("finalization scope must be null or a nonempty tag")


def validate_budget_buckets(records: Any) -> list[dict[str, Any]]:
    """Validate the explicit, closed JSON records required in a manifest.

    ``max_attempts`` is the bucket-wide bound, not a per-call retry bound.
    Per-operation and shared task bounds remain separately Registry-governed.
    """
    if not isinstance(records, list) or not records:
        raise BudgetContractError("recovery manifest requires explicit budget_buckets")
    result = []
    ids: set[str] = set()
    scopes: set[str] = set()
    fields = {"bucket_id", "budget_scope", "finalization_scope", "max_attempts"}
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise BudgetContractError("budget bucket fields differ from the contract")
        validate_budget_scopes(record["budget_scope"], record["finalization_scope"])
        bucket_id = record["bucket_id"]
        if not isinstance(bucket_id, str) or not bucket_id or bucket_id in ids:
            raise BudgetContractError("budget bucket requires a unique nonempty ID")
        if record["budget_scope"] in scopes:
            raise BudgetContractError("budget scope must identify exactly one bucket")
        bound = record["max_attempts"]
        if isinstance(bound, bool) or not isinstance(bound, int) or bound < 1:
            raise BudgetContractError("budget bucket max_attempts must be a positive integer")
        ids.add(bucket_id)
        scopes.add(record["budget_scope"])
        result.append(dict(record))
    return result


def validate_budget_binding(manifest: Mapping[str, Any],
                            binding: Mapping[str, Any]) -> dict[str, Any]:
    """Select one exact registered bucket and match its scope/config contract.

    Callers must first establish the exact manifest and binding Registry refs.
    Missing publication is an error; nothing is inferred from a role or scope.
    """
    records = validate_budget_buckets(manifest.get("budget_buckets"))
    if not {"budget_bucket_id", "budget_scope", "finalization_scope"} <= set(binding):
        raise BudgetContractError("operation binding lacks its explicit budget contract")
    validate_budget_scopes(binding["budget_scope"], binding["finalization_scope"])
    bucket_id = binding["budget_bucket_id"]
    if not isinstance(bucket_id, str) or not bucket_id:
        raise BudgetContractError("operation binding requires a nonempty budget_bucket_id")
    selected = next((record for record in records if record["bucket_id"] == bucket_id), None)
    if selected is None:
        raise BudgetContractError("operation budget bucket is not registered in the manifest")
    if any(binding[field] != selected[field]
           for field in ("budget_scope", "finalization_scope")):
        raise BudgetContractError("operation binding differs from its exact budget bucket")
    return selected
