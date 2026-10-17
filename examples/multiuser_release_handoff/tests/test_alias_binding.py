"""Native alias retargeting after read admission; deterministic and offline."""
import json

import pytest

from examples.multiuser_release_handoff.delegation import StaleVersion, _inventory
from examples.multiuser_release_handoff.tests.test_delegation import world
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _ref_payload
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import BindResourceAddress
from test_static_lease_reads import no_external_transport


def test_native_alias_retarget_after_begin_read_keeps_original_binding(world):
    source, _, bind = world
    original = source.current()
    qa = bind("qa")
    support = bind("support")
    active = source.begin_read(qa)
    frozen_result = active.result_json
    pinned = active.result
    assert pinned["body"] == "release one"
    assert pinned["observer_grant_ref"] == _ref_payload(active.observer.grant_ref)
    assert pinned["requester_principal_ref"] != pinned["source_caller_principal_ref"]
    assert pinned["address_binding_ref"] == _ref_payload(qa.address_result.binding_ref.as_version_ref())
    for field in ("source_id", "slot_ref", "lease_identity_ref", "resource_ref", "version", "token_ref"):
        assert pinned[field] == original[field]

    with _inventory(source.owner):
        _, _, marking = hydrate_module_runtime(source.owner._core)
        lease = next(t for t in marking.tokens if _ref_payload(t.token_ref) == original["token_ref"])
        firing = active.execution.operation.firing
        assert lease.token_ref in firing.claimed_input_refs
        assert _ref_payload(lease.state.lease_identity_ref) == original["slot_ref"]
        assert _resource_payload(lease.state.resource_ref) == original["resource_ref"]
        request = next(t for t in marking.tokens if t.token_ref in firing.claimed_input_refs
                       and t.state.place == "source.request0")
        # The template requests the current slot (expected_resource_ref=None);
        # the admitted firing above pins its actual lease token and resource.
        assert len(request.state.lease_claims) == 1
        claim = request.state.lease_claims[0]
        assert claim.access_mode == "read"
        assert claim.lease_identity_ref == lease.state.lease_identity_ref
        assert claim.expected_resource_ref is None
        assert _ref_payload(firing.transition_firing_ref) in source.owner.snapshot()["active_firings"]

        # A real source prepare invocation can bind its own registered output.
        # This changes address metadata, not the mutable note slot or its lease.
        mutation = source._start(f"prepare{support.reader_index}")
        outputs = source.owner.products(mutation, outcome_id="complete", products={
            f"source.request{support.reader_index}": (support.request_json,),
        }, command_id="alias-test:prepare-products")
        replacement = outputs.outputs[0].resource_ref
        assert replacement != qa.address_result.resource_ref
        context = mutation.operation.canonical.context
        kernel, _ = source.owner.operation_repository()
        changed_binding = kernel.bind_address(context, BindResourceAddress(
            qa.address_result.address, replacement, context.operation_binding_ref,
            qa.address_result.binding_ref, "alias-test:retarget"), reference_only=True)
        source.owner.succeed(outputs, command_id="alias-test:prepare-success")

    changed = source._resolve("project/release-note")
    assert changed.binding_ref == changed_binding
    assert changed.binding_ref != qa.address_result.binding_ref
    assert changed.resource_ref == replacement
    assert source._resolve("project/index").binding_ref == qa.index_address_result.binding_ref
    assert source.current() == original
    # New discovery sees the changed native address and refuses to label this
    # unrelated request resource as the canonical mutable note slot.
    with pytest.raises(StaleVersion, match="discovered address does not identify current source slot"):
        bind("qa")

    # No restoration, fake resolver or address monkeypatch: finish while the
    # live alias still targets a different resource than the retained binding.
    completed = source.finish_read(active)
    assert active.result_json == frozen_result
    assert completed["status"] == "read_complete"
    assert {key: completed[key] for key in pinned} == pinned
    assert source._resolve("project/release-note").resource_ref == replacement
    assert source.current() == original
    with _inventory(source.owner):
        assert _ref_payload(firing.transition_firing_ref) not in source.owner.snapshot()["active_firings"]
        _, _, marking = hydrate_module_runtime(source.owner._core)
        result_token = next(t for t in marking.tokens if t.state.place == "source.result0")
        persisted, observer, _ = source._material(qa.session, result_token.state.resource_ref)
        from cpn.rpnh.registry.observer_access import revoke_observer_access
        revoke_observer_access(source.owner.schema_gateway, grant_ref=observer.grant_ref,
                               command_id="alias-test:revoke-result-reader")
    assert persisted == json.loads(frozen_result)
    assert _resource_payload(result_token.state.resource_ref) in completed["source_output_refs"]
