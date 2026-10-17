"""Offline native regression for a queued message outliving its read grant."""
from datetime import datetime, timedelta, timezone
import threading

import pytest

from cpn.rpnh.collaboration import UserCollaborationHost
from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
from cpn.rpnh.registry._operation import registration as native_registration
from examples.multiuser_release_handoff import backend as backend_module
from examples.multiuser_release_handoff.tests.test_delegation import private_user
from examples.multiuser_release_handoff.user_runtime import (
    IndependentUser, grant_resource, read_resource,
)
from test_static_lease_reads import no_external_transport


@pytest.fixture
def message_backend(tmp_path, monkeypatch, no_external_transport):
    # Restore the process-wide inventory after the real owners/source compose it.
    monkeypatch.setattr(native_registration, "_OPERATION_REGISTRATION",
                        native_registration._OPERATION_REGISTRATION)
    users, executions = {}, {}
    for role in ("operations", "support"):
        owner, execution = private_user(tmp_path / role, role)
        host = UserCollaborationHost(owner._core, owner.schema_gateway)
        session = host.create_session(
            origin_core=owner._core, principal_ref=owner.principal_ref,
            command_id="message-test:owner-session:" + role,
        )
        users[role] = IndependentUser(
            owner, None, role, "step.run", False, host, session,
        )
        executions[role] = execution

    # Message delivery never touches Git/Drive/incident state. Omit only that
    # unrelated constructor so this regression cannot invoke a business CLI.
    monkeypatch.setattr(backend_module, "BusinessWorld", lambda *a, **kw: None)
    backend = backend_module.ReleaseBackend(tmp_path, users, stop=threading.Event())
    try:
        yield backend, executions
    finally:
        backend.events.close()


def _dispatch(backend, executions, role, tool, arguments, call_id):
    # Exercise the actual HOST handler under its production command lock.
    # Managed-tool preparation/socket transport is outside this regression.
    call = {
        "role": role, "tool": tool, "arguments": arguments,
        "user": backend.users[role], "execution": executions[role],
        "identity": {"call_id": call_id},
    }
    with backend.changed:
        return backend._dispatch(call)


def test_queued_message_refreshes_stale_grant_for_exact_recipient(message_backend):
    backend, executions = message_backend
    arguments = {
        "recipient": "operations", "topic": "support_initial_findings",
        "text": "Support reviewed the original release note.",
    }
    published = _dispatch(
        backend, executions, "support", "handoff", arguments, "initial-findings",
    )
    assert published["status"] == "published"
    message, = backend.messages
    original_ref = message["ref"]

    # Recreate the pre-fix queue's real owner-issued context, without mocking
    # Registry authorization or fabricating a grant/control sequence.
    with backend.changed:
        old_observer = grant_resource(
            backend.source_user, backend.users["operations"], original_ref,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            command_id="message-test:original-grant",
        )
        message["observer"] = old_observer

    # A second real source.message PN firing advances source task control.
    later = _dispatch(
        backend, executions, "operations", "handoff",
        {"recipient": "support", "topic": "revision_requested", "text": "Release the read."},
        "request-revision",
    )
    assert later["status"] == "published"
    assert later["resource_ref"] != published["resource_ref"]
    assert datetime.fromisoformat(old_observer.expires_at) > datetime.now(timezone.utc)
    with backend.changed:
        with pytest.raises(RegistryReadSessionError) as stale:
            read_resource(
                backend.source_user, backend.users["operations"], original_ref,
                observer=old_observer,
            )
    assert stale.value.code == "ACCESS_CHANGED"
    assert message["delivered"] is False

    # The patched handler must issue current exact authority and read the
    # original queued payload, not republish it or substitute the later one.
    delivered = _dispatch(
        backend, executions, "operations", "await_handoff", {}, "receive-findings",
    )
    assert delivered["status"] == "delivered"
    assert delivered["sender"] == "support"
    assert delivered["topic"] == arguments["topic"]
    assert delivered["source_id"] == published["source_id"]
    assert delivered["resource_ref"] == published["resource_ref"]
    assert delivered["native_call_receipt"]["call_id"] == "initial-findings"
    assert delivered["native_call_receipt"]["arguments"] == arguments
    assert message["ref"] == original_ref
    assert message["delivered"] is True
    assert len(backend.messages) == 2
    assert backend.messages[1]["delivered"] is False
    fresh_observer = message["observer"]
    assert fresh_observer.grant_ref != old_observer.grant_ref
    assert fresh_observer.issued_task_control_sequence > old_observer.issued_task_control_sequence

    # Even the fresh, otherwise valid context belongs only to operations.
    with backend.changed:
        with pytest.raises(ValueError, match="read grant belongs to another recipient"):
            read_resource(
                backend.source_user, backend.users["support"], original_ref,
                observer=fresh_observer,
            )
