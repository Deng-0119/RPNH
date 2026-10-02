from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from rpnh_rrsi.formal_campaign import _smoke, run_formal_campaign
from rpnh_rrsi.formal_protocol import load_formal_protocol


CONFIG = Path(__file__).parents[1] / "protocol.example.json"


class _Port:
    execution_policy = {"test": "fake"}

    def request_once(self, _attempt):  # pragma: no cover - fake runners do not dispatch
        raise AssertionError("fake campaign must not call a provider")


def _selection(tmp_path: Path):
    from cpn.llm_adapters.config import LLMExecutionSelection
    from cpn.rpnh.llm_contracts import LLMInputTarget

    adapter = tmp_path / "campaign-adapter.json"
    adapter.write_text(json.dumps({
        "schema_version": "local_process_adapter_config/v1",
        "adapter_kind": "local_process",
        "model_condition": "formal-campaign-test-model",
        "argv": ["formal-campaign-test-model"],
        "probe_argv": ["formal-campaign-test-model", "--probe"],
        "env": {}, "inherit_env": [],
    }), encoding="utf-8")
    return LLMExecutionSelection(
        LLMInputTarget("formal-campaign-test-model", 4096, 65536),
        "local_process", adapter.resolve(), 30)


def _root(tmp_path: Path) -> Path:
    return tmp_path / "run"


def _refs(label: str) -> dict:
    return {"run": {"run_ref": {"logical_id": f"run-{label}"},
                    "task_ref": {"logical_id": f"task-{label}"},
                    "result_resource_ref": {"resource_id": f"result-{label}"},
                    "terminal_evidence_version_id": f"terminal-{label}"}}


class _Fakes:
    def __init__(self, *, generic: bool = False, skip_digest: bool = False,
                 calibration_nondegenerate: bool = False):
        self.generic = generic
        self.skip_digest = skip_digest
        self.calibration_nondegenerate = calibration_nondegenerate
        self.roles: list[dict] = []
        self.policy: list[dict] = []
        self.policy_run_dirs: list[Path] = []
        self.proposer_sources: list[str] = []
        self.critic_calls = 0

    def role(self, *, run_dir, request, selection, llm_input_port, digest_runner=None):
        assert not run_dir.exists()
        assert selection.input_target.model_condition == "formal-campaign-test-model"
        self.roles.append(deepcopy(request))
        role = request["role"]
        label = request["occurrence_id"]
        if role == "analyst":
            # Exercise the exact injected independent-child callback supplied by campaign.
            child = {"schema_version": "rrsi_v06/formal_role_request/v1",
                     "protocol_id": request["protocol_id"], "round_id": request["round_id"],
                     "occurrence_id": f"{label}:digest:0", "parent_action_ref": f"{label}:action:1",
                     "role": "digester", "limits": {"max_generations": 15, "max_edits": 0,
                                                         "max_digest_chars": 6000},
                     "input": {"task_id": "evolve-blank", "lens": "failure", "traces": []}}
            assert digest_runner is not None
            if not self.skip_digest:
                digest_runner(child, 0)
            payload = {"failure_modes": ["blank"], "success_patterns": ["default"],
                       "contrasts": [], "n_digests": 1}
        elif role == "digester":
            payload = {"task_id": request["input"]["task_id"], "lens": "failure", "digest": "bounded"}
        elif role == "proposer":
            files = deepcopy(request["input"]["files"])
            self.proposer_sources.append(files[0]["content"])
            if self.generic:
                files[0]["content"] += "\n# TODO\n"
            elif "unsupported" in files[0]["content"]:
                files[0]["content"] = files[0]["content"].replace("unsupported", "supported", 1)
            else:
                files[0]["content"] = files[0]["content"].replace("supported", "handled", 1)
            payload = {"summary": "handle blank input", "component": "policy", "files": files}
        elif role == "critic":
            self.critic_calls += 1
            payload = {"decision": "accepted", "reason": "clean", "component": "policy"}
        else:  # pragma: no cover - guards the test fake, not campaign behavior
            raise AssertionError(role)
        return {**_refs(label), "result": {"payload": payload, "termination": "submitted"}}

    def policy_runner(self, *, run_dir, protocol, request, selection,
                      llm_input_port):
        assert not run_dir.exists()
        run_dir.mkdir(parents=True)
        self.policy_run_dirs.append(run_dir)
        assert request["protocol_id"] == protocol.protocol_id
        self.policy.append(deepcopy(request))
        if (self.calibration_nondegenerate
                and request["split"] == "calibration"
                and request["repetition"] == 0):
            reward, output = 0, 0
        elif request["candidate_id"] == "H0" and request["split"] == "evolve" and request["task_id"] == "evolve-blank":
            reward, output = 0, 0
        else:
            reward, output = 1, request["expected"]
        return {"run_refs": {"run_ref": {"logical_id": request["occurrence_id"]}},
                "terminal_evidence_ref": {"logical_id": f"terminal-{request['occurrence_id']}"},
                "output_ref": {"resource_id": f"output-{request['occurrence_id']}"},
                "result": {"reward": reward, "output": output, "expected": request["expected"],
                           "execution_trace": "fake-provider", "usage": None}}


def test_two_round_campaign_orders_children_keeps_heldout_from_roles_and_feeds_back(tmp_path: Path):
    fake = _Fakes()
    report = run_formal_campaign(run_dir=_root(tmp_path), protocol=load_formal_protocol(CONFIG), selection=_selection(tmp_path),
                                 llm_input_port=_Port(), role_runner=fake.role, policy_runner=fake.policy_runner)

    assert report["campaign_complete"] is False
    assert report["formal_rrsi_v06_local_complete"] is False
    assert report["execution_evidence"] == {
        "mode": "injected_test_runners",
        "structural_campaign_complete": True,
        "production_evidence_complete": False,
    }
    assert report["phase_order"][:3] == ["calibration_h0", "policy:calibration-h0:H0:calibration-positive:0",
                                          "policy:calibration-h0:H0:calibration-positive:1"]
    assert report["phase_order"].index("h0_heldout") < report["phase_order"].index("hfinal_heldout")
    assert report["phase_order"][-2] == "export"
    assert len(report["round_decisions"]) == 2
    assert len([item for item in fake.policy if item["split"] == "calibration"]) == 2
    assert len([item for item in fake.policy if item["split"] == "evolve" and item["candidate_id"] == "H0"]) == 3
    assert len([item for item in fake.policy if item["split"] == "heldout"]) == 6
    assert len([item for item in fake.policy if item["split"] == "export"]) == 1
    assert all("heldout-" not in json.dumps(request) and "export-" not in json.dumps(request)
               for request in fake.roles)
    assert any(request["role"] == "digester" for request in fake.roles)
    assert "supported" in fake.proposer_sources[1]  # round two saw the actually selected source
    assert report["h0_evolve"]["aggregate"]["score"] < report["hfinal_evolve"]["aggregate"]["score"]
    assert report["h0_heldout"]["aggregate"]["score"] == report["hfinal_heldout"]["aggregate"]["score"]
    assert all(row["complete"] for row in
               report["completion_evidence"]["rounds"])
    manifest = report["candidate_manifests"][0]
    assert "settled_workspace" not in manifest
    assert not any(key in json.dumps(manifest).lower() for key in ("hash", "checksum", "fingerprint"))
    assert Path(report["protocol"]["path"]).name == "protocol.json"
    assert len(fake.policy_run_dirs) == len(set(fake.policy_run_dirs))


def test_precheck_rejection_skips_critic_and_candidate_trials_but_continues_rounds(tmp_path: Path):
    fake = _Fakes(generic=True)
    report = run_formal_campaign(run_dir=_root(tmp_path), protocol=load_formal_protocol(CONFIG), selection=_selection(tmp_path),
                                 llm_input_port=_Port(), role_runner=fake.role, policy_runner=fake.policy_runner)

    assert fake.critic_calls == 0
    assert [item["outcome"] for item in report["round_decisions"]] == ["gate_reject", "gate_reject"]
    assert all(item["critic_calls"] == 0 and item["candidate_trials"] == 0
               for item in report["round_decisions"])
    assert {item["candidate_id"] for item in fake.policy if item["split"] == "evolve"} == {"H0"}
    assert report["hfinal_evolve"]["candidate_id"] == "H0"
    assert report["h0_heldout"]["candidate_id"] == "H0"
    assert report["hfinal_heldout"]["candidate_id"] == "H0"
    assert report["h0_heldout"]["evaluation_id"] == "heldout-h0"
    assert report["hfinal_heldout"]["evaluation_id"] == "heldout-final"
    assert len(fake.policy_run_dirs) == len(set(fake.policy_run_dirs))
    assert report["campaign_complete"] is False
    assert report["formal_rrsi_v06_local_complete"] is False


def test_missing_required_digester_cannot_claim_formal_completion(tmp_path: Path):
    fake = _Fakes(skip_digest=True)
    report = run_formal_campaign(
        run_dir=_root(tmp_path), protocol=load_formal_protocol(CONFIG),
        selection=_selection(tmp_path), llm_input_port=_Port(),
        role_runner=fake.role, policy_runner=fake.policy_runner)

    assert report["campaign_complete"] is False
    assert all(not row["checks"]["digester_submitted"]
               for row in report["completion_evidence"]["rounds"])


def test_smoke_validates_messages_for_every_frozen_evolve_input(
        tmp_path: Path, monkeypatch) -> None:
    protocol = load_formal_protocol(CONFIG)
    seen = []

    def build_messages(raw):
        seen.append(deepcopy(raw))
        if raw == {"raw": ""}:
            return []
        return [{"role": "user", "content": str(raw)}]

    monkeypatch.setattr(
        "rpnh_rrsi.formal_campaign._restricted_build_messages",
        lambda _path: build_messages)
    manifest = {"members": [{
        "path": item["path"], "after": item["content"],
        "mode": item["mode"],
    } for item in protocol.source_fixture["files"]]}

    result = _smoke(tmp_path / "candidate", manifest, protocol)

    assert result["passed"] is False
    assert seen == [task.raw_input for task in protocol.evolve.tasks]
    assert "nonempty messages" in result["error"]


def test_smoke_does_not_reclassify_materialization_failure_as_candidate_defect(
        tmp_path: Path, monkeypatch) -> None:
    protocol = load_formal_protocol(CONFIG)
    manifest = {"members": [{
        "path": item["path"], "after": item["content"],
        "mode": item["mode"],
    } for item in protocol.source_fixture["files"]]}

    def fail_write(*_args, **_kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(Path, "write_text", fail_write)
    with pytest.raises(OSError, match="storage unavailable"):
        _smoke(tmp_path / "candidate", manifest, protocol)


def test_calibration_fallback_evidence_tracks_selected_method(tmp_path: Path):
    fake = _Fakes(calibration_nondegenerate=True)
    report = run_formal_campaign(
        run_dir=_root(tmp_path), protocol=load_formal_protocol(CONFIG),
        selection=_selection(tmp_path), llm_input_port=_Port(),
        role_runner=fake.role, policy_runner=fake.policy_runner)

    assert report["calibration"]["method"] == "repeated base evaluations"
    assert report["calibration"]["bootstrap_fallback"] == {
        "available": True,
        "exercised": False,
        "reason": "repeated_base_evaluations_selected",
    }
