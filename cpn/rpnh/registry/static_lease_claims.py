"""Exact static lease-read claims at the original admission transaction cut."""
from __future__ import annotations

import json

from ..executable_net import load_compiled_net
from ..marking import TeamNetMarking
from ..runtime_net import RuntimeNet
from .declared_effect_validation import _marking, _resource_plan
from .event_store import RegistryConflict
from .identities import TypedId
from .publication import _version_from_payload
from .schema_catalog import canonical_json


def validate_static_lease_claim(context, firing, checkpoint):
    """Derive references from the adopted PN, never caller-supplied modes.

    Legacy declarations without static lease reads retain their admission
    contract. No new publication, index, capability or scheduler is involved.
    """
    def obj(ref, kind=None):
        if not context.exact_ref_exists(ref, kind):
            raise RegistryConflict('static lease claim lacks an exact registered reference')
        value = context.version_metadata(ref['version_id'], ref['entity_type'])
        if value is None:
            raise RegistryConflict('static lease claim reference is not visible')
        return value

    net = obj(firing['net_instance_ref'], 'net_instance/v1')
    declaration = net['team_net_declaration_resource_ref']
    declaration_ref = {'entity_type': 'resource_version/v1',
        'logical_id': declaration['resource_id'], 'version_id': declaration['resource_version_id']}
    metadata = obj(declaration_ref, 'resource_version/v1')
    if metadata.get('content_schema_ref') != 'rpnh/executable_net/v1':
        return
    version = TypedId.parse(declaration_ref['version_id'], expected='resource_version')
    payload = (context.event_store.path.parent / 'objects' / version.kind / version.value).read_bytes()
    if len(payload) != metadata['size']:
        raise RegistryConflict('static lease claim declaration byte size differs')
    compiled = load_compiled_net(json.loads(payload))
    lease_places = {place.name for place in compiled.symbolic.places
                    if place.token_kind == 'resource_lease'}
    if not any(arc.transition == firing['transition_id'] and arc.direction == 'input'
               and arc.mode == 'read' and arc.place in lease_places for arc in compiled.symbolic.arcs):
        return
    try:
        root = obj(net['team_design_root_ref'], 'team_design_root/v1')
        structure = RuntimeNet(compiled, net_ref=_version_from_payload(firing['net_instance_ref']),
            resource_plan=_resource_plan(net, root, compiled, obj))
        refs = firing['claimed_input_refs']
        if (sorted(ref['version_id'] for ref in refs) != firing['claimed_input_version_ids']
                or len({canonical_json(ref) for ref in refs}) != len(refs)):
            raise ValueError('claim ref/index identity differs')
        tokens = {canonical_json(ref): obj(ref, 'petri_token/v1') for ref in checkpoint['token_refs']}
        if any(state['petri_token_ref'] != json.loads(key)
               or state['net_instance_ref'] != firing['net_instance_ref'] for key, state in tokens.items()):
            raise ValueError('checkpoint token self/net identity differs')
        local = TeamNetMarking.from_authority(structure, _marking(checkpoint, tokens, set()))
        exact = tuple(_version_from_payload(ref) for ref in refs)
        epoch = local.claim_exact_registered_firing(firing['transition_id'], exact)
        consumed = {token.token_ref for token in local.claimed_tokens(firing['transition_id'], epoch)}
        references = set(exact) - consumed
        delta = obj(firing['claim_marking_delta_ref'], 'marking_delta/v1')
        declared_consumed = {_version_from_payload(ref) for ref in delta['consumed_refs']}
        if references & declared_consumed:
            raise ValueError('read reference was declared consumed')
        # Keep the existing Module admission index contract, including legacy
        # ordinary read behavior; callers cannot erase its consume exclusions.
        consumed_places = {place for place, target, _weight in structure.token_input_arcs
                           if target == firing['transition_id']}
        expected_consumed = {_version_from_payload(ref) for ref in refs
                             if tokens[canonical_json(ref)]['place'] in consumed_places}
        if declared_consumed != expected_consumed:
            raise ValueError('consume index differs from declared Module input modes')
    except (KeyError, TypeError, ValueError) as exc:
        raise RegistryConflict('static lease claim differs from adopted PN/marking: ' + str(exc)) from exc


__all__ = ('validate_static_lease_claim',)
