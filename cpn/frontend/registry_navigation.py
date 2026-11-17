"""Display links from explicitly declared exact Registry references.

This is not a relationship database or a recursive metadata crawler.
"""
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef

# Only fields whose original schemas explicitly define local exact references.
_FIELDS = {
    'resource_version/v1': ('task_ref', 'net_ref', 'producer_ref', 'round_ref'),
    'final_result_index/v1': ('terminal_result_ref', 'terminal_occurrence_ref'),
    'run_execution_authority/v1': ('task_ref', 'run_ref', 'latest_checkpoint_ref', 'terminal_evidence_ref'),
}


def declared_links(object_type, metadata):
    """Return labels and exact refs; never infer references from paths or names."""
    values = [(name, metadata.get(name)) for name in _FIELDS.get(object_type, ())]
    if object_type == 'final_result_index/v1':
        values = [(f'entries[{i}]', value) for i, value in enumerate(metadata.get('entries', []))] + values
    result = []
    for label, value in values:
        if value is None:
            continue
        if set(value) == {'resource_id', 'resource_version_id'}:
            ref = VersionRef('resource_version/v1', TypedId.parse(value['resource_id'], expected='resource'),
                             TypedId.parse(value['resource_version_id'], expected='resource_version'))
        elif set(value) == {'entity_type', 'logical_id', 'version_id'}:
            ref = VersionRef(value['entity_type'], TypedId.parse(value['logical_id']), TypedId.parse(value['version_id']))
        else:
            # Caller validates original schemas before this display projection.
            raise ValueError('declared exact reference has an unexpected shape')
        result.append((label, ref))
    return result
