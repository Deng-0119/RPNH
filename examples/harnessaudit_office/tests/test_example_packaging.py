"""Offline packaging checks. No real Registry, provider or benchmark execution."""
from pathlib import Path
import ast
import csv
import importlib.util
import json
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("office_example_entry", ROOT / "example.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def test_fifteen_scores_keep_two_conditions_and_projection_identity():
    rows = entry.history_rows()
    assert len(rows) == 15
    assert len({r["condition_id"] for r in rows}) == 2
    assert len({r["projection_revision"] for r in rows}) == 2
    assert len({r["score_run_id"] for r in rows}) == 15
    assert {r["task_id"] for r in rows} == set(entry.TASKS)


def test_grouping_never_pools_original_and_supplement():
    groups = entry.result_groups(entry.history_rows())
    assert len(groups) == 6
    assert sum(g["n"] for g in groups) == 15
    onboarding = [g for g in groups if g["task_id"] == "off-t1"]
    assert sorted(g["n"] for g in onboarding) == [1, 2]


def test_twenty_attempts_retain_unscored_truncations():
    with (ROOT / "results/attempts.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 20
    assert sum(r["included_in_final_scores"] == "true" for r in rows) == 15
    truncated = [r for r in rows if r["stop_reason"] == "task_model_call_cap"]
    assert len(truncated) == 5
    assert all(r["tcr"] == "" for r in truncated)


def arguments(output):
    return ["configure", "--task-id", "off-t6", "--executor-profile", "executor.json",
            "--executor-model", "model-a", "--executor-effort", "medium",
            "--judge-adapter", "judge.json", "--judge-model", "model-b", "--judge-effort", "high",
            "--output", str(output)]


def test_configuration_does_not_authorize_or_invent_quotas(tmp_path):
    path = tmp_path / "config.json"
    assert entry.main(arguments(path)) == 0
    config = json.loads(path.read_text())
    assert config["authorized"] is False
    assert config["execution"]["authorized"] is False
    assert config["scoring"]["authorized"] is False
    assert config["limits_per_run"] == {"max_model_calls": None, "max_tool_calls": None, "max_seconds": None}
    assert config["cost_policy"]["owner_approved_cost_cap"] is None


def test_explicit_authorization_still_only_writes_configuration(tmp_path):
    path = tmp_path / "config.json"
    assert entry.main(arguments(path) + ["--authorize"]) == 0
    assert json.loads(path.read_text())["authorized"] is True
    assert list(tmp_path.iterdir()) == [path]


def test_configuration_does_not_overwrite_prior_files(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("prior")
    assert entry.main(arguments(path)) == 2
    assert path.read_text() == "prior"


@pytest.mark.parametrize("command", [["--help"], ["results"], ["results", "--json"]])
def test_offline_entry_works_with_isolated_python(command, tmp_path):
    result = subprocess.run([sys.executable, "-I", str(ROOT / "example.py"), *command],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_documented_source_pin_matches_constants():
    pins = json.loads((ROOT / "source-pins.json").read_text())
    tree = ast.parse((ROOT / "src/rpnh_ha/constants.py").read_text())
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
    assert values["RPNH_COMMIT"] == pins["rpnh"]["commit"]
    assert values["AUDIT_COMMIT"] == pins["harnessaudit"]["commit"]


def test_distribution_contains_no_kernel_or_private_runtime():
    assert not (ROOT / "cpn").exists()
    assert not (ROOT / "src/cpn").exists()
    for file in ROOT.rglob("*"):
        if file.is_file():
            assert file.suffix not in {".sqlite", ".sqlite3", ".zip", ".db"}
            assert file.name not in {"experiment_config.snapshot.json", "scoring_config.snapshot.json"}


def test_original_grader_references_remain_external():
    source = (ROOT / "src/rpnh_ha/scoring.py").read_text()
    assert "multi_agent.checker" in source
    assert "evaluate_completion_checkpoints" in source
    assert "evaluate_operational_governance" in source
    assert not (ROOT / "multi_agent").exists()
