"""Synthetic, offline host contracts. No official task is solved or graded."""
import json
from pathlib import Path

import pytest

from examples.slopcodebench.contracts import (
    CHECKPOINTS, CheckpointLedger, CheckpointScope, current_request, digest,
    workspace_manifest,
)
from examples.slopcodebench.preflight import inspect_runner_source, report


def request(tmp_path, checkpoint="checkpoint_1", previous=None):
    work = tmp_path / "workspace"
    work.mkdir(exist_ok=True)
    return current_request(checkpoint=checkpoint, prompt="SYNTHETIC CURRENT REQUEST",
                           workspace=workspace_manifest(work),
                           definition_sha256="a" * 64, predecessor_sha256=previous)


@pytest.mark.parametrize("names", [(), ("checkpoint_2",),
                                  ("checkpoint_1", "checkpoint_3"), CHECKPOINTS[::-1]])
def test_reject_nonprefix(names):
    with pytest.raises(ValueError):
        CheckpointScope(names)


def test_scope_labels_partial_explicitly():
    assert CheckpointScope().coverage == "partial_prefix"
    assert CheckpointScope(CHECKPOINTS).coverage == "full_task"


def test_workspace_digest_tracks_bytes_and_modes_without_disclosing_content(tmp_path):
    source = tmp_path / "code_search.py"
    source.write_text("UNIQUE_PRIVATE_CONTENT")
    first = workspace_manifest(tmp_path)
    assert "UNIQUE_PRIVATE_CONTENT" not in json.dumps(first)
    assert str(tmp_path) not in json.dumps(first)
    source.write_text("CHANGED")
    second = workspace_manifest(tmp_path)
    assert first["sha256"] != second["sha256"]
    source.chmod(0o755)
    assert workspace_manifest(tmp_path)["sha256"] != second["sha256"]


def test_manifest_is_ordered_and_fails_before_silently_truncating(tmp_path):
    (tmp_path / "z.py").write_bytes(b"z")
    (tmp_path / "a.py").write_bytes(b"a")
    assert [row["path"] for row in workspace_manifest(tmp_path)["files"]] == ["a.py", "z.py"]
    with pytest.raises(ValueError, match="limits"):
        workspace_manifest(tmp_path, max_files=1)
    with pytest.raises(ValueError, match="limits"):
        workspace_manifest(tmp_path, max_bytes=1)


def test_manifest_rejects_symlink_to_oracle(tmp_path):
    oracle = tmp_path / "oracle"
    oracle.write_text("MUST NOT READ")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to(oracle)
    with pytest.raises(ValueError, match="links"):
        workspace_manifest(workspace)


def test_request_contains_current_prompt_hash_only(tmp_path):
    value = request(tmp_path)
    assert "SYNTHETIC" not in json.dumps(value)
    assert "checkpoint_2" not in json.dumps(value)
    assert value["predecessor_sha256"] is None
    with pytest.raises(ValueError, match="digest"):
        current_request(checkpoint="checkpoint_1", prompt="x", workspace={"sha256": "wrong"},
                        definition_sha256="a" * 64)


def test_no_run_record_never_becomes_a_grade_or_permits_advance(tmp_path):
    ledger = CheckpointLedger(tmp_path / "ledger")
    record = ledger.append(request(tmp_path), handoff_status="not_run", rpnh_result=None,
                           reason="Synthetic fixture; no owner has run.")
    assert record["original_evaluation"] == {"status": "not_collected", "score": None}
    assert record["rpnh_result"] is None
    with pytest.raises(ValueError, match="unfinished"):
        ledger.append(request(tmp_path, "checkpoint_2", digest(record)),
                      handoff_status="not_run", rpnh_result=None, reason="Cannot skip ahead")
    assert len(ledger.records()) == 1


def test_no_missing_result_submission(tmp_path):
    ledger = CheckpointLedger(tmp_path / "ledger")
    with pytest.raises(ValueError, match="actual RPNH"):
        ledger.append(request(tmp_path), handoff_status="submitted", rpnh_result=None)
    assert ledger.records() == []


def test_no_duplicate_or_mutable_history(tmp_path):
    ledger = CheckpointLedger(tmp_path / "ledger")
    value = request(tmp_path)
    ledger.append(value, handoff_status="blocked", rpnh_result=None, reason="Missing boundary")
    before = (ledger.root / "record-01.json").read_bytes()
    with pytest.raises(ValueError, match="next original"):
        ledger.append(value, handoff_status="blocked", rpnh_result=None, reason="Retry")
    assert (ledger.root / "record-01.json").read_bytes() == before
    corrupted = json.loads(before)
    corrupted["request"]["predecessor_sha256"] = "b" * 64
    (ledger.root / "record-01.json").write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="predecessor"):
        ledger.records()


def test_preflight_cannot_start_owner_or_model(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only preflight invoked RPNH")
    monkeypatch.setattr("cpn.rpnh.agent_tasks.run_agent_task", forbidden)
    result = report()
    assert result["faithful_adapter"]["status"] == "blocked"
    assert set(result["execution"].values()) == {"not_run"}
    assert result["scope"]["coverage"] == "partial_prefix"


def test_inspect_source_contract_without_importing_source(tmp_path):
    target = tmp_path / "src/slop_code/agent_runner/agent.py"
    target.parent.mkdir(parents=True)
    target.write_text("raise RuntimeError('must not import')\nclass Agent:\n"
                      " def setup(self, session): pass\n def run(self, task): pass\n"
                      " def reset(self): pass\n def cleanup(self): pass\n"
                      " def save_artifacts(self, path): pass\n")
    assert inspect_runner_source(tmp_path)["status"] == "passed"


@pytest.mark.parametrize("prefix", [0, 6, True])
def test_preflight_rejects_invalid_scope(prefix):
    with pytest.raises(ValueError):
        report(prefix=prefix)
