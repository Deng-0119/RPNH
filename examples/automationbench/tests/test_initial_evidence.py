"""Deterministic component tests: no model, provider, upstream API or socket."""
import copy
import hashlib
import json
import zipfile

import pytest

from rpnh_ab import experiment, io
from rpnh_ab.broker import Broker
from rpnh_ab.constants import UPSTREAM_COMMIT
from rpnh_ab.exporting import export_return
from rpnh_ab.scoring import initial_world_summary, score_attempt, summarize


PRIVATE_WORLD = "private-world-sentinel-credential"
PRIVATE_PROMPT = "private-prompt-sentinel"
PRIVATE_RUBRIC = "private-rubric-sentinel"
CLOCK = "2026-10-07T08:00:00+00:00"
LATER = "2026-10-07T09:00:00+00:00"


def case(clock=None):
    meta = {} if clock is None else {"current_time": clock}
    row = {"prompt": [{"role": "user", "content": PRIVATE_PROMPT}],
           "info": {"initial_state": {"meta": meta, "secret": PRIVATE_WORLD},
                    "assertions": [PRIVATE_RUBRIC]}}
    return {"id": "synthetic-0001", "domain": "synthetic", "task_name": "synthetic.demo",
            "attempt": "a0001", "task_contract_sha256": io.sha(row), "row": row}


class FakeUpstream:
    identity = {"commit": UPSTREAM_COMMIT, "tracked_changes": ""}
    schemas = [{"function": {"name": "api_fetch", "description": "unchanged fixture"}}]

    def __init__(self, clock=CLOCK, start_error=False):
        self.clock = clock
        self.start_error = start_error
        self.calls = []
        self.scored_inputs = []

    def start(self, row):
        if self.start_error:
            raise RuntimeError("synthetic initialization failure")
        initial = copy.deepcopy(row["info"]["initial_state"])
        world = copy.deepcopy(initial)
        world["meta"].setdefault("current_time", self.clock)
        world["meta"].setdefault("allowed_services", ["synthetic"])
        world["writes"] = []
        self.state = {"world": world, "initial_state": initial, "info": copy.deepcopy(row["info"])}
        return self.state

    def dump_world(self, state):
        # Deliberately return an alias: retaining a live dict is not a snapshot.
        return state["world"]

    def dispatch(self, state, tool, arguments):
        self.calls.append(copy.deepcopy(arguments))
        state["world"]["writes"].append(arguments["value"])
        state["world"]["meta"]["current_time"] = LATER
        state["world"]["meta"]["allowed_services"].append("mutated")
        if arguments.get("raise"):
            raise RuntimeError("synthetic exception after mutation")
        return '{"ok": true}'

    def score(self, world, initial, info):
        self.scored_inputs.append(copy.deepcopy((world, initial, info)))
        return {"partial_credit": 1.0, "task_completed_correctly": 1.0}


class InProcessBroker(Broker):
    """Exercise the real dispatch/checkpoint code without starting IPC."""
    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.closed = True


class FakeDriver:
    def __init__(self, case_value, failure=None):
        self.case = case_value
        self.failure = failure
        self.initial_bytes = None
        self.provenance_bytes = None

    def run(self, **kwargs):
        broker = kwargs["broker"]
        attempt = broker.attempt
        # These durable artifacts must exist before even the first host call.
        self.initial_bytes = (attempt / "world.initial.materialized.json").read_bytes()
        self.provenance_bytes = (attempt / "initial_world_provenance.json").read_bytes()
        provenance = json.loads(self.provenance_bytes)
        assert provenance["world_sha256"] == hashlib.sha256(self.initial_bytes).hexdigest()
        assert provenance["world_byte_size"] == len(self.initial_bytes)
        assert provenance["attempt_file_sha256"] == io.file_sha(attempt / "attempt.json")
        assert provenance["task_contract_sha256"] == self.case["task_contract_sha256"]
        assert provenance["sources"]["upstream_commit"] == UPSTREAM_COMMIT
        assert kwargs["messages"] == self.case["row"]["prompt"]
        assert kwargs["schemas"] == broker.upstream.schemas
        assert not broker.upstream.calls
        assert not (attempt / "tool_events.jsonl").exists()
        assert io.load(attempt / "world.latest.json") == json.loads(self.initial_bytes)
        if self.failure == "before_dispatch":
            raise RuntimeError("synthetic host startup failure")
        for i in range(2):
            response = broker.call({
                "run_id": broker.run_id, "tool": "api_fetch", "operation_id": "ab_api/api_fetch",
                "invocation_id": "fixture", "firing_id": "fixture", "call_id": str(i),
                "arguments": {"value": i, "raise": self.failure == "after_mutation"},
            })
            if self.failure == "after_mutation":
                assert response["ok"] is False
                raise RuntimeError("synthetic interrupted host after failed tool")
            assert response["ok"] is True
        return {"admitted": True, "host_quiescent": True, "manual_stop": False,
                "terminal": {"outcome": "complete"}}


def run_attempt(monkeypatch, work, task=None, *, clock=CLOCK, failure=None):
    import rpnh_ab.drivers
    task = task or case()
    upstream = FakeUpstream(clock, start_error=failure == "initialization")
    driver = FakeDriver(task, failure)
    monkeypatch.setattr(experiment, "Broker", InProcessBroker)
    monkeypatch.setattr(rpnh_ab.drivers, "get_driver", lambda _host: driver)
    attempt = work / "attempts" / task["id"] / "a0001"
    stopped = experiment.one_attempt(upstream, task, attempt, work / "unused-profile.json")
    return task, upstream, driver, attempt, stopped


@pytest.mark.parametrize("runtime_clock", [CLOCK, "2026-11-20T15:43:21.123456+00:00"])
@pytest.mark.parametrize("explicit_clock", [None, "2020-01-02T03:04:05+00:00"])
def test_pre_dispatch_world_is_immutable_and_clock_is_observed_not_frozen(
        tmp_path, monkeypatch, runtime_clock, explicit_clock):
    task = case(explicit_clock)
    original = copy.deepcopy(task)
    task, upstream, driver, attempt, stopped = run_attempt(
        monkeypatch, tmp_path, task, clock=runtime_clock)
    assert stopped is False
    assert task == original
    initial = io.load(attempt / "world.initial.materialized.json")
    assert initial["meta"] == {"current_time": explicit_clock or runtime_clock,
                               "allowed_services": ["synthetic"]}
    assert initial["writes"] == []
    assert (attempt / "world.initial.materialized.json").read_bytes() == driver.initial_bytes
    assert (attempt / "initial_world_provenance.json").read_bytes() == driver.provenance_bytes
    assert io.load(attempt / "world.latest.json")["writes"] == [0, 1]
    assert io.load(attempt / "final_world.json")["meta"]["current_time"] == LATER
    scoring = io.load(attempt / "scoring_input.json")
    assert scoring == {"initial_state": original["row"]["info"]["initial_state"],
                       "info": original["row"]["info"]}
    assert upstream.scored_inputs[0][1:] == (scoring["initial_state"], scoring["info"])
    assert initial_world_summary(attempt)["current_time"] == (explicit_clock or runtime_clock)
    assert initial_world_summary(attempt)["status"] == "captured"
    assert upstream.normalization_log is None
    with pytest.raises(FileExistsError):
        experiment.preserve_initial_world(upstream, upstream.state, attempt)
    assert (attempt / "world.initial.materialized.json").read_bytes() == driver.initial_bytes


@pytest.mark.parametrize("failure", ["initialization", "before_dispatch", "after_mutation"])
def test_early_failures_retain_available_evidence_without_scoring(tmp_path, monkeypatch, failure):
    task, upstream, driver, attempt, stopped = run_attempt(monkeypatch, tmp_path, failure=failure)
    assert stopped is True
    record = io.load(attempt / "attempt.json")
    assert record["initial_evidence_sources"]["upstream_commit"] == UPSTREAM_COMMIT
    assert record["task_contract_file_sha256"] == io.file_sha(attempt / "task_contract.json")
    assert io.load(attempt / "lifecycle.json")["world_owner_quiescent"] is False
    assert not (attempt / "final_world.json").exists()
    assert not list(attempt.glob("score-*.json"))
    assert not upstream.scored_inputs
    if failure == "initialization":
        assert initial_world_summary(attempt) == {"status": "unknown"}
        assert not (attempt / "world.initial.materialized.json").exists()
    else:
        assert (attempt / "world.initial.materialized.json").read_bytes() == driver.initial_bytes
        assert initial_world_summary(attempt)["status"] == "captured"
        assert io.load(attempt / "world.initial.materialized.json")["writes"] == []
    if failure == "after_mutation":
        assert io.load(attempt / "world.latest.json")["writes"] == [0]
    assert upstream.normalization_log is None


def test_scoring_input_write_failure_still_retains_initial_world(tmp_path, monkeypatch):
    original_write = experiment.write_new
    def fail_scoring_input(path, value):
        if path.name == "scoring_input.json":
            raise OSError("synthetic later evidence write failure")
        original_write(path, value)
    monkeypatch.setattr(experiment, "write_new", fail_scoring_input)
    _, upstream, driver, attempt, stopped = run_attempt(monkeypatch, tmp_path)
    assert stopped is True
    assert driver.initial_bytes is None  # The host never started.
    assert upstream.calls == []
    assert initial_world_summary(attempt)["status"] == "captured"
    assert not (attempt / "scoring_input.json").exists()


def test_provenance_write_failure_keeps_snapshot_and_never_dispatches(tmp_path, monkeypatch):
    original_write = experiment.write_new
    def fail_provenance(path, value):
        if path.name == "initial_world_provenance.json":
            raise OSError("synthetic provenance write failure")
        original_write(path, value)
    monkeypatch.setattr(experiment, "write_new", fail_provenance)
    _, upstream, driver, attempt, stopped = run_attempt(monkeypatch, tmp_path)
    assert stopped is True
    assert driver.initial_bytes is None
    assert upstream.calls == []
    assert io.load(attempt / "world.initial.materialized.json")["meta"]["current_time"] == CLOCK
    assert initial_world_summary(attempt) == {"status": "unknown"}


def test_driver_preparation_failure_retains_source_identity(tmp_path, monkeypatch):
    import rpnh_ab.drivers
    upstream = FakeUpstream()
    task = case()
    attempt = tmp_path / "attempt"
    def unavailable_driver(_host):
        raise ValueError("synthetic unsupported driver")
    monkeypatch.setattr(rpnh_ab.drivers, "get_driver", unavailable_driver)
    assert experiment.one_attempt(upstream, task, attempt, tmp_path / "unused-profile") is True
    assert io.load(attempt / "attempt.json")["initial_evidence_sources"]["upstream_commit"] == UPSTREAM_COMMIT
    assert io.load(attempt / "lifecycle.json")["execution_status"] == "configuration_blocked"
    assert initial_world_summary(attempt) == {"status": "unknown"}
    assert upstream.calls == []
    assert upstream.normalization_log is None


def test_summary_exposes_only_metadata_and_snapshot_mismatch_does_not_change_score(tmp_path, monkeypatch):
    task, _, _, attempt, _ = run_attempt(monkeypatch, tmp_path)
    plan = {"split": "public", "tasks": [task]}
    original_score = (attempt / "score-0001.json").read_bytes()
    provenance = io.load(attempt / "initial_world_provenance.json")
    provenance["unexpected_private_data"] = {"world": PRIVATE_WORLD, "prompt": PRIVATE_PROMPT}
    (attempt / "initial_world_provenance.json").write_text(io.dumps(provenance))
    summary = summarize(plan, tmp_path)
    public = io.dumps(summary)
    assert all(secret not in public for secret in (PRIVATE_WORLD, PRIVATE_PROMPT, PRIVATE_RUBRIC))
    assert "allowed_services" not in public
    assert "unexpected_private_data" not in public
    assert summary["tasks"][0]["initial_world"]["status"] == "captured"
    assert summary["passes"] == 1
    (attempt / "world.initial.materialized.json").write_text('{"meta": {}}\n')
    changed = summarize(plan, tmp_path)
    assert changed["tasks"][0]["initial_world"]["status"] == "mismatch"
    assert changed["passes"] == 1
    assert changed["tasks"][0]["status"] == "scored"
    assert (attempt / "score-0001.json").read_bytes() == original_score


@pytest.mark.parametrize("field,value", [("task_id", "wrong-task"), ("attempt", "a0002"),
                                        ("attempt_file_sha256", "0" * 64),
                                        ("task_contract_file_sha256", "0" * 64),
                                        ("world_byte_size", 0), ("current_time", LATER)])
def test_summary_rejects_mismatched_capture_identity_without_affecting_score(
        tmp_path, monkeypatch, field, value):
    task, _, _, attempt, _ = run_attempt(monkeypatch, tmp_path)
    provenance = io.load(attempt / "initial_world_provenance.json")
    provenance[field] = value
    (attempt / "initial_world_provenance.json").write_text(io.dumps(provenance))
    report = summarize({"split": "public", "tasks": [task]}, tmp_path)
    assert report["passes"] == 1
    assert report["tasks"][0]["initial_world"]["status"] == "mismatch"


def test_summary_never_publishes_non_timestamp_clock_text(tmp_path, monkeypatch):
    task, _, _, attempt, _ = run_attempt(monkeypatch, tmp_path)
    provenance = io.load(attempt / "initial_world_provenance.json")
    provenance["current_time"] = PRIVATE_WORLD
    (attempt / "initial_world_provenance.json").write_text(io.dumps(provenance))
    report = summarize({"split": "public", "tasks": [task]}, tmp_path)
    assert report["passes"] == 1
    assert report["tasks"][0]["initial_world"] == {"status": "unavailable"}
    assert PRIVATE_WORLD not in io.dumps(report)


@pytest.mark.parametrize("bad_provenance", ["not-json", '{"schema":"unsupported"}',
                                             '{"schema":"rpnh-ab/initial-world/v1"}'])
def test_unreadable_optional_provenance_does_not_break_scores(tmp_path, monkeypatch, bad_provenance):
    task, _, _, attempt, _ = run_attempt(monkeypatch, tmp_path)
    (attempt / "initial_world_provenance.json").write_text(bad_provenance)
    report = summarize({"split": "public", "tasks": [task]}, tmp_path)
    assert report["passes"] == 1
    assert report["tasks"][0]["initial_world"]["status"] in {"unknown", "unavailable"}


def old_attempt(work, task):
    attempt = work / "attempts" / task["id"] / "a0001"
    io.write_new(attempt / "task_contract.json", task["row"])
    io.write_new(attempt / "attempt.json", {"id": task["id"], "attempt": "a0001",
        "task_contract_sha256": task["task_contract_sha256"],
        "task_contract_file_sha256": io.file_sha(attempt / "task_contract.json")})
    io.write_new(attempt / "scoring_input.json", {"initial_state": {}, "info": {}})
    io.write_new(attempt / "final_world.json", {"done": True})
    io.write_new(attempt / "lifecycle.json", {"admitted": True, "execution_status": "host_terminal",
                                             "host_quiescent": True, "world_owner_quiescent": True})
    score_attempt(attempt, lambda *_: {"partial_credit": 1.0, "task_completed_correctly": 1.0}, task=task)
    return attempt


@pytest.mark.parametrize("historical", [False, True])
def test_old_and_new_exports_preserve_private_boundary_and_exact_return_inventory(
        tmp_path, monkeypatch, historical):
    work = tmp_path / "work"
    task = case()
    attempt = old_attempt(work, task) if historical else run_attempt(monkeypatch, work, task)[3]
    io.write_new(work / "plan.json", {"split": "public", "tasks": [task]})
    before = {path.name: path.read_bytes() for path in attempt.iterdir() if path.is_file()}
    monkeypatch.setenv("SYNTHETIC_API_KEY", PRIVATE_WORLD)
    output = tmp_path / "return.zip"
    export_return(work, output)
    prefix = "attempts/synthetic-0001/a0001/"
    with zipfile.ZipFile(output) as archive:
        report = json.loads(archive.read("summary.json"))
        assert report["passes"] == 1
        provenance = report["tasks"][0]["initial_world"]
        assert provenance["status"] == ("unknown" if historical else "captured")
        public = archive.read("summary.json").decode()
        assert all(secret not in public for secret in (PRIVATE_WORLD, PRIVATE_PROMPT, PRIVATE_RUBRIC))
        for name in ("world.initial.materialized.json", "initial_world_provenance.json"):
            assert (prefix + name in archive.namelist()) is not historical
        if not historical:
            returned = archive.read(prefix + "world.initial.materialized.json")
            assert PRIVATE_WORLD.encode() not in returned
            assert b"[REDACTED_KNOWN_CREDENTIAL]" in returned
            assert provenance["hash_scope"] == "retained_private_evidence"
            assert provenance["world_sha256"] == hashlib.sha256(before["world.initial.materialized.json"]).hexdigest()
            assert provenance["world_sha256"] != hashlib.sha256(returned).hexdigest()
        manifest = json.loads(archive.read("RETURN_MANIFEST.json"))
        for item in manifest["exported_file_inventory"]:
            data = archive.read(item["path"])
            assert item["sha256"] == hashlib.sha256(data).hexdigest()
            assert item["byte_size"] == len(data)
    assert {path.name: path.read_bytes() for path in attempt.iterdir() if path.is_file()} == before
