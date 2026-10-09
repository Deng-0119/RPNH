"""Pure opt-in/evidence guard regressions. Never execute a binary or native lane."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import conftest as gate
from opencode_candidate_support import (
    CandidateRun, EffectGuard, select_profile, terminal_text, valid_probe_stdout,
    validate_binary,
)


class Config:
    def __init__(self, *, version=None, binary=None, lane=None):
        self.values = dict(version=version, binary=binary, lane=lane)
        self.removed = []
        self.hook = NS(pytest_deselected=lambda items: self.removed.extend(items))

    def getoption(self, name):
        return self.values[name.removeprefix("opencode_certify_")]

    def addinivalue_line(self, *_args):
        pass


class Item:
    def __init__(self, config, lane=None, name="test_opencode_candidate_native.py"):
        self.config = config
        self.marker = NS(args=(lane,)) if lane is not None else None
        self.path = Path(name)
        self.marks = []
        self.funcargs = {}

    def get_closest_marker(self, _name):
        return self.marker

    def add_marker(self, value):
        self.marks.append(value)


def configured(**options):
    config = Config(**options)
    gate.pytest_configure(config)
    return config


def explicit_config():
    return configured(version="1.18.35", binary="/not-executed/opencode", lane="contract")


def report_hook(item, report, exc=None):
    call = NS(excinfo=NS(value=exc) if exc is not None else None)
    generator = gate.pytest_runtest_makereport(item, call)
    next(generator)
    with pytest.raises(StopIteration):
        generator.send(NS(get_result=lambda: report))


@pytest.mark.parametrize("options", [
    {"version": "1.18.35"}, {"binary": "/opencode"}, {"lane": "contract"},
    {"version": "1.18.35", "binary": "/opencode"},
    {"version": "", "binary": "/opencode", "lane": "contract"},
])
def test_partial_options_are_blocked(options):
    with pytest.raises(pytest.UsageError, match="all three"):
        configured(**options)


def test_default_only_skips_new_candidates_and_preserves_original_selection():
    config = configured()
    candidate = Item(config, "contract")
    original = Item(config, name="test_opencode_pty.py")
    ordinary = Item(config, name="test_other.py")
    items = [candidate, original, ordinary]
    gate.pytest_collection_modifyitems(config, items)
    assert items == [candidate, original, ordinary]
    assert len(candidate.marks) == 1
    assert original.marks == ordinary.marks == []
    assert config.removed == []


def test_explicit_lane_selects_exactly_one_test():
    config = explicit_config()
    selected = Item(config, "contract")
    other = Item(config, "registry-read")
    original = Item(config, name="test_opencode_pty.py")
    ordinary = Item(config, name="test_other.py")
    items = [selected, other, original, ordinary]
    gate.pytest_collection_modifyitems(config, items)
    assert items == [selected]
    assert config.removed == [other, original, ordinary]


@pytest.mark.parametrize("lanes", [[], ["registry-read"], ["contract", "contract"]])
def test_missing_or_ambiguous_selected_lane_blocks(lanes):
    config = explicit_config()
    with pytest.raises(pytest.UsageError, match="exactly"):
        gate.pytest_collection_modifyitems(config, [Item(config, lane) for lane in lanes])


def test_explicit_skip_is_a_failure():
    config = explicit_config()
    report = NS(skipped=True, failed=False, passed=False, when="setup", outcome="skipped")
    report_hook(Item(config, "contract"), report)
    assert report.outcome == "failed"
    assert "cannot pass by skipping" in report.longrepr
    assert config._opencode_candidate_completed is False


def test_collect_only_or_deselected_request_cannot_exit_successfully():
    config = explicit_config()
    session = NS(config=config, exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    # Already nonzero statuses retain their meaning; no native was invoked.
    session.exitstatus = 4
    gate.pytest_sessionfinish(session, 4)
    assert session.exitstatus == 4


def test_only_passed_call_marks_requested_scenario_completed():
    config = explicit_config()
    item = Item(config, "contract")
    report_hook(item, NS(skipped=False, failed=False, passed=True, when="setup"))
    assert not config._opencode_candidate_completed
    item.funcargs["opencode_candidate"] = NS(evidence={"status": "passed-limited-smoke"})
    report_hook(item, NS(skipped=False, failed=False, passed=True, when="call"))
    session = NS(config=config, exitstatus=0)
    gate.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


def test_successful_call_without_terminal_evidence_is_not_a_pass(tmp_path):
    config = explicit_config()
    item = Item(config, "contract")
    run = CandidateRun(tmp_path, "1.18.35", "/not-executed/opencode", "contract")
    run.evidence["status"] = "running"
    item.funcargs["opencode_candidate"] = run
    report = NS(skipped=False, failed=False, passed=True, when="call", outcome="passed")
    report_hook(item, report)
    assert report.outcome == "failed"
    assert run.evidence["status"] == "failed"
    assert not config._opencode_candidate_completed


def test_failed_call_phase_evidence_is_terminal_and_sanitized(tmp_path):
    config = explicit_config()
    item = Item(config, "contract")
    run = CandidateRun(tmp_path, "1.18.35", "/not-executed/opencode", "contract")
    run.evidence["status"] = "running"
    run.evidence["stage"] = "fake-call-phase"
    password = "session-password-example"
    basic = base64.b64encode(("rpnh:" + password).encode()).decode()
    run.secrets += [password, basic]
    run.evidence["terminal"] = [str(tmp_path), password, basic]
    item.funcargs["opencode_candidate"] = run
    report_hook(item, NS(skipped=False, failed=True, passed=False, when="call"),
                AssertionError("do not persist exception body: " + password))
    run.write_evidence()
    raw = run.evidence_path.read_text()
    evidence = json.loads(raw)
    assert evidence["status"] == "failed"
    assert evidence["first_failure"] == {"type": "AssertionError", "stage": "fake-call-phase"}
    assert password not in raw and basic not in raw and str(tmp_path) not in raw
    assert "do not persist" not in raw
    assert run.evidence_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("phase,skipped", [("teardown", False), ("call", True)])
def test_late_failure_or_skip_overwrites_already_written_pass(tmp_path, phase, skipped):
    config = explicit_config()
    config._opencode_candidate_completed = True
    item = Item(config, "contract")
    run = CandidateRun(tmp_path, "1.18.35", "/not-executed/opencode", "contract")
    run.evidence["status"] = "passed-limited-smoke"
    run.evidence["stage"] = "fake-late-report"
    run.write_evidence()
    assert json.loads(run.evidence_path.read_text())["status"] == "passed-limited-smoke"
    item.funcargs["opencode_candidate"] = run
    report = NS(skipped=skipped, failed=not skipped, passed=False, when=phase,
                outcome="skipped" if skipped else "failed")
    report_hook(item, report, RuntimeError("do not retain late failure body"))
    assert report.outcome == "failed"
    assert not config._opencode_candidate_completed
    evidence = json.loads(run.evidence_path.read_text())
    assert evidence["status"] == "failed"
    assert evidence["first_failure"] == {"type": "RuntimeError", "stage": "fake-late-report"}
    assert "do not retain" not in run.evidence_path.read_text()


def test_finalizer_cannot_leave_unfinished_evidence_running(tmp_path, monkeypatch):
    monkeypatch.setattr(CandidateRun, "prepare", lambda run: run.evidence.update(status="running"))
    config = explicit_config()
    request = NS(config=config, node=NS(user_properties=[]))
    generator = gate.opencode_candidate.__wrapped__(request, tmp_path)
    run = next(generator)
    with pytest.raises(StopIteration):
        next(generator)
    assert json.loads(run.evidence_path.read_text())["status"] == "failed"


@pytest.mark.parametrize("value", ["relative", "./opencode"])
def test_relative_binary_is_rejected(value):
    with pytest.raises(RuntimeError, match="absolute"):
        validate_binary(value)


def test_missing_preinstalled_dependency_blocks_before_any_execution(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No subprocess is allowed in this pure test")

    def dependency(name):
        if name == "numpy":
            raise ImportError("fixture missing dependency")
        return object()

    monkeypatch.setattr("opencode_candidate_support.subprocess.run", forbidden)
    monkeypatch.setattr("opencode_candidate_support.subprocess.Popen", forbidden)
    monkeypatch.setattr("opencode_candidate_support.importlib.import_module", dependency)
    monkeypatch.setattr("opencode_candidate_support.sys.platform", "linux")
    run = CandidateRun(tmp_path, "1.18.35", "/not-executed/opencode", "contract")
    with pytest.raises(RuntimeError, match="BLOCKED.*numpy"):
        run.prepare()
    assert run.evidence["status"] == "blocked"


def test_missing_binary_and_wrapper_are_blocked(tmp_path):
    with pytest.raises(RuntimeError, match="missing"):
        validate_binary(tmp_path / "missing")
    wrapper = tmp_path / "opencode"
    wrapper.write_text("#!/bin/sh\nprintf 1.18.35")
    wrapper.chmod(0o700)
    with pytest.raises(RuntimeError, match="wrapper"):
        validate_binary(wrapper)


def test_elf_identity_check_reads_but_never_executes_file(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No subprocess is allowed in this pure test")
    monkeypatch.setattr("opencode_candidate_support.subprocess.run", forbidden)
    monkeypatch.setattr("opencode_candidate_support.subprocess.Popen", forbidden)
    fake = tmp_path / "never-execute-this-fixture"
    fake.write_bytes(b"\x7fELFnot-a-real-executable")
    fake.chmod(0o700)
    assert validate_binary(fake) == fake


@pytest.mark.parametrize("version", [None, False, "1.18.34", "latest", ">=1.18.35", "1.18.35-x", "1.18.35\n"])
def test_only_exact_registered_gate_versions(version):
    with pytest.raises(ValueError):
        select_profile(version)


def test_gate_control_does_not_become_certification_candidate():
    from cpn.frontend.opencode_protocol import DEFAULT_PROFILE
    assert select_profile("1.18.32") is DEFAULT_PROFILE
    assert select_profile("1.18.35").certification_only is True


@pytest.mark.parametrize("raw", [b"1.18.32", b"1.18.35-beta", b"opencode\n1.18.35",
                                 b"1.18.35\nextra", b"\xff1.18.35", b"x" * 4097])
def test_stored_probe_rejects_forged_or_ambiguous_output(raw):
    assert not valid_probe_stdout(raw, "1.18.35")


@pytest.mark.parametrize("raw", [b"1.18.35\n", b"opencode 1.18.35\n", b"opencode\t1.18.35"])
def test_stored_probe_accepts_only_exact_single_line_version(raw):
    assert valid_probe_stdout(raw, "1.18.35")


def test_effect_guard_and_diagnostic_terminal_text_are_pure():
    guard = EffectGuard()
    guard.assert_zero()
    with pytest.raises(AssertionError, match="submit"):
        guard.deny("submit")()
    with pytest.raises(AssertionError, match="submit"):
        guard.assert_zero()
    assert guard.calls == {"submit": 1}
    assert terminal_text(b"\x1b[31mfixture answer\x1b[0m") == "fixture answer"
