"""Nine supplementary offline scenarios; real native owner and Registry only."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import traceback

import pytest
from jsonschema import Draft7Validator
from cpn.rpnh.control_server import OwnerEventLoop
from examples.tool_pipeline import tools
from examples.tool_pipeline.contracts import SCHEMAS, schema_id
from examples.tool_pipeline.host import registration
from examples.tool_pipeline.run import (create_owner, fixture, inspect_registry,
    make_harness, run_pipeline, save_evidence)


def stage(e, name):
    return [r for r in e["resources"] if r["value"]["stage"] == name]


def selected(step, wrapper, label):
    original = tools.TOOLS[step]
    tools.TOOLS[step] = wrapper
    try:
        return registration(test_identity="extra-native/" + label)
    finally:
        tools.TOOLS[step] = original


def case_root(factory, name):
    # Fixed short paths; pytest owns this fresh basetemp only.
    root = factory.getbasetemp() / name
    assert len(str(root / "r" / "owner.sock").encode()) < 108
    root.mkdir(exist_ok=False)
    return root


def native_run(root, **kwargs):
    try:
        return run_pipeline(root / "r", **kwargs)
    except PermissionError:
        (root / "blocked.traceback.txt").write_text(traceback.format_exc())
        pytest.exit("BLOCKED: native AF_UNIX permission denial; no fallback", returncode=2)


def no_publication(e):
    assert e["actual_model_call_counts"] == [0, 0]
    assert not e["terminal_evidence"] and e["final"] is None
    assert not stage(e, "validated") and not stage(e, "final")
    assert "publish_report.run" not in {f["firing"]["transition_id"] for f in e["firings"]}


@pytest.mark.parametrize("mutation", ["negative", "float", "nan", "duplicate", "overlap", "too_large"])
def test_usage_mutations_native(tmp_path_factory, mutation):
    root = case_root(tmp_path_factory, mutation)
    usage = fixture("usage.json")
    if mutation == "negative": usage["intervals"][0]["wh"] = "-1"
    elif mutation == "float": usage["intervals"][0]["wh"] = 1.5
    elif mutation == "nan": usage["intervals"][0]["wh"] = "NaN"
    elif mutation == "duplicate": usage["intervals"][1]["interval_id"] = "A"
    elif mutation == "overlap": usage["intervals"][1]["start"] = "2026-01-01T00:30:00Z"
    elif mutation == "too_large": usage["intervals"][0]["wh"] = "1000000001"
    (root / "input.json").write_text(json.dumps(usage, indent=2) + "\n")
    e = native_run(root, usage=usage)
    save_evidence(e, root / "e")  # Preserve evidence even if subsequent assertions fail.
    no_publication(e)
    assert e["transport"] == "native_owner_socket"
    assert e["stop_reason"] == "quiescent_marking"
    assert {r["value"]["data"]["step"] for r in stage(e, "rejection")} == {"check_usage_input"}
    assert all(f["state"] == "PUBLISHED" for f in e["firings"])
    assert not stage(e, "usage_checked") and not stage(e, "joined")


def test_source_version_native(tmp_path_factory):
    root = case_root(tmp_path_factory, "source")
    original = tools.TOOLS["compute_cost"]
    mutations = []
    async def wrong_source(**kwargs):
        result = await original(**kwargs)
        candidate = result.products["candidate"]
        before = copy.deepcopy(candidate["source_refs"])
        # A real delivered joined version, different from the original usage source.
        candidate["source_refs"]["usage"]["resource_version_id"] = kwargs["inputs"]["joined"]["ref"]["resource_version_id"]
        Draft7Validator(SCHEMAS[schema_id("candidate")]).validate(candidate)
        mutations.append({"before": before, "after": copy.deepcopy(candidate["source_refs"]), "schema_valid": True})
        return result
    reg = selected("compute_cost", wrong_source, "source-version")
    e = native_run(root, selected_registration=reg)
    save_evidence(e, root / "e")
    (root / "mutation.json").write_text(json.dumps(mutations, indent=2) + "\n")
    no_publication(e)
    assert len(mutations) == 1 and mutations[0]["before"] != mutations[0]["after"]
    assert len(stage(e, "candidate")) == 1
    rejected = stage(e, "rejection")
    assert len(rejected) == 1
    assert rejected[0]["value"]["data"] == {"step": "validate_report", "reason": "candidate source versions or policy differ"}
    assert e["stop_reason"] == "quiescent_marking"
    assert all(f["state"] == "PUBLISHED" for f in e["firings"])


def test_forged_parents_native(tmp_path_factory):
    root = case_root(tmp_path_factory, "parents")
    original = tools.TOOLS["read_usage"]
    calls = []
    async def forged_read(**kwargs):
        calls.append("read_usage")
        result = await original(**kwargs)
        # Schema-valid parents, but a different version than the claimed source.
        value = result.products["raw"]
        value["parents"][0]["resource_version_id"] = "forged-parent-version"
        Draft7Validator(SCHEMAS[schema_id("usage_raw")]).validate(value)
        return result
    reg = selected("read_usage", forged_read, "forged-parents")
    owner = create_owner(root / "r", selected_registration=reg)
    try:
        loop = OwnerEventLoop(owner, root / "o.sock")
    except PermissionError:
        (root / "blocked.traceback.txt").write_text(traceback.format_exc())
        save_evidence(inspect_registry(owner._core), root / "blocked-e")
        pytest.exit("BLOCKED: native AF_UNIX permission denial; no fallback", returncode=2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            runner = make_harness(owner, loop, pool)
            try:
                try:
                    runner.exact_execute()
                except ValueError:
                    expected_traceback = traceback.format_exc()
                    (root / "expected.traceback.txt").write_text(expected_traceback)
                else:
                    pytest.fail("HOST publication guard accepted forged parents")
                # exact_execute drains live sibling callbacks before raising;
                # real rebuilt Harness then observes unresolved authority, no replay.
                before = inspect_registry(owner._core)
                save_evidence(before, root / "before-rebuild")
                rebuilt = make_harness(owner, loop, pool).exact_execute()
                e = inspect_registry(owner._core)
                save_evidence(e, root / "e")
                (root / "rebuild.json").write_text(json.dumps({
                    "stop_reason": rebuilt.stop_reason, "calls": calls,
                    "new_trace_transitions": [t.transition_id for t in rebuilt.operation_execution_trace],
                    "pending_after_failure": len(runner._pending),
                    "sibling_stage_before_rebuild": len(stage(before, "tariff_raw")),
                    "sibling_stage_after_rebuild": len(stage(e, "tariff_raw"))}, indent=2) + "\n")
                assert "examples/tool_pipeline/host.py" in expected_traceback
                assert "tool output provenance differs from exact delivered inputs or policy" in expected_traceback
                assert rebuilt.stop_reason == "reconciliation_required"
                assert calls == ["read_usage"] and not runner._pending
                assert len(stage(e, "tariff_raw")) == 1
                assert not stage(e, "usage_raw") and not stage(e, "usage_validation_source")
                unresolved = [f for f in e["firings"] if f["state"] != "PUBLISHED"]
                assert {f["firing"]["transition_id"] for f in unresolved} == {"read_usage.run"}
                no_publication(e)
            finally:
                # Assertion failures must still pump the real owner gateway
                # before executor shutdown waits for admitted workers.
                if runner._pending:
                    try:
                        runner.exact_execute()
                    except Exception:
                        (root / "cleanup.traceback.txt").write_text(traceback.format_exc())
                if not (root / "e").exists():
                    save_evidence(inspect_registry(owner._core), root / "failure-e")
    finally:
        loop.close()


def test_single_worker_native(tmp_path_factory):
    root = case_root(tmp_path_factory, "single")
    e = native_run(root, max_in_flight=1)
    save_evidence(e, root / "e")
    active = maximum = 0
    capacity = []
    for event in e["events"]:
        if event["type"] == "firing_admitted/v1": active += 1
        elif event["type"] == "transition_firing_settled/v1": active -= 1
        else: continue
        capacity.append({"ordinal": event["ordinal"], "event": event["type"], "active": active})
        assert 0 <= active <= 1
        maximum = max(maximum, active)
    (root / "capacity.json").write_text(json.dumps(capacity, indent=2) + "\n")
    assert maximum == 1 and active == 0
    assert e["transport"] == "native_owner_socket" and e["stop_reason"] == "terminal"
    assert len(e["terminal_evidence"]) == 1
    assert e["terminal_evidence"][0]["run_outcome"] == "complete"
    assert e["final"]["data"]["report"] == fixture("expected-report.json")
    assert len(e["firings"]) == 10 and len(e["resources"]) == 12
    assert e["actual_model_call_counts"] == [0, 0]


def test_missing_usage_join_native(tmp_path_factory):
    """Mirrors the default fixture: tariff settles while usage body is held."""
    import threading
    import time
    from cpn.rpnh.marking import TeamNetMarking
    from cpn.rpnh.registry.module_execution import install_active_module_claims
    from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
    root = case_root(tmp_path_factory, "mirror")
    entered, release = threading.Event(), threading.Event()
    observations = []
    original = tools.TOOLS["read_usage"]
    async def gated_usage(**kwargs):
        observations.append({"branch": "usage", "phase": "entered", "thread": threading.get_ident()})
        entered.set()
        if not release.wait(180):
            raise AssertionError("mirrored usage gate was not released")
        result = await original(**kwargs)
        observations.append({"branch": "usage", "phase": "returned", "thread": threading.get_ident()})
        return result
    reg = selected("read_usage", gated_usage, "missing-usage-join")
    owner = create_owner(root / "r", selected_registration=reg)
    try:
        loop = OwnerEventLoop(owner, root / "o.sock")
    except PermissionError:
        (root / "blocked.traceback.txt").write_text(traceback.format_exc())
        save_evidence(inspect_registry(owner._core), root / "blocked-e")
        pytest.exit("BLOCKED: native AF_UNIX permission denial; no fallback", returncode=2)
    snapshots = {}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            runner = make_harness(owner, loop, pool)
            try:
                assert len(runner.schedule_ready()) == 2
                deadline = time.monotonic() + 180
                while not (entered.is_set() and any(t.transition_id == "normalize_tariff.run" for t in runner._trace)):
                    assert time.monotonic() < deadline, "mirror observation timed out"
                    loop.dispatch_ready(timeout=0.01)
                    runner.schedule_ready()
                executable, structure, marking = hydrate_module_runtime(owner._core)
                local = TeamNetMarking.from_authority(structure, marking)
                active = install_active_module_claims(owner._core, local, executable.net_ref)
                middle = inspect_registry(owner._core)
                save_evidence(middle, root / "held-e")
                normalized = stage(middle, "tariff_normalized")
                before = owner._core.event_store.max_ordinal()
                admitted = owner.admit("join_intervals.run", logical_tau=99, command_id="extra-native:missing-usage-join")
                after = owner._core.event_store.max_ordinal()
                snapshots.update(enabled=list(local.enabled_transitions()),
                    active=[{"transition": a.transition_id, "firing_version_id": str(a.transition_firing_ref.version_id)} for a in active],
                    tariff_normalized_refs=[r["ref"] for r in normalized],
                    ordinal_before_admit=before, ordinal_after_admit=after,
                    join_admitted=admitted is not None)
                assert {a.transition_id for a in active} == {"read_usage.run"}
                assert len(normalized) == 1
                assert not stage(middle, "usage_raw") and not stage(middle, "usage_normalized")
                assert {t.transition_id for t in runner._trace} >= {"read_tariff.run", "check_tariff_input.run", "normalize_tariff.run"}
                assert "join_intervals.run" not in local.enabled_transitions()
                assert "join_intervals.run" not in {f["firing"]["transition_id"] for f in middle["firings"]}
                assert admitted is None and before == after
                assert not any(o["phase"] == "returned" for o in observations)
            finally:
                # Always release AND pump before waiting for pool shutdown.
                release.set()
                try:
                    result = runner.exact_execute()
                finally:
                    e = inspect_registry(owner._core)
                    save_evidence(e, root / "e")
                    (root / "observations.json").write_text(json.dumps({"observations": observations, "snapshots": snapshots}, indent=2) + "\n")
            assert result.stop_reason == "terminal"
            assert len(e["terminal_evidence"]) == 1 and e["terminal_evidence"][0]["run_outcome"] == "complete"
            assert e["final"]["data"]["report"]["total_cny"] == "1.70"
            assert e["actual_model_call_counts"] == [0, 0]
    finally:
        release.set()
        loop.close()
