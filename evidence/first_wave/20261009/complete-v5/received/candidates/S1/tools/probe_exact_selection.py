"""Synthetic typed checkpoint probe; not a new Registry publication."""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from test_static_lease_interactions import _world, _settle
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.marking import TeamNetMarking
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import new_id
with TemporaryDirectory() as directory:
    owner = _world(Path(directory)/'run', variable_mode='produce', with_edit=True)
    admitted = owner.admit('step.run', logical_tau=1, command_id='test:interactions:admit')
    _settle(owner, admitted)
    _, structure, marking = hydrate_module_runtime(owner._core)
    carrier = next(t for t in marking.tokens if t.state.place == 'step.result')
    stale = replace(carrier, state=replace(carrier.state, lease_claims=(replace(
        carrier.state.lease_claims[0], expected_resource_ref=owner.original_input_ref),)))
    valid_ref = VersionRef('petri_token/v1', new_id('petri_token'), new_id('petri_token_version'))
    valid = replace(carrier, token_ref=valid_ref, state=replace(carrier.state,
        token_ref=valid_ref, token_id=marking.next_token_id))
    tokens = tuple(stale if t == carrier else t for t in marking.tokens) + (valid,)
    typed = replace(marking, tokens=tokens, token_refs=tuple(sorted((t.token_ref for t in tokens),
        key=lambda r: (r.entity_type,str(r.entity_id),str(r.version_id)))), next_token_id=marking.next_token_id + 1)
    local = TeamNetMarking.from_authority(structure, typed)
    exact = tuple(t.token_ref for t in tokens if t != stale)
    print('Synthetic typed checkpoint accepted. Registered resource refs are reused; added carrier is pure projection only.')
    print('default enabled:', local.is_enabled('step.edit'))
    print('exact reservation exists:', local._try_reserve('step.edit', set(),
        allowed_token_ids={t.state.token_id for t in tokens if t != stale}) is not None)
    try:
        local.claim_exact_registered_firing('step.edit', exact)
        print('exact install accepted')
    except Exception as exc:
        print('exact install rejected:', type(exc).__name__, str(exc))
