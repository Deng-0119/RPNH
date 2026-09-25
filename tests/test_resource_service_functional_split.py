from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace

import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry._resource_service import (
    addresses,
    delivery,
    publication,
    queries,
    references,
)
from cpn.rpnh.registry.errors import (
    ReleaseAuthorizationDenied,
    ReleaseWitnessConsumed,
    ResourceAddressConflict,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.resources import (
    AuthorizedResourceRelease,
    OpaqueBrokerChannelRef,
    ResourceAddress,
    ResourceVersionRef,
)


def _version(entity_type: str, logical_kind: str, version_kind: str) -> VersionRef:
    return VersionRef(
        entity_type,
        new_id(logical_kind),
        new_id(version_kind),
    )


def test_every_legacy_kernel_method_is_a_signature_preserving_component_facade(
        ) -> None:
    components = (references, queries, publication, addresses, delivery)
    delegated = {
        name: function
        for component in components
        for name, function in vars(component).items()
        if (inspect.isfunction(function)
            and function.__module__ == component.__name__
            and hasattr(_ResourceServiceKernel, name))
    }
    facade_methods = {
        name for name in vars(_ResourceServiceKernel)
        if name != "__init__" and callable(getattr(_ResourceServiceKernel, name))
    }
    assert set(delegated) == facade_methods
    for name, function in delegated.items():
        assert inspect.signature(getattr(_ResourceServiceKernel, name)) == (
            inspect.signature(function))


def test_facade_delegates_each_service_area_to_the_exact_live_kernel(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    core = _RegistryCore(tmp_path / "run", create=True)
    kernel = _ResourceServiceKernel(core)
    marker = object()
    context = object()
    command = object()
    transaction = object()

    cases = (
        (queries, "_head", lambda: kernel._head(ordinal=7),
         {"ordinal": 7}),
        (references, "_prepared", lambda: kernel._prepared(command),
         {"ref": command}),
        (publication, "publish_bytes",
         lambda: kernel.publish_bytes(context, command),
         {"context": context, "command": command}),
        (addresses, "bind_address",
         lambda: kernel.bind_address(
             context, command, native_resume=True, reference_only=True),
         {"context": context, "command": command,
          "native_resume": True, "reference_only": True}),
        (delivery, "prepare_delivery",
         lambda: kernel.prepare_delivery(
             context, command, native_resume=True),
         {"context": context, "command": command,
          "native_resume": True}),
    )

    for component, name, invoke, expected in cases:
        observed = {}

        def delegated(**kwargs):
            observed.update(kwargs)
            return marker

        monkeypatch.setattr(component, name, delegated)
        assert invoke() is marker
        assert observed.pop("self") is kernel
        assert observed == expected

    assert kernel._ResourceServiceKernel__core is core


def test_components_do_not_share_kernel_state_across_registries(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    first_core = _RegistryCore(tmp_path / "first", create=True)
    second_core = _RegistryCore(tmp_path / "second", create=True)
    first = _ResourceServiceKernel(first_core)
    second = _ResourceServiceKernel(second_core)

    def live_core(self, ref):
        return self._ResourceServiceKernel__core

    monkeypatch.setattr(references, "_prepared", live_core)
    assert first._prepared(object()) is first_core
    assert second._prepared(object()) is second_core
    assert first._ResourceServiceKernel__core is not second._ResourceServiceKernel__core
    assert (first._ResourceServiceKernel__release_channels
            is not second._ResourceServiceKernel__release_channels)


def test_publication_keeps_own_transaction_and_stage_only_paths_distinct(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _ResourceServiceKernel(
        _RegistryCore(tmp_path / "run", create=True))
    context = object()
    command = SimpleNamespace(origin=object())
    transaction = SimpleNamespace(commit=lambda: pytest.fail(
        "stage-only publication must not commit its owning transaction"))
    observed = []

    def publish(self, context, command, *,
                transaction=None, native_resume=False):
        observed.append((
            self, context, command,
            transaction, native_resume))
        return len(observed)

    monkeypatch.setattr(publication, "_publish", publish)
    assert kernel.publish_bytes(context, command) == 1
    assert kernel._publish_bytes_in_transaction(
        context, command, transaction, native_resume=True) == 2
    assert observed == [
        (kernel, context, command, None, False),
        (kernel, context, command, transaction, True),
    ]


class _TransactionProbe:
    def __init__(self) -> None:
        self.transaction_id = new_id("transaction")
        self.prewrites = []
        self.events = []

    def next_stream_sequence(self, stream: str) -> int:
        assert stream.startswith("resource-address:")
        return 1

    def prewrite(self, **kwargs) -> None:
        self.prewrites.append(kwargs)

    def append(self, event) -> None:
        self.events.append(event)


def test_address_component_preserves_cas_and_tombstone_material(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _ResourceServiceKernel(
        _RegistryCore(tmp_path / "run", create=True))
    invocation_ref = _version(
        "invocation/v1", "invocation", "invocation_version")
    principal_ref = _version(
        "principal/v1", "principal", "principal_version")
    scope_ref = _version("task_round/v1", "task_round", "task_round_version")
    authorization_ref = _version(
        "operation_binding/v1", "operation_binding",
        "operation_binding_version")
    resource_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    context = SimpleNamespace(
        invocation_ref=invocation_ref,
        principal_ref=principal_ref,
        own_transition_firing_ref=None,
    )
    address = ResourceAddress(scope_ref, "result")
    current = {"value": None}

    monkeypatch.setattr(
        addresses, "_address_scope_allowed", lambda **kwargs: None)
    monkeypatch.setattr(
        addresses, "_current_binding",
        lambda **kwargs: current["value"])

    bound_tx = _TransactionProbe()
    bound = kernel._append_binding(
        context, bound_tx, address, resource_ref, authorization_ref, None,
        idempotency_key="bind", resource_already_validated=True)
    assert bound.tombstone is False
    assert bound_tx.prewrites[0]["metadata"]["lifecycle_state"] == "bound"
    assert bound_tx.events[0].event_type == "resource_address_bound/v1"

    current["value"] = bound
    with pytest.raises(ResourceAddressConflict, match="chain head"):
        kernel._append_binding(
            context, _TransactionProbe(), address, resource_ref,
            authorization_ref, None, idempotency_key="stale",
            resource_already_validated=True)

    tombstone_tx = _TransactionProbe()
    tombstone = kernel._append_binding(
        context, tombstone_tx, address, resource_ref, authorization_ref,
        bound, idempotency_key="unbind", tombstone=True,
        resource_already_validated=True)
    assert tombstone.tombstone is True
    assert (tombstone_tx.prewrites[0]["metadata"]["lifecycle_state"]
            == "tombstoned")
    assert tombstone_tx.prewrites[0]["metadata"]["resource_ref"] is None
    assert tombstone_tx.events[0].event_type == "resource_address_unbound/v1"


def test_release_channel_is_same_kernel_only_and_one_shot(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _ResourceServiceKernel(
        _RegistryCore(tmp_path / "first", create=True))
    second = _ResourceServiceKernel(
        _RegistryCore(tmp_path / "second", create=True))
    invocation_ref = _version(
        "invocation/v1", "invocation", "invocation_version")
    delivery_ref = _version(
        "resource_delivery/v1", "resource_delivery",
        "resource_delivery_version")
    witness_ref = _version(
        "resource_release_witness/v1", "release_witness",
        "release_witness_version")
    resource_ref = ResourceVersionRef(
        new_id("resource"), new_id("resource_version"))
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=1)
    ).isoformat().replace("+00:00", "Z")
    channel_id = "same-live-kernel-channel"
    release = AuthorizedResourceRelease(
        delivery_ref, witness_ref, resource_ref, 7, "tool_result",
        expires_at, OpaqueBrokerChannelRef(channel_id))
    context = SimpleNamespace(invocation_ref=invocation_ref)
    read_view = object()
    first._ResourceServiceKernel__release_channels[channel_id] = {
        "witness_ref": witness_ref,
        "delivery_ref": delivery_ref,
        "resource_ref": resource_ref,
        "context_ref": invocation_ref,
        "byte_count": 7,
        "boundary": "tool_result",
        "expires_at": expires_at,
        "native_resume": False,
        "read_view": read_view,
        "released": False,
    }

    def read_registered(self, context, ref, *,
                        view=None, prepared=None):
        assert self is first
        assert context.invocation_ref == invocation_ref
        assert ref == resource_ref
        assert view is read_view
        assert prepared is None
        return b"payload"

    monkeypatch.setattr(references, "_read_firing_registered", read_registered)
    with pytest.raises(ReleaseAuthorizationDenied, match="unknown"):
        second._consume_authorized_release(context, release)
    assert first._consume_authorized_release(context, release) == b"payload"
    with pytest.raises(ReleaseWitnessConsumed, match="one-use"):
        first._consume_authorized_release(context, release)


def test_resume_reconciliation_delegates_on_the_same_kernel(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _ResourceServiceKernel(
        _RegistryCore(tmp_path / "run", create=True))
    reconciled = (new_id("resource_delivery"),)
    observed = []

    def reconcile(self):
        observed.append(self)
        return reconciled

    monkeypatch.setattr(
        delivery, "reconcile_incomplete_deliveries_on_resume", reconcile)
    assert kernel.reconcile_incomplete_deliveries_on_resume() == reconciled
    assert observed == [kernel]
