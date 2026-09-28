"""Focused offline evidence for the same-Registry execution-net substrate."""

from __future__ import annotations

import pytest

from cpn.rpnh import file_execution_net
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.execution_net import (
    ExecutionInputArc,
    ExecutionNetDefinition,
    ExecutionNetError,
    ExecutionOutputArc,
    ExecutionParentAuthority,
    ExecutionTransition,
)
from cpn.rpnh.registry.models import TypedRelation, VersionRef
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.execution_runtime import ExecutionRuntime
from test_structural_evidence import _owner, _products, _start


def _definition() -> ExecutionNetDefinition:
    return ExecutionNetDefinition(
        definition_key="weighted.join",
        places=("done", "left", "right", "source"),
        transitions=(
            ExecutionTransition("join", "idempotent_materialization"),
            ExecutionTransition("split", "pure"),
        ),
        input_arcs=(
            ExecutionInputArc("left", "join", 2),
            ExecutionInputArc("right", "join", 1),
            ExecutionInputArc("source", "split", 2),
        ),
        output_arcs=(
            ExecutionOutputArc("join", "done", 1),
            ExecutionOutputArc("split", "left", 2),
            ExecutionOutputArc("split", "right", 1),
        ),
        initial_place="source",
        initial_tokens=2,
        terminal_places=("done",),
    )


def _parallel_definition() -> ExecutionNetDefinition:
    return ExecutionNetDefinition(
        definition_key="parallel.branches",
        places=("done_left", "done_right", "left", "right", "source"),
        transitions=(
            ExecutionTransition("finish_left", "idempotent_materialization"),
            ExecutionTransition("finish_right", "idempotent_materialization"),
            ExecutionTransition("split", "pure"),
        ),
        input_arcs=(
            ExecutionInputArc("left", "finish_left", 1),
            ExecutionInputArc("right", "finish_right", 1),
            ExecutionInputArc("source", "split", 1),
        ),
        output_arcs=(
            ExecutionOutputArc("finish_left", "done_left", 1),
            ExecutionOutputArc("finish_right", "done_right", 1),
            ExecutionOutputArc("split", "left", 1),
            ExecutionOutputArc("split", "right", 1),
        ),
        initial_place="source",
        initial_tokens=1,
        terminal_places=("done_left", "done_right"),
    )


def _one_step_definition() -> ExecutionNetDefinition:
    return ExecutionNetDefinition(
        definition_key="one.step",
        places=("done", "pending"),
        transitions=(ExecutionTransition("finish", "pure"),),
        input_arcs=(ExecutionInputArc("pending", "finish", 1),),
        output_arcs=(ExecutionOutputArc("finish", "done", 1),),
        initial_place="pending",
        initial_tokens=1,
        terminal_places=("done",),
    )


def _parent(execution) -> ExecutionParentAuthority:
    context = execution.operation.canonical.context
    assert context.own_transition_firing_ref is not None
    return ExecutionParentAuthority(
        invocation_ref=context.invocation_ref,
        business_firing_ref=context.own_transition_firing_ref,
        business_net_ref=context.net_instance_ref,
        business_checkpoint_ref=context.admission_marking_checkpoint_ref,
    )


def _active_parent(tmp_path, name="one"):
    owner = _owner(tmp_path / name)
    execution = _start(owner, "gate.inspect")
    return owner, _parent(execution), execution


def _workspace_metadata(*, ref, parent_ref, authority_ref=None, core):
    def payload(value):
        return {
            "entity_type": value.entity_type,
            "logical_id": str(value.entity_id),
            "version_id": str(value.version_id),
        }

    return {
        "workspace_lineage_id": str(ref.entity_id),
        "workspace_revision_id": str(ref.version_id),
        "workspace_revision_ref": payload(ref),
        "run_ref": payload(VersionRef("run/v1", new_id("run"), new_id("run_version"))),
        "task_ref": payload(VersionRef("task/v1", core.task_id, new_id("task_version"))),
        "net_instance_ref": payload(VersionRef(
            "net_instance/v1", new_id("net_instance"),
            new_id("net_instance_version"))),
        "parent_revision_ref": payload(parent_ref) if parent_ref else None,
        "base_revision_ref": payload(parent_ref) if parent_ref else None,
        "producer_invocation_ref": None,
        "transition_firing_ref": None,
        "firing_workspace_binding_ref": None,
        "reopen_authorization_ref": (
            payload(authority_ref) if authority_ref else None),
        "disposition": (
            "owner_reopen" if authority_ref else
            "committed" if parent_ref else "genesis"),
        "changed_paths": [], "deleted_paths": [], "path_deltas": [],
        "inventory_paths": [], "conflict_paths": [],
        "semantic_output_refs": [], "trace_summary_refs": [],
        "payload_kind": "full_workspace_tar", "settled": True,
    }


def test_generic_workspace_head_advance_is_atomic_against_a_stale_head(
        tmp_path, monkeypatch):
    # This test targets the transaction primitive; the repository-wide schema
    # index is independently covered and may be under concurrent maintenance.
    monkeypatch.setattr(SchemaCatalog, "_verify_repository_index", lambda self: None)
    core = _RegistryCore(tmp_path / "registry", create=True)
    lineage_id = new_id("workspace_lineage")
    genesis_ref = VersionRef(
        "workspace_revision/v1", lineage_id, new_id("workspace_revision"))
    genesis = _workspace_metadata(ref=genesis_ref, parent_ref=None, core=core)
    tx = core.begin(idempotency_key="workspace-cas:genesis")
    tx.prewrite(
        object_type="workspace_revision/v1", logical_id=lineage_id,
        version_id=genesis_ref.version_id, payload=b"genesis", metadata=genesis,
        media_type="application/x-tar", schema_ref="registry_v1/workspace_revision/v1")
    tx.commit()
    with core.event_store.connect() as db:
        authority_row = db.execute(
            "SELECT object_type,logical_id,version_id FROM objects "
            "WHERE object_type='registry_type_catalog/v1'").fetchone()
    assert authority_row is not None
    authority_ref = VersionRef(
        str(authority_row["object_type"]),
        TypedId.parse(str(authority_row["logical_id"])),
        TypedId.parse(str(authority_row["version_id"])))

    successor_ref = VersionRef(
        "workspace_revision/v1", lineage_id, new_id("workspace_revision"))
    successor = _workspace_metadata(
        ref=successor_ref, parent_ref=genesis_ref, authority_ref=authority_ref,
        core=core)
    tx = core.begin(idempotency_key="workspace-cas:success")
    tx.prewrite(
        object_type="workspace_revision/v1", logical_id=lineage_id,
        version_id=successor_ref.version_id, payload=canonical_json(successor),
        metadata=successor, media_type="application/x-tar",
        schema_ref="registry_v1/workspace_revision/v1")
    tx.relate(TypedRelation(
        new_id("relation"), "derived_from", successor_ref, authority_ref,
        system_owned=True))
    tx.advance_workspace_head(
        lineage_ref=genesis_ref, expected_head_ref=genesis_ref,
        successor_ref=successor_ref, authority_ref=authority_ref)
    tx.commit()

    stale_ref = VersionRef(
        "workspace_revision/v1", lineage_id, new_id("workspace_revision"))
    stale = _workspace_metadata(
        ref=stale_ref, parent_ref=genesis_ref, authority_ref=authority_ref,
        core=core)
    tx = core.begin(idempotency_key="workspace-cas:stale")
    tx.prewrite(
        object_type="workspace_revision/v1", logical_id=lineage_id,
        version_id=stale_ref.version_id, payload=canonical_json(stale),
        metadata=stale, media_type="application/x-tar",
        schema_ref="registry_v1/workspace_revision/v1")
    tx.relate(TypedRelation(
        new_id("relation"), "derived_from", stale_ref, authority_ref,
        system_owned=True))
    tx.advance_workspace_head(
        lineage_ref=genesis_ref, expected_head_ref=genesis_ref,
        successor_ref=stale_ref, authority_ref=authority_ref)
    with pytest.raises(RegistryConflict, match="compare-and-swap conflict"):
        tx.commit()
    with core.event_store.connect() as db:
        head = db.execute(
            "SELECT workspace_revision_version_id FROM workspace_lineage_heads "
            "WHERE workspace_lineage_id=?", (str(lineage_id),)).fetchone()
        stale_object = db.execute(
            "SELECT 1 FROM objects WHERE version_id=?", (str(stale_ref.version_id),)).fetchone()
    assert head["workspace_revision_version_id"] == str(successor_ref.version_id)
    assert stale_object is None


def test_definition_rejects_nonmechanical_weights_and_topology():
    with pytest.raises(ExecutionNetError, match="weight must be positive"):
        ExecutionInputArc("source", "split", 0)
    with pytest.raises(ExecutionNetError, match="unknown endpoint"):
        ExecutionNetDefinition(
            definition_key="invalid.net",
            places=("done", "source"),
            transitions=(ExecutionTransition("split"),),
            input_arcs=(ExecutionInputArc("missing", "split"),),
            output_arcs=(ExecutionOutputArc("split", "done"),),
            initial_place="source",
            initial_tokens=1,
            terminal_places=("done",),
        )


def test_weighted_lifecycle_isolated_replay_stale_and_map_ready(tmp_path):
    owner, parent, business_execution = _active_parent(tmp_path)
    core = owner._core
    runtime = ExecutionRuntime(core)
    business_events_before = core.event_store.list_events_by_type(
        ("marking_checkpoint_committed/v1",))
    business_tokens_before = tuple(
        row["version_id"]
        for row in core.event_store.object_rows_by_type("petri_token/v1"))

    initial = runtime.instantiate(
        parent=parent, definition=_definition(),
        idempotency_key="execution:test:instantiate")
    initial_replay = runtime.instantiate(
        parent=parent, definition=_definition(),
        idempotency_key="execution:test:instantiate")
    assert initial_replay == initial
    assert initial.checkpoint.marking == {"source": 2}
    assert runtime.enabled_transitions(initial) == ("split",)
    initial_checkpoint_ref = initial.checkpoint.checkpoint_ref
    with pytest.raises(ExecutionNetError, match="is not enabled"):
        runtime.start(
            instance_ref=initial.instance_ref, parent=parent,
            checkpoint_ref=initial_checkpoint_ref, transition_id="join",
            materialization_key="materialize:not-enabled",
            idempotency_key="execution:test:not-enabled")

    split_active = runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=initial_checkpoint_ref, transition_id="split",
        idempotency_key="execution:test:split:start")
    split_firing_ref = split_active.checkpoint.active_firing_refs[0]
    assert split_active.checkpoint.marking == {}
    split_recovery = runtime.classify_active_firings(split_active)[0]
    assert (split_recovery.recovery_mode, split_recovery.action,
            split_recovery.materialization_key) == (
                "pure", "replay_pure", None)
    assert runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=initial_checkpoint_ref, transition_id="split",
        idempotency_key="execution:test:split:start") == split_active

    split_settled = runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split_active.checkpoint.checkpoint_ref,
        firing_ref=split_firing_ref,
        idempotency_key="execution:test:split:settle")
    assert split_settled.checkpoint.marking == {"left": 2, "right": 1}
    assert runtime.enabled_transitions(split_settled) == ("join",)
    assert runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split_active.checkpoint.checkpoint_ref,
        firing_ref=split_firing_ref,
        idempotency_key="execution:test:split:settle") == split_settled

    with pytest.raises(RegistryConflict, match="checkpoint predecessor is stale"):
        runtime.start(
            instance_ref=initial.instance_ref, parent=parent,
            checkpoint_ref=initial_checkpoint_ref, transition_id="split",
            idempotency_key="execution:test:stale:start")

    join_active = runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split_settled.checkpoint.checkpoint_ref,
        transition_id="join",
        materialization_key="materialize:weighted-result",
        idempotency_key="execution:test:join:start")
    join_firing_ref = join_active.checkpoint.active_firing_refs[0]
    join_recovery = runtime.classify_active_firings(join_active)
    assert len(join_recovery) == 1
    assert (join_recovery[0].recovery_mode, join_recovery[0].action,
            join_recovery[0].materialization_key) == (
                "idempotent_materialization",
                "retry_same_materialization",
                "materialize:weighted-result")
    recovery_reader = ExecutionRuntime(
        _RegistryCore(core.run_dir, create=False, read_only=True))
    assert recovery_reader.classify_active_firings(
        recovery_reader.hydrate(initial.instance_ref)) == join_recovery

    terminal = runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=join_active.checkpoint.checkpoint_ref,
        firing_ref=join_firing_ref,
        idempotency_key="execution:test:join:settle",
        evidence_refs=(initial.definition_ref,))
    assert terminal.checkpoint.marking == {"done": 1}
    assert terminal.checkpoint.map_ready
    assert terminal.map_ready_checkpoint_ref == terminal.checkpoint.checkpoint_ref
    assert runtime.enabled_transitions(terminal) == ()

    checkpoints = core.event_store.object_rows_by_logical(
        initial.checkpoint.checkpoint_ref.entity_id,
        object_type="execution_checkpoint/v1")
    assert [row["version_id"] for row in checkpoints] == [
        str(initial_checkpoint_ref.version_id),
        str(split_active.checkpoint.checkpoint_ref.version_id),
        str(split_settled.checkpoint.checkpoint_ref.version_id),
        str(join_active.checkpoint.checkpoint_ref.version_id),
        str(terminal.checkpoint.checkpoint_ref.version_id),
    ]
    assert core.event_store.list_events_by_type(
        ("marking_checkpoint_committed/v1",)) == business_events_before
    assert tuple(
        row["version_id"]
        for row in core.event_store.object_rows_by_type("petri_token/v1")
    ) == business_tokens_before

    reopened = _RegistryCore(core.run_dir, create=False, read_only=True)
    assert ExecutionRuntime(reopened).hydrate(
        initial.instance_ref).checkpoint == terminal.checkpoint

    business_outputs = _products(owner, business_execution)
    owner.succeed(
        business_outputs,
        command_id="execution:test:publish-parent-business-firing")
    assert runtime.instantiate(
        parent=parent, definition=_definition(),
        idempotency_key="execution:test:instantiate") == terminal
    assert runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=initial_checkpoint_ref, transition_id="split",
        idempotency_key="execution:test:split:start") == terminal
    assert runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split_active.checkpoint.checkpoint_ref,
        firing_ref=split_firing_ref,
        idempotency_key="execution:test:split:settle") == terminal
    with pytest.raises(ExecutionNetError, match="not exact and open"):
        runtime.instantiate(
            parent=parent, definition=_definition(),
            idempotency_key="execution:test:new-after-parent-published")


def test_parent_crossing_is_rejected(tmp_path):
    owner, parent, _ = _active_parent(tmp_path, "first")
    state = ExecutionRuntime(owner._core).instantiate(
        parent=parent, definition=_definition(),
        idempotency_key="execution:test:parent")
    _, other_parent, _ = _active_parent(tmp_path, "second")
    with pytest.raises(ExecutionNetError, match="another parent invocation"):
        ExecutionRuntime(owner._core).hydrate(
            state.instance_ref, parent=other_parent)


def test_independent_branches_can_remain_active_and_settle_in_any_order(
        tmp_path):
    owner, parent, _ = _active_parent(tmp_path, "parallel")
    runtime = ExecutionRuntime(owner._core)
    initial = runtime.instantiate(
        parent=parent, definition=_parallel_definition(),
        idempotency_key="execution:parallel:instantiate")
    split = runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=initial.checkpoint.checkpoint_ref,
        transition_id="split", idempotency_key="execution:parallel:split:start")
    split_settled = runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split.checkpoint.checkpoint_ref,
        firing_ref=split.checkpoint.active_firing_refs[0],
        idempotency_key="execution:parallel:split:settle")
    assert runtime.enabled_transitions(split_settled) == (
        "finish_left", "finish_right")

    left = runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=split_settled.checkpoint.checkpoint_ref,
        transition_id="finish_left",
        materialization_key="execution:parallel:left",
        idempotency_key="execution:parallel:left:start")
    left_firing, = left.checkpoint.active_firing_refs
    assert runtime.enabled_transitions(left) == ("finish_right",)
    both = runtime.start(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=left.checkpoint.checkpoint_ref,
        transition_id="finish_right",
        materialization_key="execution:parallel:right",
        idempotency_key="execution:parallel:right:start")
    assert len(both.checkpoint.active_firing_refs) == 2
    right_firing = next(
        ref for ref in both.checkpoint.active_firing_refs
        if ref != left_firing)
    assert {item.transition_id for item in runtime.classify_active_firings(both)} == {
        "finish_left", "finish_right"}

    with pytest.raises(
            RegistryConflict, match="checkpoint predecessor is stale"):
        runtime.settle(
            instance_ref=initial.instance_ref, parent=parent,
            checkpoint_ref=left.checkpoint.checkpoint_ref,
            firing_ref=left_firing,
            evidence_refs=(initial.instance_ref,),
            idempotency_key="execution:parallel:left:stale-settle")

    left_done = runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=both.checkpoint.checkpoint_ref,
        firing_ref=left_firing,
        evidence_refs=(initial.instance_ref,),
        idempotency_key="execution:parallel:left:settle")
    assert left_done.checkpoint.active_firing_refs == (right_firing,)
    assert left_done.checkpoint.marking == {"done_left": 1}
    assert not left_done.checkpoint.map_ready
    terminal = runtime.settle(
        instance_ref=initial.instance_ref, parent=parent,
        checkpoint_ref=left_done.checkpoint.checkpoint_ref,
        firing_ref=right_firing,
        evidence_refs=(initial.definition_ref,),
        idempotency_key="execution:parallel:right:settle")
    assert terminal.checkpoint.marking == {
        "done_left": 1, "done_right": 1}
    assert terminal.checkpoint.active_firing_refs == ()
    assert terminal.checkpoint.evidence_refs == (
        initial.instance_ref, initial.definition_ref)
    assert terminal.checkpoint.map_ready


def test_success_child_set_seal_rejects_late_execution_instance(
        tmp_path, monkeypatch):
    owner, parent, business_execution = _active_parent(tmp_path, "child-race")
    runtime = ExecutionRuntime(owner._core)
    outputs = _products(owner, business_execution)
    original = file_execution_net.stage_execution_terminal_mappings
    late = {}

    def attach_after_enumeration(*args, **kwargs):
        mapping_refs = original(*args, **kwargs)
        if not late:
            late["state"] = runtime.instantiate(
                parent=parent, definition=_one_step_definition(),
                idempotency_key="execution:race:late-instance")
        return mapping_refs

    monkeypatch.setattr(
        file_execution_net, "stage_execution_terminal_mappings",
        attach_after_enumeration)
    with pytest.raises(RegistryConflict, match="execution-children"):
        owner.succeed(outputs, command_id="execution:race:success")

    publication = owner._core.event_store.firing_publication_row(
        parent.business_firing_ref.version_id)
    assert publication is not None
    assert publication["state"] == "PROVISIONAL"
    monkeypatch.setattr(
        file_execution_net, "stage_execution_terminal_mappings", original)
    with pytest.raises(
            ResourceIntegrityFault,
            match="every execution instance map-ready"):
        owner.succeed(outputs, command_id="execution:race:success")

    state = late["state"]
    active = runtime.start(
        instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=state.checkpoint.checkpoint_ref,
        transition_id="finish",
        idempotency_key="execution:race:finish:start")
    terminal = runtime.settle(
        instance_ref=state.instance_ref, parent=parent,
        checkpoint_ref=active.checkpoint.checkpoint_ref,
        firing_ref=active.checkpoint.active_firing_refs[0],
        evidence_refs=(state.definition_ref,),
        idempotency_key="execution:race:finish:settle")
    assert terminal.checkpoint.map_ready
    owner.succeed(outputs, command_id="execution:race:success")

    assert owner._core.event_store.firing_publication_row(
        parent.business_firing_ref.version_id)["state"] == "PUBLISHED"
    assert len(owner._core.event_store.canonical_object_rows(
        object_type="execution_terminal_mapping/v1")) == 1
    assert len(owner._core.event_store.list_events_by_type(
        ("execution_instance_attached/v1",))) == 1
    assert len(owner._core.event_store.list_events_by_type(
        ("execution_children_sealed/v1",))) == 1
