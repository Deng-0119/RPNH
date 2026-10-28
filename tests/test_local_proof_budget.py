from concurrent.futures import ThreadPoolExecutor
import sys

import pytest

from cpn.rpnh.registry._event_store.local_memo import BoundedProofSet
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import EventStore, RegistryCorruptError, verified_adoption_lineage
from cpn.rpnh.registry.identities import new_id
from test_adoption_prefix import adopted_fixture


def test_lru_budget_oversize_and_clear():
    cache = BoundedProofSet(max_entries=2, max_estimated_bytes=1000)
    cache.add('a'); cache.add('b')
    assert 'a' in cache
    cache.add('c')
    assert 'a' in cache and 'c' in cache and 'b' not in cache
    cache.add('x' * 2000)
    assert len(cache) == 2
    cache.clear()
    cache.add('')
    assert '' not in cache
    assert cache.statistics()['estimated_bytes'] == 0
    with pytest.raises(TypeError): cache.add(1)


def test_byte_budget_and_threads():
    size = sys.getsizeof('a') + 128
    cache = BoundedProofSet(max_entries=50, max_estimated_bytes=size * 2)
    def work(i):
        for j in range(100):
            key = str(i * 100 + j)
            cache.add(key)
            _ = key in cache
    with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(work, range(8)))
    assert cache.statistics()['estimated_bytes'] <= size * 2
    assert len(cache) <= 2


@pytest.mark.parametrize('entries,bytes_', [(0,1000),(100,0)])
def test_zero_budget(entries, bytes_):
    cache = BoundedProofSet(max_entries=entries,max_estimated_bytes=bytes_)
    cache.add('a')
    assert 'a' not in cache


def test_real_empty_lineages_no_dead_retention(tmp_path):
    core = _RegistryCore(tmp_path / 'run', create=True)
    for _ in range(20):
        with pytest.raises(RegistryCorruptError, match='no adopted net'):
            verified_adoption_lineage(core.event_store, core.catalog, new_id('task'))
    assert core.event_store._adoption_lineage_memo == {}
    assert core.event_store._checkpoint_head_memo == {}


def test_actual_publication_proof_eviction_revalidates_and_writer_never_reuses(tmp_path, monkeypatch):
    core = _RegistryCore(tmp_path / 'run', create=True)
    versions = []
    for i in range(2):
        logical, version = new_id('task'), new_id('task_version')
        core.publish_bytes(object_type='task/v1', logical_id=logical, version_id=version,
            payload=b'{}', metadata={'task_id': str(logical), 'task_version_id': str(version)},
            media_type='application/json', schema_ref='registry_v1/task/v1', idempotency_key=f'task:{i}')
        versions.append(version)
    reader = EventStore(core.event_store.path, core.catalog, read_only=True)
    reader._verified_read_only_transactions = BoundedProofSet(max_entries=1)
    calls = []
    original = reader._verified_persisted_event_record
    def check(db, row):
        calls.append(row['transaction_id'])
        return original(db,row)
    monkeypatch.setattr(reader, '_verified_persisted_event_record', check)
    def read(v): return reader.object_publication_event(v, object_type='task/v1')
    first = read(versions[0]); assert read(versions[0]) == first
    assert len(calls) == 1
    read(versions[1]); assert read(versions[0]) == first
    assert len(calls) == 3 and len(reader._verified_read_only_transactions) == 1
    reader._verified_read_only_transactions.clear()
    def fail(db,row): raise ValueError('injected full proof failure')
    monkeypatch.setattr(reader, '_verified_persisted_event_record', fail)
    with pytest.raises(ValueError,match='proof failure'): read(versions[0])
    assert len(reader._verified_read_only_transactions) == 0
    writer_calls=[]
    original_writer=core.event_store._verified_persisted_event_record
    def writer_check(db,row):
        writer_calls.append(1); return original_writer(db,row)
    monkeypatch.setattr(core.event_store,'_verified_persisted_event_record',writer_check)
    for _ in range(2): core.event_store.object_publication_event(versions[0],object_type='task/v1')
    assert len(writer_calls)==2
    assert len(core.event_store._verified_read_only_transactions)==0


def test_real_successful_lineage_is_not_retained(adopted_fixture):
    core, publication, _ = adopted_fixture
    for _ in range(3):
        assert verified_adoption_lineage(core.event_store, core.catalog, core.task_id) == (publication.net_ref,)
    assert core.event_store._adoption_lineage_memo == {}


def test_entry_only_memo_alias_copy_zero_and_atomic_concurrency():
    from copy import deepcopy
    from cpn.rpnh.registry._event_store.local_memo import EntryBoundedMemo
    memo = EntryBoundedMemo(2)
    value = {'nested': []}
    memo['a'] = value; memo['b'] = {}
    assert memo.get('a') is value
    memo['c'] = {}
    assert memo.get('b') is None
    value['nested'].append('caller mutation remains visible')
    assert memo.get('a')['nested']
    copied = deepcopy(memo)
    assert copied == memo and copied.get('a') is not value
    zero = EntryBoundedMemo(0); zero['x'] = value
    assert len(zero) == 0
    def worker(i):
        for j in range(200):
            key = str(i * 200 + j)
            memo[key] = {}
            found = memo.get(key)
            assert found is None or found == {}
    with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(worker,range(8)))
    assert len(memo) <= 2


def test_real_net_eviction_revalidates_and_failure_not_cached(adopted_fixture, monkeypatch):
    from cpn.rpnh.registry._event_store.local_memo import EntryBoundedMemo
    from cpn.rpnh.registry._event_store.net_lineage import validate_registered_net_closure
    core, pub, _ = adopted_fixture
    core.event_store._net_closure_memo = EntryBoundedMemo(1)
    first = validate_registered_net_closure(core.event_store,core.catalog,pub.net_ref)
    calls=[]
    original=core.event_store.connect
    def connect(*a,**kw): calls.append(1); return original(*a,**kw)
    monkeypatch.setattr(core.event_store,'connect',connect)
    assert validate_registered_net_closure(core.event_store,core.catalog,pub.net_ref) is first
    assert calls == []
    core.event_store._net_closure_memo['eviction-fixture']={}
    assert validate_registered_net_closure(core.event_store,core.catalog,pub.net_ref) == first
    assert len(calls)==1
    core.event_store._net_closure_memo.clear()
    with original() as db:
        db.execute('DELETE FROM objects WHERE version_id=?',(str(pub.net_ref.version_id),))
    with pytest.raises(RegistryCorruptError):
        validate_registered_net_closure(core.event_store,core.catalog,pub.net_ref)
    assert len(core.event_store._net_closure_memo)==0


def test_net_memo_empty_hit_and_offline_plain_dict_contract(adopted_fixture, monkeypatch):
    from cpn.rpnh.registry._event_store.net_lineage import validate_registered_net_closure
    core, pub, _ = adopted_fixture
    def forbidden(*a, **kw): pytest.fail('cache hit must not open database')
    monkeypatch.setattr(core.event_store, 'connect', forbidden)
    core.event_store._net_closure_memo[str(pub.net_ref.version_id)] = {}
    assert validate_registered_net_closure(core.event_store, core.catalog, pub.net_ref) == {}
    key=(pub.net_ref.entity_type,str(pub.net_ref.entity_id),str(pub.net_ref.version_id))
    assert validate_registered_net_closure(core.event_store, core.catalog, pub.net_ref, _memo={key: None}) is None
    class NotPlain(dict): pass
    with pytest.raises(TypeError, match='plain per-read dictionary'):
        validate_registered_net_closure(core.event_store, core.catalog, pub.net_ref, _memo=NotPlain())
