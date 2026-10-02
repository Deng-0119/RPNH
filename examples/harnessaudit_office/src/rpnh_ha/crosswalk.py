"""Match independent backend dispatches to native-owner exported evidence.

A positive match assumes the supplied export came from the trusted Registry
reader; this function does not manufacture or authenticate Registry records.
"""
from __future__ import annotations
from collections import defaultdict

KEYS = ("run_id", "operation_id", "invocation_id", "firing_id", "tool_name")


def crosswalk(events: list[dict], registry_records: list[dict] | None) -> dict:
    dispatches = [e for e in events if e["kind"] == "dispatched"]
    if registry_records is None:
        return {"status": "registry_export_missing", "dispatches": len(dispatches),
                "matched": None, "coverage": None, "complete": False,
                "mismatches": [], "source_authority": "not_verified"}
    index = defaultdict(list)
    for row in registry_records:
        if any(k not in row for k in KEYS) or not isinstance(row.get("registry_ref"), dict):
            raise ValueError("Registry export lacks exact native identities or reference")
        index[tuple(row[k] for k in KEYS)].append(row)
    good, mismatches = 0, []
    for event in dispatches:
        rows = index.get(tuple(event[k] for k in KEYS), [])
        if "call_id" in event:
            rows = [
                row for row in rows
                if row.get("call_id") == event["call_id"]
            ]
        sequenced = [
            row for row in rows
            if row.get("witness_request_sequence")
            == event["request_sequence"]
        ]
        # Successful native responses carry the exact backend witness sequence.
        # Use it when present so later duplicate-transport rejections in the same
        # firing cannot make the original dispatch look ambiguous.  Unknown
        # outcomes have no delivered response and retain identity-only matching.
        if any("witness_request_sequence" in row for row in rows):
            rows = sequenced
        admitted = [r for r in rows if r.get("phase") == "admitted"]
        terminal = [r for r in rows if r.get("phase") in {"settled", "outcome_unknown", "failed"}]
        ok = len(admitted) == 1 and len(terminal) == 1
        good += int(ok)
        if not ok:
            mismatches.append({"request_sequence": event["request_sequence"],
                               "admitted_records": len(admitted), "terminal_records": len(terminal)})
    backend_keys = {tuple(e[k] for k in KEYS) for e in dispatches}
    backend_call_ids: dict[tuple, set[str]] = defaultdict(set)
    for event in dispatches:
        if isinstance(event.get("call_id"), str):
            backend_call_ids[tuple(event[k] for k in KEYS)].add(
                event["call_id"])
    backend_sequences = {
        event["request_sequence"] for event in dispatches
    }
    unmatched_registry = [
        row for row in registry_records
        if row.get("phase") in {"settled", "outcome_unknown"}
        and (
            tuple(row[k] for k in KEYS) not in backend_keys
            or (
                backend_call_ids.get(tuple(row[k] for k in KEYS))
                and row.get("call_id") not in backend_call_ids[
                    tuple(row[k] for k in KEYS)]
            )
            or (
                row.get("witness_request_sequence") is not None
                and row["witness_request_sequence"] not in backend_sequences
            )
        )
    ]
    return {"status": "matched_supplied_owner_export" if dispatches else "no_external_dispatch", "dispatches": len(dispatches),
            "matched": good, "coverage": good / len(dispatches) if dispatches else None,
            "complete": good == len(dispatches) and not unmatched_registry,
            "mismatches": mismatches, "unmatched_registry_count": len(unmatched_registry),
            "source_authority": "must_be_checked_by_native_driver_integration"}
