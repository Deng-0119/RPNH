"""Focused offline evidence. Native sockets by default; pipe must be explicit."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import threading
import time

import pytest

from cpn.components.execution_services import ExecutionServices, invoke_registered_tool
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.registry.module_execution import active_module_firings, install_active_module_claims
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.operations import OperationAuthorityError
from examples.tool_pipeline import tools
from examples.tool_pipeline.contracts import RULES
from examples.tool_pipeline.host import registration, tool_key
from examples.tool_pipeline.run import (create_owner, fixture, inspect_registry, make_harness,
                                      readback, run_pipeline, save_evidence)


def _snapshot(owner):
    executable, structure, marking = hydrate_module_runtime(owner._core)
    local = TeamNetMarking.from_authority(structure, marking)
    active = install_active_module_claims(owner._core, local, executable.net_ref)
    return {"enabled": list(local.enabled_transitions()),
            "active": [{"transition": item.transition_id, "firing_ref": str(item.transition_firing_ref.version_id)}
                       for item in active], "ordinal": owner._core.event_store.max_ordinal()}


def _pump(loop, runner, condition, timeout=180):
    # Timeout is a deadlock bound, not evidence of concurrency or ordering.
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("event-controlled observation timed out")
        loop.dispatch_ready(timeout=0.01)
        runner.schedule_ready()


def _stage(evidence, name):
    return [r for r in evidence["resources"] if r["value"]["stage"] == name]


@pytest.fixture(scope="module")
def controlled_run(tmp_path_factory, loop_factory):
    root = tmp_path_factory.mktemp("tool-pipeline-controlled")
    entered = {name: threading.Event() for name in ("usage", "tariff")}
    release = {name: threading.Event() for name in ("usage", "tariff")}
    observations = []
    originals = {name: tools.TOOLS["read_" + name] for name in entered}

    async def read_usage(**kwargs):
        observations.append({"branch": "usage", "phase": "entered", "thread": threading.get_ident()})
        entered["usage"].set()
        if not release["usage"].wait(180):
            raise AssertionError("usage gate was not released")
        result = await originals["usage"](**kwargs)
        observations.append({"branch": "usage", "phase": "returned"})
        return result

    async def read_tariff(**kwargs):
        observations.append({"branch": "tariff", "phase": "entered", "thread": threading.get_ident()})
        entered["tariff"].set()
        if not release["tariff"].wait(180):
            raise AssertionError("tariff gate was not released")
        result = await originals["tariff"](**kwargs)
        observations.append({"branch": "tariff", "phase": "returned"})
        return result

    tools.TOOLS.update(read_usage=read_usage, read_tariff=read_tariff)
    try:
        selected = registration(test_identity="controlled-read-gates/v1")
    finally:
        tools.TOOLS.update({"read_" + name: fn for name, fn in originals.items()})
    owner = create_owner(root / "run", selected_registration=selected)
    loop = loop_factory(owner, root / "owner.sock")
    snapshots = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            runner = make_harness(owner, loop, pool)
            observation_error = None
            try:
                assert len(runner.schedule_ready()) == 2
                _pump(loop, runner, lambda: all(event.is_set() for event in entered.values()))
                snapshots["both_read_tools_entered"] = _snapshot(owner)
                assert len(snapshots["both_read_tools_entered"]["active"]) == 2
                assert {x["transition"] for x in snapshots["both_read_tools_entered"]["active"]} == {
                    "read_usage.run", "read_tariff.run"}
                assert not any(row["phase"] == "returned" for row in observations)
                before = owner._core.event_store.max_ordinal()
                for name in ("join_intervals.run", "publish_report.run"):
                    assert owner.admit(name, logical_tau=99, command_id="test:disabled:" + name) is None
                assert owner._core.event_store.max_ordinal() == before
                # Registered-tool identity and allowed-tool checks reject before
                # creating/awaiting any additional coroutine.
                execution = next(e for e in runner._pending.values()
                                 if e.operation.firing.transition_id == "read_usage.run")
                kernel, repository = owner.operation_repository()
                declared = selected.declaration("tool", tool_key("read_usage"))
                with pytest.raises(OperationAuthorityError):
                    invoke_registered_tool(owner, kernel, repository, execution, tool_key("read_usage"),
                        identity={**declared["identity"], "revision": "wrong"}, contracts=declared["contracts"])
                with pytest.raises(OperationAuthorityError):
                    invoke_registered_tool(owner, kernel, repository, execution, tool_key("publish_report"),
                        identity=declared["identity"], contracts=declared["contracts"])
                release["usage"].set()
                _pump(loop, runner, lambda: any(t.transition_id == "normalize_usage.run"
                                              for t in runner._trace))
                snapshots["usage_normalized_while_tariff_reading"] = _snapshot(owner)
                slow = snapshots["usage_normalized_while_tariff_reading"]
                assert {x["transition"] for x in slow["active"]} == {"read_tariff.run"}
                assert "join_intervals.run" not in slow["enabled"]
                # This reads the actual committed normalized Registry resource.
                middle = inspect_registry(owner._core)
                assert len(_stage(middle, "usage_normalized")) == 1
                assert not _stage(middle, "tariff_raw")
                snapshots["normalized_resource_ref"] = _stage(middle, "usage_normalized")[0]["ref"]
                # Same Registry is not ambient permission to read this sibling.
                from cpn.rpnh.registry.errors import UnauthorizedResourceDelivery
                tariff_execution = next(e for e in runner._pending.values()
                    if e.operation.firing.transition_id == "read_tariff.run")
                normalized = next(t.output_resource_refs[0] for t in runner._trace
                    if t.transition_id == "normalize_usage.run")
                services = ExecutionServices(owner=owner, event_loop=loop)
                with pytest.raises(UnauthorizedResourceDelivery):
                    services._verify_resource(tariff_execution.operation.canonical, normalized)
                snapshots["unclaimed_sibling_read"] = "UnauthorizedResourceDelivery"

            except BaseException as exc:
                observation_error = exc
            finally:
                release["usage"].set()
                release["tariff"].set()
            # Even a failed observation must pump queued worker gateway work
            # before ThreadPoolExecutor.shutdown waits for those same workers.
            try:
                result = runner.exact_execute()
            finally:
                if observation_error is not None:
                    raise observation_error
            assert result.stop_reason == "terminal"
            # Reconstructing the Harness must observe terminal, never run again.
            before = owner._core.event_store.max_ordinal()
            restarted = make_harness(owner, loop, pool).exact_execute()
            assert restarted.stop_reason == "terminal" and not restarted.operation_execution_trace
            assert owner._core.event_store.max_ordinal() == before
        evidence = readback(root / "run")
        evidence.update(stop_reason=result.stop_reason, transport=loop_factory.__name__)
        save_evidence(evidence, root / "export")
        (root / "controlled-observations.json").write_text(json.dumps({
            "observations": observations, "snapshots": snapshots}, indent=2) + "\n")
        return {"root": root, "evidence": evidence, "observations": observations,
                "snapshots": snapshots, "trace": result.operation_execution_trace}
    finally:
        loop.close()


def test_complete_report_and_independent_validation(controlled_run):
    e = controlled_run["evidence"]
    assert e["actual_model_call_counts"] == [0, 0]
    assert len(e["terminal_evidence"]) == 1
    assert e["terminal_evidence"][0]["run_outcome"] == "complete"
    report = e["final"]["data"]["report"]
    assert report == fixture("expected-report.json")
    assert report["total_cny"] == "1.70" and report["total_kwh"] == "2.000"
    assert [row["amount_cny"] for row in report["intervals"]] == ["1.20", "0.50"]
    assert len(e["firings"]) == 10 and len(e["resources"]) == 12
    assert e["final"]["data"]["oracle"] == "integer-fen-half-up/v1"


def test_real_overlap_capacity_and_branch_progress(controlled_run):
    e = controlled_run["evidence"]
    observations = controlled_run["observations"]
    assert {row["branch"] for row in observations[:2]} == {"usage", "tariff"}
    assert all(row["phase"] == "entered" for row in observations[:2])
    assert len({row["thread"] for row in observations[:2]}) == 2
    assert controlled_run["snapshots"]["normalized_resource_ref"]["resource_version_id"]
    active, maximum = 0, 0
    for event in e["events"]:
        if event["type"] == "firing_admitted/v1":
            active += 1
            maximum = max(maximum, active)
        elif event["type"] == "transition_firing_settled/v1":
            active -= 1
        assert 0 <= active <= 2
    assert maximum == 2 and active == 0


def test_exact_lineage_and_partial_order(controlled_run):
    e = controlled_run["evidence"]
    traces = {t.transition_id.split(".")[0]: t for t in controlled_run["trace"]}
    resources = {r["ref"]["resource_version_id"]: r for r in e["resources"]}
    for step, trace in traces.items():
        actual = {str(i.resource_ref.resource_version_id) for i in trace.execution.operation.inputs}
        for ref in trace.output_resource_refs:
            r = resources[str(ref.resource_version_id)]
            assert {x["resource_version_id"] for x in r["value"]["parents"]} == actual
            assert {x["version_id"] for x in r["metadata"]["reference_provenance"]["derived_from_refs"]} == actual
            assert r["metadata"]["origin_kind"] == "petri_output"
            assert r["metadata"]["content_schema_authority_ref"]["resource_version_id"]
            assert r["metadata"]["producer_ref"]["version_id"] == str(trace.execution.operation.canonical.context.invocation_ref.version_id)
    join = traces["join_intervals"].execution.operation.inputs
    assert len(join) == 2
    expected = {str(traces[s].output_resource_refs[0].resource_version_id)
                for s in ("normalize_usage", "normalize_tariff")}
    assert {str(i.resource_ref.resource_version_id) for i in join} == expected
    intervals = {}
    for firing in e["firings"]:
        events = firing["ordered_event_records"]
        admitted = min(x["ordinal"] for x in events)
        settled = next(x["ordinal"] for x in events if x["event_type"] == "transition_firing_settled/v1")
        intervals[firing["firing"]["transition_id"].split(".")[0]] = (admitted, settled)
    for branch in ("usage", "tariff"):
        chain = ["read_" + branch, "check_" + branch + "_input", "normalize_" + branch, "join_intervals",
                 "compute_cost", "validate_report", "publish_report"]
        assert all(intervals[a][1] < intervals[b][0] for a, b in zip(chain, chain[1:]))


def test_readback_needs_no_exports_or_process_cache(controlled_run):
    root = controlled_run["root"]
    import shutil
    shutil.rmtree(root / "export")
    again = readback(root / "run")
    assert again["final"] == controlled_run["evidence"]["final"]
    assert again["checkpoint_ref"] == controlled_run["evidence"]["checkpoint_ref"]
    assert again["actual_model_call_counts"] == [0, 0]
    save_evidence(again, root / "rebuilt-export")


def test_round_each_interval_half_up(tmp_path, loop_factory):
    e = run_pipeline(tmp_path / "run", usage=fixture("usage-rounding.json"),
        tariff=fixture("tariff-rounding.json"), event_loop_factory=loop_factory)
    report = e["final"]["data"]["report"]
    assert [r["amount_cny"] for r in report["intervals"]] == ["0.01", "0.01"]
    assert report["total_cny"] == "0.02"  # rounding the aggregate would be 0.01
    save_evidence(e, tmp_path / "export")


@pytest.mark.parametrize("case", ["unit", "time", "both_invalid", "candidate"])
def test_invalid_data_cannot_reach_final(tmp_path, loop_factory, monkeypatch, case):
    usage, tariff = fixture("usage.json"), fixture("tariff.json")
    if case == "unit":
        usage["unit"] = "kWh"
    elif case == "time":
        tariff["intervals"][0]["start"] = "2026-01-01T00:01:00Z"
    elif case == "both_invalid":
        usage["unit"], tariff["unit"] = "watt", "USD/kWh"
    elif case == "candidate":
        original = tools.compute_cost
        async def wrong_compute(**kwargs):
            result = await original(**kwargs)
            result.products["candidate"]["data"]["total_cny"] = "1.71"
            return result
        monkeypatch.setitem(tools.TOOLS, "compute_cost", wrong_compute)
    e = run_pipeline(tmp_path / "run", usage=usage, tariff=tariff,
        selected_registration=registration(test_identity=case), event_loop_factory=loop_factory)
    assert e["stop_reason"] == "quiescent_marking"
    assert not e["terminal_evidence"] and e["final"] is None
    assert not _stage(e, "validated") and not _stage(e, "final")
    steps = {f["firing"]["transition_id"] for f in e["firings"]}
    assert "publish_report.run" not in steps
    rejected = _stage(e, "rejection")
    expected = {"unit": {"check_usage_input"}, "time": {"join_intervals"},
                "both_invalid": {"check_usage_input", "check_tariff_input"}, "candidate": {"validate_report"}}[case]
    assert {x["value"]["data"]["step"] for x in rejected} == expected
    assert all(f["state"] == "PUBLISHED" for f in e["firings"])
    if case in {"unit", "both_invalid"}:
        assert "normalize_usage.run" not in steps and "join_intervals.run" not in steps
    assert e["actual_model_call_counts"] == [0, 0]
    save_evidence(e, tmp_path / "export")


@pytest.mark.parametrize("mutation", ["negative", "float", "duplicate", "nan", "timezone", "overlap", "too_large"])
def test_input_middleware_rejects_invalid_values(mutation):
    value = fixture("usage.json")
    if mutation == "negative": value["intervals"][0]["wh"] = "-1"
    elif mutation == "float": value["intervals"][0]["wh"] = 1.5
    elif mutation == "nan": value["intervals"][0]["wh"] = "NaN"
    elif mutation == "duplicate": value["intervals"][1]["interval_id"] = "A"
    elif mutation == "timezone": value["timezone"] = "Asia/Shanghai"
    elif mutation == "overlap": value["intervals"][1]["start"] = "2026-01-01T00:30:00Z"
    elif mutation == "too_large": value["intervals"][0]["wh"] = "1000000001"
    with pytest.raises(ValueError):
        tools.check_document(value, "usage", RULES)


def test_worker_exception_keeps_unresolved_firing(tmp_path, loop_factory, monkeypatch):
    calls = []
    async def crashing_read(**kwargs):
        calls.append("read_usage")
        raise RuntimeError("explicit test worker crash")
    monkeypatch.setitem(tools.TOOLS, "read_usage", crashing_read)
    owner = create_owner(tmp_path / "run", selected_registration=registration(test_identity="worker-crash"))
    loop = loop_factory(owner, tmp_path / "owner.sock")
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            runner = make_harness(owner, loop, pool)
            with pytest.raises(RuntimeError, match="test worker crash"):
                runner.exact_execute()
            reconstructed = make_harness(owner, loop, pool).exact_execute()
            assert reconstructed.stop_reason == "reconciliation_required"
            assert calls == ["read_usage"]
        e = inspect_registry(owner._core)
        assert not e["terminal_evidence"] and e["final"] is None
        active = _snapshot(owner)["active"]
        assert {x["transition"] for x in active} == {"read_usage.run"}
        assert len(_stage(e, "tariff_raw")) == 1
        assert not _stage(e, "candidate")
        assert e["actual_model_call_counts"] == [0, 0]
        save_evidence(e, tmp_path / "export")
    finally:
        loop.close()


def test_truncated_final_payload_fails_readback(controlled_run, tmp_path):
    # Explicit copied-store fault injection; no mutation of the accepted run.
    import shutil
    from cpn.rpnh.registry._registry import _RegistryCore
    from cpn.rpnh.registry.object_store import ObjectIntegrityError
    from cpn.rpnh.registry.schema_catalog import SchemaCatalog
    clone = tmp_path / "damaged-run"
    shutil.copytree(controlled_run["root"] / "run", clone)
    catalog = SchemaCatalog()
    registration().bind_schema_catalog(catalog)
    core = _RegistryCore(clone, create=False, read_only=True, catalog=catalog)
    ref = controlled_run["evidence"]["terminal_evidence"][0]["terminal_result_ref"]
    resource = core.get_version(ref["version_id"])
    path = core.object_store.path_for_version(resource.version_id)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ObjectIntegrityError):
        readback(clone)


@pytest.mark.parametrize("mutation", ["source_version", "line_amount", "total_usage", "missing_line"])
def test_independent_validator_rejects_other_schema_valid_candidates(controlled_run, mutation):
    # Business-function unit coverage. The 1.71 integration case above separately
    # proves that a real rejected validator prevents PN final admission.
    e = controlled_run["evidence"]
    selected = {"candidate": _stage(e, "candidate")[0],
                "usage_source": _stage(e, "usage_validation_source")[0],
                "tariff_source": _stage(e, "tariff_validation_source")[0]}
    inputs = {role: {"ref": row["ref"], "value": copy.deepcopy(row["value"])}
              for role, row in selected.items()}
    candidate = inputs["candidate"]["value"]
    if mutation == "source_version":
        candidate["source_refs"]["usage"]["resource_version_id"] = inputs["usage_source"]["ref"]["resource_version_id"]
    elif mutation == "line_amount":
        candidate["data"]["intervals"][0]["amount_cny"] = "1.21"
    elif mutation == "total_usage":
        candidate["data"]["total_kwh"] = "2.001"
    elif mutation == "missing_line":
        candidate["data"]["intervals"].pop()
    result = asyncio.run(tools.validate_report(inputs=inputs, rules=RULES))
    assert result.outcome == "rejected" and set(result.products) == {"rejection"}
