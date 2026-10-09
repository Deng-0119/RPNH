"""Fixed-cut heterogeneous public readers. This module grants no authority.

Only a session-created canonical snapshot is accepted by the session adapter.
Callbacks never discover current facts, compile definitions, or load a HOST.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from contextlib import nullcontext
from collections.abc import Mapping

from ..registry.identities import TypedId
from ..registry.models import PreparedObject
from ..registry.schema_catalog import canonical_json
from .references import SourceQualifiedResourceRef, SourceQualifiedVersionRef


class TypedReadError(ValueError):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


def wire_ref(reference):
    if isinstance(reference, (SourceQualifiedResourceRef, SourceQualifiedVersionRef)):
        reference = reference.to_dict()
    if not isinstance(reference, dict) or set(reference) != {'schema_version', 'source_id', 'ref'}:
        raise TypedReadError('INVALID_REFERENCE')
    ref = reference['ref']
    if reference['schema_version'] == 'rpnh/collaboration/source_resource_ref/v1':
        if set(ref) != {'resource_id', 'resource_version_id'}:
            raise TypedReadError('INVALID_REFERENCE')
        TypedId.parse(ref['resource_id'], expected='resource')
        TypedId.parse(ref['resource_version_id'], expected='resource_version')
        local = dict(entity_type='resource_version/v1', logical_id=ref['resource_id'], version_id=ref['resource_version_id'])
    elif reference['schema_version'] == 'rpnh/collaboration/source_version_ref/v1':
        if set(ref) != {'entity_type', 'logical_id', 'version_id'}:
            raise TypedReadError('INVALID_REFERENCE')
        TypedId.parse(ref['logical_id']); TypedId.parse(ref['version_id'])
        local = dict(ref)
    else:
        raise TypedReadError('INVALID_REFERENCE')
    if not isinstance(reference['source_id'], str) or not reference['source_id']:
        raise TypedReadError('INVALID_REFERENCE')
    return reference['source_id'], local


def qualify(source_id, ref):
    if 'resource_id' in ref:
        return {'schema_version': 'rpnh/collaboration/source_resource_ref/v1', 'source_id': source_id, 'ref': dict(ref)}
    return {'schema_version': 'rpnh/collaboration/source_version_ref/v1', 'source_id': source_id, 'ref': dict(ref)}


def _context(snapshot):
    return getattr(snapshot, '_origin_context', None)


def _cached_read(core, reference, snapshot, namespace, contract, loader):
    context = _context(snapshot)
    if context is None:
        return loader()
    source, ref = wire_ref(reference)
    key = (source, ref['entity_type'], ref['logical_id'], ref['version_id'])
    return context.cached(namespace, key, contract, loader)


def prepared_at(core, reference, snapshot):
    return _cached_read(core, reference, snapshot, 'prepared', 'exact-schema/v1',
        lambda: _prepared_at(core, reference, snapshot))


def _prepared_at(core, reference, snapshot):
    source_id, ref = wire_ref(reference)
    kind=ref['entity_type'].split('/')[0]
    expected=({'resource_version':('resource','resource_version'),'native_run_identity':('run','run_version'),
        'run_terminal_evidence':('terminal_evidence','terminal_evidence_version'),'plan_version':('plan','plan_version'),
        'node_declaration':('node','node_declaration_version'),
        'workspace_write_intent':('write_intent','write_intent_version')}.get(kind)
        or (('resource','resource_version') if kind.startswith('collaboration_') else None))
    if expected is None and kind in {'task','task_round','principal','bootstrap_command','team_design_root','net_instance',
            'marking_checkpoint','petri_token','execution_checkpoint','operation_binding','operation_spec',
            'output_binding','executable_transition_binding','user_authority_decision',
            'invocation','transition_firing','firing_admission','firing_completion',
            'operation_result','marking_delta','operation_execution_lease'}:
        expected=(kind,kind+'_version')
    if expected is not None and (TypedId.parse(ref['logical_id']).kind,TypedId.parse(ref['version_id']).kind)!=expected:
        raise TypedReadError('INTEGRITY_FAILED')
    if getattr(snapshot, 'source_id', source_id) != source_id:
        raise TypedReadError('INVALID_REFERENCE')
    row = snapshot.objects.get(ref['version_id'])
    if row is None:
        raise TypedReadError('NOT_PRESENT_AT_CUT')
    if row['object_type'] != ref['entity_type'] or row['logical_id'] != ref['logical_id']:
        raise TypedReadError('INTEGRITY_FAILED')
    context = _context(snapshot)
    if context is not None:
        context.reserve(size=context.bytes_bound(row['metadata_json'] if 'metadata_json' in row else row['metadata']) + 512)
        if row['schema_ref'] != 'registry_v1/' + ref['entity_type']:
            raise TypedReadError('INTEGRITY_FAILED')
    body = json.loads(row['metadata_json']) if 'metadata_json' in row else row['metadata']
    core.catalog.validate_instance(ref['entity_type'], category='object', instance=body)
    if ref['entity_type']=='resource_version/v1' and (body['resource_id']!=ref['logical_id']
            or body['resource_version_id']!=ref['version_id'] or body['size']!=row['size']
            or body['media_type']!=row['media_type'] or body['task_ref']['logical_id']!=str(core.task_id)):
        raise TypedReadError('INTEGRITY_FAILED')
    return PreparedObject(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id']),
        row['size'], row['media_type'], row['schema_ref'],
        TypedId.parse(row['producer_invocation_id']) if row['producer_invocation_id'] else None,
        row['storage_locator'], body)


def payload_at(core, reference, snapshot, *, material=False, max_bytes=4 * 1024 * 1024):
    return _cached_read(core, reference, snapshot, 'payload', ('envelope-digest/v1', material, max_bytes),
        lambda: _payload_at(core, reference, snapshot, material=material, max_bytes=max_bytes))


def _payload_at(core, reference, snapshot, *, material=False, max_bytes=4 * 1024 * 1024):
    """Bounded immutable read. Legacy material checksums are not invented."""
    if material:
        checker = getattr(snapshot, 'check_authorized', None)
        if checker is None:
            raise TypedReadError('MATERIAL_ACCESS_NOT_GRANTED')
        checker(reference, 'material')
    prepared = prepared_at(core, reference, snapshot)
    if prepared.size > max_bytes:
        raise TypedReadError('MATERIAL_TOO_LARGE')
    context = _context(snapshot)
    if context is not None:
        context.reserve(size=prepared.size + 1)
    store = core.object_store
    guard = (context.scratch(size=context.bytes_bound(prepared.metadata) + 1024)
        if context is not None else nullcontext())
    with guard:
        store.validate_envelope(prepared)
    if prepared.storage_locator != store.locator_for_version(prepared.version_id):
        raise TypedReadError('INTEGRITY_FAILED')
    path = store.path_for_version(prepared.version_id)
    if any(value.is_symlink() for value in (store.root,path.parent,path)):
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        if hasattr(os,'O_NOFOLLOW') and os.open in os.supports_dir_fd:
            # Pin each directory descriptor and refuse symlink traversal at every
            # store-owned component, including races after the initial check.
            root_fd=os.open(store.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                kind_fd=os.open(prepared.version_id.kind,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root_fd)
                try:
                    fd=os.open(prepared.version_id.value,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=kind_fd)
                    with os.fdopen(fd,'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise TypedReadError('INTEGRITY_FAILED')
                        raw=stream.read(prepared.size+1)
                finally:os.close(kind_fd)
            finally:os.close(root_fd)
        else:
            # The platform's configured read-only store must still provide an
            # ordinary, non-symlink file and stable parent identities.
            parents=tuple((p.stat().st_dev,p.stat().st_ino) for p in (store.root,path.parent))
            with path.open('rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise TypedReadError('INTEGRITY_FAILED')
                raw=stream.read(prepared.size+1)
            if parents!=tuple((p.stat().st_dev,p.stat().st_ino) for p in (store.root,path.parent)):
                raise TypedReadError('INTEGRITY_FAILED')
    except OSError as exc:
        raise TypedReadError('INTEGRITY_FAILED') from exc
    if len(raw) != prepared.size: raise TypedReadError('INTEGRITY_FAILED')
    recorded = prepared.metadata.get('descriptors', {}).get('content_sha256')
    if recorded is not None and recorded != hashlib.sha256(raw).hexdigest():
        raise TypedReadError('INTEGRITY_FAILED')
    return raw, prepared


def _descriptor_body_at(core, reference, snapshot):
    def load():
        raw, prepared = payload_at(core, reference, snapshot)
        if prepared.object_type == 'resource_version/v1':
            raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
        if prepared.media_type != 'application/json':
            raise TypedReadError('INTEGRITY_FAILED')
        context = _context(snapshot)
        if context is not None:
            context.reserve(size=12 * len(raw) + 256)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise TypedReadError('INTEGRITY_FAILED') from exc
        guard = (context.scratch(size=context.bytes_bound(body) + context.bytes_bound(prepared.metadata))
            if context is not None else nullcontext())
        with guard:
            if canonical_json(body) != canonical_json(prepared.metadata):
                raise TypedReadError('INTEGRITY_FAILED')
        return body
    return _cached_read(core, reference, snapshot, 'descriptor-body', 'canonical-metadata/v1', load)


def descriptor_at(core, reference, snapshot):
    return _cached_read(core, reference, snapshot, 'descriptor', 'identity-and-limited-dependencies/v1',
        lambda: _descriptor_at(core, reference, snapshot))


def _descriptor_at(core, reference, snapshot):
    body = _descriptor_body_at(core, reference, snapshot)
    source, local = wire_ref(reference)
    stem = local['entity_type'].split('/')[0]
    self_key = {'collaboration_net_revision':'revision_ref','collaboration_assembly_revision':'revision_ref',
        'collaboration_branch':'branch_ref','collaboration_source_set':'source_set_ref',
        'collaboration_candidate_plan':'plan_ref','run_terminal_evidence':'terminal_evidence_ref',
        'node_declaration':'node_ref','user_authority_decision':'decision_ref'}.get(stem,stem+'_ref')
    own = body.get(self_key)
    if own is not None:
        if 'source_id' in own:
            if wire_ref(own) != (source, local):
                raise TypedReadError('INTEGRITY_FAILED')
        elif own != local:
            raise TypedReadError('INTEGRITY_FAILED')
    scalar_stem={'native_run_identity':'run','node_declaration':'node','plan_version':'plan',
        'workspace_write_intent':'write_intent'}.get(stem,stem)
    if scalar_stem!='schema':
        for suffix,expected in (('_id',local['logical_id']),('_version_id',local['version_id'])):
            if scalar_stem+suffix in body and body[scalar_stem+suffix]!=expected:
                raise TypedReadError('INTEGRITY_FAILED')
    owner = body.get('owner_task_ref') or body.get('task_ref')
    if owner:
        owner_local = owner.get('ref', owner)
        if owner_local['logical_id'] != str(core.task_id):
            raise TypedReadError('INTEGRITY_FAILED')
    for key in ('owner_task_ref', 'producer_principal_ref', 'publisher_bootstrap_ref', 'head_revision_ref',
                'fork_base_revision_ref', 'predecessor_branch_ref', 'expected_head_revision_ref', 'net_instance_ref'):
        dep = body.get(key)
        if dep is not None:
            dep = dep if 'source_id' in dep else qualify(source, dep)
            if dep['source_id'] == source:
                authority=prepared_at(core, dep, snapshot)
                if authority.object_type!='resource_version/v1' and str(authority.version_id)!=local['version_id']:
                    if _context(snapshot) is None:
                        raw, _ = payload_at(core, dep, snapshot)
                        if canonical_json(json.loads(raw)) != canonical_json(authority.metadata):
                            raise TypedReadError('INTEGRITY_FAILED')
                    else:
                        _descriptor_body_at(core, dep, snapshot)
    return body


_AUTHOR = ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
    'definition_kind', 'definition_ref', 'parent_revision_refs', 'selected_change_refs',
    'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref', 'open_region_contract_ref', 'subnet_provenance_ref')
_BRANCH = ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref', 'fork_base_revision_ref',
    'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id')
_ASSEMBLY = ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
    'parent_revision_ref', 'plan_ref', 'generated_revision_ref', 'compiled_inventory_ref', 'lowering_mapping_ref')
_FIELDS = {
    'resource_version/v1': ('resource_id', 'resource_version_id', 'media_type', 'content_schema_ref', 'byte_count', 'summary'),
    **{f'collaboration_net_revision/v{v}': _AUTHOR if v == 1 else tuple(x for x in _AUTHOR if x not in {
        'selected_change_refs', 'open_region_contract_ref'}) + ('source_contract','graph_source_ref','graph_recipe_ref','graph_source_mapping_ref')
        + (('author_kind','author_command_ref','resolution_ref') if v == 3 else ()) for v in (1,2,3)},
    **{f'collaboration_branch/v{v}': _BRANCH for v in (1,2,3)},
    **{f'collaboration_assembly_revision/v{v}': _ASSEMBLY for v in range(1,10)},
    'collaboration_source_set/v1': ('schema_version','source_set_ref','owner_task_ref','predecessor_ref','sequence','command_id','members'),
    **{f'collaboration_candidate_plan/v{v}': ('schema_version','plan_ref','manifest_ref','source_id','command_id','owner_task_ref',
        'producer_principal_ref','author_ref','principal_ref','bootstrap_ref','task_round_ref','schema_refs','operation_refs') for v in (1,2)},
    'net_instance/v1': ('net_instance_ref','task_round_ref','plan_ref','team_design_root_ref','llm_macro_net_ref',
        'team_net_declaration_resource_ref','executable_transition_binding_refs','node_refs','operation_binding_refs','output_binding_refs'),
    'marking_checkpoint/v1': ('checkpoint_commit_ordinal','net_ref','marking_checkpoint_ref','net_instance_ref','epoch','token_refs','settled','previous_checkpoint_ref','transition_firing_refs'),
    'petri_token/v1': ('petri_token_ref','net_instance_ref','place','kind','resource_ref','epoch','consumed_by','token_id'),
    'execution_checkpoint/v1': ('execution_checkpoint_ref','execution_instance_ref','execution_net_definition_ref','sequence',
        'previous_checkpoint_ref','transition_firing_ref','token_refs','active_firing_refs','evidence_refs','status'),
    'native_run_identity/v1': ('run_id','run_version_id','task_ref','task_branch_ref','branch_id','protocol_versions'),
    'run_terminal_evidence/v1': ('terminal_evidence_ref','run_ref','terminal_occurrence_ref','run_outcome','terminal_result_ref','final_result_index_ref','final_checkpoint_ref'),
}

_FIELDS['collaboration_assembly_revision/v6'] = tuple(field for field in _ASSEMBLY if field!='parent_revision_ref') + ('operation','parent_revision_refs','lineage_root_ref','analysis_ref')
_FIELDS = {kind:(*fields,'commit_ordinal') for kind,fields in _FIELDS.items()}

# Compatibility defaults are explicit baseline data, independent of _FIELDS.
# Preserve field order and the original index exclusions, including arrays that
# were intentionally present. Adding a catalog field must not widen a default.
# Future reader types require explicit self-ref-only defaults; never fall back
# to all catalog fields. No new entry types are enabled by this table.
_LEGACY_DEFAULT_PROJECTIONS = (
    (
        ('collaboration_assembly_revision/v1', 'collaboration_assembly_revision/v2',
            'collaboration_assembly_revision/v3', 'collaboration_assembly_revision/v4',
            'collaboration_assembly_revision/v5', 'collaboration_assembly_revision/v7',
            'collaboration_assembly_revision/v8', 'collaboration_assembly_revision/v9'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'parent_revision_ref', 'plan_ref', 'generated_revision_ref', 'compiled_inventory_ref',
            'lowering_mapping_ref', 'commit_ordinal'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'parent_revision_ref', 'plan_ref', 'generated_revision_ref', 'compiled_inventory_ref',
            'lowering_mapping_ref', 'commit_ordinal'),
    ),
    (
        ('collaboration_assembly_revision/v6',),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'plan_ref', 'generated_revision_ref', 'compiled_inventory_ref', 'lowering_mapping_ref',
            'operation', 'parent_revision_refs', 'lineage_root_ref', 'analysis_ref', 'commit_ordinal'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'plan_ref', 'generated_revision_ref', 'compiled_inventory_ref', 'lowering_mapping_ref',
            'operation', 'parent_revision_refs', 'lineage_root_ref', 'analysis_ref', 'commit_ordinal'),
    ),
    (
        ('collaboration_branch/v1', 'collaboration_branch/v2', 'collaboration_branch/v3'),
        ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref', 'fork_base_revision_ref',
            'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id', 'commit_ordinal'),
        ('schema_version', 'branch_ref', 'owner_task_ref', 'head_revision_ref', 'fork_base_revision_ref',
            'upstream_branch_ref', 'predecessor_branch_ref', 'sequence', 'command_id', 'commit_ordinal'),
    ),
    (
        ('collaboration_candidate_plan/v1', 'collaboration_candidate_plan/v2'),
        ('schema_version', 'plan_ref', 'manifest_ref', 'source_id', 'command_id', 'owner_task_ref',
            'producer_principal_ref', 'author_ref', 'principal_ref', 'bootstrap_ref', 'task_round_ref',
            'schema_refs', 'operation_refs', 'commit_ordinal'),
        ('schema_version', 'plan_ref', 'manifest_ref', 'source_id', 'command_id', 'owner_task_ref',
            'producer_principal_ref', 'author_ref', 'principal_ref', 'bootstrap_ref', 'task_round_ref',
            'schema_refs', 'operation_refs', 'commit_ordinal'),
    ),
    (
        ('collaboration_net_revision/v1',),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'selected_change_refs',
            'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref',
            'open_region_contract_ref', 'subnet_provenance_ref', 'commit_ordinal'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'selected_change_refs',
            'element_mapping_ref', 'boundary_mapping_ref', 'host_requirements_ref',
            'open_region_contract_ref', 'subnet_provenance_ref', 'commit_ordinal'),
    ),
    (
        ('collaboration_net_revision/v2',),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'element_mapping_ref',
            'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref', 'source_contract',
            'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref', 'commit_ordinal'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'element_mapping_ref',
            'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref', 'source_contract',
            'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref', 'commit_ordinal'),
    ),
    (
        ('collaboration_net_revision/v3',),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'element_mapping_ref',
            'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref', 'source_contract',
            'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref', 'author_kind',
            'author_command_ref', 'resolution_ref', 'commit_ordinal'),
        ('schema_version', 'revision_ref', 'owner_task_ref', 'producer_principal_ref', 'command_id',
            'definition_kind', 'definition_ref', 'parent_revision_refs', 'element_mapping_ref',
            'boundary_mapping_ref', 'host_requirements_ref', 'subnet_provenance_ref', 'source_contract',
            'graph_source_ref', 'graph_recipe_ref', 'graph_source_mapping_ref', 'author_kind',
            'author_command_ref', 'resolution_ref', 'commit_ordinal'),
    ),
    (
        ('collaboration_source_set/v1',),
        ('schema_version', 'source_set_ref', 'owner_task_ref', 'predecessor_ref', 'sequence', 'command_id',
            'members', 'commit_ordinal'),
        ('schema_version', 'source_set_ref', 'owner_task_ref', 'predecessor_ref', 'sequence', 'command_id',
            'commit_ordinal'),
    ),
    (
        ('execution_checkpoint/v1',),
        ('execution_checkpoint_ref', 'execution_instance_ref', 'execution_net_definition_ref', 'sequence',
            'previous_checkpoint_ref', 'transition_firing_ref', 'token_refs', 'active_firing_refs',
            'evidence_refs', 'status', 'commit_ordinal'),
        ('execution_checkpoint_ref', 'execution_instance_ref', 'execution_net_definition_ref', 'sequence',
            'previous_checkpoint_ref', 'transition_firing_ref', 'status', 'commit_ordinal'),
    ),
    (
        ('marking_checkpoint/v1',),
        ('checkpoint_commit_ordinal', 'net_ref', 'marking_checkpoint_ref', 'net_instance_ref', 'epoch',
            'token_refs', 'settled', 'previous_checkpoint_ref', 'transition_firing_refs', 'commit_ordinal'),
        ('checkpoint_commit_ordinal', 'net_ref', 'marking_checkpoint_ref', 'net_instance_ref', 'epoch',
            'settled', 'previous_checkpoint_ref', 'transition_firing_refs', 'commit_ordinal'),
    ),
    (
        ('native_run_identity/v1',),
        ('run_id', 'run_version_id', 'task_ref', 'task_branch_ref', 'branch_id', 'protocol_versions',
            'commit_ordinal'),
        ('run_id', 'run_version_id', 'task_ref', 'task_branch_ref', 'branch_id', 'protocol_versions',
            'commit_ordinal'),
    ),
    (
        ('net_instance/v1',),
        ('net_instance_ref', 'task_round_ref', 'plan_ref', 'team_design_root_ref', 'llm_macro_net_ref',
            'team_net_declaration_resource_ref', 'executable_transition_binding_refs', 'node_refs',
            'operation_binding_refs', 'output_binding_refs', 'commit_ordinal'),
        ('net_instance_ref', 'task_round_ref', 'plan_ref', 'team_design_root_ref', 'llm_macro_net_ref',
            'team_net_declaration_resource_ref', 'commit_ordinal'),
    ),
    (
        ('petri_token/v1',),
        ('petri_token_ref', 'net_instance_ref', 'place', 'kind', 'resource_ref', 'epoch', 'consumed_by',
            'token_id', 'commit_ordinal'),
        ('petri_token_ref', 'net_instance_ref', 'place', 'kind', 'resource_ref', 'epoch', 'consumed_by',
            'token_id', 'commit_ordinal'),
    ),
    (
        ('resource_version/v1',),
        ('resource_id', 'resource_version_id', 'media_type', 'content_schema_ref', 'byte_count', 'summary',
            'commit_ordinal'),
        ('resource_id', 'resource_version_id', 'media_type', 'content_schema_ref', 'byte_count', 'summary',
            'commit_ordinal'),
    ),
    (
        ('run_terminal_evidence/v1',),
        ('terminal_evidence_ref', 'run_ref', 'terminal_occurrence_ref', 'run_outcome',
            'terminal_result_ref', 'final_result_index_ref', 'final_checkpoint_ref', 'commit_ordinal'),
        ('terminal_evidence_ref', 'run_ref', 'terminal_occurrence_ref', 'run_outcome',
            'terminal_result_ref', 'final_result_index_ref', 'final_checkpoint_ref', 'commit_ordinal'),
    ),
)
_DEFAULT_RECORD_PROJECTIONS = {
    kind: record for kinds, record, index in _LEGACY_DEFAULT_PROJECTIONS for kind in kinds}
_DEFAULT_INDEX_PROJECTIONS = {
    kind: index for kinds, record, index in _LEGACY_DEFAULT_PROJECTIONS for kind in kinds}


# Six finite origin readers. Derived Start fields remain explicitly opt-in.
_START_FIELDS = (
    'start_event_id', 'start_transaction_id', 'start_ordinal',
    'start_input_binding_refs', 'start_input_resource_refs')
_ORIGIN_FIELDS = {
    'invocation/v1': {
        'invocation_ref': 'ref', 'task_ref': 'ref', 'net_instance_ref': 'ref',
        'own_transition_firing_ref': 'ref', 'operation_binding_ref': 'ref',
        'operation_execution_lease_ref': 'ref'},
    'transition_firing/v1': {
        'transition_firing_ref': 'ref', 'task_ref': 'ref', 'net_instance_ref': 'ref',
        'operation_binding_ref': 'ref', 'firing_admission_ref': 'ref',
        'claim_marking_delta_ref': 'ref', 'admission_marking_checkpoint_ref': 'ref',
        'claimed_input_refs': 'array', 'start_event_id': 'string', 'start_transaction_id': 'string',
        'start_ordinal': 'number', 'start_input_binding_refs': 'array', 'start_input_resource_refs': 'array'},
    'firing_admission/v1': {
        'firing_admission_ref': 'ref', 'transition_firing_ref': 'ref', 'invocation_ref': 'ref',
        'operation_execution_lease_ref': 'ref', 'claim_marking_delta_ref': 'ref',
        'admission_marking_checkpoint_ref': 'ref'},
    'firing_completion/v2': {
        'firing_completion_ref': 'ref', 'transition_firing_ref': 'ref', 'invocation_ref': 'ref',
        'operation_result_ref': 'ref', 'successor_checkpoint_ref': 'ref'},
    'operation_result/v1': {
        'operation_result_ref': 'ref', 'invocation_ref': 'ref', 'transition_firing_ref': 'ref',
        'business_outcome': 'string', 'output_resource_refs': 'array'},
    'marking_delta/v1': {
        'marking_delta_ref': 'ref', 'net_instance_ref': 'ref', 'phase': 'string',
        'transition_firing_refs': 'array', 'operation_binding_refs': 'array', 'consumed_refs': 'array'},
}
_RESOURCE_TRACE_FIELDS = {
    'origin_kind': 'string', 'producer_ref': 'ref',
    'provenance_producer_invocation_ref': 'ref', 'provenance_operation_binding_ref': 'ref'}
# Explicit semantic types for the selected finite fields. Schema-level generic
# refs are deliberately insufficient at this typed-reader boundary.
_ORIGIN_REF_FIELDS = {
    'petri_token_ref': ('petri_token/v1', 'petri_token', 'petri_token_version'),
    'marking_delta_ref': ('marking_delta/v1', 'marking_delta', 'marking_delta_version'),
    'invocation_ref': ('invocation/v1', 'invocation', 'invocation_version'),
    'task_ref': ('task/v1', 'task', 'task_version'),
    'net_instance_ref': ('net_instance/v1', 'net_instance', 'net_instance_version'),
    'own_transition_firing_ref': ('transition_firing/v1', 'transition_firing', 'transition_firing_version'),
    'transition_firing_ref': ('transition_firing/v1', 'transition_firing', 'transition_firing_version'),
    'operation_binding_ref': ('operation_binding/v1', 'operation_binding', 'operation_binding_version'),
    'operation_execution_lease_ref': ('operation_execution_lease/v1', 'operation_execution_lease', 'operation_execution_lease_version'),
    'firing_admission_ref': ('firing_admission/v1', 'firing_admission', 'firing_admission_version'),
    'claim_marking_delta_ref': ('marking_delta/v1', 'marking_delta', 'marking_delta_version'),
    'admission_marking_checkpoint_ref': ('marking_checkpoint/v1', 'marking_checkpoint', 'marking_checkpoint_version'),
    'firing_completion_ref': ('firing_completion/v2', 'firing_completion', 'firing_completion_version'),
    'operation_result_ref': ('operation_result/v1', 'operation_result', 'operation_result_version'),
    'successor_checkpoint_ref': ('marking_checkpoint/v1', 'marking_checkpoint', 'marking_checkpoint_version'),
    'provenance_producer_invocation_ref': ('invocation/v1', 'invocation', 'invocation_version'),
    'provenance_operation_binding_ref': ('operation_binding/v1', 'operation_binding', 'operation_binding_version'),
}
_ORIGIN_NULLABLE_FIELDS = frozenset({
    ('invocation/v1', 'own_transition_firing_ref'),
    ('firing_completion/v2', 'invocation_ref'),
    ('resource_version/v1', 'provenance_producer_invocation_ref'),
    ('resource_version/v1', 'provenance_operation_binding_ref'),
})


def _selected_exact_ref(value, expected=None, *, nullable=False):
    if value is None and nullable:
        return
    if (not isinstance(value, Mapping) or set(value) != {'entity_type', 'logical_id', 'version_id'}
            or not isinstance(value['entity_type'], str)):
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        logical = TypedId.parse(value['logical_id'])
        version = TypedId.parse(value['version_id'])
    except (TypeError, ValueError):
        raise TypedReadError('INTEGRITY_FAILED') from None
    if expected is not None and (value['entity_type'], logical.kind, version.kind) != expected:
        raise TypedReadError('INTEGRITY_FAILED')


# Concrete member/scalar contracts are fingerprinted alongside the public
# coarse field kinds. The latter remain the legacy IndexQuery kind vocabulary.
_ORIGIN_ARRAY_FIELDS = {
    'output_resource_refs': (('resource_version/v1', 'resource', 'resource_version'),),
    'claimed_input_refs': (('petri_token/v1', 'petri_token', 'petri_token_version'),),
    'consumed_refs': (('petri_token/v1', 'petri_token', 'petri_token_version'),),
    'transition_firing_refs': (('transition_firing/v1', 'transition_firing', 'transition_firing_version'),),
    'operation_binding_refs': (('operation_binding/v1', 'operation_binding', 'operation_binding_version'),),
    'start_input_binding_refs': (('resource_version/v1', 'resource', 'resource_version'),
        ('petri_token/v1', 'petri_token', 'petri_token_version')),
    'start_input_resource_refs': (('resource_id', 'resource'), ('resource_version_id', 'resource_version')),
}
_ORIGIN_SCALAR_FIELDS = {
    'start_event_id': ('typed_id', 'event'),
    'start_transaction_id': ('typed_id', 'transaction'),
    'start_ordinal': ('strict_integer', 0),
    'phase': ('enum', 'claim', 'settlement'),
    'business_outcome': ('enum', 'completed'),
    'origin_kind': ('nonempty_string',),
}
_TOKEN_SELECTED_FIELDS = {'petri_token_ref': 'ref', 'net_instance_ref': 'ref', 'resource_ref': 'ref'}
_ORIGIN_RESOURCE_REF_CONTRACT = {
    'petri_token/v1.resource_ref': {'nullable': True, 'members': ('resource_id', 'resource_version_id'),
        'kinds': ('resource', 'resource_version')},
    'transition_firing/v1.start_input_resource_refs': {'nullable': False, 'unique': False,
        'members': ('resource_id', 'resource_version_id'), 'kinds': ('resource', 'resource_version')},
}


def _selected_resource_ref(value, *, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, Mapping) or set(value) != {'resource_id', 'resource_version_id'}:
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        TypedId.parse(value['resource_id'], expected='resource')
        TypedId.parse(value['resource_version_id'], expected='resource_version')
    except (TypeError, ValueError):
        raise TypedReadError('INTEGRITY_FAILED') from None


def _validate_origin_projection(entry_type, body, selected, context):
    if entry_type not in _ORIGIN_FIELDS and entry_type not in ('resource_version/v1', 'petri_token/v1'):
        return
    for field in selected:
        if (entry_type == 'resource_version/v1' and field not in _RESOURCE_TRACE_FIELDS
                or entry_type == 'petri_token/v1' and field not in _TOKEN_SELECTED_FIELDS):
            continue
        if field not in body:
            raise TypedReadError('INTEGRITY_FAILED')
        value = body[field]
        if field in _ORIGIN_REF_FIELDS:
            _selected_exact_ref(value, _ORIGIN_REF_FIELDS[field],
                nullable=(entry_type, field) in _ORIGIN_NULLABLE_FIELDS)
        elif field == 'resource_ref':
            _selected_resource_ref(value, nullable=True)
        elif field == 'producer_ref':
            # Other legal origins have producer types with different kind names.
            _selected_exact_ref(value)
        elif field in _ORIGIN_SCALAR_FIELDS:
            contract = _ORIGIN_SCALAR_FIELDS[field]
            try:
                if contract[0] == 'typed_id':
                    TypedId.parse(value, expected=contract[1])
                elif contract[0] == 'strict_integer':
                    if type(value) is not int or value < contract[1]:
                        raise ValueError
                elif contract[0] == 'enum':
                    if type(value) is not str or value not in contract[1:]:
                        raise ValueError
                elif type(value) is not str or not value:
                    raise ValueError
            except (TypeError, ValueError):
                raise TypedReadError('INTEGRITY_FAILED') from None
        elif field in _ORIGIN_ARRAY_FIELDS:
            if type(value) is not list:
                raise TypedReadError('INTEGRITY_FAILED')
            def validate(values):
                seen = set()
                for item in values:
                    if field == 'start_input_resource_refs':
                        _selected_resource_ref(item)
                        continue  # Repeated actual resources retain positions.
                    _selected_exact_ref(item)
                    identity = (item['entity_type'], TypedId.parse(item['logical_id']).kind,
                        TypedId.parse(item['version_id']).kind)
                    if identity not in _ORIGIN_ARRAY_FIELDS[field]:
                        raise TypedReadError('INTEGRITY_FAILED')
                    key = (item['entity_type'], item['logical_id'], item['version_id'])
                    if key in seen:
                        raise TypedReadError('INTEGRITY_FAILED')
                    seen.add(key)
                return True
            if context is None:
                validate(value)
            else:
                # Public selected shape validation is distinct from the stronger
                # whole-F C summary and Core O membership contracts.
                with context.scratch(size=context.bytes_bound(value) + 128 * len(value)):
                    context.semantic_array(value, 'A', ('selected-origin-ref-array/v1', field), validate)


_FIELDS['resource_version/v1'] += tuple(_RESOURCE_TRACE_FIELDS)
for _entry_type, _field_types in _ORIGIN_FIELDS.items():
    _FIELDS[_entry_type] = tuple(_field_types)
    _self = (_entry_type.split('/')[0] + '_ref',)
    _DEFAULT_RECORD_PROJECTIONS[_entry_type] = _self
    _DEFAULT_INDEX_PROJECTIONS[_entry_type] = _self


def _kind(field):
    if field in {'schema_refs','operation_refs'}: return 'object'
    if field in {'content_schema_ref','consumed_by'}: return 'nullable_string'
    if field.endswith('_refs') or field in {'members','protocol_versions'}: return 'array'
    if field.endswith('_ref'): return 'ref'
    if field in {'byte_count','epoch','sequence','commit_ordinal','checkpoint_commit_ordinal','token_id'}: return 'number'
    if field == 'settled': return 'boolean'
    return 'string'


class TypedReaderCatalog:
    version = 'rpnh/typed_reader_catalog/v1'
    entry_types = tuple(sorted(_FIELDS))
    public_projection_types = ('net_instance/v1', 'marking_checkpoint/v1',
        'collaboration_net_revision/v1', 'collaboration_assembly_revision/v9')

    def fingerprint(self):
        from ._origin_core_contract import core_contract
        return hashlib.sha256(canonical_json({'version':self.version,
            'fields':{kind:self.fields(kind) for kind in self.entry_types},
            'default_record_projections':{kind:self.default_record_projection(kind) for kind in self.entry_types},
            'default_index_projections':{kind:self.default_index_projection(kind) for kind in self.entry_types},
            'callback_contract':'canonical_snapshot_descriptor_and_owned_public_projection/v1',
            'origin_core_contract': core_contract(),
            'origin_ref_fields': _ORIGIN_REF_FIELDS,
            'origin_nullable_fields': sorted(_ORIGIN_NULLABLE_FIELDS),
            'origin_start_fields': _START_FIELDS,
            'origin_array_fields': _ORIGIN_ARRAY_FIELDS,
            'origin_scalar_fields': _ORIGIN_SCALAR_FIELDS,
            'origin_resource_ref_contract': _ORIGIN_RESOURCE_REF_CONTRACT,
            'token_selected_fields': _TOKEN_SELECTED_FIELDS})).hexdigest()

    def fields(self, entry_type):
        if entry_type not in _FIELDS: raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
        if entry_type in _ORIGIN_FIELDS:
            return dict(_ORIGIN_FIELDS[entry_type])
        return {key:(_RESOURCE_TRACE_FIELDS[key] if entry_type == 'resource_version/v1'
            and key in _RESOURCE_TRACE_FIELDS else _TOKEN_SELECTED_FIELDS[key] if entry_type == 'petri_token/v1'
            and key in _TOKEN_SELECTED_FIELDS else _kind(key)) for key in _FIELDS[entry_type]}

    def default_index_projection(self, entry_type):
        self.fields(entry_type)
        if entry_type not in _DEFAULT_INDEX_PROJECTIONS:
            raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
        return _DEFAULT_INDEX_PROJECTIONS[entry_type]

    def default_record_projection(self, entry_type):
        self.fields(entry_type)
        if entry_type not in _DEFAULT_RECORD_PROJECTIONS:
            raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
        return _DEFAULT_RECORD_PROJECTIONS[entry_type]

    def read_exact(self, core, entry_ref, *, snapshot, projection=None):
        _, local = wire_ref(entry_ref)
        entry_type = local['entity_type']
        wire=entry_ref.to_dict() if hasattr(entry_ref,'to_dict') else entry_ref
        if entry_type=='resource_version/v1' and wire['schema_version']!='rpnh/collaboration/source_resource_ref/v1':
            raise TypedReadError('INVALID_REFERENCE')
        allowed = self.fields(entry_type)
        selected = self.default_record_projection(entry_type) if projection is None else tuple(projection)
        if len(selected) != len(set(selected)) or set(selected) - set(allowed):
            raise TypedReadError('INVALID_PROJECTION')
        context = _context(snapshot)
        if entry_type == 'resource_version/v1':
            item = prepared_at(core, entry_ref, snapshot)
            if context is not None:
                # Include the derived flat trace fields before constructing the
                # resource projection source, not just the metadata copy.
                context.reserve(size=1024 + 2 * context.bytes_bound(item.metadata))
            body = dict(item.metadata)
            trace = item.metadata.get('reference_provenance', {})
            if 'origin_kind' in selected:
                body['origin_kind'] = item.metadata.get('origin_kind')
            if 'provenance_producer_invocation_ref' in selected:
                body['provenance_producer_invocation_ref'] = trace.get('producer_invocation_ref')
            if 'provenance_operation_binding_ref' in selected:
                body['provenance_operation_binding_ref'] = trace.get('operation_binding_ref')
            body.update(resource_id=local['logical_id'], resource_version_id=local['version_id'], byte_count=item.size)
        else:
            body = descriptor_at(core, entry_ref, snapshot)
            if context is not None:
                context.retain(body)
                body = dict(body)
            self._typed(core, entry_type, body)
            if entry_type == 'collaboration_net_revision/v1' and 'subnet_provenance_ref' in selected:
                target = entry_ref.to_dict() if hasattr(entry_ref,'to_dict') else entry_ref
                candidates = []
                for row in snapshot.objects.values():
                    if row['object_type'] != 'resource_version/v1': continue
                    meta = json.loads(row['metadata_json']) if 'metadata_json' in row else row['metadata']
                    if meta.get('descriptors',{}).get('subnet_import_revision_ref') == canonical_json(target).decode():
                        candidates.append(qualify(target['source_id'],{'resource_id':row['logical_id'],'resource_version_id':row['version_id']}))
                if len(candidates)>1: raise TypedReadError('INTEGRITY_FAILED')
                if candidates:
                    snapshot.check_authorized(candidates[0],'record')
                    body['subnet_provenance_ref'] = candidates[0]
        if entry_type == 'transition_firing/v1' and set(selected).intersection(_START_FIELDS):
            if context is None:
                # Only a session can establish the bounded same-cut owner.
                raise TypedReadError('INTEGRITY_FAILED')
            from ._product_origin_includes import _validated_start_fields
            derived = _validated_start_fields(context, local)
            context.reserve(size=context.bytes_bound(derived) + 256)
            body.update(derived)
        if (entry_type in _ORIGIN_FIELDS or entry_type == 'petri_token/v1'
                and any(field in _TOKEN_SELECTED_FIELDS for field in selected) or entry_type == 'resource_version/v1'
                and any(field in _RESOURCE_TRACE_FIELDS for field in selected)):
            if context is None:
                _validate_origin_projection(entry_type, body, selected, None)
            else:
                context.cached('projection-types', (entry_type, local['logical_id'], local['version_id'], selected),
                    'finite-origin-selected-fields/v1',
                    lambda: _validate_origin_projection(entry_type, body, selected, context))
        if local['entity_type']=='marking_checkpoint/v1':
            if context is not None:
                context.reserve(size=512 + context.bytes_bound(body['net_instance_ref']))
            body['net_ref']=qualify(wire_ref(entry_ref)[0],body['net_instance_ref'])
            body['checkpoint_commit_ordinal']=_checkpoint_commit_ordinal(body,snapshot)
        if 'commit_ordinal' in selected:
            ordinals = getattr(snapshot,'publication_ordinals',{})
            if local['version_id'] in ordinals: body['commit_ordinal']=ordinals[local['version_id']]
        if context is not None:
            context.reserve(size=256 + sum(context.bytes_bound(body[key]) + context.bytes_bound(key)
                for key in selected if key in body))
        return {key:deepcopy(body[key]) for key in selected if key in body}

    def read_index(self, core, entry_ref, *, snapshot, projection=None):
        """Read only the index fields needed for projection and predicates."""
        _, local = wire_ref(entry_ref)
        return self.read_exact(core, entry_ref, snapshot=snapshot,
            projection=self.default_index_projection(local['entity_type']) if projection is None else projection)

    @staticmethod
    def _typed(core, entry_type, body):
        if entry_type == 'collaboration_net_revision/v1':
            from .authoring import NetRevision
            NetRevision.from_dict(body, catalog=core.catalog)
        elif entry_type == 'collaboration_net_revision/v2':
            from .graph_authoring import GraphNetRevision
            GraphNetRevision.from_dict(body, catalog=core.catalog)
        elif entry_type == 'collaboration_net_revision/v3':
            from .graph_merge import GraphMergeNetRevision
            GraphMergeNetRevision.from_dict(body,catalog=core.catalog)
        elif entry_type == 'collaboration_source_set/v1':
            from .source_sets import SourceSetVersion
            SourceSetVersion.from_dict(body,core.catalog)
        elif entry_type.startswith('collaboration_branch/'):
            from .branches import _branch_record_type
            _branch_record_type(entry_type).from_dict(body, catalog=core.catalog)
        elif entry_type.startswith('collaboration_assembly_revision/'):
            import importlib
            version = int(entry_type.rsplit('/v',1)[1])
            module=importlib.import_module('.assemblies' if version==1 else f'.assembly_v{version}',__package__)
            cls=getattr(module,'AssemblyRevision' if version==1 else f'AssemblyRevisionV{version}')
            cls.from_dict(body,catalog=core.catalog)
        # Other finite versions expose only a schema-validated descriptor. They
        # do not claim material, lowering, candidate or runtime validity.

    def read_public_projection(self, core, entry_ref, *, snapshot):
        from .public_projections import read_public_projection
        return read_public_projection(core, entry_ref, snapshot=snapshot)

    def read_public_mapping(self, core, left_ref, right_ref, *, snapshot):
        from .public_projections import read_public_mapping
        return read_public_mapping(core, left_ref, right_ref, snapshot=snapshot)


DEFAULT_TYPED_READER_CATALOG = TypedReaderCatalog()


def _checkpoint_commit_ordinal(body, snapshot):
    context = _context(snapshot)
    if context is None:
        return _checkpoint_commit_ordinal_uncached(body, snapshot)
    ref = body['marking_checkpoint_ref']
    key = tuple(ref[name] for name in ('entity_type', 'logical_id', 'version_id'))
    return context.cached('checkpoint', key, 'committed-arrays/v1',
        lambda: _checkpoint_commit_ordinal_uncached(body, snapshot))


def _checkpoint_commit_ordinal_uncached(body, snapshot):
    ref = body['marking_checkpoint_ref']
    context = _context(snapshot)
    if context is None:
        witnesses = [e for e in snapshot.events if e.event_type == 'marking_checkpoint_committed/v1'
            and e.payload.get('checkpoint_ref') == ref]
    else:
        witnesses = context.checkpoint_events(ref)
        context.reserve('E', rows=len(witnesses), size=128 * len(witnesses))
    if (len(witnesses) != 1 or body.get('settled') is not True
            or context is not None and (witnesses[0].event_type != 'marking_checkpoint_committed/v1'
                or witnesses[0].payload_schema_ref != 'registry_v1/marking_checkpoint_committed/v1')):
        raise TypedReadError('INTEGRITY_FAILED')
    witness = witnesses[0]
    if context is None:
        commits = [e for e in snapshot.events if e.event_type == 'transaction_committed/v1'
            and e.transaction_id == witness.transaction_id]
    else:
        commits = context.commits_by_transaction.get(str(witness.transaction_id), ())
        context.reserve('E', rows=len(commits), size=128 * len(commits))
        # These are actual semantic array equality checks in the legacy
        # checkpoint predicate; reserve every compared position beforehand.
        for field in ('transition_firing_refs', 'workspace_revision_refs'):
            context.reserve('A', rows=len(witness.payload.get(field, ())) + len(body.get(field, ())))
    row = snapshot.objects[ref['version_id']]
    if (len(commits) != 1 or context is not None and (commits[0].event_type != 'transaction_committed/v1'
                or commits[0].payload_schema_ref != 'registry_v1/transaction_committed/v1')
            or row['transaction_id'] != str(witness.transaction_id)
            or any(witness.payload.get(field) != body.get(field) for field in ('net_instance_ref','team_design_root_ref',
                'previous_checkpoint_ref','settlement_delta_ref','transition_firing_refs','workspace_revision_refs','settled'))):
        raise TypedReadError('INTEGRITY_FAILED')
    return commits[0].ordinal
