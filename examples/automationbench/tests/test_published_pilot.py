"""Checks for the sanitized published pilot; no provider calls or task replay."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from rpnh_ab.upstream import normalize_api_fetch_arguments


ROOT = Path(__file__).resolve().parents[1]

HISTORICAL_SHA256 = {
    "conditions-20261002.json": "cf640896fa90712242831f91129e97771418dce9403e512881ecef3ac9b30bb1",
    "operations-0009-remediation-20261002.json": "89b7023928ffee28c9a63a1aba4acbb138f5f8ae446fa25dfdda17c9131ebfe8",
    "stratified-pilot-first-attempt-20261002.json": "31d87b37c5475257a8c7d3d918c11363c180d912d09aaef0b5ddeae11f66767b",
    "stratified-pilot-plan-20261002.json": "7f99846bd6247987dd9030342beaa9c0cd70ac675da35dcb30e9abd056f68f11",
}


def load(name: str) -> dict:
    return json.loads((ROOT / "results" / name).read_text(encoding="utf-8"))


def test_historical_result_bytes_are_immutable() -> None:
    for name, expected in HISTORICAL_SHA256.items():
        assert hashlib.sha256((ROOT / "results" / name).read_bytes()).hexdigest() == expected


def test_strict_first_attempt_summary_is_complete_and_not_public_600() -> None:
    result = load("stratified-pilot-first-attempt-20261002.json")
    assert result["planned"] == result["finished"] == 18
    assert result["infrastructure_successes"] == 17
    assert result["benchmark_task_successes"] == 8
    assert result["actual_model_calls"] == 437
    assert result["successful_tool_dispatches"] == 1081
    assert result["full_600_claimed"] is False
    assert len(result["results"]) == len({row["task_id"] for row in result["results"]}) == 18
    scored = [row for row in result["results"] if row["partial_credit"] is not None]
    assert len(scored) == 17
    assert sum(row["partial_credit"] for row in scored) / len(scored) == 0.8877005347593583

    conditions = load("conditions-20261002.json")
    assert conditions["executor"]["model_label"] == "deepseek-v4-pro"
    assert conditions["upstream"]["commit"] == "4a8e1061254004d9dac807054eed33fad7d1ff14"
    assert conditions["raw_registry_and_provider_evidence_public"] is False
    assert conditions["full_600_claimed"] is False


def test_plan_is_score_blind_and_covers_all_eighteen_strata() -> None:
    plan = load("stratified-pilot-plan-20261002.json")
    assert plan["source_task_count"] == 600
    assert plan["sampling"]["score_blind"] is True
    assert plan["sampling"]["sample_count"] == 18
    cells = {(row["domain"], row["integration_breadth"]) for row in plan["selected_tasks"]}
    assert len(cells) == 18
    assert {row["domain"] for row in plan["selected_tasks"]} == {
        "finance", "hr", "marketing", "operations", "sales", "support"
    }
    assert {row["integration_breadth"] for row in plan["selected_tasks"]} == {
        "focused", "standard", "broad"
    }


def test_remediation_remains_separate_from_first_attempt() -> None:
    first = load("stratified-pilot-first-attempt-20261002.json")
    remediation = load("operations-0009-remediation-20261002.json")
    original = next(row for row in first["results"] if row["task_id"] == "operations-0009")
    assert original["infrastructure_success"] is False
    assert original["task_completed_correctly"] is None
    assert remediation["infrastructure_success"] is True
    assert remediation["task_completed_correctly"] == 1.0
    assert remediation["actual_model_calls"] == 11
    assert remediation["successful_tool_dispatches"] == 26


def test_string_null_and_trello_scalar_compatibility_are_narrow() -> None:
    original = {
        "method": "POST",
        "url": "https://api.trello.com/1/cards/card_455/idLabels",
        "params": "null",
        "body": '"lbl_vendor_hold"',
    }
    normalized, changed, rules = normalize_api_fetch_arguments(original)
    assert normalized["params"] is None
    assert json.loads(normalized["body"]) == {"value": "lbl_vendor_hold"}
    assert changed == ("params", "body")
    assert rules == (
        "json-string-null-to-native-no-value",
        "trello-add-label-json-scalar-to-value-object",
    )
    assert original["params"] == "null"
    assert original["body"] == '"lbl_vendor_hold"'

    unrelated = dict(original, url="https://api.trello.com/1/cards/card_455/actions/comments")
    normalized, changed, rules = normalize_api_fetch_arguments(unrelated)
    assert normalized["params"] is None
    assert normalized["body"] == '"lbl_vendor_hold"'
    assert changed == ("params",)
    assert rules == ("json-string-null-to-native-no-value",)


def test_retained_result_is_inspectable_without_install_or_writes(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "example.py"), "results", "--json"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["strict_passes"] == 8
    assert payload["planned"] == 18
    assert list(tmp_path.iterdir()) == []


def test_public_pages_keep_the_scope_and_comparison_boundaries() -> None:
    for name in ("README.md", "README_ZH.md", "RESULTS.md", "RESULTS_ZH.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "18" in text
        assert "600" in text
    for name in ("COMPARISON.md", "COMPARISON_ZH.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "45.83" in text
        assert "per-task" in text or "逐题" in text
        assert "matched" in text or "同条件" in text


def test_public_reference_rows_and_markdown_links() -> None:
    references = json.loads((ROOT / "config/public_references.json").read_text(encoding="utf-8"))
    assert len(references["rows"]) == 10
    assert references["published_per_task_records_in_pinned_tree"] is False
    assert references["local_executor_label_in_table"] is False
    assert next(row for row in references["rows"] if row["model"] == "GPT-5.6 Sol")["pass_rate"] == 0.4583

    for page in ROOT.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            target = (page.parent / link.split("#", 1)[0]).resolve()
            assert target.exists(), (page.relative_to(ROOT), link)
