"""Bounded inert copy-origin verification; no destination authority is imported."""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import replace

from .share_packages import canonical_bytes, sha256, PackageError


def verify_prior_evidence(record, rows, artifacts, limits, budget, depth):
    from .subnet_exchange import (SubnetExchangeError, PROVENANCE_SCHEMA, _charge_exchange_archive, _exchange)
    from .package_preview import preview_package
    from .package_resolution import resolve_package
    try:
        def document(ref):
            return json.loads(artifacts[rows[canonical_bytes(ref)]['artifact_path']])
        provenance = document(record['subnet_provenance_ref'])
        if (set(provenance) != {'schema_version', 'command_id', 'plan_digest', 'revision_ref', 'entry_id',
                'origin_to_local_map', 'source_provenance', 'root_archive_base64', 'dependency_archives_base64', 'package_lock'}
                or provenance['schema_version'] != PROVENANCE_SCHEMA
                or provenance['revision_ref'] != record['revision_ref']):
            raise ValueError('prior provenance differs from exact imported revision')
        if (record['command_id'] != 'subnet-import:' + provenance['command_id'] + ':author'
                or record['parent_revision_refs']):
            raise ValueError('copy command differs from imported root')
        encoded = provenance['dependency_archives_base64']
        if not isinstance(encoded, list): raise ValueError('invalid retained dependency list')
        if depth + 1 >= limits.dependency_depth or len(encoded) + 1 + budget['archives'] > limits.packages:
            raise SubnetExchangeError('LIMIT_EXCEEDED')
        initial_bytes = limits.closure_bytes - budget['bytes']
        initial_expanded = limits.closure_expanded_bytes - budget['expanded']
        initial_members = limits.closure_members - budget['members']
        initial_packages = limits.packages - budget['archives']
        previews = []
        for item in (provenance['root_archive_base64'], *encoded):
            remaining_bytes = limits.closure_bytes - budget['bytes']
            remaining_expanded = limits.closure_expanded_bytes - budget['expanded']
            remaining_members = limits.closure_members - budget['members']
            if (min(remaining_bytes, remaining_expanded, remaining_members) <= 0
                    or not isinstance(item, str) or len(item) > ((min(limits.archive_bytes, remaining_bytes) + 2) // 3) * 4):
                raise SubnetExchangeError('LIMIT_EXCEEDED')
            raw = base64.b64decode(item, validate=True)
            bounded = replace(limits, archive_bytes=min(limits.archive_bytes, remaining_bytes),
                expanded_bytes=min(limits.expanded_bytes, remaining_expanded), members=min(limits.members, remaining_members))
            preview = preview_package(raw, limits=bounded)
            _charge_exchange_archive(preview, limits, budget, depth + 1)
            previews.append(preview)
        original, *dependencies = previews
        bounded = replace(limits, closure_bytes=initial_bytes, closure_expanded_bytes=initial_expanded,
            members=min(limits.members, initial_members), packages=initial_packages)
        lock = resolve_package(original, dependencies, provenance['entry_id'], limits=bounded)
        exact = {(node['manifest_digest'], node['archive_digest']) for node in lock.to_dict()['nodes']}
        supplied = [(package.manifest_digest, package.archive_digest) for package in previews]
        if len(supplied) != len(exact) or set(supplied) != exact:
            raise ValueError('retained archives must be exactly the selected closure')
        if canonical_bytes(lock.to_dict()) != canonical_bytes(provenance['package_lock']):
            raise ValueError('prior exact package lock differs')
        # Already admitted before parsing siblings; descendants continue using
        # this same remaining budget. Nothing here becomes a Registry object.
        prior = _exchange(original, limits, _budget=budget, _depth=depth + 1, _admitted=True)
        for package in dependencies:
            _exchange(package, limits, _budget=budget, _depth=depth + 1, _admitted=True)
        if canonical_bytes(prior) != canonical_bytes(provenance['source_provenance']):
            raise ValueError('retained source exchange differs from original archive')
        entry = original.manifest['entries'][0]
        module = json.loads(dict(original.artifacts)[entry['declaration_path']])
        if canonical_bytes(module) != canonical_bytes(document(record['definition_ref'])):
            raise ValueError('imported definition differs from its retained source')
        target = {'source_ref': record['owner_task_ref']}
        plan = {'schema_version': 'rpnh/subnet_import_plan/v1', 'target': target, 'entry_id': provenance['entry_id'],
            'manifest_digest': original.manifest_digest, 'archive_digest': original.archive_digest,
            'package_lock': lock.to_dict(), 'package_lock_digest': lock.package_lock_digest,
            'origin_kind': 'registry_export' if prior else 'package_artifact', 'definition_closure_status': 'complete',
            'boundary_status': 'closed', 'readiness': 'not_established'}
        if sha256(canonical_bytes(plan)) != provenance['plan_digest']:
            raise ValueError('prior import plan differs')
        local_elements = document(record['element_mapping_ref'])['elements']
        previous = {}
        if prior:
            origin = next(r for r in prior['records'] if r['revision_ref'] == prior['root_revision_ref'])
            path = next(r['artifact_path'] for r in prior['materials'] if r['ref'] == origin['element_mapping_ref'])
            previous = {r['locator']: r['element_id'] for r in json.loads(dict(original.artifacts)[path])['elements']}
        expected = []
        for item in sorted(local_elements, key=lambda item: item['locator']):
            locator = item['locator']
            identity = 'element:' + uuid.uuid5(uuid.NAMESPACE_URL, canonical_bytes({
                'task': record['owner_task_ref']['ref']['logical_id'], 'source': record['revision_ref']['source_id'],
                'command': provenance['command_id'], 'locator': locator}).decode()).hex
            if item['element_id'] != identity:
                raise ValueError('copy command local element identity differs')
            origin = ({'kind': 'registry_export', 'revision_ref': prior['root_revision_ref'], 'element_id': previous[locator]}
                if prior else {'kind': 'package_artifact', 'manifest_digest': original.manifest_digest,
                    'artifact_id': entry['declaration_path'], 'entry_id': entry['entry_id'], 'element_locator': locator})
            expected.append({'origin': origin, 'local_revision_ref': record['revision_ref'],
                'local_element_id': item['element_id'], 'local_locator': locator, 'relation_kind': 'copied_from',
                'semantic_claim': 'author_correspondence'})
        if canonical_bytes(expected) != canonical_bytes(provenance['origin_to_local_map']):
            raise ValueError('prior copy-origin map differs')
    except (SubnetExchangeError, PackageError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, StopIteration, binascii.Error) as exc:
        raise SubnetExchangeError('INTEGRITY_FAILED') from exc
