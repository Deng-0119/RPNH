"""Read-only audit of complete raw Registry exports; no runtime decisions."""
import json
from pathlib import Path

ROOT = Path('<WORKSPACE>')
TASK = ROOT / 'task-owner-entry-validation-20261008'

def exact_ref(ref):
    return (ref.get('resource_id', ref.get('logical_id')),
            ref.get('resource_version_id', ref.get('version_id')))

def audit(e):
    assert e['actual_model_call_counts'] == [0, 0]
    assert len(e['terminal_evidence']) == 1
    assert e['terminal_evidence'][0]['run_outcome'] == 'complete'
    assert len(e['firings']) == 10 and len(e['resources']) == 12
    assert len(e['source_resources']) == 2
    events = e['events']
    assert [x['ordinal'] for x in events] == sorted(set(x['ordinal'] for x in events))
    objects = {x['payload']['version_id']: x for x in events
               if x['type'] == 'object_version_published/v1'}
    resources = {exact_ref(x['ref']): x for x in e['resources']}
    terminal_ref = exact_ref(e['terminal_evidence'][0]['terminal_result_ref'])
    assert resources[terminal_ref]['value'] == e['final']
    table = {}
    for f in e['firings']:
        assert f['state'] == 'PUBLISHED'
        ref = f['firing_version_id']
        relevant = [x for x in events
                    if x['payload'].get('transition_firing_ref', {}).get('version_id') == ref]
        def one(kind):
            found = [x for x in relevant if x['type'] == kind]
            assert len(found) == 1, (f['firing']['transition_id'], kind, len(found))
            return found[0]
        admitted = one('firing_admitted/v1')
        started = one('transition_firing_started/v1')
        execution = one('operation_execution_started/v1')
        completed = one('registered_operation_completion_recorded/v1')
        settled = one('transition_firing_settled/v1')
        assert admitted['ordinal'] < started['ordinal'] < execution['ordinal']
        assert completed['payload']['selected_outcome_id'] == 'complete'
        assert settled['payload']['business_outcome'] == 'completed'
        claims = [objects[x['version_id']]['payload']['metadata']['resource_ref']
                  for x in f['firing']['claimed_input_refs']]
        assert sorted(map(exact_ref, claims)) == sorted(map(exact_ref, execution['payload']['input_resource_refs']))
        outputs = completed['payload']['ordered_outputs']
        assert outputs
        publication_ordinals = []
        for out in outputs:
            r = resources[exact_ref(out['resource_ref'])]
            published = objects[r['ref']['resource_version_id']]
            publication_ordinals.append(published['ordinal'])
            assert execution['ordinal'] < published['ordinal'] < completed['ordinal'] < settled['ordinal']
            assert sorted(map(exact_ref, r['value']['parents'])) == sorted(map(exact_ref, claims))
            assert sorted(map(exact_ref, r['metadata']['reference_provenance']['derived_from_refs'])) == sorted(map(exact_ref, claims))
            assert r['metadata']['origin_kind'] == 'petri_output'
            assert r['metadata']['origin']['primary_ref'] == out['output_binding_ref']
            assert r['metadata']['producer_ref'] == execution['payload']['invocation_ref']
            assert r['metadata']['content_schema_authority_ref']['resource_version_id']
        table[f['firing']['transition_id']] = {
            'firing_version_id': ref, 'admitted': admitted['ordinal'],
            'started': started['ordinal'], 'execution_started': execution['ordinal'],
            'output_registered': publication_ordinals, 'success_recorded': completed['ordinal'],
            'settled': settled['ordinal'], 'claimed_resources': claims,
            'output_refs': [x['resource_ref'] for x in outputs]}
    for branch in ('usage', 'tariff'):
        chain = ['read_' + branch, 'check_' + branch + '_input', 'normalize_' + branch,
                 'join_intervals', 'compute_cost', 'validate_report', 'publish_report']
        for left, right in zip(chain, chain[1:]):
            assert table[left + '.run']['settled'] < table[right + '.run']['admitted']
    active, maximum = set(), 0
    for event in events:
        if event['type'] == 'firing_admitted/v1':
            ref = event['payload']['transition_firing_ref']['version_id']
            assert ref not in active
            active.add(ref)
            maximum = max(maximum, len(active))
        elif event['type'] == 'transition_firing_settled/v1':
            active.remove(event['payload']['transition_firing_ref']['version_id'])
        assert len(active) <= 2
    assert not active
    return {'status': 'PASS', 'max_active': maximum, 'terminal_result_ref': terminal_ref,
            'firings': table, 'lineage_outputs_checked': 12, 'max_ordinal': events[-1]['ordinal']}


def main():
    standard = json.loads((ROOT / '.o26/c/export/evidence.json').read_text())
    reopened = json.loads((ROOT / '.o26/c/readback/evidence.json').read_text())
    result = audit(standard)
    assert standard['transport'] == 'native_owner_socket'
    assert standard['final']['data']['report']['total_cny'] == '1.70'
    assert standard['final']['data']['report']['total_kwh'] == '2.000'
    assert standard.keys() - reopened.keys() == {'stop_reason', 'transport'}
    assert not reopened.keys() - standard.keys()
    assert {k:v for k,v in standard.items() if k not in {'stop_reason','transport'}} == reopened
    before = json.loads((TASK / 'logs/readback-before.stdout.log').read_text())
    after = json.loads((TASK / 'logs/readback-after.stdout.log').read_text())
    assert before == after
    assert before['max_ordinal'] == standard['events'][-1]['ordinal']
    result.update(native_transport=standard['transport'],total_kwh='2.000',total_cny='1.70',
        stable_fields_deep_equal=True,live_only_fields=['stop_reason','transport'],
        persisted_registry_before=before,persisted_registry_after=after)
    (TASK / 'native-cli-readback-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':'PASS','native_transport':standard['transport'],
        'firings':10,'business_resources':12,'source_resources':2,
        'actual_model_call_counts':standard['actual_model_call_counts'],
        'readback_registry_unchanged':True,'total_kwh':'2.000','total_cny':'1.70'}))

if __name__ == '__main__':
    main()
