"""Native Registry/PN association and controlled HTTP fixtures; no cryptography."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest
from cpn.rpnh.registry.event_store import RegistryConflict
from examples.a2a_quote.app import A2AQuoteApp
from examples.a2a_quote.contracts import evidence


@pytest.fixture
def app(tmp_path):
    return A2AQuoteApp(tmp_path / "demo", workspace=tmp_path)


def selection(app, fixture="trusted25", command="selection-1"):
    return {"command_id": command, "fixture": fixture,
            "association": app.state(app.sessions["holder"])["association"]}


def test_native_quote_once_and_concurrent_replay(app):
    from concurrent.futures import ThreadPoolExecutor
    waiting = app.state(app.sessions["holder"])
    assert waiting["status"] == "waiting"
    assert waiting["counts"]["quote_firings"] == 0
    assert len({s.actor for s in app.sessions.values()}) == 4
    body = selection(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: app.select(app.sessions["holder"], body), range(2)))
    assert receipts[0]["receipt_ref"] == receipts[1]["receipt_ref"]
    assert sorted(x["replayed"] for x in receipts) == [False, True]
    state = app.state(app.sessions["holder"])
    assert state["status"] == "completed"
    assert state["result"]["amount_minor"] == 15000
    assert state["result"]["verdict"] == "fixture_eligible"
    assert state["result"]["association"] == waiting["association"]
    assert state["counts"] == {"quote_firings": 1, "input_receipts": 1, "model_calls": [0, 0]}
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], {**body, "fixture": "deny"})


@pytest.mark.parametrize("fixture,verdict", [("untrusted50", "fixture_untrusted"),
    ("expired", "fixture_expired"), ("deny", "denied")])
def test_fixture_failure_and_deny_yield_one_undiscounted_quote(app, fixture, verdict):
    app.select(app.sessions["holder"], selection(app, fixture))
    state = app.state(app.sessions["holder"])
    assert state["status"] == "completed"
    assert state["result"]["verdict"] == verdict
    assert state["result"]["discount_percent"] == 0
    assert state["result"]["amount_minor"] == 20000
    assert state["counts"]["quote_firings"] == 1


def test_exact_association_and_host_authority(app):
    original = selection(app)
    fields = ["run_ref", "task_ref", "net_ref", "wait_ref", "request_resource_ref", "generation",
        "assistant_task_id", "merchant_task_id", "assistant_context_id", "merchant_context_id",
        "verifier_identity_ref", "verifier_host_session_ref", "verification_session_ref",
        "authorization_request_ref", "challenge_ref", "expires_at_utc"]
    for field in fields:
        changed = deepcopy(original)
        changed["association"][field] = "wrong-exact-field"
        with pytest.raises(RegistryConflict):
            app.select(app.sessions["holder"], changed)
    for role in ("holder", "owner", "unrelated"):
        with pytest.raises(PermissionError):
            app.submit_evidence(app.sessions[role], evidence(original["association"], "trusted25"), command_id="forged")
    with pytest.raises(ValueError):
        app.select(app.sessions["holder"], {**original, "approved": True})
    with pytest.raises(PermissionError):
        app.select(app.sessions["unrelated"], original)
    forged = evidence(original["association"], "untrusted50")
    forged["verdict"], forged["discount_percent"] = "fixture_eligible", 25
    with pytest.raises(RegistryConflict):
        app.submit_evidence(app.sessions["verifier"], forged, command_id="forged")
    # The native input guard also denies holder/owner authority, independently
    # of the application's action routing.
    from cpn.rpnh.registry.publication import _version_from_payload
    from cpn.rpnh.registry.schema_catalog import canonical_json
    from cpn.rpnh.run import OwnerInput
    from examples.a2a_quote.contracts import schema_id
    wait = app._current_wait()
    for role in ("holder", "owner"):
        with pytest.raises(RegistryConflict):
            app.inputs.submit(app.sessions[role], wait_ref=wait["wait_ref"],
                expected_checkpoint_ref=_version_from_payload(app._document(wait)["checkpoint_ref"]),
                binding_ref=wait["spec"].binding_ref,
                declaration=OwnerInput(schema_id("evidence"), canonical_json(evidence(original["association"], "trusted25")), "forged holder input"),
                command_id="forged-native:" + role)
    assert app.state(app.sessions["holder"])["counts"]["input_receipts"] == 0


def test_same_context_two_requests_and_renewed_old_challenge(app, tmp_path):
    other = A2AQuoteApp(tmp_path / "other", workspace=tmp_path, request_id="synthetic-task-2")
    original, second = selection(app), selection(other)
    assert original["association"]["merchant_context_id"] == second["association"]["merchant_context_id"]
    with pytest.raises(RegistryConflict):
        other.select(other.sessions["holder"], original)
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], second)
    app.renew(app.sessions["holder"], {"association": original["association"]})
    renewed = selection(app)
    assert renewed["association"]["generation"] == 2
    assert renewed["association"]["challenge_ref"] != original["association"]["challenge_ref"]
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], original)
    assert other.state(other.sessions["holder"])["status"] == "waiting"
    app.select(app.sessions["holder"], renewed)
    assert app.state(app.sessions["holder"])["counts"]["quote_firings"] == 1
    assert other.state(other.sessions["holder"])["counts"]["quote_firings"] == 0


def advance_past_deadline(app, monkeypatch):
    import examples.a2a_quote.app as application
    import cpn.rpnh.registry._event_store.run_inputs as guard
    after = datetime.fromisoformat(selection(app)["association"]["expires_at_utc"]) + timedelta(seconds=1)
    monkeypatch.setattr(guard, "now", lambda: after)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return after
    monkeypatch.setattr(application, "datetime", Clock)


def test_expiry_and_late_success_cannot_wake_wait(app, monkeypatch):
    body = selection(app)
    advance_past_deadline(app, monkeypatch)
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], body)
    assert app.state(app.sessions["holder"])["counts"]["quote_firings"] == 0
    app.expire_due()
    state = app.state(app.sessions["holder"])
    assert state["result"]["verdict"] == "expired"
    assert state["result"]["discount_percent"] == 0
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], body)
    assert app.state(app.sessions["holder"])["counts"]["quote_firings"] == 1


def test_native_reopen_preserves_association_and_terminal_readonly(app, tmp_path):
    from cpn.rpnh.registry.event_store import StaleWriterError
    before = app.state(app.sessions["holder"])
    body = selection(app)
    reopened = A2AQuoteApp(app.directory, workspace=tmp_path, reopen=True)
    after = reopened.state(reopened.sessions["holder"])
    assert (before["association"], before["checkpoint_ref"]) == (after["association"], after["checkpoint_ref"])
    with pytest.raises(StaleWriterError):
        app.select(app.sessions["holder"], body)
    reopened.select(reopened.sessions["holder"], body)
    core = reopened.owner._core
    head, epoch = core.event_store.max_ordinal(), core.event_store.writer_epoch
    terminal = A2AQuoteApp(app.directory, workspace=tmp_path, reopen=True)
    assert terminal.read_only
    assert terminal.state(terminal.sessions["holder"])["result"] == reopened.state(reopened.sessions["holder"])["result"]
    assert (core.event_store.max_ordinal(), core.event_store.writer_epoch) == (head, epoch)


def test_missing_persisted_association_is_unresolved_no_guess(app, monkeypatch):
    from dataclasses import replace
    wait = app._current_wait()
    missing = {**wait, "spec": replace(wait["spec"], expiry_input=None)}
    body = selection(app)
    monkeypatch.setattr(app, "_current_wait", lambda: missing)
    assert app.state(app.sessions["holder"])["status"] == "unresolved"
    with pytest.raises(RegistryConflict, match="missing"):
        app.select(app.sessions["holder"], body)
    assert app.state(app.sessions["holder"])["counts"]["quote_firings"] == 0


def test_accepted_before_admission_reopens_and_expires_atomically(app, tmp_path, monkeypatch):
    original_progress = app._progress
    monkeypatch.setattr(app, "_progress", lambda: None)
    app.select(app.sessions["holder"], selection(app))
    advance_past_deadline(app, monkeypatch)
    monkeypatch.setattr(app, "_progress", original_progress)
    reopened = A2AQuoteApp(app.directory, workspace=tmp_path, reopen=True)
    state = reopened.state(reopened.sessions["holder"])
    assert state["result"]["verdict"] == "expired"
    assert state["result"]["amount_minor"] == 20000
    assert state["counts"]["quote_firings"] == 1


def test_restart_missing_persisted_wait_does_not_reconstruct_mapping(tmp_path, monkeypatch):
    # Simulate interruption after original startup but before wait publication.
    # Nothing deletes or alters an immutable Registry fact.
    original = A2AQuoteApp._open_wait
    monkeypatch.setattr(A2AQuoteApp, "_open_wait", lambda *args: None)
    directory = tmp_path / "interrupted"
    with pytest.raises(RegistryConflict, match="missing"):
        A2AQuoteApp(directory, workspace=tmp_path)
    monkeypatch.setattr(A2AQuoteApp, "_open_wait", original)
    from cpn.rpnh.registry.errors import ResourceIntegrityFault
    with pytest.raises(ResourceIntegrityFault, match="recovery requires exactly one active firing"):
        A2AQuoteApp(directory, workspace=tmp_path, reopen=True)
    from cpn.rpnh.registry._registry import _RegistryCore
    from examples.a2a_quote.native import catalog
    core = _RegistryCore(directory / "run", create=False, read_only=True, catalog=catalog())
    assert not core.event_store.object_rows_by_type("run_input_wait/v1")
    assert not core.event_store.object_rows_by_type("transition_firing/v1")


def test_http_frontdoor_and_actual_viewer(app):
    from threading import Thread
    from urllib.request import build_opener, Request, ProxyHandler
    from urllib.error import HTTPError
    from examples.a2a_quote.server import A2AQuoteServer
    server = A2AQuoteServer(app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = build_opener(ProxyHandler({}))  # Loopback fixture must never inherit external proxies.
    def call(path, body=None, origin=True, host=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = server.origin
        if host:
            headers["Host"] = host
        req = Request(server.origin + path, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
        try:
            with client.open(req, timeout=60) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
    try:
        assert call("/")[0] == 200
        assert call("/a2a.js")[0] == 200
        waiting = json.loads(call("/api/state")[1])
        assert waiting["status"] == "waiting"
        for path in ("/viewer", "/api/v1/dashboard", "/api/v1/net"):
            assert call(path)[0] == 200
        body = selection(app)
        assert call("/api/select", body, origin=False)[0] == 403
        assert call("/api/state", host="example.invalid")[0] == 403
        assert call("/api/select", {**body, "approved": True})[0] == 400
        assert call("/api/select", {**body, "session_ref": waiting["client"]["session_ref"]})[0] == 400
        assert call("/api/callback", {"approved": True})[0] == 404
        assert call("/api/select", body)[0] == 200
        assert call("/api/select", body)[0] == 200
        completed = json.loads(call("/api/state")[1])
        assert completed["status"] == "completed"
        assert completed["result"]["amount_minor"] == 15000
        assert completed["counts"]["quote_firings"] == 1
        assert completed["stage2"] == "BLOCKED_NOT_RUN"
        viewer = call("/viewer")
        assert viewer[0] == 200 and b"Return to quote app" in viewer[1]
        assert call("/api/v1/dashboard")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_association_integer_representation_is_immutable(app):
    body = selection(app)
    changed = deepcopy(body)
    changed["association"]["generation"] = float(body["association"]["generation"])
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], changed)
    assert app.state(app.sessions["holder"])["status"] == "waiting"
    accepted = app.select(app.sessions["holder"], body)
    assert not accepted["replayed"]
    with pytest.raises(RegistryConflict):
        app.select(app.sessions["holder"], changed)
