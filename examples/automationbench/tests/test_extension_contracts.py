import json
import zipfile

import pytest

from rpnh_ab import io
from rpnh_ab.cli import main
from rpnh_ab.cohort import plan_for_cohort, resolve_frozen_cases
from rpnh_ab.exporting import export_return
from rpnh_ab.scoring import score_attempt, summarize


def _case(task_id="sales-0001", contract="a" * 64):
    return {"id": task_id, "domain": "sales", "task_name": "sales.demo", "domain_index": 0,
            "example_id": 0, "example_id_assigned_by_adapter": False,
            "task_contract_sha256": contract, "row": {"private": True}}


class _Upstream:
    identity = {"commit": "pinned"}
    def __init__(self, cases): self._cases = cases
    def cases(self, split): return self._cases


def _eligible_attempt(path, task):
    path.mkdir(parents=True)
    io.write_new(path / "task_contract.json", {"private": True})
    io.write_new(path / "attempt.json", {"id": task["id"],
                 "task_contract_sha256": task["task_contract_sha256"],
                 "task_contract_file_sha256": io.file_sha(path / "task_contract.json")})
    io.write_new(path / "scoring_input.json", {"initial_state": {}, "info": {}})
    io.write_new(path / "final_world.json", {"done": True})
    io.write_new(path / "lifecycle.json", {"admitted": True, "execution_status": "host_terminal",
                                             "host_quiescent": True, "world_owner_quiescent": True})


def test_cohort_resolves_only_ordered_selected_tasks_and_freezes_contracts(tmp_path):
    source = tmp_path / "cohort.json"
    io.write_new(source, {"schema": "rpnh-ab-local/stratified-pilot-plan/v1", "split": "public",
                          "selected_tasks": [{"sample_index": 1, "task_id": "sales-0002", "domain": "sales", "task_name": "sales.demo"},
                                             {"sample_index": 2, "task_id": "sales-0001", "domain": "sales", "task_name": "sales.demo"}]})
    upstream = _Upstream([_case("sales-0001"), _case("sales-0002", "b" * 64)])
    plan = plan_for_cohort(upstream, source)
    assert [task["id"] for task in plan["tasks"]] == ["sales-0002", "sales-0001"]
    assert [case["id"] for case in resolve_frozen_cases(upstream, plan)] == ["sales-0002", "sales-0001"]
    upstream._cases[0]["task_contract_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="contract drift"):
        resolve_frozen_cases(upstream, plan)


def test_stale_score_is_rejected_and_summary_marks_it_unscored(tmp_path):
    task = _case()
    attempt = tmp_path / "attempts/sales-0001/a0001"
    _eligible_attempt(attempt, task)
    score_attempt(attempt, lambda *_: {"partial_credit": 1.0, "task_completed_correctly": 1.0}, task=task)
    io.write_new(attempt / "later.json", {})
    # Mutating immutable evidence is not supported in production; replace it here
    # to prove cached-score hash validation is a strict read-time gate.
    (attempt / "final_world.json").write_text('{"done":false}\n')
    with pytest.raises(ValueError, match="stale"):
        score_attempt(attempt, lambda *_: {}, task=task)
    report = summarize({"split": "public", "tasks": [task]}, tmp_path)
    assert report["unscored"] == ["sales-0001"]
    assert report["tasks"][0]["status"] == "ineligible_evidence"


def test_changed_task_contract_is_ineligible_even_before_first_score(tmp_path):
    task = _case()
    attempt = tmp_path / "attempts/sales-0001/a0001"
    _eligible_attempt(attempt, task)
    (attempt / "task_contract.json").write_text('{"private":false}\n')
    with pytest.raises(ValueError, match="task contract file differs"):
        score_attempt(attempt, lambda *_: {}, task=task)


def test_export_includes_normalization_with_exported_byte_inventory(tmp_path):
    task = _case(); work = tmp_path / "work"; work.mkdir()
    io.write_new(work / "plan.json", {"split": "public", "tasks": [task]})
    attempt = work / "attempts/sales-0001/a0001"; _eligible_attempt(attempt, task)
    (attempt / "normalization_events.jsonl").write_text('{"rule":"x"}\n')
    output = tmp_path / "return.zip"; export_return(work, output)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("RETURN_MANIFEST.json"))
        item = next(row for row in manifest["exported_file_inventory"] if row["path"].endswith("normalization_events.jsonl"))
        data = archive.read(item["path"])
        assert item["byte_size"] == len(data)
        assert item["sha256"] == __import__("hashlib").sha256(data).hexdigest()


def test_cli_stop_writes_only_scoped_request_and_reports_durable_state(tmp_path, capsys):
    io.write_new(tmp_path / "plan.json", {"manifest_sha256": "frozen", "tasks": []})
    io.write_new(tmp_path / "batch-status.json", {"status": "running", "terminal": False})
    assert main(["stop", "--work", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "running"
    assert io.load(tmp_path / "stop.request")["manifest_sha256"] == "frozen"


def test_cli_lock_contention_does_not_replace_active_batch_status(
        tmp_path, capsys, monkeypatch,
):
    import rpnh_ab.experiment as experiment
    import rpnh_ab.run_spec as run_spec
    import rpnh_ab.upstream as upstream_module

    work = tmp_path / "work"
    work.mkdir()
    active = {"status": "running", "terminal": False, "owner": "first"}
    io.write_new(work / "batch-status.json", active)
    profile = tmp_path / "profile.json"
    config = tmp_path / "launch.json"
    upstream_path = tmp_path / "upstream"
    profile.write_text("{}")
    config.write_text("{}")
    upstream_path.mkdir()
    monkeypatch.setattr(upstream_module, "Upstream", lambda _path: object())
    monkeypatch.setattr(run_spec, "load_launch", lambda _path: {
        "work": str(work), "upstream": str(upstream_path.resolve()),
        "profile": str(profile.resolve()),
    })
    monkeypatch.setattr(
        experiment, "run_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            experiment.BatchOwnerActive("another process owns this batch")),
    )
    assert main(["run", "--upstream", str(upstream_path), "--work", str(work),
                 "--profile", str(profile), "--config", str(config)]) == 2
    assert io.load(work / "batch-status.json") == active
    assert not (work / "run.error.json").exists()
    assert "BatchOwnerActive" in capsys.readouterr().err


def test_batch_lock_covers_final_summary_and_terminal_status(
        tmp_path, monkeypatch,
):
    import fcntl
    import rpnh_ab.experiment as experiment
    import rpnh_ab.run_spec as run_spec

    work = tmp_path / "work"
    work.mkdir()
    plan = {"schema": "rpnh-ab/cohort-plan/v1", "split": "public",
            "manifest_sha256": "manifest", "tasks": []}
    conditions = {
        "benchmark_spec": {"split": "public", "manifest_sha256": "manifest",
                           "task_contracts": []},
        "execution_spec": {"executor_host": "native"},
        "environment": {"env": "fixed"}, "rpnh": {"runtime": "fixed"},
        "business_tool_helper": {"helper": "fixed"},
        "configured_model": {"profile": "fixed"},
    }
    io.write_new(work / "plan.json", plan)
    io.write_new(work / "conditions.json", conditions)
    io.write_new(work / "doctor.json", {
        "status": "passed", "selected_split": "public",
        "selected_manifest_sha256": "manifest", "ready_for_live_batch": True,
    })
    monkeypatch.setattr(run_spec, "validate_acceptance", lambda *_args: None)
    monkeypatch.setattr(experiment, "resolve_frozen_cases", lambda *_args: [])
    monkeypatch.setattr(experiment, "environment_identity", lambda: {"env": "fixed"})
    monkeypatch.setattr(experiment, "installed_rpnh_identity",
                        lambda: {"runtime": "fixed"})
    monkeypatch.setattr(experiment, "helper_environment", lambda: {"helper": "fixed"})

    lock_observations = []
    def assert_locked():
        with (work / ".batch.lock").open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_observations.append(True)

    monkeypatch.setattr(experiment, "summarize",
                        lambda *_args: (assert_locked(), {"complete": True})[1])
    original_replace = experiment.replace_checkpoint
    def checked_replace(path, value):
        if path.name == "batch-status.json" and value.get("terminal") is True:
            assert_locked()
        original_replace(path, value)
    monkeypatch.setattr(experiment, "replace_checkpoint", checked_replace)
    report = experiment.run_batch(
        object(), work, tmp_path / "profile.json",
        launch={"acceptance": "unused", "native_run_root": None,
                "dsh_checkout": None},
    )
    assert report == {"complete": True}
    assert len(lock_observations) == 2
    assert io.load(work / "batch-status.json")["status"] == "completed"


def test_cli_summarize_refuses_to_overwrite_while_batch_owner_is_active(
        tmp_path, capsys,
):
    from rpnh_ab.experiment import batch_owner_lock

    work = tmp_path / "work"
    work.mkdir()
    io.write_new(work / "plan.json", {"split": "public", "tasks": []})
    existing = {"owner": "live", "status": "partial"}
    io.write_new(work / "summary.live.json", existing)
    with batch_owner_lock(work):
        assert main(["summarize", "--work", str(work)]) == 2
    assert io.load(work / "summary.live.json") == existing
    assert not (work / "summarize.error.json").exists()
    assert "BatchOwnerActive" in capsys.readouterr().err
