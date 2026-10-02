from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest

from cpn.rpnh.llm_contracts import LLMInputResponseBytes
from cpn.rpnh.registry.schema_catalog import canonical_json

from rpnh_rrsi.formal_campaign import _smoke, run_formal_campaign
from rpnh_rrsi.formal_protocol import load_formal_protocol


CONFIG = Path(__file__).parents[1] / "protocol.example.json"


class _Port:
    execution_policy = {"test": "fake"}

    def request_once(self, _attempt):  # pragma: no cover - fake runners do not dispatch
        raise AssertionError("fake campaign must not call a provider")


class _CampaignScriptedPort:
    """Drive the real role and Policy child runners without a provider."""

    def __init__(self, selection):
        self.execution_policy = selection.as_registry_policy()
        self.requests: list[dict] = []

    @staticmethod
    def _tool_response(name: str, arguments: dict, ordinal: int) -> dict:
        return {
            "protocol": "llm_response_envelope/v1",
            "text": "",
            "tool_calls": [{
                "id": f"scripted-{ordinal}", "name": name,
                "arguments": json.dumps(arguments, separators=(",", ":")),
            }],
            "finish_reason": "tool_calls",
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "total_tokens": 15},
        }

    @staticmethod
    def _policy_response(messages: list[dict]) -> dict:
        raw_input = ast.literal_eval(messages[-1]["content"])
        raw = raw_input.get("raw")
        if raw is None:
            timeout = 30
        elif isinstance(raw, str) and not raw.strip():
            timeout = 0
        else:
            timeout = int(raw)
        return {
            "protocol": "llm_response_envelope/v1",
            "text": json.dumps({"timeout": timeout}, separators=(",", ":")),
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "total_tokens": 15},
        }

    def request_once(self, attempt):
        request = json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        messages = request["messages"]
        tool_names = {
            item["function"]["name"] for item in request.get("tools", [])
        }
        ordinal = len(self.requests)
        if not tool_names:
            response = self._policy_response(messages)
        else:
            completed_actions = sum(
                message.get("role") == "tool" for message in messages)
            initial_input = json.loads(messages[1]["content"])
            if "rrsi_digest_many" in tool_names:
                if completed_actions == 0:
                    task_id = initial_input["traces"][0]["task_id"]
                    response = self._tool_response("rrsi_digest_many", {
                        "requests": [{
                            "task_id": task_id, "lens": "failure",
                            "questions": ["What behavior should change?"],
                        }],
                    }, ordinal)
                else:
                    response = self._tool_response("rrsi_submit_analysis", {
                        "failure_modes": ["blank input keeps the legacy behavior"],
                        "success_patterns": ["integer inputs remain stable"],
                        "contrasts": ["blank and nonblank tasks differ"],
                    }, ordinal)
            elif "rrsi_return_digest" in tool_names:
                if completed_actions == 0:
                    response = self._tool_response("rrsi_read_trace", {
                        "from_line": 1, "to_line": 20,
                    }, ordinal)
                else:
                    response = self._tool_response("rrsi_return_digest", {
                        "digest": "The selected trace records the bounded Policy result.",
                    }, ordinal)
            elif "rrsi_edit_source" in tool_names:
                if completed_actions == 0:
                    response = self._tool_response("rrsi_read_source", {
                        "path": "policy.py",
                    }, ordinal)
                elif completed_actions == 1:
                    policy = next(
                        row["content"] for row in initial_input["files"]
                        if row["path"] == "policy.py")
                    old = "Emit only JSON"
                    if old not in policy:
                        old = "Reply with exactly one JSON object"
                    response = self._tool_response("rrsi_edit_source", {
                        "path": "policy.py", "old": old,
                        "new": "Return exactly one JSON object",
                    }, ordinal)
                else:
                    response = self._tool_response("rrsi_submit_proposal", {
                        "summary": "Clarify the exact Policy response contract.",
                        "component": "policy",
                    }, ordinal)
            else:
                response = self._tool_response("rrsi_submit_critique", {
                    "decision": "accepted", "reason": "bounded prompt-only edit",
                    "component": "policy",
                }, ordinal)
        return LLMInputResponseBytes(
            canonical_json(response), status_code=None,
            external_request_id=f"scripted-{ordinal}")

    def close(self):
        pass


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
                 calibration_nondegenerate: bool = False,
                 runtime_smoke_failure: bool = False):
        self.generic = generic
        self.skip_digest = skip_digest
        self.calibration_nondegenerate = calibration_nondegenerate
        self.runtime_smoke_failure = runtime_smoke_failure
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
            if self.runtime_smoke_failure:
                policy = next(item for item in files if item["path"] == "policy.py")
                policy["content"] = (
                    "def build_messages(raw):\n"
                    "    return [{'role': 'user', 'content': str({raw: 'x'})}]\n"
                )
            elif self.generic:
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


def test_full_campaign_uses_real_child_runners_with_scripted_input_port(
        tmp_path: Path) -> None:
    protocol = load_formal_protocol(CONFIG)
    selection = _selection(tmp_path)
    port = _CampaignScriptedPort(selection)

    report = run_formal_campaign(
        run_dir=_root(tmp_path), protocol=protocol, selection=selection,
        llm_input_port=port)

    assert report["execution_evidence"] == {
        "mode": "injected_test_runners",
        "structural_campaign_complete": True,
        "production_evidence_complete": False,
    }
    assert report["campaign_complete"] is False
    assert report["formal_rrsi_v06_local_complete"] is False
    assert report["completion_evidence"]["actual_policy_children"] == 18
    assert len([row for row in report["child_runs"]
                if row["kind"] == "policy"]) == 18
    role_rows = [row for row in report["child_runs"]
                 if row["kind"] == "role"]
    assert len(role_rows) == 8
    assert all(row["termination"] == "submitted" for row in role_rows)
    assert all(row["terminal_evidence_ref"] and row["output_ref"]
               for row in report["child_runs"])
    assert [row["outcome"] for row in report["round_decisions"]] == [
        "evaluated", "evaluated"]
    assert port.requests


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


def test_candidate_runtime_type_error_is_smoke_failure_and_campaign_continues(
        tmp_path: Path) -> None:
    fake = _Fakes(runtime_smoke_failure=True)

    report = run_formal_campaign(
        run_dir=_root(tmp_path), protocol=load_formal_protocol(CONFIG),
        selection=_selection(tmp_path), llm_input_port=_Port(),
        role_runner=fake.role, policy_runner=fake.policy_runner)

    assert [row["outcome"] for row in report["round_decisions"]] == [
        "smoke_fail", "smoke_fail"]
    assert all("TypeError" in row["smoke"]["error"]
               for row in report["round_decisions"])
    assert all(row["candidate_trials"] == 0
               for row in report["round_decisions"])
    assert {row["candidate_id"] for row in fake.policy
            if row["split"] == "evolve"} == {"H0"}
    assert report["hfinal_evolve"]["candidate_id"] == "H0"
    assert report["hfinal_heldout"]["candidate_id"] == "H0"
    assert report["export"]["candidate_id"] == "H0"
    assert (_root(tmp_path) / "formal-report.json").is_file()


def test_smoke_does_not_reclassify_builder_oserror_as_candidate_defect(
        tmp_path: Path, monkeypatch) -> None:
    protocol = load_formal_protocol(CONFIG)
    manifest = {"members": [{
        "path": item["path"], "after": item["content"],
        "mode": item["mode"],
    } for item in protocol.source_fixture["files"]]}

    def build_messages(_raw):
        raise OSError("builder infrastructure unavailable")

    monkeypatch.setattr(
        "rpnh_rrsi.formal_campaign._restricted_build_messages",
        lambda _path: build_messages)
    with pytest.raises(OSError, match="builder infrastructure unavailable"):
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
