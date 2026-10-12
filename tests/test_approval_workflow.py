"""Actual offline HOST/PN and HTTP boundaries; no provider or financial effects."""
from copy import deepcopy
import json

import pytest

from examples.approval_workflow.app import ApprovalApp
from cpn.rpnh.registry.event_store import RegistryConflict


def request_body(command="request-1"):
    return {"command_id": command, "case_id": "case-001", "amount_minor": 2500,
            "currency": "USD", "reason": "Synthetic unused item"}


def decision_body(app, decision="approve", command="decision-1"):
    candidate = app.state(app.sessions["approver"])["case"]["candidate"]
    return {"command_id": command, "decision": decision, "candidate_ref": candidate["candidate_ref"],
            "wait_ref": candidate["wait_ref"], "target": candidate["target"],
            "candidate": candidate["immutable_candidate"]}


@pytest.fixture
def app(tmp_path):
    return ApprovalApp(tmp_path / "demo", workspace=tmp_path)


def test_native_request_wait_approve_once(app):
    assert len({s.actor for s in app.sessions.values()}) == 4
    assert app.state(app.sessions["requester"])["case"]["status"] == "not_requested"
    first = app.request(app.sessions["requester"], request_body())
    waiting = app.state(app.sessions["approver"])
    assert waiting["case"]["status"] == "waiting"
    assert app.state(app.sessions["requester"])["case"]["candidate"] is None
    body = decision_body(app)
    accepted = app.decide(app.sessions["approver"], body)
    result = app.state(app.sessions["requester"])
    assert result["case"]["status"] == "completed"
    assert result["result"]["mock_refund"] is True
    assert result["result"]["order"] == waiting["case"]["candidate"]["order"] == "synthetic-order-001"
    assert result["result"]["target"] == waiting["case"]["candidate"]["target"]
    assert app.decide(app.sessions["approver"], body) == {**accepted, "replayed": True}
    assert app.request(app.sessions["requester"], request_body()) == {**first, "replayed": True}
    assert len(app.owner._core.event_store.object_rows_by_type("transition_firing/v1")) == 2
    assert len(app.owner._core.event_store.object_rows_by_type("run_input_receipt/v1")) == 2
    assert app.owner._core.event_store.actual_model_call_counts() == (0, 0)


def test_wrong_subject_and_stale_exact_body(app):
    for name in ("approver", "unrelated", "owner"):
        with pytest.raises(PermissionError):
            app.request(app.sessions[name], request_body())
    with pytest.raises(PermissionError):
        app.state(app.sessions["unrelated"])
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    for name in ("requester", "unrelated", "owner"):
        with pytest.raises(PermissionError):
            app.decide(app.sessions[name], body)
    for field in ("candidate_ref", "wait_ref", "target", "candidate"):
        changed = deepcopy(body)
        if field == "candidate":
            changed[field]["amount_minor"] = 2600
        else:
            changed[field] = {}
        with pytest.raises(RegistryConflict):
            app.decide(app.sessions["approver"], changed)
    spoof = {**body, "actor": app.sessions["approver"].actor}
    with pytest.raises(ValueError):
        app.decide(app.sessions["approver"], spoof)
    app.decide(app.sessions["approver"], body)
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], {**body, "decision": "deny"})


def test_deny_has_no_success_effect(app):
    app.request(app.sessions["requester"], request_body())
    app.decide(app.sessions["approver"], decision_body(app, "deny"))
    state = app.state(app.sessions["approver"])
    assert state["case"]["status"] == "denied"
    assert state["result"]["mock_refund"] is False


@pytest.mark.parametrize("amount", [True, 25.5, "2500", 0, 10001])
def test_minor_units_policy(app, amount):
    with pytest.raises(ValueError):
        app.request(app.sessions["requester"], {**request_body(), "amount_minor": amount})


def test_waiting_reopen_retains_run_net_anchor_and_fences_old_writer(app, tmp_path):
    from cpn.rpnh.registry.event_store import StaleWriterError
    app.request(app.sessions["requester"], request_body())
    before = app.state(app.sessions["approver"])
    body = decision_body(app)
    reopened = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    after = reopened.state(reopened.sessions["approver"])
    assert before["case"] == after["case"]
    assert before["checkpoint_ref"] == after["checkpoint_ref"]
    with pytest.raises(StaleWriterError):
        app.decide(app.sessions["approver"], body)
    reopened.decide(reopened.sessions["approver"], body)
    core = reopened.owner._core
    head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
    personal_heads = {name: (person.event_store.max_ordinal(), person.event_store.writer_epoch)
                      for name, (person, _) in reopened.people.items()}
    terminal = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    assert terminal.read_only
    assert terminal.owner is None and terminal.inputs is None and terminal.host is None
    assert terminal.state(terminal.sessions["requester"])["result"] == reopened.state(reopened.sessions["requester"])["result"]
    assert (core.event_store.max_ordinal(), core.event_store.writer_epoch) == (head, epoch)
    assert {name: (person.event_store.max_ordinal(), person.event_store.writer_epoch)
            for name, (person, _) in terminal.people.items()} == personal_heads
    with pytest.raises(RegistryConflict):
        terminal.decide(terminal.sessions["approver"], body)


def test_ready_request_wait_reopens_without_new_checkpoint(app, tmp_path):
    before = app.state(app.sessions["requester"])
    reopened = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    assert reopened.state(reopened.sessions["requester"])["checkpoint_ref"] == before["checkpoint_ref"]
    reopened.request(reopened.sessions["requester"], request_body())
    assert reopened.state(reopened.sessions["approver"])["case"]["status"] == "waiting"


def test_deadline_expiry_and_late_approval(app, monkeypatch):
    from datetime import datetime, timedelta
    import examples.approval_workflow.app as application
    import cpn.rpnh.registry._event_store.run_inputs as guard
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    deadline = app._wait("approval:decision-wait")["spec"].expires_at_utc
    after = datetime.fromisoformat(deadline) + timedelta(seconds=1)
    monkeypatch.setattr(guard, "now", lambda: after)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after
    monkeypatch.setattr(application, "datetime", Clock)
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], body)
    app.expire_due()
    state = app.state(app.sessions["approver"])
    assert state["case"]["status"] == "expired"
    assert state["result"]["mock_refund"] is False
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], body)


def test_concurrent_duplicate_delivery_once(app):
    from concurrent.futures import ThreadPoolExecutor
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: app.decide(app.sessions["approver"], body), range(2)))
    assert receipts[0]["receipt_ref"] == receipts[1]["receipt_ref"]
    assert sorted(r["replayed"] for r in receipts) == [False, True]
    assert len(app.owner._core.event_store.object_rows_by_type("transition_firing/v1")) == 2


def test_http_two_independent_clients_and_sensitive_read_fence(app):
    import base64
    from threading import Thread
    from urllib.request import build_opener, Request, ProxyHandler
    from urllib.error import HTTPError
    from examples.approval_workflow.server import ApprovalServer
    server = ApprovalServer(app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clients = {name: build_opener(ProxyHandler({})) for name in server.passwords}
    def call(name, path, body=None, origin=True):
        auth = base64.b64encode((name + ":" + server.passwords[name]).encode()).decode()
        headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = server.origin
        request = Request(server.origin + path, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            with clients[name].open(request, timeout=60) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
    try:
        for path in ("/api/state", "/viewer", "/api/v1/dashboard", "/api/v1/net", "/approver"):
            assert call("unrelated", path)[0] == 403
        assert call("requester", "/approver")[0] == 403
        assert call("requester", "/api/request", request_body(), origin=False)[0] == 403
        assert call("requester", "/api/request", {**request_body(), "role": "approver"})[0] == 400
        assert call("requester", "/api/request", request_body())[0] == 200
        state = json.loads(call("approver", "/api/state")[1])
        body = decision_body(app)
        assert state["case"]["status"] == "waiting"
        assert call("requester", "/api/decision", body)[0] == 403
        assert call("approver", "/api/decision", body)[0] == 200
        assert call("approver", "/api/decision", body)[0] == 200
        a = json.loads(call("requester", "/api/state")[1])
        b = json.loads(call("approver", "/api/state")[1])
        assert a["result"] == b["result"] and a["case"]["status"] == "completed"
        assert call("approver", "/viewer")[0] == 200
        assert call("approver", "/api/v1/dashboard")[0] == 200
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_runtime_destination_rejects_symlink_and_hardlink(tmp_path):
    from examples.approval_workflow.app import safe_destination
    target = tmp_path / "owned"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        safe_destination(link / "runtime", tmp_path)
    one = target / "one"
    one.write_text("synthetic")
    (target / "two").hardlink_to(one)
    with pytest.raises(ValueError, match="hard link"):
        safe_destination(target, tmp_path)


def test_exact_completion_reopens_without_executor_redispatch(app, tmp_path, monkeypatch):
    import examples.approval_workflow.host as implementation
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    def response_lost(*args, **kwargs):
        raise RuntimeError("injected response loss after exact completion before settlement")
    monkeypatch.setattr(app.owner, "succeed", response_lost)
    with pytest.raises(RuntimeError, match="injected response loss"):
        app.decide(app.sessions["approver"], body)
    assert app.state(app.sessions["approver"])["case"]["status"] == "unknown"
    before = app._products("refund")
    completions = app.owner._core.event_store.list_events_by_type(("registered_operation_completion_recorded/v1",))
    assert len(completions) == 2
    def never_redispatch(**kwargs):
        pytest.fail("original completion must settle without executor redispatch")
    monkeypatch.setattr(implementation, "execute", never_redispatch)
    reopened = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    assert reopened.state(reopened.sessions["requester"])["case"]["status"] == "completed"
    assert reopened._products("refund") == before
    assert len(reopened.owner._core.event_store.object_rows_by_type("transition_firing/v1")) == 2


def test_missing_completion_stays_unknown_no_blind_reopen(app, tmp_path, monkeypatch):
    from cpn.rpnh.registry.errors import ResourceIntegrityFault
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    monkeypatch.setattr(app, "_progress", lambda: None)
    app.decide(app.sessions["approver"], body)
    admitted = app.owner.admit("refund.run", logical_tau=0, command_id="test-unknown:admit")
    app.owner.start(admitted, command_id="test-unknown:start")
    assert app.state(app.sessions["requester"])["case"]["status"] == "unknown"
    core = app.owner._core
    head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
    with pytest.raises(ResourceIntegrityFault):
        ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    assert (core.event_store.max_ordinal(), core.event_store.writer_epoch) == (head, epoch)
    assert app._products("refund") is None


def test_accepted_before_admission_reopens_and_expires(app, tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    import examples.approval_workflow.app as application
    import cpn.rpnh.registry._event_store.run_inputs as guard
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    monkeypatch.setattr(app, "_progress", lambda: None)
    app.decide(app.sessions["approver"], body)
    deadline = app._wait("approval:decision-wait")["spec"].expires_at_utc
    after = datetime.fromisoformat(deadline) + timedelta(seconds=1)
    monkeypatch.setattr(guard, "now", lambda: after)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after
    monkeypatch.setattr(application, "datetime", Clock)
    reopened = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    result = reopened.state(reopened.sessions["requester"])
    assert result["case"]["status"] == "expired"
    assert result["result"]["mock_refund"] is False


def test_accepted_before_admission_reopens_same_run(app, tmp_path, monkeypatch):
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    monkeypatch.setattr(app, "_progress", lambda: None)
    app.decide(app.sessions["approver"], body)
    reopened = ApprovalApp(app.directory, workspace=tmp_path, reopen=True)
    result = reopened.state(reopened.sessions["requester"])
    assert result["case"]["status"] == "completed"
    assert result["result"]["target"] == body["target"]


def test_cross_run_decision_rejected(app, tmp_path):
    app.request(app.sessions["requester"], request_body())
    other = ApprovalApp(tmp_path / "other", workspace=tmp_path)
    other.request(other.sessions["requester"], request_body())
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], decision_body(other))
    with pytest.raises(RegistryConflict):
        app.request(app.sessions["requester"], {**request_body(), "amount_minor": 2600})


def test_candidate_integer_representation_is_immutable(app):
    app.request(app.sessions["requester"], request_body())
    body = decision_body(app)
    changed = deepcopy(body)
    changed["candidate"]["amount_minor"] = float(body["candidate"]["amount_minor"])
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], changed)
    assert app.state(app.sessions["approver"])["case"]["status"] == "waiting"
    accepted = app.decide(app.sessions["approver"], body)
    assert not accepted["replayed"]
    with pytest.raises(RegistryConflict):
        app.decide(app.sessions["approver"], changed)
