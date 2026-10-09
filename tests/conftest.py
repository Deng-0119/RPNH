"""Explicit, test-only OpenCode candidate lane selection.

No options changes the existing pinned PTY test's selection or behavior.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("opencode-certification")
    group.addoption("--opencode-certify-version", default=None,
                    help="Exact registered version; no production pin change")
    group.addoption("--opencode-certify-binary", default=None,
                    help="Absolute path to a pre-provisioned stock Linux ELF executable")
    group.addoption("--opencode-certify-lane", default=None,
                    choices=("contract", "registry-read"),
                    help="Run one limited native smoke lane, never the full certification matrix")


def pytest_configure(config):
    config.addinivalue_line("markers", "opencode_candidate(lane): explicit native certification smoke")
    values = [config.getoption("opencode_certify_" + suffix)
              for suffix in ("version", "binary", "lane")]
    if any(value is not None for value in values) and not all(values):
        raise pytest.UsageError(
            "BLOCKED: certification requires all three --opencode-certify- options")
    config._opencode_candidate_request = tuple(values) if all(values) else None
    config._opencode_candidate_completed = False


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    request = config._opencode_candidate_request
    if request is None:
        for item in items:
            if item.get_closest_marker("opencode_candidate"):
                item.add_marker(pytest.mark.skip(reason="Native candidate lane not requested"))
        return
    lane = request[2]
    chosen = [item for item in items
              if item.get_closest_marker("opencode_candidate") is not None
              and item.get_closest_marker("opencode_candidate").args == (lane,)]
    if len(chosen) != 1:
        raise pytest.UsageError(
            "BLOCKED: explicitly requested lane must select exactly its one smoke test; "
            "run tests/test_opencode_candidate_native.py without -k/-m exclusions")
    # A certification invocation runs exactly this native scenario, even if the
    # operator accidentally selected the whole repository. Never launch another
    # native test, a PATH-selected binary, or unrelated work as a side effect.
    removed = [item for item in items if item not in chosen]
    items[:] = [item for item in items if item not in removed]
    if removed:
        config.hook.pytest_deselected(items=removed)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (item.config._opencode_candidate_request is not None
            and item.get_closest_marker("opencode_candidate") is not None):
        run = item.funcargs.get("opencode_candidate")
        if report.failed or report.skipped:
            item.config._opencode_candidate_completed = False
            if report.skipped:
                report.outcome = "failed"
                report.longrepr = "BLOCKED: an explicitly requested native lane cannot pass by skipping"
            if run is not None:
                run.fail(call.excinfo.value if call.excinfo is not None else AssertionError())
                # A teardown report can arrive after the yield-fixture finalizer
                # has already written its evidence. Never leave a stale PASS.
                run.write_evidence()
            return
        if report.when == "call" and report.passed:
            if run is None or run.evidence["status"] != "passed-limited-smoke":
                report.outcome = "failed"
                report.longrepr = "BLOCKED: requested lane did not establish terminal smoke evidence"
                item.config._opencode_candidate_completed = False
                if run is not None:
                    run.fail(AssertionError())
                    run.write_evidence()
            else:
                item.config._opencode_candidate_completed = True


def pytest_sessionfinish(session, exitstatus):
    if (session.config._opencode_candidate_request is not None
            and not session.config._opencode_candidate_completed and exitstatus == 0):
        # --collect-only and a post-collection deselection are not evidence.
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def opencode_candidate(request, tmp_path):
    from opencode_candidate_support import CandidateRun
    selected = request.config._opencode_candidate_request
    if selected is None:
        pytest.skip("Native candidate lane not requested")
    run = CandidateRun(tmp_path, *selected)
    try:
        run.prepare()
        yield run
    except BaseException as exc:
        run.fail(exc)
        raise
    finally:
        if run.evidence["status"] == "running":
            run.fail(AssertionError("Scenario did not establish all smoke assertions"))
        run.write_evidence()
        request.node.user_properties.append(("opencode_evidence", str(run.evidence_path)))
