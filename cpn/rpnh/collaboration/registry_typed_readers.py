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


def prepared_at(core, reference, snapshot):
    source_id, ref = wire_ref(reference)
    kind=ref['entity_type'].split('/')[0]
    expected=({'resource_version':('resource','resource_version'),'native_run_identity':('run','run_version'),
        'run_terminal_evidence':('terminal_evidence','terminal_evidence_version'),'plan_version':('plan','plan_version'),
        'node_declaration':('node','node_declaration_version')}.get(kind)
        or (('resource','resource_version') if kind.startswith('collaboration_') else None))
    if expected is None and kind in {'task','task_round','principal','bootstrap_command','team_design_root','net_instance',
            'marking_checkpoint','petri_token','execution_checkpoint','operation_binding','operation_spec',
            'output_binding','executable_transition_binding','user_authority_decision'}:
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
    """Bounded immutable read. Legacy material checksums are not invented."""
    if material:
        checker = getattr(snapshot, 'check_authorized', None)
        if checker is None:
            raise TypedReadError('MATERIAL_ACCESS_NOT_GRANTED')
        checker(reference, 'material')
    prepared = prepared_at(core, reference, snapshot)
    if prepared.size > max_bytes:
        raise TypedReadError('MATERIAL_TOO_LARGE')
    store = core.object_store
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


def descriptor_at(core, reference, snapshot):
    raw, prepared = payload_at(core, reference, snapshot)
    if prepared.object_type == 'resource_version/v1':
        raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
    if prepared.media_type != 'application/json':
        raise TypedReadError('INTEGRITY_FAILED')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise TypedReadError('INTEGRITY_FAILED') from exc
    if canonical_json(body) != canonical_json(prepared.metadata):
        raise TypedReadError('INTEGRITY_FAILED')
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
    scalar_stem={'native_run_identity':'run','node_declaration':'node','plan_version':'plan'}.get(stem,stem)
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
                    raw,_=payload_at(core,dep,snapshot)
                    if canonical_json(json.loads(raw))!=canonical_json(authority.metadata):
                        raise TypedReadError('INTEGRITY_FAILED')
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

    def fingerprint(self):
        return hashlib.sha256(canonical_json({'version':self.version,'fields':_FIELDS,
            'callback_contract':'canonical_snapshot_descriptor_and_owned_public_projection/v1'})).hexdigest()

    def fields(self, entry_type):
        if entry_type not in _FIELDS: raise TypedReadError('UNSUPPORTED_ENTRY_TYPE')
        return {key:_kind(key) for key in _FIELDS[entry_type]}

    def default_index_projection(self, entry_type):
        return tuple(key for key in self.fields(entry_type) if key not in {'members','token_refs','active_firing_refs',
            'evidence_refs','node_refs','operation_binding_refs','output_binding_refs','executable_transition_binding_refs'})

    def default_record_projection(self, entry_type):
        return tuple(self.fields(entry_type))

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
        if entry_type == 'resource_version/v1':
            item = prepared_at(core, entry_ref, snapshot)
            body = dict(item.metadata)
            body.update(resource_id=local['logical_id'], resource_version_id=local['version_id'], byte_count=item.size)
        else:
            body = descriptor_at(core, entry_ref, snapshot)
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
        if local['entity_type']=='marking_checkpoint/v1':
            body['net_ref']=qualify(wire_ref(entry_ref)[0],body['net_instance_ref'])
            body['checkpoint_commit_ordinal']=_checkpoint_commit_ordinal(body,snapshot)
        if 'commit_ordinal' in selected:
            ordinals = getattr(snapshot,'publication_ordinals',{})
            if local['version_id'] in ordinals: body['commit_ordinal']=ordinals[local['version_id']]
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


def _checkpoint_commit_ordinal(body,snapshot):
    ref=body['marking_checkpoint_ref']
    witnesses=[e for e in snapshot.events if e.event_type=='marking_checkpoint_committed/v1' and e.payload.get('checkpoint_ref')==ref]
    if len(witnesses)!=1 or body.get('settled') is not True:
        raise TypedReadError('INTEGRITY_FAILED')
    witness=witnesses[0]
    commits=[e for e in snapshot.events if e.event_type=='transaction_committed/v1' and e.transaction_id==witness.transaction_id]
    row=snapshot.objects[ref['version_id']]
    if (len(commits)!=1 or row['transaction_id']!=str(witness.transaction_id)
            or any(witness.payload.get(field)!=body.get(field) for field in ('net_instance_ref','team_design_root_ref',
                'previous_checkpoint_ref','settlement_delta_ref','transition_firing_refs','workspace_revision_refs','settled'))):
        raise TypedReadError('INTEGRITY_FAILED')
    return commits[0].ordinal
