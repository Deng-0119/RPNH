"""Measure the real stock-project exchange with genuine offline Registry facts.

The test fixture grants access explicitly to its own synthetic local objects.
This validates definition/proof/material exchange, not protected HOST adoption,
agent cognition, task success, or the product's scoped read-plan UX.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import test_subnet_merge_exchange_unittest as fixture
from build_project import ROOT
from cpn.rpnh.agent_tasks import agent_task_registration
from cpn.rpnh.collaboration import PlainModuleMergeAnalyzer, PlainModuleMergeAuthor
from cpn.rpnh.collaboration.subnet_exchange import (plan_subnet_import, import_subnet, _exchange,
    plan_subnet_export, export_subnet)
from cpn.rpnh.collaboration.share_packages import DEFAULT_LIMITS
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.compiler import compile_module


def budget(preview, limits=DEFAULT_LIMITS):
    counter = {'archives': 0, 'bytes': 0, 'expanded': 0, 'members': 0, 'edges': 0, 'active': set()}
    _exchange(preview, limits, _budget=counter)
    return {key: value for key, value in counter.items() if key != 'active'}


def export(w, ref, limits=DEFAULT_LIMITS):
    with fixture.reader(w) as session:
        cut = session.capture_cut(w['source'])
        plan = plan_subnet_export(session, root_revision_ref=ref, cuts={w['source']: cut}, limits=limits)
        return export_subnet(session, plan=plan, destination='review-local')


def run(limits=DEFAULT_LIMITS, *, evidence_dir=None):
    evidence_dir = ROOT / 'evidence' if evidence_dir is None else evidence_dir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result = {'scope': 'real no-socket Registry exchange only; no model, business task, protected HOST or Viewer execution',
              'limits': {'members': limits.members, 'closure_members': limits.closure_members}, 'stages': []}
    with TemporaryDirectory() as directory, patch.object(fixture, 'registration', side_effect=lambda **kwargs: agent_task_registration()):
        root = Path(directory)
        w = fixture.world(root / 'source', 'source')
        seed = (ROOT / 'scenario/generated/seed-project.zip').read_bytes()
        imported = import_subnet(w['author'], plan=plan_subnet_import(seed, target=fixture.target(w), limits=limits),
                                 command_id='import-seed', expected_target_head=fixture.head(w))
        base = w['author']._validate(imported.revision_ref)
        ids = {row['locator']: row['element_id'] for row in base.element_map['elements']}
        parents = []
        for name in ('contributor-a', 'contributor-b'):
            document = json.loads((ROOT / f'scenario/generated/{name}.json').read_text())
            parents.append(w['author'].publish(module=ModuleDeclaration.from_dict(document), element_ids=ids,
                parent_ref=base.revision.revision_ref, command_id=name))
        analysis = PlainModuleMergeAnalyzer(w['gateway'], w['registration'], w['producer']).analyze(
            base_ref=base.revision.revision_ref, local_ref=parents[0].revision.revision_ref,
            incoming_ref=parents[1].revision.revision_ref, command_id='analyze')
        result['initial_analysis'] = analysis.document
        (evidence_dir / 'preview-actual-analysis.json').write_text(json.dumps(analysis.document, indent=2) + '\n')
        assert {row['reason'] for row in analysis.document['conflicts']} == {'divergent_atom', 'coupled_contract'}
        # The actual analyzer conservatively couples this connected contract.
        # An explicit reviewed author revision incorporates both useful edits;
        # never pretend unrelated JSON fields are automatically mergeable.
        reconciled = json.loads((ROOT / 'scenario/generated/contributor-a-resolved.json').read_text())
        revised = w['author'].publish(module=ModuleDeclaration.from_dict(reconciled), element_ids=ids,
            parent_ref=base.revision.revision_ref, command_id='contributor-a-resolved')
        analysis = PlainModuleMergeAnalyzer(w['gateway'], w['registration'], w['producer']).analyze(
            base_ref=base.revision.revision_ref, local_ref=revised.revision.revision_ref,
            incoming_ref=parents[1].revision.revision_ref, command_id='reanalyze-resolved')
        result['analysis'] = analysis.document
        choices = [{'conflict_id': row['conflict_id'], 'choice': 'local',
                    'reason': 'Explicit offline fixture selection; not a human decision or model-authored contribution',
                    'delete_element_ids': []} for row in analysis.document['conflicts']]
        merged = PlainModuleMergeAuthor(w['gateway'], w['registration'], w['producer']).publish(
            analysis_ref=analysis.analysis_ref, choices=choices, command_id='merge')
        document = merged.module.to_dict()
        a = revised.module.to_dict()
        b = parents[1].module.to_dict()
        assert document['components'][1]['operations'][0] == a['components'][1]['operations'][0]
        assert document['components'][1]['config'] == b['components'][1]['config']
        assert document['components'][0]['operations'][0] == a['components'][0]['operations'][0]
        compiled = compile_module(merged.module, w['registration'])
        capacities = {p.name: p.capacity for p in compiled.symbolic.places if p.capacity is not None}
        result['merged_capacity_places'] = capacities
        assert list(capacities.values()).count(1) >= 2
        exported = export(w, merged.revision.revision_ref, limits)
        artifacts = dict(exported.package.artifacts)
        archive_dir = evidence_dir / 'exchange-archives'
        archive_dir.mkdir(exist_ok=True)
        (archive_dir / 'merged.zip').write_bytes(exported.package.archive_bytes)
        for path in ('fixtures/inputs.json', 'environment/task.json', 'licenses/terminal-bench.txt',
                     'upstream/multi-source-data-merger/tests/test_outputs.py',
                     'upstream/multi-source-data-merger/environment/data/source_c/users.parquet.base64.json'):
            assert path in artifacts
        result['stages'].append({'name': 'seed_import_merge_export', 'status': 'passed',
            'schema': exported.package.manifest['schema_version'], 'aggregate': budget(exported.package, limits),
            'artifact_count': len(artifacts), 'archive_bytes': len(exported.package.archive_bytes),
            'archive_sha256': hashlib.sha256(exported.package.archive_bytes).hexdigest()})
        (evidence_dir / 'preview-progress.json').write_text(json.dumps({k: v for k, v in result.items() if k not in {'analysis', 'initial_analysis'}}, indent=2) + '\n')
        target = fixture.world(root / 'target', 'target')
        copied = import_subnet(target['author'], plan=plan_subnet_import(exported, target=fixture.target(target), limits=limits),
                               command_id='import-merged', expected_target_head=fixture.head(target))
        value = target['author']._validate(copied.revision_ref)
        assert value.module.to_dict() == document
        assert value.revision.parent_revision_refs == ()
        assert all(row['origin']['element_id'] != row['local_element_id'] for row in copied.origin_to_local_map)
        result['stages'].append({'name': 'merged_import_new_identity', 'status': 'passed'})
        (evidence_dir / 'preview-progress.json').write_text(json.dumps({k: v for k, v in result.items() if k not in {'analysis', 'initial_analysis'}}, indent=2) + '\n')
        reexport = export(target, value.revision.revision_ref, limits)
        (archive_dir / 'reexported.zip').write_bytes(reexport.package.archive_bytes)
        second = dict(reexport.package.artifacts)
        for path in ('fixtures/inputs.json', 'environment/task.json', 'licenses/terminal-bench.txt'):
            assert second[path] == artifacts[path]
        result['stages'].append({'name': 'imported_reexport', 'status': 'passed',
            'schema': reexport.package.manifest['schema_version'], 'aggregate': budget(reexport.package, limits),
            'artifact_count': len(second), 'archive_bytes': len(reexport.package.archive_bytes)})
        from cpn.rpnh.collaboration.share_packages import PackageError
        try:
            budget(reexport.package, replace(limits, closure_members=194))
        except PackageError as exc:
            result['negative_194_members'] = {'status': 'correctly_rejected', 'code': exc.code}
        else:
            raise AssertionError('195-member closure was not rejected at explicit 194 cap')
        third = fixture.world(root / 'third', 'third')
        again = import_subnet(third['author'], plan=plan_subnet_import(reexport, target=fixture.target(third), limits=limits),
                              command_id='third-copy', expected_target_head=fixture.head(third))
        assert third['author']._validate(again.revision_ref).module.to_dict() == document
        result['stages'].append({'name': 'reexport_import', 'status': 'passed'})
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--measurement-members', type=int)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    limits = DEFAULT_LIMITS if args.measurement_members is None else replace(DEFAULT_LIMITS, members=args.measurement_members)
    try:
        result = run(limits, evidence_dir=args.report.parent)
    except Exception as exc:
        import traceback
        result = {'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc),
                  'traceback': traceback.format_exc(), 'measurement_only': args.measurement_members is not None}
        args.report.write_text(json.dumps(result, indent=2) + '\n')
        raise
    result['measurement_only'] = args.measurement_members is not None
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key not in {'analysis', 'initial_analysis'}}, indent=2))
