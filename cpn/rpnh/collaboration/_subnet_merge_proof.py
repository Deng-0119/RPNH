"""Offline verification of inert exact plain-merge exchange evidence.

Uses the original producer's verifier. No Registry is opened or written, and
no source principal, branch, session, grant or runtime is installed locally.
The bytes establish internally consistent source history, not authentication
of an external publisher or authorization in the receiving Registry.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from .share_packages import canonical_bytes, sha256


def verify_exchange_merge(record, stored, rows, artifacts, limits):
    from ._plain_merge_projection import verify, PRODUCER, STORED_SCHEMA
    from .public_projections import make_public_projection
    from .registry_typed_readers import wire_ref
    from .schema_catalog import plain_merge_result_schema_data
    from ..registry.schema_catalog import SchemaCatalog, SchemaGovernanceError
    from ..registry.identities import TypedId
    from .subnet_exchange import SubnetExchangeError

    try:
        if (set(stored) != {'schema_version', 'projection', 'compiled', 'elements', 'dependencies', 'merge_proof'}
                or stored['schema_version'] != STORED_SCHEMA):
            raise ValueError('unsupported merge proof envelope')
        pins = {}
        for pin in stored['dependencies']:
            if (set(pin) != {'ref', 'bytes', 'sha256', 'metadata'} or type(pin['bytes']) is not int
                    or pin['bytes'] < 0 or wire_ref(pin['ref'])[0] != record['revision_ref']['source_id']):
                raise ValueError('invalid merge proof pin')
            key = canonical_bytes(pin['ref'])
            if key in pins: raise ValueError('duplicate merge proof pin')
            pins[key] = pin
        if len(pins) > limits.dependency_edges or len(stored['merge_proof']['inventories']) > limits.members:
            raise SubnetExchangeError('LIMIT_EXCEEDED')
        used = set()

        def read(reference):
            key = canonical_bytes(reference)
            pin, row = pins[key], rows[key]
            raw = artifacts[row['artifact_path']]
            if len(raw) != pin['bytes'] or sha256(raw) != pin['sha256']:
                raise ValueError('merge proof bytes differ')
            kind = wire_ref(reference)[1]['entity_type']
            if kind != 'resource_version/v1' and canonical_bytes(pin['metadata']) != raw:
                raise ValueError('noncanonical source descriptor')
            used.add(key)
            return raw, SimpleNamespace(metadata=pin['metadata'], object_type=kind, producer_invocation_id=None)

        schemas, types, paths = plain_merge_result_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
        binding = json.loads(read(stored['merge_proof']['binding_ref'])[0])
        definition_pin = pins[canonical_bytes(record['definition_ref'])]
        core = SimpleNamespace(catalog=catalog, task_id=TypedId.parse(binding['task_ref']['logical_id']),
            branch_id=definition_pin['metadata']['branch_id'])
        exact_record = {k: v for k, v in record.items() if k not in {'subnet_provenance_ref', 'commit_ordinal'}}
        result = verify(core, exact_record, stored['compiled'], stored['elements'], stored['merge_proof'], read)
        if used != set(pins): raise ValueError('unused merge proof pin')
        expected = make_public_projection(result.revision, result.compiled, result.element_map, producer_contract=PRODUCER)
        if canonical_bytes(expected) != canonical_bytes(stored['projection']):
            raise ValueError('merge projection differs from verified proof')
    except SubnetExchangeError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, SchemaGovernanceError) as exc:
        raise SubnetExchangeError('INTEGRITY_FAILED') from exc
