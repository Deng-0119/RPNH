"""Explicit read-only managed call evidence for one canonical firing at a cut.

Only program facts and their exact receipt/output resources are disclosed. This
does not add Petri nodes, execution authority, or an arbitrary resource reader.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from . import checkpoint_view as checkpoints
from .firing_activity import _json

SCHEMA = 'rpnh/managed_tools/v1'
PROGRAM = 'agent_tool_program_invocation/v1'
CALL = 'agent_tool_program_call/v1'


class ManagedQueryError(ValueError):
    pass


class ManagedStale(RuntimeError):
    pass


def parse_query(query):
    try:
        q = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        if set(q) != {'net_ref', 'checkpoint_ref', 'cut', 'firing_ref'} or any(len(v) != 1 for v in q.values()):
            raise ValueError('query keys differ')
        selected = checkpoints.selector(_json(q['net_ref'][0]), _json(q['checkpoint_ref'][0]), int(q['cut'][0]))
        return {**selected, 'firing_ref': checkpoints.reference(_json(q['firing_ref'][0]), 'transition_firing/v1')}
    except (ValueError, TypeError, KeyError) as exc:
        raise ManagedQueryError('invalid managed evidence query') from exc


def managed_tools(provider, *, net_ref, checkpoint_ref, cut, firing_ref):
    selected = checkpoints.selector(net_ref, checkpoint_ref, cut)
    firing_ref = checkpoints.reference(firing_ref, 'transition_firing/v1')
    core, anchor = checkpoints._capture_anchor(provider)
    _, _, identity = checkpoints._chain(provider, core, anchor, selected)
    reads = checkpoints._BoundedReads(core, cut)
    checkpoint, commit = reads.checkpoint(checkpoint_ref)
    if commit.ordinal != cut:
        raise ManagedQueryError('checkpoint cut differs')
    _, bindings, _ = checkpoints._inventory(reads, selected, checkpoint, identity)
    firing = reads.metadata(firing_ref, 'transition_firing/v1')
    binding = next((b for b in bindings if b['transition_id'] == firing['transition_id']), None)
    if (firing['task_ref'] != identity['task_ref'] or firing['net_instance_ref'] != net_ref
            or binding is None or any(firing[k] != binding[k] for k in ('node_ref', 'operation_binding_ref'))):
        raise ManagedQueryError('firing is outside selected net binding')

    records = {}
    def add(ref, kind, value):
        key = json.dumps(ref, sort_keys=True)
        if key not in records:
            records[key] = {'ref': ref, 'kind': kind, 'value': value, 'links': []}
        return records[key]

    def fact(ref, kind):
        value = reads.metadata(ref, kind)
        field = 'program_invocation_ref' if kind == PROGRAM else 'program_call_ref'
        if value[field] != ref:
            raise ValueError('program fact self reference differs')
        return add(ref, kind, value)

    def resource(ref, kind, execution_ref=None):
        typed = checkpoints._resource_ref(ref)
        if kind == 'managed_plugin_call':
            # Native managed receipts use invocation_ref provenance (not an
            # agent_loop_ref). Read their own canonical publication and caller
            # binding, as ManagedPluginToolService does, without invoking the
            # general agent resource-consumption authority reader.
            row = core.event_store.object_row_for_view(reads.view, typed['version_id'])
            if row is None or row['object_type'] != 'resource_version/v1' or row['logical_id'] != typed['logical_id']:
                raise ValueError('receipt is not canonical at checkpoint')
            reads.verify_transaction(row['transaction_id'])
            metadata = json.loads(row['metadata_json'])
            core.catalog.validate_instance('resource_version/v1', category='object', instance=metadata)
            event, = [e for e in reads.events if str(e.event_id) == row['published_event_id']]
            if (event.event_type != 'object_version_published/v1'
                    or event.payload['metadata'] != metadata
                    or str(event.transaction_id) != row['transaction_id']
                    or any(event.payload[k] != row[k] for k in
                           ('object_type', 'logical_id', 'version_id', 'size', 'media_type', 'schema_ref', 'storage_locator'))):
                raise ValueError('receipt publication differs')
            lease = reads.metadata(execution_ref, 'operation_execution_lease/v1')
            provenance = metadata['reference_provenance']
            if (metadata['resource_id'] != ref['resource_id']
                    or metadata['resource_version_id'] != ref['resource_version_id']
                    or metadata['origin_kind'] != 'managed_native_plugin_invocation'
                    or metadata['task_ref'] != identity['task_ref'] or metadata['net_ref'] != net_ref
                    or metadata['producer_ref'] != lease['invocation_ref']
                    or metadata['lifetime_ref'] != execution_ref
                    or provenance['invocation_ref'] != lease['invocation_ref']
                    or provenance['operation_binding_ref'] != binding['operation_binding_ref']
                    or row['producer_invocation_id'] != lease['invocation_ref']['logical_id']
                    or str(event.producer_invocation_id) != row['producer_invocation_id']
                    or not metadata['descriptors'].get('managed_plugin_call')):
                raise ValueError('receipt caller provenance differs')
        else:
            row = reads.row(typed, 'resource_version/v1')
            metadata = reads.metadata(typed, 'resource_version/v1')
        value = json.loads(core.object_store.read_registered(reads.prepared(row, metadata)))
        if kind == 'managed_plugin_call' and metadata['descriptors']['phase'] != value['state']:
            raise ValueError('receipt publication phase differs')
        return add(ref, kind, value)

    def link(record, label, target):
        record['links'].append({'label': label, 'ref': target['ref']})

    def belongs(program):
        lease = reads.metadata(program['execution_ref'], 'operation_execution_lease/v1')
        invocation = reads.metadata(lease['invocation_ref'], 'invocation/v1')
        return invocation['own_transition_firing_ref'] == firing_ref

    # Iterate canonical publications at this checkpoint, never a latest-row
    # query that could backfill later results into historical inspection.
    parents = []
    for event in reads.events:
        p = event.payload
        if event.event_type != 'object_version_published/v1' or p.get('object_type') != PROGRAM:
            continue
        candidate = p['metadata']
        if belongs(candidate):
            record = fact(candidate['program_invocation_ref'], PROGRAM)
            parents.append(record)

    final_parents = []
    for parent in parents:
        value = parent['value']
        if value['output_resource_ref'] is None:
            continue
        final_parents.append(parent['ref'])
        output = resource(value['output_resource_ref'], 'tool_program_output/v1')
        output_value = output['value']
        admitted = fact(output_value['program_invocation_ref'], PROGRAM)
        if (output_value.get('kind') != 'tool_program_output/v1'
                or admitted['ref']['logical_id'] != parent['ref']['logical_id']
                or not belongs(admitted['value'])
                or output_value['agent_action_ref'] != value['agent_action_ref']
                or output_value['status'] != value['status']):
            raise ValueError('program output identity differs')
        link(parent, 'admitted_parent', admitted)
        link(admitted, 'completed_parent', parent)
        link(parent, 'output', output)
        link(output, 'parent', parent)
        children = []
        for ref in value['call_refs']:
            child = fact(ref, CALL)
            c = child['value']
            if c['program_invocation_ref'] != admitted['ref'] or c['agent_action_ref'] != value['agent_action_ref']:
                raise ValueError('child parent identity differs')
            children.append(c)
            link(parent, c['logical_key'], child)
            link(child, 'parent', admitted)
            link(child, 'completed_parent', parent)
            receipts = {}
            for field, label in (('started_receipt_ref', 'Start'), ('terminal_receipt_ref', 'terminal')):
                if c[field] is None:
                    continue
                receipt = resource(c[field], 'managed_plugin_call', value['execution_ref'])
                r = receipt['value']
                if (r['caller_firing_ref'] != firing_ref or r['caller_execution_ref'] != value['execution_ref']
                        or r['call_id'] != c['managed_call_id'] or r['arguments'] != c['arguments']
                        or r['registration']['key'] != c['registration_key']
                        or r['state'] != ('started' if label == 'Start' else c['status'])):
                    raise ValueError('managed receipt identity differs')
                receipts[label] = receipt
                link(child, label, receipt)
                link(receipt, c['logical_key'], child)
            if 'terminal' in receipts:
                terminal = receipts['terminal']['value']
                if (terminal['started_receipt_ref'] != c['started_receipt_ref']
                        or terminal.get('output') != c['output']
                        or terminal.get('error_code') != (c['error']['code'] if c['error'] else None)):
                    raise ValueError('terminal receipt result differs')
                link(receipts['terminal'], 'Start', receipts['Start'])
        if output_value['children'] != children:
            raise ValueError('program output children differ from exact typed records')

    provider._bound()
    try:
        provider._stable(core, anchor)
    except RuntimeError as exc:
        raise ManagedStale('Registry advanced during managed detail read') from exc
    # The evidence view intentionally discloses a subset of each original
    # document. Source code, runtime paths, policy and ambient authority are not
    # needed to follow the exact call/receipt/result chain.
    fields = {
        PROGRAM: ('program_invocation_ref', 'agent_action_ref', 'execution_ref',
                  'status', 'call_refs', 'output_resource_ref'),
        CALL: ('program_invocation_ref', 'agent_action_ref', 'program_call_ref',
               'logical_key', 'managed_call_id', 'tool_name', 'registration_key',
               'arguments', 'effect', 'accepted_ordinal', 'status',
               'started_receipt_ref', 'terminal_receipt_ref', 'output', 'error'),
        'tool_program_output/v1': ('kind', 'program_invocation_ref', 'agent_action_ref',
                                   'status', 'aggregate', 'runtime_status', 'setup_error', 'exit_code'),
        'managed_plugin_call': ('schema_version', 'caller_execution_ref', 'caller_firing_ref',
                                'call_id', 'selector', 'arguments', 'state',
                                'started_receipt_ref', 'output', 'error_code'),
    }
    for record in records.values():
        record['value'] = {k: record['value'][k] for k in fields[record['kind']] if k in record['value']}
    return {'schema_version': SCHEMA, 'selector': selected, 'firing_ref': firing_ref,
            'task_id': provider.task_id,
            'capture': {'head_ordinal': anchor['source']['verified_head_ordinal'],
                        'writer_fencing_epoch': anchor['source']['writer_fencing_epoch']},
            'program_refs': final_parents or [p['ref'] for p in parents],
            'records': list(records.values()),
            'coverage': 'canonical_programs_at_checkpoint', 'read_only': True}
