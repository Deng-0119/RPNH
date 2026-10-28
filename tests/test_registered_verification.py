"""Real-file bounded verification; prepare ordering probes are not owner E2E."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cpn.rpnh.registry.object_store import ObjectStore, ObjectIntegrityError
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.errors import ResourceIntegrityFault, UnauthorizedResourceDelivery
from cpn.rpnh.registry._resource_service import delivery, references


def make_object(tmp_path, payload):
    store = ObjectStore(tmp_path / 'objects', SchemaCatalog())
    logical, version = new_id('task'), new_id('task_version')
    prepared = store.prewrite(object_type='task/v1', logical_id=logical,
        version_id=version, payload=payload,
        metadata={'task_id': str(logical), 'task_version_id': str(version)},
        media_type='application/json', schema_ref='registry_v1/task/v1')
    return store, prepared


@pytest.mark.parametrize('payload', [b'', b'x', b'x' * (2 * 1024 * 1024 + 3)])
def test_bounded_scan_reads_all_and_closes(tmp_path, monkeypatch, payload):
    store, prepared = make_object(tmp_path, payload)
    original = Path.open
    reads, handles = [], []
    class Reader:
        def __init__(self, file): self.file = file
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def read(self, size):
            reads.append(size)
            assert 0 < size <= store._CHUNK_SIZE
            return self.file.read(size)
    def opened(path, *args, **kwargs):
        file = original(path, *args, **kwargs)
        handles.append(file)
        return Reader(file)
    monkeypatch.setattr(Path, 'open', opened)
    assert store.verify_registered(prepared) is None
    assert len(reads) == len(payload) // store._CHUNK_SIZE + 1 + bool(len(payload) % store._CHUNK_SIZE)
    assert all(h.closed for h in handles)


@pytest.mark.parametrize('failure', ['short', 'long', 'missing', 'locator'])
def test_same_error_as_registered_read(tmp_path, failure):
    store, prepared = make_object(tmp_path, b'abc')
    path = store.path_for_version(prepared.version_id)
    if failure == 'short': path.write_bytes(b'ab')
    elif failure == 'long': path.write_bytes(b'abcd')
    elif failure == 'missing': path.unlink()
    else: prepared = replace(prepared, storage_locator='registry-object:task_version:wrong')
    errors = []
    for method in (store.read_registered, store.verify_registered):
        with pytest.raises(Exception) as caught: method(prepared)
        errors.append((type(caught.value), str(caught.value)))
    assert errors[0] == errors[1]


def test_firing_verifier_preserves_gate_and_fault_chain(tmp_path):
    store, prepared = make_object(tmp_path, b'abc')
    store.path_for_version(prepared.version_id).unlink()
    calls = []
    fake = SimpleNamespace(_ResourceServiceKernel__core=SimpleNamespace(object_store=store),
        _firing_prepared=lambda *a, **kw: calls.append(kw) or prepared)
    with pytest.raises(ResourceIntegrityFault, match='firing-view registered') as caught:
        references._verify_firing_registered(fake, object(), object(), view='view')
    assert calls == [{'view': 'view'}]
    assert isinstance(caught.value.__cause__, ObjectIntegrityError)
    failure = UnauthorizedResourceDelivery('denied')
    def deny(*a, **kw): raise failure
    fake._firing_prepared = deny
    with pytest.raises(UnauthorizedResourceDelivery) as caught:
        references._verify_firing_registered(fake, object(), object())
    assert caught.value is failure


@pytest.mark.parametrize('denied', [True, False])
def test_prepare_failure_never_publishes_and_denial_never_reads(tmp_path, denied):
    store, prepared = make_object(tmp_path, b'abc')
    store.path_for_version(prepared.version_id).write_bytes(b'ab')
    events = []
    def unexpected(*a, **kw): pytest.fail('must not start publication')
    core = SimpleNamespace(object_store=store,
        event_store=SimpleNamespace(firing_view=lambda **kw: 'view'), begin=unexpected)
    def authorize(*a, **kw):
        events.append('authorize')
        if denied: raise UnauthorizedResourceDelivery('denied')
    fake = SimpleNamespace(_ResourceServiceKernel__core=core,
        _revalidate_invocation=lambda *a, **kw: events.append('context'),
        _firing_prepared=lambda *a, **kw: events.append('firing') or prepared,
        _authorize_resource=authorize)
    def verify(*a, **kw):
        events.append('physical')
        return references._verify_firing_registered(fake, *a, **kw)
    fake._verify_firing_registered = verify
    context = SimpleNamespace(own_transition_firing_ref=VersionRef(
        'transition_firing/v1', new_id('transition_firing'), new_id('transition_firing_version')),
        invocation_ref=VersionRef('invocation/v1', new_id('invocation'), new_id('invocation_version')))
    command = SimpleNamespace(delivery_id=new_id('resource_delivery'),
        consumer_boundary='tool_result', resource_ref=object(), authorization_ref=object())
    with pytest.raises(UnauthorizedResourceDelivery if denied else ResourceIntegrityFault) as caught:
        delivery.prepare_delivery(fake, context, command)
    assert events == ['context', 'firing', 'authorize'] + ([] if denied else ['physical'])
    if not denied:
        assert isinstance(caught.value.__cause__, ResourceIntegrityFault)
        assert isinstance(caught.value.__cause__.__cause__, ObjectIntegrityError)
