"""Fresh source declarations support native recovery of abandoned read claims."""
import json

from cpn.rpnh.collaboration.sources import get_local_source_identity
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.registry._operation import registration as native_registration
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.module_execution import install_active_module_claims
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.run import resume_run
from examples.multiuser_release_handoff.delegation import SourceDelegation
from examples.multiuser_release_handoff.tests.test_delegation import private_user
from test_static_lease_reads import no_external_transport


def _live_lease(marking):
    lease, = (
        token for token in marking.tokens
        if token.state.place == "source.lease"
        and token.state.consumed_by is None and token.state.epoch == marking.epoch
    )
    return lease


def test_source_checkpoint_reentry_interrupts_two_reads(tmp_path, monkeypatch, no_external_transport):
    # The real registration module owns this process-wide inventory.
    monkeypatch.setattr(native_registration, "_OPERATION_REGISTRATION",
                        native_registration._OPERATION_REGISTRATION)
    users = {role: private_user(tmp_path / role, role) for role in ("qa", "support")}
    body = "Original canonical release note; no revision has been published."
    source = SourceDelegation.create(
        run_dir=tmp_path / "source", body=body,
        requesters={role: owner for role, (owner, _) in users.items()},
        editors=(), workspace_root=tmp_path,
    )
    old_owner = source.owner
    original_snapshot = old_owner.snapshot()
    _, _, original_marking = hydrate_module_runtime(old_owner._core)
    original_lease = _live_lease(original_marking)
    checkpoint = original_marking.checkpoint_ref  # Before either read prepares.
    original_source_id = get_local_source_identity(old_owner._core).source_id
    writer_epoch = old_owner._core.writer_epoch
    assert original_snapshot["active_firings"] == []

    # Every fresh source operation has one empty, effect-free interruption;
    # complete still requires its original products.
    for component in original_snapshot["declaration"]["components"]:
        for operation in component["operations"]:
            interrupted, = (o for o in operation["outcomes"] if o["name"] == "interrupted")
            complete, = (o for o in operation["outcomes"] if o["name"] == "complete")
            assert interrupted["products"] == []
            assert interrupted.get("effects", []) == []
            assert complete["products"]

    reads = []
    for role, (owner, execution) in users.items():
        binding = source.bind_execution(
            origin_owner=owner, execution=execution, principal_ref=owner.principal_ref,
            call_id="source-recovery:read:" + role, tool_name="read_note", arguments={},
        )
        read = source.begin_read(binding)
        assert read.result["body"] == body
        assert read.result["token_ref"] == _ref_payload(original_lease.token_ref)
        assert read.result["version"] == str(original_lease.state.resource_ref.resource_version_id)
        reads.append(read)

    executable, structure, active_marking = hydrate_module_runtime(old_owner._core)
    live = TeamNetMarking.from_authority(structure, active_marking)
    active = install_active_module_claims(old_owner._core, live, executable.net_ref)
    old_firings = {read.execution.operation.firing.transition_firing_ref for read in reads}
    assert len(old_firings) == 2
    assert {firing.transition_firing_ref for firing in active} == old_firings
    assert len(live.active_claim_occurrences(live.epoch)) == 2
    assert all(original_lease.token_ref in firing.claimed_input_refs for firing in active)

    # Simulate writer loss by abandoning the old owner without finish_read,
    # products, succeed, or owner-stop. Only native resume fences and drains it.
    recovered = resume_run(
        old_owner.registration, run_dir=old_owner._core.run_dir,
        model_condition="trusted-local-host-no-provider", catalog=old_owner._core.catalog,
        checkpoint_version_id=str(checkpoint.version_id),
        reopen_command_id="source-recovery:original-checkpoint",
        reopen_reason="Recover abandoned source reads at the original canonical body",
    )
    assert recovered.identity.task_ref == old_owner.identity.task_ref
    assert recovered.identity.run_ref == old_owner.identity.run_ref
    assert recovered.principal_ref == old_owner.principal_ref
    assert recovered._core.writer_epoch == writer_epoch + 1
    assert get_local_source_identity(recovered._core).source_id == original_source_id

    # Use native owner/marking inspection: no foreign rebind or inventory bypass.
    snapshot = recovered.snapshot()
    executable, structure, marking = hydrate_module_runtime(recovered._core)
    assert snapshot["active_firings"] == []
    assert marking.epoch > active_marking.epoch
    assert marking.checkpoint_ref != checkpoint
    lease = _live_lease(marking)
    assert lease.state.lease_identity_ref == original_lease.state.lease_identity_ref
    assert lease.state.resource_ref == original_lease.state.resource_ref
    assert lease.token_ref != original_lease.token_ref
    prepared = recovered._core.get_version(lease.state.resource_ref.resource_version_id)
    assert json.loads(recovered._core.object_store.read_registered(prepared)) == {"body": body}
    live = TeamNetMarking.from_authority(structure, marking)
    assert not install_active_module_claims(recovered._core, live, executable.net_ref)
    assert not live.active_claim_occurrences(live.epoch)
    assert all(not token.state.lease_claims for token in marking.tokens
               if token.state.epoch == marking.epoch and token.state.consumed_by is None)

    # Each exact old firing settled through native interruption with no read
    # products. Native settlement's generic business_outcome is "completed";
    # that field alone must never be mistaken for a successful source read.
    for firing_ref in old_firings:
        settlement, = recovered._core.event_store.list_events_by_aggregate(
            str(firing_ref.entity_id), event_types=("transition_firing_settled/v1",),
        )
        assert settlement.payload["transition_firing_ref"] == _ref_payload(firing_ref)
        assert settlement.command_id.startswith("rpnh:reopen:registered-operation-interruption:")
        result_ref = settlement.payload["operation_result_ref"]
        result = recovered._core.get_version(TypedId.parse(result_ref["version_id"])).metadata
        assert result["output_resource_refs"] == []
        assert result["provider_attempt_evidence_refs"] == []
        assert result["workspace_access_set_ref"] is None
    assert not any(token["place"] in {"source.result0", "source.result1"}
                   for token in snapshot["marking"])
