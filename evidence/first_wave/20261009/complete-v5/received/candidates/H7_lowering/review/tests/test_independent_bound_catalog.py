"""Protected catalog/origin exact-ref integrity under deterministic DB damage."""
import json
import pytest
from cpn.rpnh.registry import parent_bound, parent_child as h7
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from bound_lowering_fixtures import compiled_bound_owner
from test_static_lease_reads import _registration, _simple_module


@pytest.mark.parametrize('damage', ['same_schema_foreign_catalog_ref', 'catalog_schema_body'])
def test_protected_origin_requires_exact_installed_catalog(tmp_path, monkeypatch, damage):
    owner, _ = compiled_bound_owner(tmp_path / 'child', monkeypatch, _registration(), _simple_module())
    core = owner._core
    parent_bound.assert_bound_integrity(core)
    with core.event_store.connect() as db:
        origin = json.loads(db.execute("SELECT metadata_json FROM objects WHERE object_type='parent_bound_origin/v1'").fetchone()[0])
        marker = json.loads(db.execute("SELECT metadata_json FROM objects WHERE object_type='parent_bound_bootstrap/v1'").fetchone()[0])
        genesis = json.loads(db.execute('SELECT metadata_json FROM objects WHERE version_id=?', (marker['genesis_ref']['version_id'],)).fetchone()[0])
        if damage == 'same_schema_foreign_catalog_ref':
            version = origin['capability_ref']['version_id']
            body = json.loads(db.execute('SELECT metadata_json FROM objects WHERE version_id=?', (version,)).fetchone()[0])
            authority = body['content_schema_authority_ref']
            kind = authority['version_id'].split(':')[0]
            authority['version_id'] = str(new_id(kind))
        else:
            version = genesis['type_catalog_ref']['version_id']
            body = json.loads(db.execute('SELECT metadata_json FROM objects WHERE version_id=?', (version,)).fetchone()[0])
            schema = json.loads(body['schemas'][h7.CAPABILITY_SCHEMA]['source'])
            schema['review_corruption'] = True
            body['schemas'][h7.CAPABILITY_SCHEMA]['source'] = json.dumps(schema)
        db.execute('UPDATE objects SET metadata_json=? WHERE version_id=?', (json.dumps(body), version))
        db.commit()
    head = core.event_store.max_ordinal()
    with pytest.raises(RegistryConflict):
        parent_bound.assert_bound_integrity(core)
    assert core.event_store.max_ordinal() == head
