"""Finite copied-Registry corruption cases; no production repair/write fallback."""
from copy import deepcopy
import json
import shutil
import sqlite3

import pytest

from test_local_takeover_normal_boundaries import (
    prepared, complete, reject, receipt, authority, _RegistryCore, TypedId, canonical_json,
    read_root_terminal,
)


@pytest.fixture(scope='module')
def closed(prepared):
    f, proposal = prepared
    proposal.commit()
    value = complete(f)
    receipt('C02-baseline', closed=value, target_run_dir=str(f.target._core.run_dir))
    return f, value


def damage_descriptor(core, db, version, change):
    row = db.execute('SELECT * FROM objects WHERE version_id=?', (version,)).fetchone()
    document = json.loads(row['metadata_json'])
    change(document)
    payload = canonical_json(document)
    core.object_store.path_for_version(TypedId.parse(version)).write_bytes(payload)
    db.execute('UPDATE objects SET metadata_json=?,size=? WHERE version_id=?',
               (json.dumps(document), len(payload), version))
    event = db.execute('SELECT payload_json FROM events WHERE event_id=?', (row['published_event_id'],)).fetchone()
    publication = json.loads(event[0])
    publication.update(metadata=document, size=len(payload))
    db.execute('UPDATE events SET payload_json=? WHERE event_id=?',
               (json.dumps(publication), row['published_event_id']))


@pytest.mark.parametrize('damage', ['bytes', 'provenance', 'transaction', 'child-set',
    'seal-ref', 'checkpoint', 'mapping'])
def test_C02_full_reader_rejects_isolated_damage_without_repair(closed, tmp_path, damage):
    f, value = closed
    origin = f.target._core
    run = tmp_path / 'damaged'
    shutil.copytree(origin.run_dir, run)
    # Opening readonly never upgrades writer epoch/catalog or repairs missing facts.
    core = _RegistryCore(run, create=False, read_only=True, catalog=origin.catalog)
    root_ref = value['root']['record_ref']
    seal_ref = value['seal']['execution_child_seal_ref']
    child = value['seal']['children'][0]
    version = seal_ref['version_id']
    with sqlite3.connect(core.event_store.path) as db:
        db.row_factory = sqlite3.Row
        if damage == 'bytes':
            path = core.object_store.path_for_version(TypedId.parse(version))
            old = path.read_bytes()
            path.write_bytes(b'!' + old[1:])
        elif damage == 'provenance':
            db.execute('UPDATE objects SET producer_invocation_id=NULL WHERE version_id=?', (version,))
        elif damage == 'transaction':
            db.execute("UPDATE transactions SET status='aborted' WHERE transaction_id=?", (value['seal']['success_transaction_id'],))
        elif damage == 'child-set':
            db.execute("DELETE FROM events WHERE event_type='execution_instance_attached/v1'")
        elif damage == 'seal-ref':
            # A well-shaped but unrelated exact reference, not invalid JSON.
            from cpn.rpnh.registry.publication import _ref_payload
            def change(doc):
                doc['body']['required_child_seal_ref']['ref'] = _ref_payload(f.products.outputs[0].resource_ref.as_version_ref())
            damage_descriptor(core, db, root_ref['ref']['version_id'], change)
        elif damage == 'checkpoint':
            damage_descriptor(core, db, child['execution_checkpoint_ref']['version_id'],
                              lambda doc: doc.update(status='running'))
        else:
            damage_descriptor(core, db, child['execution_terminal_mapping_ref']['version_id'],
                              lambda doc: doc.update(evidence_refs=[]))
    before = {str(p.relative_to(core.object_store.root)): p.read_bytes()
              for p in core.object_store.root.rglob('*') if p.is_file()}
    reject(core, 'C02-' + damage, lambda: read_root_terminal(core, root_ref))
    after = {str(p.relative_to(core.object_store.root)): p.read_bytes()
             for p in core.object_store.root.rglob('*') if p.is_file()}
    assert after == before
    assert read_root_terminal(_RegistryCore(origin.run_dir, create=False, read_only=True,
        catalog=origin.catalog), root_ref)['root'] == value['root']
    receipt('C02-no-repair-' + damage, authority_unchanged=True, object_bytes_unchanged=True,
            original_still_readable=True, damaged_copy=str(run))


def test_N08_same_target_real_donor_cannot_be_borrowed(closed):
    f, value = closed
    core = f.target._core
    tx = core.begin(idempotency_key='b4:borrow-committed-donor')
    refs = [value['seal']['execution_child_seal_ref'], value['root']['record_ref']['ref'],
            *[c['execution_terminal_mapping_ref'] for c in value['seal']['children']]]
    tx._objects = [core.get_version(TypedId.parse(ref['version_id'])) for ref in refs]
    assert {core.event_store.object_row(obj.version_id)['transaction_id'] for obj in tx._objects} == {value['seal']['success_transaction_id']}
    assert str(tx.transaction_id) != value['seal']['success_transaction_id']
    reject(core, 'N08-real-same-target-donor', tx.commit)
