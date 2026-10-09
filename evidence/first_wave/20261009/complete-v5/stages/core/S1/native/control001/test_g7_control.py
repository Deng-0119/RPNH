"""One authorized causal control: same G7 fixture, no pending replacement."""
import json
from pathlib import Path
from test_native_s1 import Native, _world, _fresh_leases, current, save


def test_g7_no_pending_replacement(tmp_path):
    original = json.loads((Path(__file__).resolve().parents[1] / 'a004/g7.json').read_text())[0]
    owner = _world(tmp_path / 'r', same_pool=False)
    executable, structure, before = current(owner)
    assert structure.compiled.source.to_dict() == original['af_unix_response']['result']['declaration']
    initial = _fresh_leases(before)
    adoptions_before = len(owner._core.event_store.list_events_by_type(('net_adopted/v1',)))
    with Native(owner, 'g7-control') as n:
        n.snapshot('control-before')
        assert len(n.harness.schedule_ready()) == 1
        live = n.snapshot('control-live')
        assert live['active'] == 1 and live['af_unix_response']['result']['pending_edits'] == []
        n.release(0)
        final = n.snapshot('control-terminal')
        result = n.harness.result()
        after_net, _, after = current(owner)
        retained = _fresh_leases(after)
        assert {p: (t.token_ref, t.state) for p, t in retained.items()} == {
            p: (t.token_ref, t.state) for p, t in initial.items()}
        assert after_net.net_ref == executable.net_ref
        assert len(owner._core.event_store.list_events_by_type(('net_adopted/v1',))) == adoptions_before
        assert result.stop_reason == 'terminal' and result.goal_reached
        assert result.terminal_evidence_ref is not None
        assert len(owner._core.event_store.object_rows_by_type('run_terminal_evidence/v1')) == 1
        assert final['active'] == 0 and n.body_count == 1
        assert owner._core.event_store.actual_model_call_counts() == (0, 0)
        save('control-summary', dict(status='PASS', classification='NATIVE_CAUSAL_CONTROL_SEPARATE_FROM_FINAL_TEN',
            same_fixture_declaration=True, same_terminal_spec=True, pending_replacement=False,
            terminal_evidence_ref=result.terminal_evidence_ref, terminal_product_refs=result.goal_resource_refs,
            producing_firing_ref=result.operation_execution_trace[0].execution.operation.firing.transition_firing_ref,
            producing_net_ref=result.operation_execution_trace[0].execution.operation.firing.net_ref,
            current_net_ref=after_net.net_ref, body_count=n.body_count, final_state=final,
            model_counts=[0, 0], comparison='No replacement: terminal closes. Frozen a004 pending replacement: terminal provenance fault.'))
