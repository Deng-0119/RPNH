"""Read original verifier reports; never run the grader or recompute its score."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re

from .source import TASK_IDS, UPSTREAM_COMMIT, json_bytes, load_json, safe_file, sha256

RAW_NAMES = ("reward.txt", "reward.json", "rule_results.tsv", "optimality.json", "spend.json")
SPEND_KEYS = frozenset(("total_new_spend", "expected_spend", "spend_delta", "spend_delta_abs",
                        "spend_score", "optimality_score", "finished_goods_spend",
                        "component_spend", "assembly_spend", "other_spend"))
OPTIMALITY_KEYS = frozenset(("primary_actual", "primary_expected", "primary_score",
                            "secondary_actual_spend", "secondary_expected_spend",
                            "secondary_score", "optimality_score"))


def _number(value, name, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"invalid numeric metric: {name}")


def _rule_rows(data):
    rows = []
    for line in data.decode("utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError("malformed original rule_results.tsv")
        dim, expr, status = parts
        tokens = expr.split()
        if dim not in ("constraint", "hygiene") or status not in ("PASS", "FAIL", "NA"):
            raise ValueError("invalid rule dimension/status")
        if not tokens or not re.fullmatch(r"[a-z][a-z0-9_]*", tokens[0]):
            raise ValueError("invalid original rule name")
        rows.append({"dimension": dim, "rule": tokens[0], "args": tokens[1:], "expr": expr,
                     "status": status, "passed": status == "PASS", "applicable": status != "NA"})
    return rows


def _rule_summary(rows):
    dimensions = {}
    for row in rows:
        dimensions.setdefault(row["dimension"], []).append(row)
    return {"total": len(rows), "applicable": sum(row["applicable"] for row in rows),
            "passed": sum(row["status"] == "PASS" for row in rows),
            "failed": sum(row["status"] == "FAIL" for row in rows),
            "not_applicable": sum(row["status"] == "NA" for row in rows),
            "by_dimension": dimensions,
            "failed_rules": [row for row in rows if row["status"] == "FAIL"]}


@dataclass(frozen=True)
class OriginalScore:
    task_id: str
    reward: dict
    raw_digests: dict
    rules: list
    optimality: dict | None
    spend: dict | None
    verifier_exit_code: int | None
    _verifier_root: Path = field(repr=False)

    def verify_raw_reports(self) -> None:
        """Detect stale result objects before publishing their original digests."""
        for name, identity in self.raw_digests.items():
            data = safe_file(self._verifier_root, name).read_bytes()
            if sha256(data) != identity["sha256"] or len(data) != identity["bytes"]:
                raise ValueError("original report changed after parsing")

    @property
    def overall_score(self):
        return self.reward["overall_score"]

    @property
    def passed(self):
        return self.reward["passed"]

    def components(self) -> dict:
        """Exact emitted numeric names, qualified by their original JSON parent.

        No 0–1 normalization; booleans and NA statuses live in the projection.
        An absent optional report is absent, never converted to a zero score.
        """
        result = {"overall_score": self.overall_score}
        for parent in ("constraint", "hygiene", "optimality"):
            result.update({f"{parent}.{k}": v for k, v in self.reward[parent].items()})
        result.update({f"rules.{k}": self.reward["rules"][k]
                       for k in ("total", "applicable", "passed", "failed", "not_applicable")})
        for parent, payload, keys in (("optimality_detail", self.optimality, OPTIMALITY_KEYS),
                                      ("spend", self.spend, SPEND_KEYS)):
            if payload is not None:
                result.update({f"{parent}.{k}": v for k, v in payload.items() if k in keys})
        return result

    def public_projection(self) -> dict:
        """Faithful allowlisted metrics; original args/expressions stay private."""
        reward = {key: self.reward[key] for key in
                  ("overall_score", "passed", "constraint", "hygiene", "optimality")}
        reward["rules"] = {key: self.reward["rules"][key] for key in
                           ("total", "applicable", "passed", "failed", "not_applicable")}
        reward["rules"]["rows"] = [{key: row[key] for key in
                                    ("dimension", "rule", "status", "passed", "applicable")}
                                   for row in self.rules]
        if self.optimality is not None:
            reward["optimality_detail"] = {k: v for k, v in self.optimality.items()
                                            if k in OPTIMALITY_KEYS or k == "objective_kind"}
        if self.spend is not None:
            reward["spend"] = {k: v for k, v in self.spend.items()
                               if k in SPEND_KEYS or k == "verified_spend_complete"}
        return {"schema_version": "rpnh/erp-original-score-projection/v1",
                "task_id": self.task_id, "scorer_revision": UPSTREAM_COMMIT,
                "score_scale": "0-100", "passed_threshold": 99,
                "original_reports": self.raw_digests,
                "verifier_exit_code": self.verifier_exit_code,
                "changed_bytes": True,
                "projection_scope": "Allowlisted emitted metrics and rule statuses; original bytes retained privately.",
                "omitted_fields": ["rules.args", "rules.expr", "unrecognized report fields"],
                "components": self.components(), "reward": reward}

    def score_record(self, *, claim: str, evidence: list[str]) -> dict:
        if claim not in ("benchmark_result", "grader_compatibility"):
            raise ValueError("original score cannot be relabeled as a supplementary metric")
        return {"claim": claim, "scorer_id": f"erp-bench/{self.task_id}/tests/test.sh",
                "scorer_revision": UPSTREAM_COMMIT, "components": self.components(),
                "evidence": list(evidence)}


def parse_original_score(verifier_dir, task_id: str, *, verifier_exit_code=None) -> OriginalScore | None:
    if task_id not in TASK_IDS:
        raise ValueError("unsupported task ID")
    if verifier_exit_code is not None and type(verifier_exit_code) is not int:
        raise ValueError("verifier exit code must be integer or unknown")
    root = Path(verifier_dir)
    existing = [name for name in RAW_NAMES if (root / name).exists() or (root / name).is_symlink()]
    if not existing:
        return None
    if verifier_exit_code not in (None, 0):
        raise ValueError("verifier did not complete successfully; retain raw reports as failure evidence")
    if not set(RAW_NAMES[:3]) <= set(existing):
        raise ValueError("incomplete original verifier reports")
    raw = {name: safe_file(root, name).read_bytes() for name in existing}
    reward = load_json(raw["reward.json"])
    if not isinstance(reward, dict):
        raise ValueError("reward must be an object")
    for key in ("overall_score", "passed", "constraint", "hygiene", "optimality", "rules"):
        if key not in reward:
            raise ValueError(f"missing emitted reward field: {key}")
    _number(reward["overall_score"], "overall_score")
    try:
        txt_score = float(raw["reward.txt"].decode().strip())
    except (ValueError, UnicodeError) as exc:
        raise ValueError("malformed reward.txt") from exc
    if not math.isfinite(txt_score) or txt_score != reward["overall_score"] or not 0 <= txt_score <= 100:
        raise ValueError("reward text/JSON mismatch or out-of-range score")
    if type(reward["passed"]) is not bool or reward["passed"] != (txt_score >= 99):
        raise ValueError("original passed threshold mismatch")
    for dim in ("constraint", "hygiene"):
        row = reward[dim]
        if (not isinstance(row, dict) or set(row) != {"earned", "total"}
                or any(type(row[k]) is not int for k in row)
                or not 0 <= row["earned"] <= row["total"]):
            raise ValueError("invalid original earned/total denominator")
    opt = reward["optimality"]
    if not isinstance(opt, dict) or set(opt) != {"score", "total"}:
        raise ValueError("invalid optimality dimension")
    for key, value in opt.items():
        _number(value, key)
    if not 0 <= opt["score"] <= 100 or opt["total"] != 100:
        raise ValueError("invalid original optimality scale")
    rows = _rule_rows(raw["rule_results.tsv"])
    if json_bytes(reward["rules"]) != json_bytes(_rule_summary(rows)):
        raise ValueError("reward rule summary differs from original TSV")
    for dim in ("constraint", "hygiene"):
        applicable = sum(r["dimension"] == dim and r["applicable"] for r in rows)
        if reward[dim]["total"] != applicable:
            raise ValueError("NA/applicable rule denominator mismatch")
    details = {}
    for filename, key, numeric in (("optimality.json", "optimality_detail", OPTIMALITY_KEYS),
                                    ("spend.json", "spend", SPEND_KEYS)):
        payload = load_json(raw[filename]) if filename in raw else None
        if filename in raw and not isinstance(payload, dict):
            raise ValueError("invalid original detail object")
        if payload != reward.get(key):
            raise ValueError(f"stale or inconsistent original report: {filename}")
        if payload is not None:
            for name in numeric & payload.keys():
                _number(payload[name], name, nullable=True)
        details[key] = payload
    optimality = details["optimality_detail"]
    if optimality is not None:
        required = {"objective_kind", "primary_actual", "primary_expected", "primary_score", "optimality_score"}
        if task_id == TASK_IDS[1]:
            required |= {"secondary_actual_spend", "secondary_expected_spend", "secondary_score"}
        if not required <= optimality.keys():
            raise ValueError("incomplete original optimality report")
        expected_kind = "constraint_only" if task_id == TASK_IDS[0] else "repair_plan"
        if optimality.get("objective_kind") != expected_kind or optimality.get("optimality_score") != opt["score"]:
            raise ValueError("optimality task/score identity mismatch")
    spend = details["spend"]
    if spend is not None:
        if not SPEND_KEYS <= spend.keys() or type(spend.get("verified_spend_complete")) is not bool:
            raise ValueError("incomplete spend report or invalid completeness flag")
    digests = {name: {"sha256": sha256(data), "bytes": len(data),
                      "hash_scope": "retained_private_bytes"} for name, data in raw.items()}
    return OriginalScore(task_id, reward, digests, rows, optimality, spend, verifier_exit_code, root.resolve())
