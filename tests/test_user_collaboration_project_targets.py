"""Reject invalid immutable project targets before original Registry commit."""
import pytest

import test_user_collaboration_authority as support
from cpn.rpnh.collaboration import UserCollaborationHost
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.publication import _version_from_payload


@pytest.mark.parametrize('invalid_field', ['branch', 'runtime', 'both'])
def test_project_rejects_wrong_target_kind_without_lockout(tmp_path, monkeypatch, invalid_field):
    monkeypatch.setattr(support.plain, 'plain_merge_schema_data', support.fixture_inventory)
    fixture = support.plain.fixture.__wrapped__(tmp_path, monkeypatch)
    core, gateway, _, _, _, base, _ = fixture
    row = core.event_store.object_rows_by_type('principal/v1')[0]
    principal = _version_from_payload({'entity_type': 'principal/v1',
        'logical_id': row['logical_id'], 'version_id': row['version_id']})
    host = UserCollaborationHost(core, gateway)
    session = host.create_session(origin_core=core, principal_ref=principal, command_id='owner')
    branch = gateway.create_author_branch(head_revision_ref=base.revision.revision_ref, command_id='official')
    before = support.plain.counts(core)
    with pytest.raises(RegistryConflict, match='exact canonical reference'):
        host.protect(session,
            branch_ref=principal if invalid_field in ('branch', 'both') else branch.branch_ref,
            runtime_net_ref=principal if invalid_field in ('runtime', 'both') else None,
            command_id='invalid-target')
    assert support.plain.counts(core) == before
    assert not core.event_store.object_rows_by_type(support.PROJECT)
    valid = host.protect(session, branch_ref=branch.branch_ref, command_id='valid-target')
    assert host.read_fact(session, valid)['data']['branch_ref']['entity_type'] == 'collaboration_branch/v1'
    after = support.plain.counts(core)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 2
