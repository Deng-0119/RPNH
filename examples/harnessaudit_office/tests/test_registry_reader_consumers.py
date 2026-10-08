"""Both adapters consume the same Registry-native current-run read contract.

Real temporary Registries and explicit static products only. No dispatch,
provider, owner socket, grant issuance, or event append is permitted on reads.
"""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from test_terminal_identity import (
    owner_at, finish, export, reenter, fingerprint, forbidden, collector,
)
from cpn.rpnh.agent_tasks import agent_task_catalog
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import CanonicalView, EventStore
from cpn.rpnh.registry.resource_service import _ResourceServiceKernel
from cpn.rpnh.registry.publication import _ref_payload, _version_from_payload
from cpn.rpnh.registry.run_authority import (
    RunReadCut, current_run_execution_authority, read_run_execution, read_run_terminal_bytes,
)
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.collaboration import environment_host
from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError


def reader(owner):
    core = _RegistryCore(owner._core.run_dir, create=False, read_only=True,
                         catalog=agent_task_catalog())
    return core, _ResourceServiceKernel(core)


def test_two_consumers_share_exact_identity_payload_and_read_only_source(tmp_path):
    owner = owner_at(tmp_path / "run")
    expected = finish(owner)
    before = fingerprint(owner)
    core, kernel = reader(owner)
    with patch.object(_RegistryCore, "begin", forbidden), \
            patch("cpn.rpnh.run.resume_run", forbidden), \
            patch("cpn.rpnh.registry.observer_access.issue_observer_access", forbidden), \
            patch("socket.socket", forbidden):
        read = read_run_execution(core, kernel)
        assert read.cut._core.read_only is True
        assert read.execution_generation == 0
        assert _ref_payload(read.terminal.evidence_ref) == expected
        assert read_run_terminal_bytes(core, read) == b'"canonical final answer"'
        ha, observations = export(owner, expected)
        env = environment_host._terminal_result(owner, _version_from_payload(expected), "terminal")
    assert ha.terminal_evidence_ref == env["terminal_evidence_ref"] == expected
    assert ha.capture_diagnostics["terminal_identity"]["terminal_result_ref"] == env["terminal_result_ref"]
    assert env["output"] == observations._observations[0].data["content"] == "canonical final answer"
    assert fingerprint(owner) == before


def test_two_consumers_reject_superseded_terminal_generation(tmp_path):
    owner = owner_at(tmp_path / "run")
    old = finish(owner)
    owner = reenter(owner)
    current = _ref_payload(owner.terminal())
    before = fingerprint(owner)
    with pytest.raises(RuntimeError, match="expected terminal evidence"):
        export(owner, old)
    with pytest.raises(EnvironmentContractError, match="expected terminal evidence"):
        environment_host._terminal_result(owner, _version_from_payload(old), "terminal")
    env = environment_host._terminal_result(owner, _version_from_payload(current), "terminal")
    ha, _ = export(owner, current)
    assert env["terminal_evidence_ref"] == ha.terminal_evidence_ref == current
    assert fingerprint(owner) == before


@pytest.mark.parametrize("context", ["run", "task", "net"])
def test_reader_expected_owner_context_never_selects_identity(tmp_path, context):
    owner = owner_at(tmp_path / "run"); finish(owner)
    core, kernel = reader(owner)
    original = {"run": owner.identity.run_ref, "task": owner.identity.task_ref,
                "net": owner.publication.net_ref}[context]
    wrong = replace(original, entity_id=new_id(original.entity_id.kind))
    before = fingerprint(owner)
    with pytest.raises(RuntimeError, match="expected " + context):
        read_run_execution(core, kernel, **{"expected_" + context + "_ref": wrong})
    assert fingerprint(owner) == before


def test_current_cut_rejects_historical_view_substitution(tmp_path):
    owner = owner_at(tmp_path / "run")
    finish(owner)
    old_head = owner._core.event_store.max_ordinal()
    owner = reenter(owner); owner.terminal()
    core, kernel = reader(owner)
    cut = RunReadCut.capture(core)
    with pytest.raises(RuntimeError, match="Registry advanced"):
        read_run_execution(core, kernel, cut=replace(cut, view=CanonicalView(old_head)))


def test_authority_selection_respects_supplied_canonical_view(tmp_path):
    owner = owner_at(tmp_path / "run"); finish(owner)
    core, kernel = reader(owner)
    view = core.event_store.canonical_view()
    old_ref, old = current_run_execution_authority(core, kernel, view=view)
    owner = reenter(owner); owner.terminal()
    exact_ref, exact = current_run_execution_authority(core, kernel, view=view)
    live_ref, _ = current_run_execution_authority(core, kernel)
    assert (exact_ref, exact) == (old_ref, old)
    assert live_ref != old_ref


@pytest.mark.parametrize("field", ["head", "epoch"])
def test_environment_renderer_rechecks_same_cut_before_delivery(tmp_path, monkeypatch, field):
    owner = owner_at(tmp_path / "run"); expected = finish(owner)
    original = environment_host._bounded_terminal_json
    before = fingerprint(owner)
    real_head = EventStore.max_ordinal
    real_epoch = EventStore.writer_epoch
    def renderer(core, prepared):
        result = original(core, prepared)
        if field == "head":
            monkeypatch.setattr(EventStore, "max_ordinal", lambda self: real_head(self) + 1)
        else:
            monkeypatch.setattr(EventStore, "writer_epoch", property(lambda self: real_epoch.fget(self) + 1))
        return result
    monkeypatch.setattr(environment_host, "_bounded_terminal_json", renderer)
    with pytest.raises(EnvironmentContractError, match="Registry advanced"):
        environment_host._terminal_result(owner, _version_from_payload(expected), "terminal")
    monkeypatch.undo()
    assert fingerprint(owner) == before


def test_result_provenance_is_native_reader_requirement(tmp_path, monkeypatch):
    owner = owner_at(tmp_path / "run"); expected = finish(owner)
    real = _ResourceServiceKernel._prepared_reference
    calls = []
    def require(self, ref, *, prepared=None, view=None):
        calls.append(view)
        raise RuntimeError("native provenance unavailable")
    monkeypatch.setattr(_ResourceServiceKernel, "_prepared_reference", require)
    with pytest.raises(RuntimeError, match="native provenance unavailable"):
        export(owner, expected)
    with pytest.raises(EnvironmentContractError, match="native provenance unavailable"):
        environment_host._terminal_result(owner, _version_from_payload(expected), "terminal")
    assert len(calls) == 2 and all(type(view) is CanonicalView for view in calls)


def test_descriptor_bound_and_product_physical_bound_are_enforced(tmp_path):
    owner = owner_at(tmp_path / "run"); finish(owner)
    core, kernel = reader(owner)
    with pytest.raises(RuntimeError, match="descriptor exceeds"):
        read_run_execution(core, kernel, max_descriptor_bytes=1)
    read = read_run_execution(core, kernel)
    with pytest.raises(RuntimeError, match="exceeds reader byte bound"):
        read_run_terminal_bytes(core, read, max_bytes=1)
    path = core.object_store.path_for_version(read.terminal.result.version_id)
    path.write_bytes(b'x' * 100)
    with pytest.raises(RuntimeError, match="size differs"):
        read_run_terminal_bytes(core, read, max_bytes=32)


def test_environment_rejects_descriptor_byte_corruption(tmp_path):
    from cpn.rpnh.registry.schema_catalog import canonical_json
    owner = owner_at(tmp_path / "run"); expected = finish(owner)
    ref = _version_from_payload(expected)
    prepared = owner._core.get_version(ref.version_id)
    document = deepcopy(dict(prepared.metadata))
    document["run_ref"]["logical_id"] = str(new_id("run"))
    raw = canonical_json(document)
    assert len(raw) == prepared.size
    owner._core.object_store.path_for_version(ref.version_id).write_bytes(raw)
    with pytest.raises(EnvironmentContractError, match="bytes differ from registered metadata"):
        environment_host._terminal_result(owner, ref, "terminal")


def test_cut_is_bound_to_exact_registry_handle_and_kernel(tmp_path):
    owner = owner_at(tmp_path / "run"); finish(owner)
    core, kernel = reader(owner)
    other_core, other_kernel = reader(owner)
    assert core.event_store.max_ordinal() == other_core.event_store.max_ordinal()
    assert core.event_store.writer_epoch == other_core.event_store.writer_epoch
    cut = RunReadCut.capture(core)
    with pytest.raises(RuntimeError, match="Registry advanced"):
        read_run_execution(other_core, other_kernel, cut=cut)
    with pytest.raises(TypeError, match="Core and its Kernel"):
        read_run_execution(core, other_kernel)


def test_environment_preserves_fresh_owner_net_assertion(tmp_path):
    from types import SimpleNamespace
    owner = owner_at(tmp_path / "run"); expected = finish(owner)
    original = owner.publication.net_ref
    owner.publication = SimpleNamespace(net_ref=replace(original, entity_id=new_id(original.entity_id.kind)))
    with pytest.raises(EnvironmentContractError, match="expected net"):
        environment_host._terminal_result(owner, _version_from_payload(expected), "terminal")


def test_both_consumers_reject_missing_actual_resource_provenance(tmp_path, monkeypatch):
    owner = owner_at(tmp_path / "run"); expected = finish(owner)
    original = _ResourceServiceKernel._exact_object_for_view
    before = fingerprint(owner)
    def without_provenance(self, view, selected, **kwargs):
        prepared = original(self, view, selected, **kwargs)
        if prepared.object_type == "resource_version/v1":
            metadata = deepcopy(dict(prepared.metadata))
            metadata.pop("reference_provenance", None)
            return replace(prepared, metadata=metadata)
        return prepared
    monkeypatch.setattr(_ResourceServiceKernel, "_exact_object_for_view", without_provenance)
    with pytest.raises(RuntimeError, match="lacks exact reference provenance"):
        export(owner, expected)
    with pytest.raises(EnvironmentContractError, match="lacks exact reference provenance"):
        environment_host._terminal_result(owner, _version_from_payload(expected), "terminal")
    assert fingerprint(owner) == before
