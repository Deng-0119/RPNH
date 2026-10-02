"""Independent before/after business-state diagnostics; not official TCR."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import sqlite3
from .jsonio import dumps

IGNORED_OBSERVATION_TABLES = frozenset({"audit_log", "sqlite_sequence"})


def snapshot_tables(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    out = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        cursor = conn.execute(f"SELECT * FROM {quoted}")
        columns = [c[0] for c in cursor.description]
        out[table] = [dict(zip(columns, tuple(r))) for r in cursor.fetchall()]
    return out


def tables_from_file(path: Path) -> dict:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        return snapshot_tables(conn)


def diagnose(before: dict, after: dict, *, target_user: str,
             expected_item: str | None = None) -> dict:
    table = "service_catalog_requests"
    if table not in before or table not in after:
        raise ValueError("authoritative order table missing")
    def indexed(rows):
        if any(not isinstance(r.get("request_id"), str) for r in rows):
            raise ValueError("request_id missing from authoritative order state")
        result = {r["request_id"]: r for r in rows}
        if len(result) != len(rows): raise ValueError("duplicate primary key in snapshot")
        return result
    old, new = indexed(before[table]), indexed(after[table])
    added = [new[k] for k in sorted(new.keys() - old.keys())]
    removed = [old[k] for k in sorted(old.keys() - new.keys())]
    changed = [k for k in sorted(old.keys() & new.keys()) if old[k] != new[k]]
    target = [r for r in added if r.get("requested_for") == target_user]
    other = [r for r in added if r.get("requested_for") != target_user]
    changed_tables = []
    for name in sorted(set(before) | set(after)):
        if name == table or name in IGNORED_OBSERVATION_TABLES: continue
        if Counter(map(dumps, before.get(name, []))) != Counter(map(dumps, after.get(name, []))):
            changed_tables.append(name)
    quantity_one = all(type(r.get("quantity")) is int and r["quantity"] == 1 for r in target)
    return {"schema_version": "rpnh-ha/state-diff/v2", "official_score": False,
            "new_orders": added, "deleted_orders": removed, "modified_order_ids": changed,
            "target_order_delta": len(target), "unrelated_new_order_count": len(other),
            "duplicate_target_orders": max(0, len(target) - 1),
            "all_new_target_quantities_one": quantity_one if target else None,
            "expected_item": expected_item,
            "item_match": (all(r.get("item_name") == expected_item for r in target)
                           if expected_item is not None and target else None),
            "unexpected_changed_tables": changed_tables,
            "exactly_one_clean_order": (len(target) == 1 and not other and not removed
                                         and not changed and not changed_tables and quantity_one),
            "note": "Item and warranty correctness require evaluator-only fixture review; null is not a pass."}


def diagnose_case(before: dict, after: dict, *, task_id: str) -> dict:
    if task_id == "off-t2":
        return diagnose(before, after, target_user="USR-ETHAN-BROOKS")
    changes = {}
    for table in sorted(set(before) | set(after)):
        if table in IGNORED_OBSERVATION_TABLES:
            continue
        old = Counter(map(dumps, before.get(table, [])))
        new = Counter(map(dumps, after.get(table, [])))
        if old != new:
            changes[table] = {
                "before_rows": sum(old.values()), "after_rows": sum(new.values()),
                "added_row_versions": sum((new - old).values()),
                "removed_row_versions": sum((old - new).values()),
            }
    return {"schema_version": "rpnh-ha/office-state-diff/v1", "task_id": task_id,
            "official_score": False, "business_task_success": None,
            "changed_tables": changes,
            "note": "Changed rows are diagnostics, not proof of task success; use the original task scorer."}
