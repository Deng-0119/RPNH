"""Synthetic lifecycle checks; these are not Docker or provider acceptance."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rpnh_erp_bench import driver
from rpnh_erp_bench.source import TASK_IDS, VerifiedTask
from test_scoring import reports


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    events, captured = [], {}
    task = VerifiedTask(TASK_IDS[0], tmp_path / "task", b"original fixture\n",
        {"metadata": {"seed": 7}, "agent": {"timeout_sec": 3600.0},
         "verifier": {"timeout_sec": 300.0}, "environment": {"build_timeout_sec": 600.0}}, {})
    identity = {"owned_tree_sha256": "a" * 64}
    owner = {"task_id": "synthetic-owner", "owner_quiescent": True,
        "process_exit_confirmed": True, "process_exit_code": 0, "forced_termination": False,
        "terminal": {"terminal_evidence_ref": {"entity_type": "fixture/v1", "logical_id": "e", "version_id": "v"},
            "terminal_result_ref": None, "actual_model_call_counts": [3, 0], "run_outcome": "complete"},
        "result_evidence": {"actual_model_call_counts": [3, 0], "actions": []}}

    class World:
        def __init__(self, task_dir, root, condition):
            self.source, self.condition_id = task, condition
            self.container_id, self.phase = "synthetic-container", "new"
            self.metadata, self.initial_identity, self.snapshot, self.sandbox = {}, None, None, None
            root.mkdir(mode=0o700)
            verifier = root / "verifier"
            verifier.mkdir()
            self.paths = SimpleNamespace(verifier_dir=verifier)
            self.environment = object()
            captured["world"] = self
        async def start(self):
            events.append("start")
            if captured.get("start_failure"):
                raise RuntimeError("private development path must not be exported")
            self.phase = "ready"
        async def prepare_solver(self):
            events.append("isolation")
            self.sandbox = SimpleNamespace(probe={"uid": 1000}, condition="fixture")
        async def freeze(self, **kw):
            assert kw == {"owner_quiescent": True, "bridge_quiescent": True}
            events.append("freeze")
            self.phase = "frozen"
        async def grade(self, **kw):
            events.append("grade")
            self.metadata["verifier_exit_code"] = 0
            reports(self.paths.verifier_dir, score=12.5)
            self.phase = "graded"
        async def stop(self):
            events.append("stop")
            self.phase = "stopped"

    class Backend:
        def __init__(self, environment, loop, sandbox):
            assert loop.is_running()
        async def quiesce(self):
            events.append("quiesce")
            return {"status": "quiescent", "solver_uid_processes": 0}

    class Bridge:
        def __init__(self, *args):
            pass
        def __enter__(self):
            events.append("admit")
            return self
        def close_admission(self):
            events.append("close")
        def __exit__(self, *args):
            events.append("bridge_exit")

    def run_owner(spec, control, **kw):
        assert type(kw["agent_timeout_seconds"]) is int
        assert kw["agent_timeout_seconds"] == 3600
        events.append("owner")
        return owner

    def manifest(**kw):
        captured["manifest"] = kw
        return {"record_id": kw["record_id"], "stages": kw["stages"]}

    def write_manifest(value, path, **kw):
        path.write_text(json.dumps(value))

    monkeypatch.setattr(driver, "validate_task", lambda *a: task)
    monkeypatch.setattr(driver, "source_identity", lambda *a: identity.copy())
    monkeypatch.setattr(driver, "profile_identity", lambda *a: {"adapter_kind": "local_process", "model_condition": "synthetic"})
    monkeypatch.setattr(driver, "freeze_profile", lambda original, destination: destination / "selection.json")
    monkeypatch.setattr(driver, "is_codex_profile", lambda *a: False)
    monkeypatch.setattr(driver, "OfficialWorld", World)
    monkeypatch.setattr(driver, "HarborBackend", Backend)
    monkeypatch.setattr(driver, "Bridge", Bridge)
    monkeypatch.setattr(driver, "build_spec", lambda *a: object())
    monkeypatch.setattr(driver, "run_owner", run_owner)
    monkeypatch.setattr(driver, "build_result_manifest", manifest)
    monkeypatch.setattr(driver, "write_result_manifest", write_manifest)
    # Test-owned tmp roots can be deep; transport is explicitly mocked here.
    monkeypatch.setattr(driver.os, "fsencode", lambda value: b"synthetic-short-path")
    args = dict(upstream=tmp_path, task_id=TASK_IDS[0], execution_selection=tmp_path / "profile",
        run_root=tmp_path / "run", condition_id="synthetic-condition", source_root=tmp_path,
        shared_validator=tmp_path / "validator", authorize_existing_model=True)
    return args, captured, events, owner


def test_authorization_precedes_source_reads_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "validate_task", lambda *a: pytest.fail("unauthorized source inspection"))
    with pytest.raises(ValueError, match="authorization"):
        asyncio.run(driver.run_trial(upstream=tmp_path, task_id=TASK_IDS[0], execution_selection=tmp_path,
            run_root=tmp_path / "new", condition_id="fixture", source_root=tmp_path, shared_validator=tmp_path))
    assert not (tmp_path / "new").exists()


def test_failed_environment_never_launches_model_or_invents_zero(runtime):
    args, captured, events, _ = runtime
    captured["start_failure"] = True
    result = asyncio.run(driver.run_trial(**args))
    assert events == ["start", "stop"]
    assert result["model_calls"]["status"] == "unknown"
    assert result["model_calls"]["real_provider_calls"] is None
    assert result["overall_score"] is None
    assert all(result["stages"][k]["status"] == "blocked" for k in ("native", "provider", "evaluation"))
    assert (args["run_root"] / "failure.txt").is_file()
    assert "private development path" not in (args["run_root"] / "public" / "lifecycle.json").read_text()


def test_owner_must_exit_before_freeze_and_original_low_score_is_not_success(runtime):
    args, captured, events, _ = runtime
    result = asyncio.run(driver.run_trial(**args))
    assert events == ["start", "isolation", "admit", "owner", "close", "quiesce", "bridge_exit", "freeze", "grade", "stop"]
    assert result["overall_score"] == 12.5 and result["business_passed"] is False
    assert result["stages"]["evaluation"]["status"] == "passed"
    assert captured["manifest"]["scores"][0]["components"]["overall_score"] == 12.5
    assert captured["manifest"]["scores"][0]["claim"] == "grader_compatibility"
    assert result["model_calls"]["real_provider_calls"] == 3
    assert all(result["stages"][k]["status"] == "not_run" for k in ("offline", "mock"))
    with pytest.raises(FileExistsError):
        asyncio.run(driver.run_trial(**args))


def test_nonquiescent_owner_blocks_grader_even_after_container_cleanup(runtime):
    args, _, events, owner = runtime
    owner.update(owner_quiescent=False, forced_termination=True, terminal=None)
    result = asyncio.run(driver.run_trial(**args))
    assert "quiesce" in events and events[-1] == "stop"
    assert "freeze" not in events and "grade" not in events
    assert result["stages"]["evaluation"]["status"] == "blocked"
    assert result["model_calls"]["real_provider_calls"] is None


def test_bridge_bind_failure_still_tears_down_official_world(runtime, monkeypatch):
    args, _, events, _ = runtime
    from rpnh_erp_bench.bridge import Bridge
    endpoint = args["run_root"] / "bridge.sock"
    # A stale owned-path entry is a supported startup failure, never overwritten.
    class OccupiedBridge(Bridge):
        def __enter__(self):
            endpoint.write_text("existing")
            raise OSError("address already in use")
    monkeypatch.setattr(driver, "Bridge", OccupiedBridge)
    result = asyncio.run(driver.run_trial(**args))
    assert events == ["start", "isolation", "stop"]
    assert result["failed_phase"] == "environment"
    assert endpoint.read_text() == "existing"
    assert result["stages"]["evaluation"]["status"] == "blocked"


def test_synthetic_runtime_record_validates_with_frozen_shared_contract(runtime, monkeypatch):
    validator = os.environ.get("ERP_SHARED_VALIDATOR")
    if not validator:
        pytest.skip("explicit frozen ERP_SHARED_VALIDATOR not supplied")
    from rpnh_erp_bench import evidence, export
    from rpnh_erp_bench.source import RPNH_BASE
    args, _, _, _ = runtime
    args["shared_validator"] = Path(validator)
    identity = {"base_commit": RPNH_BASE, "tested_commit": RPNH_BASE, "head_tree": "b" * 40,
                "tracked_clean": True, "owned_files": [], "owned_tree_sha256": "a" * 64,
                "changed_files": [], "identity_scope": "Synthetic test identity only."}
    monkeypatch.setattr(driver, "source_identity", lambda *a: identity)
    monkeypatch.setattr(export, "source_identity", lambda *a: identity)
    monkeypatch.setattr(evidence, "source_identity", lambda *a: identity)
    monkeypatch.setattr(driver, "build_result_manifest", evidence.build_result_manifest)
    monkeypatch.setattr(driver, "write_result_manifest", evidence.write_result_manifest)
    asyncio.run(driver.run_trial(**args))
    public = args["run_root"] / "public"
    manifest = json.loads((public / "result-manifest.json").read_bytes())
    assert evidence.validate_result_manifest(manifest, shared_validator_path=validator,
        artifacts_root=public, source_root=args["source_root"])["contract_valid"]
    assert manifest["scores"][0]["claim"] == "grader_compatibility"
