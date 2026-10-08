"""Original report shapes with synthetic numbers, never an ERP gold plan."""
import copy
from pathlib import Path
import tempfile
import unittest

from rpnh_erp_bench.scoring import parse_original_score, _rule_summary, _rule_rows
from rpnh_erp_bench.source import TASK_IDS, json_bytes, sha256


def reports(root, task_id=TASK_IDS[0], score=12.5):
    tsv = b"constraint\tfixture_rule item 7\tPASS\nconstraint\tfixture_rule item 8\tFAIL\nhygiene\tfixture_link\tNA\n"
    opt = {"objective_kind": "constraint_only" if task_id == TASK_IDS[0] else "repair_plan",
           "primary_actual": None if task_id == TASK_IDS[1] else 0.0,
           "primary_expected": 0.0, "primary_score": 73.0, "optimality_score": 73.0}
    if task_id == TASK_IDS[1]:
        opt.update(secondary_actual_spend=18.0, secondary_expected_spend=17.0, secondary_score=91.0)
    spend = {"total_new_spend": 18.0, "expected_spend": 17.0, "spend_delta": 1.0,
             "spend_delta_abs": 1.0, "spend_score": 91.0, "optimality_score": None,
             "verified_spend_complete": True, "finished_goods_spend": 18.0,
             "component_spend": 0.0, "assembly_spend": 0.0, "other_spend": 0.0}
    reward = {"overall_score": score, "passed": score >= 99,
              "constraint": {"earned": 1, "total": 2}, "hygiene": {"earned": 0, "total": 0},
              "optimality": {"score": 73.0, "total": 100.0},
              "rules": _rule_summary(_rule_rows(tsv)), "spend": spend, "optimality_detail": opt}
    raw = {"reward.txt": f"{score:.2f}\n".encode(), "reward.json": json_bytes(reward),
           "rule_results.tsv": tsv, "spend.json": json_bytes(spend), "optimality.json": json_bytes(opt)}
    for name, data in raw.items():
        (root / name).write_bytes(data)
    return reward


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_unrun_is_none_not_zero(self):
        self.assertIsNone(parse_original_score(self.root, TASK_IDS[0], verifier_exit_code=0))

    def test_both_formats_preserve_names_denominators_na(self):
        for tid in TASK_IDS:
            with self.subTest(task=tid):
                reports(self.root, tid)
                result = parse_original_score(self.root, tid, verifier_exit_code=0)
                self.assertEqual(result.overall_score, 12.5)
                self.assertFalse(result.passed)
                self.assertEqual(result.components()["constraint.total"], 2)
                self.assertEqual(result.components()["hygiene.total"], 0)
                self.assertEqual(result.components()["rules.not_applicable"], 1)
                self.assertIsNone(result.components()["spend.optimality_score"])
                self.assertEqual(result.public_projection()["reward"]["rules"]["rows"][-1]["status"], "NA")
                self.assertNotIn("args", result.public_projection()["reward"]["rules"]["rows"][0])
                self.assertTrue(result.public_projection()["changed_bytes"])
                self.assertEqual(result.raw_digests["reward.json"]["sha256"], sha256((self.root/"reward.json").read_bytes()))

    def test_zero_exit_is_not_business_pass(self):
        reports(self.root, score=0)
        result = parse_original_score(self.root, TASK_IDS[0], verifier_exit_code=0)
        self.assertEqual(result.overall_score, 0)
        self.assertFalse(result.passed)

    def test_emitted_threshold_99(self):
        for number, passed in ((98.99, False), (99, True), (100, True)):
            reports(self.root, score=number)
            self.assertEqual(parse_original_score(self.root, TASK_IDS[0]).passed, passed)

    def test_nonzero_exit_rejected(self):
        reports(self.root)
        with self.assertRaisesRegex(ValueError, "did not complete"):
            parse_original_score(self.root, TASK_IDS[0], verifier_exit_code=7)

    def test_partial_report_rejected(self):
        (self.root/"reward.txt").write_text("0\n")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_original_score(self.root, TASK_IDS[0])

    def test_stale_detail_rejected(self):
        reports(self.root)
        (self.root/"spend.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "stale"):
            parse_original_score(self.root, TASK_IDS[0])

    def test_tsv_and_json_must_agree(self):
        reports(self.root)
        p = self.root/"rule_results.tsv"
        p.write_bytes(p.read_bytes().replace(b"NA", b"PASS"))
        with self.assertRaisesRegex(ValueError, "summary differs"):
            parse_original_score(self.root, TASK_IDS[0])

    def test_malformed_metrics_rejected(self):
        mutations = [lambda r: r.update(passed=True),
                     lambda r: r["constraint"].update(earned=3),
                     lambda r: r["hygiene"].update(total=1),
                     lambda r: r["optimality"].update(total=1),
                     lambda r: r.update(overall_score=True)]
        for mutate in mutations:
            reward = reports(self.root)
            mutate(reward)
            (self.root/"reward.json").write_bytes(json_bytes(reward))
            with self.assertRaises(ValueError):
                parse_original_score(self.root, TASK_IDS[0])

    def test_nonfinite_duplicate_and_infinite_text(self):
        for data in (b'{"overall_score":NaN}', b'{"overall_score":1e999}', b'{"x":1,"x":2}'):
            reports(self.root)
            (self.root/"reward.json").write_bytes(data)
            with self.assertRaises(ValueError):
                parse_original_score(self.root, TASK_IDS[0])
        reports(self.root)
        (self.root/"reward.txt").write_text("inf")
        with self.assertRaises(ValueError):
            parse_original_score(self.root, TASK_IDS[0])

    def test_optional_reports_absent_not_fabricated(self):
        reward = reports(self.root)
        for file, key in (("spend.json", "spend"), ("optimality.json", "optimality_detail")):
            (self.root/file).unlink()
            del reward[key]
        (self.root/"reward.json").write_bytes(json_bytes(reward))
        result = parse_original_score(self.root, TASK_IDS[0])
        self.assertIsNone(result.spend)
        self.assertNotIn("spend.total_new_spend", result.components())


if __name__ == "__main__":
    unittest.main()
