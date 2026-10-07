"""Offline fault injection: no native owner, model, subprocess, or network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpn.rpnh.llm_contracts import LLMInputPortFailure, LLMInputPortInterrupted
from rpnh_rrsi.formal_campaign import run_formal_campaign
from rpnh_rrsi.formal_execution import close_child
from rpnh_rrsi.formal_protocol import load_formal_protocol
from rpnh_rrsi.formal_reporting import atomic_report

from test_formal_campaign import CONFIG, _Fakes, _Port, _selection
from test_formal_observation import _SuccessPort, _attempt


PRIVATE = "private-exception-payload-do-not-export"


def _run(tmp_path, **kwargs):
    return run_formal_campaign(
        run_dir=tmp_path / "run", protocol=load_formal_protocol(CONFIG),
        selection=_selection(tmp_path), **kwargs)


def _report(tmp_path):
    return json.loads((tmp_path / "run" / "formal-report.json").read_text())


@pytest.mark.parametrize("error", [
    LLMInputPortFailure("submission_unknown", submission_state="submission_unknown",
                        failure_code=PRIVATE),
    TimeoutError(PRIVATE), OSError(PRIVATE), KeyboardInterrupt(PRIVATE),
    SystemExit(PRIVATE),
    LLMInputPortInterrupted(submission_state="request_write_completed_no_response_headers"),
])
def test_failed_second_invocation_retains_first_result_and_exact_error(tmp_path, error):
    fake = _Fakes()

    class Port(_SuccessPort):
        calls = 0

        def request_once(self, attempt):
            self.calls += 1
            if self.calls == 2:
                raise error
            return super().request_once(attempt)

    port = Port()

    def runner(**kwargs):
        kwargs["llm_input_port"].request_once(_attempt())
        return fake.policy_runner(**kwargs)

    with pytest.raises(type(error)) as caught:
        _run(tmp_path, llm_input_port=port, policy_runner=runner, role_runner=fake.role)
    assert caught.value is error
    report = _report(tmp_path)
    assert report["report_status"] == "incomplete"
    assert report["execution_control_version"] == "rrsi_v06/execution_control/v2"
    assert not report["campaign_complete"]
    assert not report["formal_rrsi_v06_local_complete"]
    assert len(report["child_runs"]) == 2
    first, failed = report["child_runs"]
    assert first["status"] == "returned" and first["run_ref"]
    assert first["result"]["reward"] == 1
    assert "result" not in failed
    assert report["termination"]["stage"] == f"policy:{failed['label']}:runner"
    assert report["termination"]["error_type"] == type(error).__name__
    assert len(report["attempt_inventory"]) == port.calls == 2
    assert report["usage"]["policy"]["input_port_invocations"] == 2
    assert report["usage"]["policy"]["total_tokens"] is None
    assert report["completed_evaluations"] == []
    assert PRIVATE not in json.dumps(report)


def test_role_failure_keeps_finished_policy_evaluations(tmp_path):
    fake = _Fakes()
    error = OSError(PRIVATE)

    def role(**kwargs):
        kwargs["llm_input_port"].request_once(_attempt())
        raise error

    with pytest.raises(OSError) as caught:
        _run(tmp_path, llm_input_port=_SuccessPort(),
             policy_runner=fake.policy_runner, role_runner=role)
    assert caught.value is error
    report = _report(tmp_path)
    assert len(report["completed_evaluations"]) == 2
    assert len(report["child_runs"]) == 6
    assert report["child_runs"][-1]["status"] == "failed"
    assert report["attempt_inventory"][0]["owner"] == "analyst"
    assert report["termination"]["stage"] == "role:round-1:analyst:runner"


def test_success_report_roundtrip_and_legacy_fake_runner_signatures(tmp_path):
    fake = _Fakes()
    result = _run(tmp_path, llm_input_port=_Port(),
                  role_runner=fake.role, policy_runner=fake.policy_runner)
    assert _report(tmp_path) == json.loads(json.dumps(result))
    assert result["report_status"] == "completed"
    assert len(result["child_runs"]) == 26
    assert result["execution_evidence"]["structural_campaign_complete"] is True
    assert result["formal_rrsi_v06_local_complete"] is False
    assert result["usage"]["policy"]["physical_attempts"] == 0
    assert result["usage"]["policy"]["provider_physical_calls"] is None


def test_final_report_write_error_does_not_repeat_campaign(tmp_path, monkeypatch):
    import rpnh_rrsi.formal_reporting as reporting
    original = reporting.atomic_report
    error = OSError(PRIVATE)
    writes = []

    def fail_final(path, report):
        writes.append(report["report_status"])
        if report["report_status"] == "completed":
            raise error
        return original(path, report)

    monkeypatch.setattr(reporting, "atomic_report", fail_final)
    fake = _Fakes()
    with pytest.raises(OSError) as caught:
        _run(tmp_path, llm_input_port=_Port(),
             role_runner=fake.role, policy_runner=fake.policy_runner)
    assert caught.value is error
    report = _report(tmp_path)
    assert report["termination"] == {
        "status": "failed", "stage": "report_write", "error_type": "OSError"}
    assert report["report_status"] == "incomplete"
    assert len(fake.policy) == 18
    assert len(report["child_runs"]) == 26
    assert writes.count("completed") == 1


def test_primary_error_survives_report_and_close_errors(tmp_path, monkeypatch):
    import cpn.llm_adapters
    import rpnh_rrsi.formal_reporting as reporting
    original = reporting.atomic_report
    primary = OSError(PRIVATE)
    failed = False
    fake = _Fakes()

    class Port(_Port):
        close_count = 0

        def close(self):
            self.close_count += 1
            raise ValueError("private-close-payload")

    port = Port()
    monkeypatch.setattr(cpn.llm_adapters, "build_llm_input_port", lambda *a, **k: port)

    def write(path, report):
        if failed:
            raise PermissionError("private-write-payload")
        original(path, report)

    def runner(**kwargs):
        nonlocal failed
        if fake.policy:
            failed = True
            raise primary
        return fake.policy_runner(**kwargs)

    monkeypatch.setattr(reporting, "atomic_report", write)
    with pytest.raises(OSError) as caught:
        _run(tmp_path, role_runner=fake.role, policy_runner=runner)
    assert caught.value is primary
    assert port.close_count == 1
    assert any("report_write" in note for note in primary.__notes__)
    assert any("input_port_close" in note for note in primary.__notes__)
    assert all("payload" not in note for note in primary.__notes__)
    # The prior atomic checkpoint survives, and explicitly says still running.
    assert _report(tmp_path)["report_status"] == "running"
    assert _report(tmp_path)["child_runs"][0]["result"]["reward"] == 1


def test_pre_stopped_campaign_never_builds_port(tmp_path, monkeypatch):
    import cpn.llm_adapters

    def forbidden(*_a, **_k):
        raise AssertionError("port construction must not happen")

    monkeypatch.setattr(cpn.llm_adapters, "build_llm_input_port", forbidden)
    with pytest.raises(LLMInputPortInterrupted):
        _run(tmp_path, interruption_requested=lambda: True)
    report = _report(tmp_path)
    assert report["child_runs"] == []
    assert report["termination"]["submission_state"] == "not_submitted"
    assert report["termination"]["status"] == "interrupted"


def test_campaign_stop_between_children_never_starts_next_runner(tmp_path):
    fake = _Fakes()
    probe = lambda: len(fake.policy) == 1

    def runner(**kwargs):
        assert kwargs.pop("interruption_requested") is probe
        return fake.policy_runner(**kwargs)

    with pytest.raises(LLMInputPortInterrupted):
        _run(tmp_path, llm_input_port=_Port(), role_runner=fake.role,
             policy_runner=runner, interruption_requested=probe)
    report = _report(tmp_path)
    assert len(fake.policy) == 1
    assert report["child_runs"][-1]["status"] == "interrupted"
    assert report["termination"]["stage"].endswith(":before_start")
    assert report["attempt_inventory"] == []


def test_callback_is_forwarded_to_policy_roles_and_nested_digester(tmp_path):
    fake = _Fakes()
    probe = lambda: False
    seen = []

    def policy(**kwargs):
        assert kwargs.pop("interruption_requested") is probe
        seen.append("policy")
        return fake.policy_runner(**kwargs)

    def role(**kwargs):
        assert kwargs.pop("interruption_requested") is probe
        seen.append(kwargs["request"]["role"])
        return fake.role(**kwargs)

    _run(tmp_path, llm_input_port=_Port(), role_runner=role,
         policy_runner=policy, interruption_requested=probe)
    assert seen.count("policy") == 18
    assert seen.count("digester") == 2
    assert seen.count("analyst") == 2


def test_atomic_replace_failure_retains_previous_json_and_cleans_temp(tmp_path, monkeypatch):
    import rpnh_rrsi.formal_reporting as reporting
    path = tmp_path / "formal-report.json"
    atomic_report(path, {"old": True})
    error = OSError(PRIVATE)

    def fail(*_a):
        raise error

    monkeypatch.setattr(reporting.os, "replace", fail)
    with pytest.raises(OSError) as caught:
        atomic_report(path, {"new": True})
    assert caught.value is error
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".formal-report-*.tmp"))


def test_child_cleanup_preserves_original_and_attempts_both_resources():
    primary = OSError(PRIVATE)
    calls = []

    class Resource:
        def close(self):
            calls.append(1)
            raise ValueError(PRIVATE)

    close_child(owner=None, event_loop=Resource(), input_port=Resource(),
                created_port=True, primary_error=primary)
    assert len(calls) == 2
    assert len(primary.__notes__) == 2
    assert PRIVATE not in " ".join(primary.__notes__)
