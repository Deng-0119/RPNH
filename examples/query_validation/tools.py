"""Actual SQLite reads. These observations are not the upstream verifier."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import sqlite3
import tempfile
import time

PINNED_DATABASE_SHA256 = "89eab7555b6b86a74b8e0ee5a7db7beec0b2575a45cd2b22fe132eae79f5ad5a"
PINNED_ORIGINAL_SQL_SHA256 = "43382792f0f438625d643e34abe144febd5669518f4ab1ab550a15c6d9314a21"
MAX_ROWS = 10000  # Observation bound, never an alteration to the original grader.


@dataclass(frozen=True)
class ToolResult:
    products: dict
    outcome: str = "complete"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sql_value(sql):
    return {"sql": sql, "sha256": sha256(sql.encode("utf-8"))}


def candidate_format(sql):
    """Reject comments and multiple statements without mistaking quoted tokens."""
    if len(sql.strip()) > 2000:
        raise ValueError("candidate exceeds the upstream 2000-character limit")
    i, quote, semicolons = 0, None, 0
    while i < len(sql):
        c = sql[i]
        if quote:
            if c == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in "'\"`[":
            quote = "]" if c == "[" else c
        elif sql[i:i + 2] in ("--", "/*"):
            raise ValueError("candidate must contain no SQL comments")
        elif c == ";":
            semicolons += 1
        i += 1
    if quote or semicolons != 1 or not sql.rstrip().endswith(";"):
        raise ValueError("candidate must be one semicolon-terminated statement")
    if sql.lstrip().split(None, 1)[0].upper() not in {"SELECT", "WITH"}:
        raise ValueError("candidate must be SELECT or WITH")


@contextmanager
def snapshot(database):
    """Read one opened source into a digest-checked, private immutable snapshot.

    The registered descriptor records an exact content hash, not a claim that an
    ambient path is a Registry blob. SQLite never executes against the source.
    """
    if database["condition"] == "tb2_pinned_database" and database["sha256"] != PINNED_DATABASE_SHA256:
        raise ValueError("database does not match the pinned upstream verifier hash")
    with tempfile.TemporaryDirectory(prefix="rpnh-sql-snapshot-") as raw:
        target = Path(raw) / "input.sqlite"
        digest = hashlib.sha256()
        with Path(database["path"]).open("rb") as source, target.open("wb") as dest:
            while block := source.read(1024 * 1024):
                digest.update(block)
                dest.write(block)
        if digest.hexdigest() != database["sha256"]:
            raise ValueError("external database bytes changed after registration")
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
            def authorize(action, first, second, db, trigger):
                if action == sqlite3.SQLITE_FUNCTION and str(second).lower() == "load_extension":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
            connection.set_authorizer(authorize)
            yield connection
        finally:
            connection.close()


def product(stage, inputs, rules, data):
    return {"stage": stage, "rules_id": rules["id"],
            "parents": [item["ref"] for item in inputs.values()], "data": data}


async def inspect_inputs(*, inputs, rules):
    database = inputs["database"]["value"]
    original, candidate = (inputs[key]["value"] for key in ("original_sql", "candidate_sql"))
    for value in (original, candidate):
        if sha256(value["sql"].encode("utf-8")) != value["sha256"]:
            raise ValueError("SQL bytes differ from their registered digest")
    if (database["condition"] == "tb2_pinned_database"
            and original["sha256"] != PINNED_ORIGINAL_SQL_SHA256):
        raise ValueError("original SQL differs from the pinned upstream task input")
    candidate_format(candidate["sql"])
    with snapshot(database) as con:
        schema = con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
    data = {"database": database, "original_sql": original, "candidate_sql": candidate,
            "database_schema": schema, "sqlite_version": sqlite3.sqlite_version,
            "input_refs": {key: item["ref"] for key, item in inputs.items()}}
    return ToolResult({"request": product("request", inputs, rules, data)})


def execute(con, sql, timeout):
    deadline = time.perf_counter() + timeout
    con.set_progress_handler(lambda: int(time.perf_counter() >= deadline), 1000)
    try:
        plan = con.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        start = time.perf_counter()
        cur = con.execute(sql)
        if cur.description is None:
            raise ValueError("statement did not produce a query result")
        columns = [item[0] for item in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise ValueError("observation row bound exceeded; no truncated success")
        # The selected task returns integers/text. Refuse lossy JSON encoding.
        if any(type(value) not in (type(None), int, float, str)
               or (type(value) is float and not math.isfinite(value))
               for row in rows for value in row):
            raise ValueError("non-JSON SQLite value requires a separate typed adapter")
        return {"columns": columns, "rows": rows, "row_count": len(rows),
                "query_plan": plan, "elapsed_s": time.perf_counter() - start}
    finally:
        con.set_progress_handler(None, 0)


async def execute_queries(*, inputs, rules):
    request = inputs["request"]["value"]["data"]
    results = {}
    # Each query gets an independently opened snapshot, so no shared temporary
    # tables/session state and no implied fair performance benchmark.
    for role in ("original", "candidate"):
        with snapshot(request["database"]) as con:
            observation = execute(con, request[role + "_sql"]["sql"], request["database"]["query_timeout_s"])
        data = {**observation, "database_sha256": request["database"]["sha256"],
                "condition": request["database"]["condition"],
                "sql_sha256": request[role + "_sql"]["sha256"],
                "sql_input_ref": request["input_refs"][role + "_sql"],
                "database_input_ref": request["input_refs"]["database"]}
        if role == "candidate":
            data["candidate_sql"] = request["candidate_sql"]
        results[role + "_result"] = product(role + "_result", inputs, rules, data)
    return ToolResult(results)


async def compare_results(*, inputs, rules):
    original, candidate = (inputs[key]["value"]["data"] for key in ("original_result", "candidate_result"))
    if original["database_sha256"] != candidate["database_sha256"] or original["condition"] != candidate["condition"]:
        raise ValueError("result resources refer to different database conditions")
    equal = original["columns"] == candidate["columns"] and original["rows"] == candidate["rows"]
    data = {"equivalent_to_original": equal, "comparison": "exact_columns_and_ordered_rows",
            "condition": original["condition"], "database_sha256": original["database_sha256"],
            "original_row_count": original["row_count"], "candidate_row_count": candidate["row_count"],
            "result_refs": {key: item["ref"] for key, item in inputs.items()},
            "original_verifier": "not_run", "benchmark_pass": None,
            "model_calls": 0, "timing_scope": "single_observation_not_benchmark"}
    final_sql = {**candidate["candidate_sql"], "equivalent_to_original": equal,
                 "candidate_result_ref": inputs["candidate_result"]["ref"],
                 "original_verifier": "not_run", "benchmark_pass": None}
    return ToolResult({"report": product("report", inputs, rules, data),
                       "final_sql": product("final_sql", inputs, rules, final_sql)})


TOOLS = {function.__name__: function for function in (inspect_inputs, execute_queries, compare_results)}
