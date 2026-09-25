"""Optional real Python DSH/Registry boundary; effects are explicit substitutes."""
from __future__ import annotations
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dsh_request_binding_reads_real_existing_registry_without_execution(tmp_path, monkeypatch):
    if importlib.util.find_spec('cpn.dsh') is None:
        pytest.skip('optional DSH runtime is not part of the main viewer')
    from cpn.frontend.net_view_adapters.dsh import bind_dsh
    from cpn.frontend.server import handle_request
    from cpn.rpnh.registry._registry import _RegistryCore
    spec = importlib.util.spec_from_file_location('viewer_dsh_boundary_fixture', ROOT / 'tests/test_dsh_backend.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    effect = fixture.PhysicalCounter()
    host = fixture.DshBackend(tmp_path, 's1', effect, create=True)
    assert host.turn(fixture.request())['status'] == 'terminal'
    assert len(effect.calls) == 5
    before = host.core.event_store.max_ordinal()
    def forbidden(*args, **kwargs):
        raise AssertionError('read constructed an execution host or began a transaction')
    monkeypatch.setattr(fixture.DshBackend, '__init__', forbidden)
    monkeypatch.setattr(_RegistryCore, 'begin', forbidden)
    binding = bind_dsh(tmp_path, 's1', request_id='q1')
    child = _RegistryCore(binding.run_dir, create=False, read_only=True)
    child_before = child.event_store.max_ordinal()
    snapshot = binding()
    assert len(snapshot['nodes']) == 16 and len(snapshot['edges']) == 27
    assert next(n for n in snapshot['nodes'] if n['id'] == 'dsh.model')['runtime']['firing_count'] == 3
    assert handle_request(binding, 'GET', '/api/v1/net').status == 200
    assert host.core.event_store.max_ordinal() == before
    assert child.event_store.max_ordinal() == child_before
    assert len(effect.calls) == 5
