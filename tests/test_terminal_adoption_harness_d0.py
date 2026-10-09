"""D0 Harness completion through the original OwnerEventLoop with memory I/O.

Only socket/selector transport is synthetic. No real AF_UNIX socket, worker,
model, provider, subprocess, or replacement Registry is permitted.
The three memory transport classes are copied unchanged from the existing
registered_agent_owner_fixtures test helper (no HOST/numerical hooks imported).
"""
from collections import Counter
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import selectors
import socket
import pytest
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.harness import Harness, OperationDispatch, OperationProducts
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement, apply_replacement
from cpn.rpnh.registry.schema_catalog import canonical_json
from test_terminal_owner_adoption import make_owner, current, events, carrier, terminal_count

class MemorySocket:
    """Only the owner wake channel's byte operations; no client/network API."""

    def __init__(self, state, identity):
        self.state = state
        self.identity = identity
        self.peer = None
        self.received = bytearray()
        self.closed = False

    def fileno(self):
        return self.identity

    def bind(self, address):
        self.state.counts['memory_bind'] += 1
        # The original unix_socket_address points through an open directory
        # descriptor. This is only an inert ordinary-file identity marker.
        with Path(address).open('xb'):
            pass

    def setblocking(self, value):
        if value is not False:
            raise AssertionError('fixture owner transport must be nonblocking')
        self.state.counts['memory_setblocking'] += 1

    def listen(self):
        self.state.counts['memory_listen'] += 1

    def send(self, data):
        if self.closed or self.peer is None or self.peer.closed:
            raise BrokenPipeError('closed in-memory owner wake channel')
        self.state.counts['memory_send'] += 1
        self.state.counts['memory_sent_bytes'] += len(data)
        self.peer.received.extend(data)
        return len(data)

    def recv(self, size):
        if not self.received:
            raise BlockingIOError('empty in-memory owner wake channel')
        self.state.counts['memory_recv'] += 1
        data = bytes(self.received[:size])
        del self.received[:size]
        return data

    def accept(self):
        raise AssertionError('fixture has no owner socket clients')

    def close(self):
        if not self.closed:
            self.state.counts['memory_socket_close'] += 1
        self.closed = True


class MemorySelector:
    def __init__(self, state):
        self.state = state
        self.keys = {}
        self.closed = False

    def register(self, fileobj, events, data=None):
        if fileobj in self.keys:
            raise KeyError('in-memory selector already registered')
        key = selectors.SelectorKey(fileobj, fileobj.fileno(), events, data)
        self.keys[fileobj] = key
        self.state.counts['memory_register'] += 1
        return key

    def unregister(self, fileobj):
        return self.keys.pop(fileobj)

    def modify(self, fileobj, events, data=None):
        self.unregister(fileobj)
        return self.register(fileobj, events, data)

    def select(self, timeout=None):
        if self.closed:
            raise RuntimeError('in-memory selector closed')
        self.state.counts['memory_select'] += 1
        return [(key, selectors.EVENT_READ) for key in self.keys.values()
                if key.events & selectors.EVENT_READ
                and key.fileobj.received and not key.fileobj.closed]

    def close(self):
        self.state.counts['memory_selector_close'] += 1
        self.keys.clear()
        self.closed = True


class OwnerTestBoundaries:
    """Counts permitted synthetic boundaries and forbidden real attempts."""

    def __init__(self):
        self.counts = Counter()
        self.sockets = []

    def _socket(self):
        channel = MemorySocket(self, 1000000 + len(self.sockets))
        self.sockets.append(channel)
        return channel

    def socket(self, family, kind):
        if (family, kind) != (socket.AF_UNIX, socket.SOCK_STREAM):
            raise AssertionError('only synthetic owner AF_UNIX is supported')
        self.counts['memory_socket'] += 1
        return self._socket()

    def socketpair(self):
        self.counts['memory_socketpair'] += 1
        left, right = self._socket(), self._socket()
        left.peer, right.peer = right, left
        return left, right

    def selector(self):
        self.counts['memory_selector'] += 1
        return MemorySelector(self)



def memory_transport(monkeypatch):
    from cpn.rpnh import control_server
    state = OwnerTestBoundaries()
    monkeypatch.setattr(control_server, "socket", SimpleNamespace(
        socket=state.socket, socketpair=state.socketpair,
        AF_UNIX=socket.AF_UNIX, SOCK_STREAM=socket.SOCK_STREAM))
    monkeypatch.setattr(control_server, "selectors", SimpleNamespace(
        DefaultSelector=state.selector, EVENT_READ=selectors.EVENT_READ,
        EVENT_WRITE=selectors.EVENT_WRITE))
    return state


@pytest.mark.parametrize("static_read", [False, True], ids=["ordinary", "static-read-removal"])
def test_harness_drains_one_completion_adopts_and_publishes_terminal(tmp_path, monkeypatch, static_read):
    from cpn.components.basic import CONFIG_SCHEMA_ID
    from test_static_lease_interactions import _world, _fresh_leases
    state = memory_transport(monkeypatch)
    owner = _world(tmp_path / "run", same_pool=False) if static_read else make_owner(tmp_path)
    old_net, old_structure, old_marking = current(owner)
    document = old_structure.compiled.source.to_dict()
    document["name"] = "HarnessReplacement"
    if static_read:
        original_lower = owner.registration.resolve("component", "interactions")
        def lower(config, context):
            fragment = original_lower(config, context)
            return replace(fragment, arcs=tuple(arc for arc in fragment.arcs
                if not (arc.place == "static" and arc.direction == "input" and arc.mode == "read")))
        owner.registration.register_component("interactions_without_static", lower,
            identity={"implementation_id": "test.static_lease_interactions_without_static", "revision": "v1"},
            contracts={"config_schema": CONFIG_SCHEMA_ID})
        next(component for component in document["components"] if component["name"] == "step")["key"] = "interactions_without_static"
    loop = OwnerEventLoop(owner, tmp_path / "owner.sock")
    futures, invocations, executions = [], [], []
    def prepare(*, execution, **_kwargs):
        executions.append(execution)
        def invoke():
            invocations.append(execution.operation_execution_lease_ref)
            return OperationProducts(owner.products(execution, outcome_id="complete",
                products={"step.result": (canonical_json("done"),)}, command_id="harness-d0:products"))
        return OperationDispatch(execution, invoke)
    def submit(invoke):
        future = Future()
        futures.append((future, invoke))
        return future
    harness = Harness(owner=owner, event_loop=loop, prepare_dispatcher=prepare,
        submit_operation=submit, select_ready=lambda **kwargs: tuple(
            name for name in kwargs["enabled"] if name == "step.run"))
    advances = []
    old_advance = owner.control.edits.advance
    def advance():
        advances.append(len(events(owner, "transition_firing_settled/v1")))
        return old_advance()
    monkeypatch.setattr(owner.control.edits, "advance", advance)
    try:
        assert len(harness.schedule_ready()) == 1
        assert len(futures) == len(executions) == 1
        before = len(events(owner, "transition_firing_settled/v1"))
        planned = prepare_replacement(owner, ModuleDeclaration.from_dict(document))
        accepted = apply_replacement(owner, planned, command_id="harness-d0:replace")
        assert accepted["status"] == "DRAINING"
        future, invoke = futures[0]
        future.set_result(invoke())
        assert len(invocations) == 1
        loop.dispatch_ready(timeout=0)
        result = harness.result()
        if result.completion_error is not None:
            raise result.completion_error
        assert result.goal_reached and result.stop_reason == "terminal"
        assert result.terminal_evidence_ref == owner.terminal()
        assert len(result.operation_execution_trace) == 1
        assert terminal_count(owner) == 1
        assert len(events(owner, "transition_firing_settled/v1")) == before + 1
        assert advances == [before, before + 1]  # submit check, then one completion advance
        assert not harness._pending and not owner.control.edits.queue
        new_net, new_structure, new_marking = current(owner)
        assert new_net.net_ref != old_net.net_ref
        assert carrier(owner).state.producer is None
        if static_read:
            assert not new_structure.lease_reference_arcs
            original_leases, new_leases = _fresh_leases(old_marking), _fresh_leases(new_marking)
            assert set(original_leases) == set(new_leases)
            for name in original_leases:
                assert original_leases[name].state.lease_identity_ref == new_leases[name].state.lease_identity_ref
                assert original_leases[name].state.resource_ref == new_leases[name].state.resource_ref
        print("D0_HARNESS_COUNTS", dict(state.counts), "host_invocations", len(invocations), "completion_settlements", 1)
    finally:
        loop.close()
    assert not loop.socket_path.exists()
    assert all(channel.closed for channel in state.sockets)
