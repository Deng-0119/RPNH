"""Independent result check against original input, not driver-produced flags.

This verifier deliberately does not import scripted_model or its calculations.
It does not establish cognition, remote identity or unvisited execution paths.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path


def _same_json(left, right):
    # Python equality treats False == 0 and 12.0 == 12. The declared JSON
    # contracts require integer indices/counts/minutes and boolean checks.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def verify_result(inputs, final):
    report = json.loads(final) if isinstance(final, str) else final
    assert report["schema"] == "agent_cobuild_report/v1"
    rows = inputs["records"]
    assert all(type(row["minutes"]) is int and row["minutes"] >= 0 for row in rows)
    assert len({row["id"] for row in rows}) == len(rows)
    categories = sorted({row["category"] for row in rows})
    expected = {"record_count": len(rows), "total_minutes": sum(row["minutes"] for row in rows),
        "by_category": [{"category": category,
            "record_count": sum(row["category"] == category for row in rows),
            "total_minutes": sum(row["minutes"] for row in rows if row["category"] == category)}
            for category in categories]}
    assert _same_json(report["totals"], expected)
    assert report["lineage_retained"] is True and report["source_reconciliation_required"] is True
    observed = report["source_reconciliation"]
    assert len(observed) == len(rows)
    assert Counter(item["source_id"] for item in observed) == Counter(row["id"] for row in rows)
    for index, (row, item) in enumerate(zip(rows, observed)):
        assert _same_json(item, {"source_id": row["id"], "source_index": index,
                        "category": row["category"], "minutes": row["minutes"]})
    assert _same_json(report["count_checks"], {"input_record_count": len(rows),
        "normalized_record_count": len(rows), "reconciled_source_count": len(rows),
        "reported_record_count": len(rows), "counts_match": True})
    assert _same_json(report["checks"], {"input_count_matches": True, "record_count_matches": True,
        "category_totals_match": True, "total_minutes_match": True,
        "unique_source_ids": True, "source_indices_complete": True})
    return {"status": "PASS", "verified_against": "original_input_not_report_flags",
            "record_count": len(rows), "source_ids": [row["id"] for row in rows],
            "totals": expected, "claim": "deterministic_result_and_contract_compliance_only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify_result(json.loads(args.input.read_text()), json.loads(args.result.read_text())), indent=2))


if __name__ == "__main__":
    main()
