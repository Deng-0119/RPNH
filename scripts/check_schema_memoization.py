"""Paired pure-schema benchmark; does not read runs or call providers.

This is not a model latency, whole-harness throughput or Registry benchmark.
"""
from __future__ import annotations
import argparse
import json
import time
from jsonschema import Draft7Validator
from cpn.rpnh._schema_validation import check_draft7_schema, _check_canonical_schema
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, CURRENT_SCHEMA_REFS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 100:
        parser.error('iterations must be within 1..100')
    documents = [json.loads(SchemaCatalog._mechanical_schema_path(ref).read_text())
                 for ref in CURRENT_SCHEMA_REFS]
    def measure(check):
        start = time.perf_counter()
        for _ in range(args.iterations):
            for document in documents:
                check(document)
        return time.perf_counter() - start
    direct = measure(Draft7Validator.check_schema)
    _check_canonical_schema.cache_clear()
    cold_and_warm = measure(check_draft7_schema)
    warm = measure(check_draft7_schema)
    print(json.dumps({'schema_count': len(documents),
        'iterations': args.iterations, 'direct_seconds': direct,
        'cold_and_warm_seconds': cold_and_warm, 'warm_seconds': warm,
        'cache': _check_canonical_schema.cache_info()._asdict(),
        'scope': 'Pure schema meta-validation only; instance and Registry checks unchanged',
        'supplier_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
