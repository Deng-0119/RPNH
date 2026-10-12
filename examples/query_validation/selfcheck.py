"""Bounded offline wiring and read-only checks, explicitly not Terminal-Bench."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys

from .contracts import RULES
from .run import database_value, readback, run_observation, save_evidence
from .tools import candidate_format, compare_results, execute, snapshot, sql_value

ORIGINAL = "SELECT id, label FROM items ORDER BY id;"
CANDIDATE = "WITH selected AS (SELECT id, label FROM items) SELECT id, label FROM selected ORDER BY id;"
LOOP_KWARGS = {}


def fixture(path):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE items(id INTEGER, label TEXT)")
        con.executemany("INSERT INTO items VALUES (?, ?)", [(2, "second"), (1, "first"), (1, "first")])


class Checks(unittest.TestCase):
    def test_read_only_authorizer_and_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "fixture.sqlite"
            fixture(path)
            database = database_value(path, condition="synthetic_wiring_smoke")
            before = path.read_bytes()
            forbidden = [
                "ATTACH DATABASE 'other.sqlite' AS other;", "DETACH DATABASE main;",
                "PRAGMA journal_mode=WAL;", "PRAGMA writable_schema=ON;",
                "WITH x AS (SELECT 1) DELETE FROM items;", "SELECT load_extension('missing');",
                "VACUUM INTO 'other.sqlite';", "CREATE TEMP TABLE hacked(x);",
                "INSERT INTO items VALUES (3, 'x');", "DROP TABLE items;",
            ]
            with snapshot(database) as con:
                for sql in forbidden:
                    with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                        con.execute(sql)
                self.assertEqual(con.execute("SELECT count(*) FROM items").fetchone(), (3,))
                with self.assertRaisesRegex(ValueError, "non-JSON"):
                    execute(con, "SELECT 1e999;", 1)
            self.assertEqual(before, path.read_bytes())
            path.write_bytes(before + b"changed")
            with self.assertRaisesRegex(ValueError, "changed after registration"), snapshot(database):
                pass

    def test_candidate_format(self):
        candidate_format("SELECT ';--/*' AS literal;")
        for sql in ("SELECT 1; SELECT 2;", "SELECT 1; -- comment", "DELETE FROM items;", "SELECT 1"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                candidate_format(sql)

    def test_exact_order_columns_and_duplicates(self):
        base = {"database_sha256": "a" * 64, "condition": "synthetic_wiring_smoke", "columns": ["id"], "rows": [[1], [1], [2]], "row_count": 3, "candidate_sql": sql_value(CANDIDATE)}
        def check(candidate):
            inputs = {key: {"ref": {"resource_id": key, "resource_version_id": key + "-v1"}, "value": {"data": value}}
                      for key, value in (("original_result", base), ("candidate_result", candidate))}
            return asyncio.run(compare_results(inputs=inputs, rules=RULES)).products["report"]["data"]
        self.assertTrue(check(dict(base))["equivalent_to_original"])
        for candidate in ({**base, "columns": ["ID"]}, {**base, "rows": [[2], [1], [1]]}, {**base, "rows": [[1], [2]], "row_count": 2}):
            self.assertFalse(check(candidate)["equivalent_to_original"])

    def test_registry_terminal_provenance_and_readback(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "fixture.sqlite"
            fixture(path)
            database = database_value(path, condition="synthetic_wiring_smoke")
            # Keep the owner socket within platform AF_UNIX path bounds.
            evidence = run_observation(root / "run", database=database, original_sql=ORIGINAL, candidate_sql=CANDIDATE, **LOOP_KWARGS)
            self.assertEqual(len(evidence["firings"]), 3)
            self.assertEqual(evidence["actual_model_call_counts"], [0, 0])
            self.assertEqual(evidence["terminal_evidence"][-1]["run_outcome"], "complete")
            report = evidence["final"]["data"]
            self.assertTrue(report["equivalent_to_original"])
            self.assertIsNone(report["benchmark_pass"])
            self.assertEqual(report["original_verifier"], "not_run")
            resources = {item["ref"]["resource_version_id"]: item for item in evidence["resources"]}
            self.assertEqual(len(resources), 5)
            final_sql = evidence["final_sql_resource"]
            self.assertEqual(final_sql["value"]["data"]["sql"], CANDIDATE)
            self.assertEqual(final_sql["value"]["data"]["sha256"], sql_value(CANDIDATE)["sha256"])
            for ref in report["result_refs"].values():
                self.assertIn(ref["resource_version_id"], resources)
            for item in resources.values():
                parents = item["metadata"]["reference_provenance"]["derived_from_refs"]
                self.assertEqual({ref["version_id"] for ref in parents},
                                 {ref["resource_version_id"] for ref in item["value"]["parents"]})
            final_resource = next(item for item in resources.values() if item["value"]["stage"] == "report")
            self.assertEqual({p["resource_version_id"] for p in final_resource["value"]["parents"]},
                             {ref["resource_version_id"] for ref in report["result_refs"].values()})
            again = readback(root / "run")
            self.assertEqual(again["final"], evidence["final"])
            self.assertEqual(again["verified_head_ordinal"], evidence["verified_head_ordinal"])
            self.assertEqual(again["dashboard"]["source"]["mode"], "registry_current")
            save_evidence(again, root / "export")
            exported = json.loads((root / "export/evidence.json").read_text())
            self.assertEqual(exported["final"], evidence["final"])
            self.assertEqual((root / "export/sol.sql").read_text(), CANDIDATE)

    def test_mismatch_export_and_readback_without_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "fixture.sqlite"
            fixture(path)
            evidence = run_observation(root / "run",
                database=database_value(path, condition="synthetic_wiring_smoke"),
                original_sql=ORIGINAL, candidate_sql="SELECT id, label FROM items ORDER BY id DESC;",
                **LOOP_KWARGS)
            self.assertEqual(evidence["terminal_evidence"][-1]["run_outcome"], "complete")
            self.assertFalse(evidence["final"]["data"]["equivalent_to_original"])
            path.unlink()
            again = readback(root / "run")
            self.assertEqual(again["final"], evidence["final"])
            save_evidence(again, root / "export")
            self.assertFalse(json.loads((root / "export/report.json").read_text())["data"]["equivalent_to_original"])


def export_smoke(run_root, output, **loop_kwargs):
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=False)
    path = run_root / "fixture.sqlite"
    fixture(path)
    evidence = run_observation(run_root / "run", database=database_value(path, condition="synthetic_wiring_smoke"),
                               original_sql=ORIGINAL, candidate_sql=CANDIDATE, **loop_kwargs)
    save_evidence(evidence, output)
    return evidence


if __name__ == "__main__":
    if "--pipe-transport" in sys.argv:
        # Explicit existing repository test fixture, never an automatic retry.
        from examples.tool_pipeline.tests.pipe_transport import PipeOwnerEventLoop
        LOOP_KWARGS["event_loop_factory"] = PipeOwnerEventLoop
        sys.argv.remove("--pipe-transport")
    unittest.main()
