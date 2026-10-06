"""Governed catalog coexistence; no Registry database or runtime execution."""

from cpn.rpnh.registry.schema_catalog import SchemaCatalog


def test_typed_call_schema_is_in_the_governed_default_catalog():
    # Construction itself verifies the complete CURRENT_SCHEMA_REFS / INDEX
    # agreement before any author can publish Registry materials.
    catalog = SchemaCatalog()
    assert [
        (item.schema_id, item.path)
        for item in catalog.schema_index()
        if item.schema_id == "rpnh/typed_call/v1"
    ] == [("rpnh/typed_call/v1", "rpnh/typed_call.v1.schema.json")]
