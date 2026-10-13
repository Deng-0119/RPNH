"""Offline, real Registry/session regressions; runnable without pytest.

The synthetic author fixture follows test_collaboration_plain_merge. It is not
an external-user authentication or completed-agent-task demonstration.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import base64
import unittest
import uuid
from unittest.mock import patch

from cpn.components.basic import CONFIG_SCHEMA_ID, register_basic_components
from cpn.rpnh.collaboration import (ClosedModuleAuthor, PlainModuleMergeAnalyzer, PlainModuleMergeAuthor,
    SourceQualifiedVersionRef, SourceQualifiedResourceRef, plain_merge_result_schema_data)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.registry_read_contracts import ReadSessionRequest, ExplicitSources, SourceSelection
from cpn.rpnh.collaboration.registry_read_session import (RegistryReadHostBinding, ExistingReadAuthorityProvider,
    open_registry_session, open_readonly_source, RegistryReadSessionError)
from cpn.rpnh.collaboration.subnet_exchange import (plan_subnet_export, export_subnet, plan_subnet_import,
    import_subnet, TargetRegistrySelection, SubnetExchangeError, _exchange, _zip)
from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256, DEFAULT_LIMITS, PackageError
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registration import Registration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id, TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.observer_access import ObserverReadScope, issue_observer_access, observer_access_schema_data

TEXT = 'test/exchange_text/v1'
CONFIG = 'test/exchange_config/v1'
EXECUTOR = 'test/exchange-executor/v1'
TERMINAL = 'test/exchange-terminal/v1'


def forbidden(*args, **kwargs):
    raise AssertionError('exchange must not execute external or business effects')


def registration(*, include_text=True):
    value = Registration()
    register_basic_components(value)
    if include_text:
        value.register_schema(TEXT, {'$id': TEXT, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'string'})
    value.register_schema(CONFIG, {'$id': CONFIG, '$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object'})
    value.register_executor(EXECUTOR, forbidden,
        identity={'implementation_id': 'test.exchange', 'revision': 'v1'},
        contracts={'transport': 'deterministic', 'input_ports': None, 'output_ports': None, 'config_schema': CONFIG})
    value.register_tool(TERMINAL, forbidden,
        identity={'implementation_id': 'test.exchange_terminal', 'revision': 'v1'},
        contracts={'binding_protocol': 'rpnh/module_terminal/v1'})
    return value


def module():
    component = {'name': 'agent_work', 'key': 'operation', 'config_schema': CONFIG_SCHEMA_ID, 'config': {},
        'ports': [{'name': 'request', 'direction': 'input', 'schema': TEXT},
                  {'name': 'result', 'direction': 'output', 'schema': TEXT}],
        'operations': [{'name': 'run', 'executor': EXECUTOR, 'inputs': ['request'], 'outputs': ['result'],
            'request_port': None, 'tools': [], 'config': {},
            'outcomes': [{'name': 'complete', 'products': [{'port': 'result'}]}]}]}
    return ModuleDeclaration.from_dict({'schema_version': 'rpnh/module_declaration/v1', 'name': 'AgentWorkFixture',
        'components': [component], 'links': [], 'entry': {'request': {'component': 'agent_work', 'port': 'request'}},
        'exit': {'result': {'component': 'agent_work', 'port': 'result'}},
        'terminal': {'key': TERMINAL, 'source': {'component': 'agent_work', 'port': 'result'},
                     'operation': 'run', 'outcome': 'complete', 'config': {}},
        'required_schemas': [CONFIG_SCHEMA_ID, CONFIG, TEXT], 'budgets': {}})


def world(path, source, *, include_text=True):
    schemas, types, paths = plain_merge_result_schema_data()
    other, definitions, locations = observer_access_schema_data()
    schemas.update(other); paths.update(locations)
    types = tuple({item.name: item for item in (*types, *definitions)}.values())
    core = _RegistryCore(path, create=True, catalog=SchemaCatalog(schemas=schemas, types=types, schema_paths=paths))
    owner = _bootstrap_identity(core, NativeBootstrapManifest(('exchange-offline-fixture/v1',)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta('bootstrap_command_ref')))
    gateway = RegistryRegistrationGateway(core, owner.task_ref, bootstrap)
    gateway.bind_source_identity(source_id=source, command_id='bind')
    principal = VersionRef('principal/v1', new_id('principal'), new_id('principal_version'))
    body = {'principal_id': str(principal.entity_id), 'principal_version_id': str(principal.version_id), 'display_name': 'Fixture author'}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id, version_id=principal.version_id,
        payload=canonical_json(body), metadata=body, media_type='application/json', schema_ref='registry_v1/principal/v1', idempotency_key='principal')
    producer = SourceQualifiedVersionRef(source, principal)
    selected = registration(include_text=include_text)
    author = ClosedModuleAuthor(gateway, selected, producer)
    return dict(core=core, owner=owner, gateway=gateway, principal=principal, producer=producer,
        registration=selected, author=author, source=source)


def merged(w):
    original = module(); ids = {path: 'element:' + uuid.uuid5(uuid.NAMESPACE_URL, path).hex for path in _elements(original)}
    base = w['author'].publish(module=original, element_ids=ids, command_id='base')
    parents = []
    for command, value in [('left', True), ('right', 1)]:
        document = original.to_dict(); document['components'][0]['operations'][0]['config'] = {'value': value}
        parents.append(w['author'].publish(module=ModuleDeclaration.from_dict(document), element_ids=ids,
            parent_ref=base.revision.revision_ref, command_id=command))
    analyzer = PlainModuleMergeAnalyzer(w['gateway'], w['registration'], w['producer'])
    analysis = analyzer.analyze(local_ref=parents[0].revision.revision_ref, incoming_ref=parents[1].revision.revision_ref,
        base_ref=base.revision.revision_ref, command_id='analysis')
    choices = [{'conflict_id': row['conflict_id'], 'choice': 'local', 'reason': 'Explicit fixture choice', 'delete_element_ids': []}
        for row in analysis.document['conflicts']]
    assert choices
    value = PlainModuleMergeAuthor(w['gateway'], w['registration'], w['producer']).publish(
        analysis_ref=analysis.analysis_ref, choices=choices, command_id='merge')
    return base, *parents, value


def reader(w, *, omit_export=None, omit_material=None):
    cat = TypedReaderCatalog(); fields = {kind: tuple(cat.fields(kind)) for kind in cat.entry_types}
    refs = []
    for row in w['core'].event_store.object_rows():
        if row['object_type'] == 'resource_version/v1':
            ref = SourceQualifiedResourceRef(w['source'], ResourceVersionRef(TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
        else:
            ref = SourceQualifiedVersionRef(w['source'], VersionRef(row['object_type'], TypedId.parse(row['logical_id']), TypedId.parse(row['version_id'])))
        refs.append(ref)
    materials = tuple(ref for ref in refs if isinstance(ref, SourceQualifiedResourceRef) and ref != omit_material)
    grant = issue_observer_access(w['gateway'], principal_ref=w['principal'],
        scope=ObserverReadScope(fields, fields, materials, tuple(ref for ref in refs if ref != omit_export), ('review-local',)),
        purpose='exchange-test', expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), command_id='grant:' + uuid.uuid4().hex)
    w['last_grant'] = grant
    grants = {('fixture:verified', w['source'], 'local'): grant}
    def resolver(source_id, access_path):
        assert source_id == w['source'] and access_path == 'local'
        return open_readonly_source(w['core'].run_dir, catalog=w['core'].catalog)
    return open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(
        SourceQualifiedVersionRef(w['source'], w['owner'].task_ref), 'local'),)), 'exchange-test'),
        host=RegistryReadHostBinding('fixture:verified', resolver, ExistingReadAuthorityProvider(grants), cat))


def export(w, value):
    with reader(w) as session:
        cut = session.capture_cut(w['source'])
        plan = plan_subnet_export(session, root_revision_ref=value.revision.revision_ref, cuts={w['source']: cut})
        return export_subnet(session, plan=plan, destination='review-local')


def target(w):
    return TargetRegistrySelection(SourceQualifiedVersionRef(w['source'], w['owner'].task_ref))


def head(w):
    return {'ordinal': w['core'].event_store.max_ordinal(), 'writer_fencing_epoch': w['core'].writer_epoch}


class MergeExchangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory(); cls.addClassCleanup(cls.directory.cleanup)
        cls.w = world(Path(cls.directory.name) / 'source', 'source')
        cls.base, cls.left, cls.right, cls.value = merged(cls.w)
        cls.package = export(cls.w, cls.value)

    def setUp(self):
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)


    def test_real_session_merge_export_import(self):
        package = self.package
        exchange = _exchange(package.package)
        self.assertEqual(exchange['schema_version'], 'rpnh/subnet_exchange/v2')
        self.assertEqual(package.package.manifest['schema_version'], 'rpnh/share_package/v3')
        self.assertEqual(exchange['root_revision_ref'], self.value.revision.revision_ref.to_dict())
        self.assertEqual(len(exchange['records']), 4)
        dest = world(self.root / 'target', 'target')
        plan = plan_subnet_import(package, target=target(dest)); before = head(dest)
        result = import_subnet(dest['author'], plan=plan, command_id='copy', expected_target_head=before)
        self.assertEqual(result.revision_ref.source_id, 'target')
        self.assertTrue(all(row['local_element_id'] != row['origin']['element_id'] for row in result.origin_to_local_map))
        count = dest['core'].event_store.max_ordinal()
        self.assertEqual(import_subnet(dest['author'], plan=plan, command_id='copy', expected_target_head=before), result)
        self.assertEqual(dest['core'].event_store.max_ordinal(), count)
        with self.assertRaises(RegistryConflict):
            import_subnet(dest['author'], plan=plan, command_id='stale', expected_target_head=before)
        value = dest['author']._validate(result.revision_ref)
        self.assertEqual(value.revision.parent_revision_refs, ())
        self.assertEqual(value.module.to_dict(), self.value.module.to_dict())
        for kind in ('collaboration_branch/v1', 'net_instance/v1', 'user_collaboration_session/v1', 'registry_observer_grant/v2'):
            self.assertEqual(dest['core'].event_store.object_rows_by_type(kind), ())
        self.assertFalse(result.to_dict()['adopted']); self.assertFalse(result.to_dict()['executed'])
        reexport = export(dest, value)
        self.assertEqual(_exchange(reexport.package)['root_revision_ref'], result.revision_ref.to_dict())
        # The source M remains in inert prior archive evidence, not new parents.
        third = world(self.root / 'third', 'third')
        again = import_subnet(third['author'], plan=plan_subnet_import(reexport, target=target(third)),
            command_id='copy-again', expected_target_head=head(third))
        self.assertEqual(third['author']._validate(again.revision_ref).module.to_dict(), self.value.module.to_dict())


    def damaged(self, edit, package=None):
        preview = self.package.package if package is None else package.package
        files = dict(preview.artifacts)
        exchange_path = next(path for path, raw in files.items()
            if path.startswith('subnet-exchange'))
        document = json.loads(files[exchange_path])
        edit(document, files)
        files[exchange_path] = canonical_bytes(document)
        manifest = deepcopy(preview.manifest)
        for row in manifest['artifacts']:
            row.update(bytes=len(files[row['path']]), sha256=sha256(files[row['path']]))
        for row in manifest['provenance']:
            if row['relation'] == 'copied': row['origin_digest'] = sha256(files[row['artifact_path']])
        files['manifest.json'] = canonical_bytes(manifest)
        return preview_package(_zip(files, compression=zipfile.ZIP_DEFLATED))

    def test_ancestor_record_cannot_disagree_with_proof_descriptor(self):
        def damage(document, files):
            row = next(r for r in document['records'] if r['revision_ref'] == self.left.revision.revision_ref.to_dict())
            row['command_id'] = 'invented-different-author-command'
        with self.assertRaisesRegex(SubnetExchangeError, 'INTEGRITY_FAILED'):
            _exchange(self.damaged(damage))

    def test_merge_parents_and_proof_graph_tamper_rejected(self):
        def parents(document, files):
            row = next(r for r in document['records'] if r['revision_ref'] == self.value.revision.revision_ref.to_dict())
            row['parent_revision_refs'].reverse()
        def graph(document, files):
            for row in document['materials']:
                body = json.loads(files[row['artifact_path']])
                if body.get('schema_version') == 'rpnh/stored_plain_merge_public_pn/v1':
                    body['projection']['nodes'][0]['label'] = 'fabricated'
                    raw = canonical_bytes(body); files[row['artifact_path']] = raw
                    row.update(bytes=len(raw), sha256=sha256(raw))
                    return
            self.fail('missing merge proof fixture')
        for edit in (parents, graph):
            with self.subTest(edit=edit.__name__), self.assertRaisesRegex(SubnetExchangeError, 'INTEGRITY_FAILED'):
                _exchange(self.damaged(edit))

    def test_missing_parent_material_or_merge_proof_rejected(self):
        def parent(document, files):
            document['records'] = [r for r in document['records'] if r['revision_ref'] != self.left.revision.revision_ref.to_dict()]
        def material(document, files):
            document['materials'] = [r for r in document['materials'] if r['ref'] != self.base.revision.definition_ref.to_dict()]
        def proof(document, files):
            document['materials'] = [r for r in document['materials'] if
                json.loads(files[r['artifact_path']]).get('schema_version') != 'rpnh/stored_plain_merge_public_pn/v1']
        for edit in (parent, material, proof):
            with self.subTest(edit=edit.__name__), self.assertRaises(SubnetExchangeError):
                _exchange(self.damaged(edit))

    def test_same_schema_id_cannot_replace_source_host_schema(self):
        def damage(document, files):
            path = next(row['path'] for row in self.package.package.manifest['artifacts']
                if row['role'] == 'schema' and json.loads(files[row['path']])['$id'] == TEXT)
            body = json.loads(files[path]); body['description'] = 'different schema bytes at same id'
            files[path] = canonical_bytes(body)
        damaged = self.damaged(damage)
        dest = world(self.root / 'target', 'target', include_text=False)
        plan = plan_subnet_import(damaged, target=target(dest)); before = head(dest)
        with self.assertRaisesRegex(SubnetExchangeError, 'HOST_REQUIREMENTS_MISMATCH'):
            import_subnet(dest['author'], plan=plan, command_id='bad-schema', expected_target_head=before)
        self.assertEqual(head(dest), before)

    def test_source_descriptor_export_permission_is_required(self):
        with reader(self.w, omit_export=self.w['producer']) as session:
            cut = session.capture_cut('source')
            plan = plan_subnet_export(session, root_revision_ref=self.value.revision.revision_ref, cuts={'source': cut})
            self.assertIn(self.w['producer'].to_dict(), plan.to_dict()['included_refs'])
            with self.assertRaises(RegistryReadSessionError) as caught:
                export_subnet(session, plan=plan, destination='review-local')
            self.assertEqual(caught.exception.code, 'EXPORT_ACCESS_NOT_GRANTED')

    def test_ancestor_material_denial_and_current_revocation(self):
        with reader(self.w, omit_material=self.base.revision.definition_ref) as session:
            cut = session.capture_cut('source')
            with self.assertRaises(RegistryReadSessionError) as caught:
                plan_subnet_export(session, root_revision_ref=self.value.revision.revision_ref, cuts={'source': cut})
            self.assertEqual(caught.exception.code, 'MATERIAL_ACCESS_NOT_GRANTED')
        with reader(self.w) as session:
            cut = session.capture_cut('source')
            plan = plan_subnet_export(session, root_revision_ref=self.value.revision.revision_ref, cuts={'source': cut})
            from cpn.rpnh.registry.observer_access import revoke_observer_access
            revoke_observer_access(self.w['gateway'], grant_ref=self.w['last_grant'].grant_ref,
                command_id='revoke:' + uuid.uuid4().hex)
            with self.assertRaises(RegistryReadSessionError) as caught:
                export_subnet(session, plan=plan, destination='review-local')
            self.assertEqual(caught.exception.code, 'ACCESS_CHANGED')

    def test_all_proof_edges_count_toward_import_budget(self):
        document = _exchange(self.package.package)
        pins = next(json.loads(dict(self.package.package.artifacts)[row['artifact_path']])['dependencies']
            for row in document['materials'] if json.loads(dict(self.package.package.artifacts)[row['artifact_path']]).get('schema_version')
                == 'rpnh/stored_plain_merge_public_pn/v1')
        # Pins alone fit; parents + pins exceed this budget.
        limits = replace(DEFAULT_LIMITS, dependency_edges=len(pins))
        with self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
            _exchange(self.package.package, limits)

    def test_original_single_parent_v1_export_still_imports(self):
        package = export(self.w, self.left)
        self.assertEqual(_exchange(package.package)['schema_version'], 'rpnh/subnet_exchange/v1')
        dest = world(self.root / 'target', 'target')
        plan = plan_subnet_import(package, target=target(dest))
        result = import_subnet(dest['author'], plan=plan, command_id='v1', expected_target_head=head(dest))
        self.assertEqual(dest['author']._validate(result.revision_ref).module.to_dict(), self.left.module.to_dict())

    def test_target_mismatch_is_write_free(self):
        dest = world(self.root / 'target', 'target'); another = world(self.root / 'another', 'another')
        plan = plan_subnet_import(self.package, target=target(dest)); before = head(another)
        with self.assertRaisesRegex(SubnetExchangeError, 'TARGET_MISMATCH'):
            import_subnet(another['author'], plan=plan, command_id='wrong-target', expected_target_head=before)
        self.assertEqual(head(another), before)


    def test_shared_ancestor_long_path_cannot_bypass_depth_budget(self):
        # The left path visits base at depth two first. The right path reaches
        # that already-seen base at depth three and must still fail depth=3.
        ids = {row['locator']: row['element_id'] for row in self.right.element_map['elements']}
        tail = self.w['author'].publish(module=self.right.module, element_ids=ids,
            parent_ref=self.right.revision.revision_ref, command_id='long-tail')
        analysis = PlainModuleMergeAnalyzer(self.w['gateway'], self.w['registration'], self.w['producer']).analyze(
            local_ref=self.left.revision.revision_ref, incoming_ref=tail.revision.revision_ref,
            base_ref=self.base.revision.revision_ref, command_id='long-analysis')
        choices = [{'conflict_id': row['conflict_id'], 'choice': 'local', 'reason': 'Select local', 'delete_element_ids': []}
            for row in analysis.document['conflicts']]
        value = PlainModuleMergeAuthor(self.w['gateway'], self.w['registration'], self.w['producer']).publish(
            analysis_ref=analysis.analysis_ref, choices=choices, command_id='long-merge')
        limited = replace(DEFAULT_LIMITS, dependency_depth=3)
        with reader(self.w) as session:
            cut = session.capture_cut('source')
            with self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
                plan_subnet_export(session, root_revision_ref=value.revision.revision_ref, cuts={'source': cut}, limits=limited)
        package = export(self.w, value)
        with self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
            _exchange(package.package, limited)

    def test_source_merge_proof_does_not_call_lowerers_or_writers(self):
        from cpn.rpnh.collaboration import _subnet_merge_proof
        with patch('cpn.rpnh.compiler.compile_module', forbidden), \
                patch('cpn.rpnh.collaboration.materials.compile_module', forbidden), \
                patch.object(_RegistryCore, 'begin', forbidden):
            self.assertEqual(_exchange(self.package.package)['root_revision_ref'], self.value.revision.revision_ref.to_dict())

    def test_unsupported_contracts_stay_rejected(self):
        from cpn.rpnh.collaboration.subnet_exchange import _ordinary
        for change in ('graph', 'constraints', 'assembly', 'selected', 'open', 'third-parent'):
            document = module().to_dict(); revision = self.value.revision.to_dict()
            if change == 'graph': document['components'][0]['key'] = 'rpnh/agent-workflow-graph/v4'
            elif change == 'constraints': document['designer_constraints'] = {'unverified': True}
            elif change == 'assembly': revision['command_id'] = 'collaboration-assembly-test'
            elif change == 'selected': revision['selected_change_refs'] = [revision['definition_ref']]
            elif change == 'open': revision['definition_kind'] = 'open_region'
            else: revision['parent_revision_refs'].append(self.base.revision.revision_ref.to_dict())
            with self.subTest(change=change), self.assertRaisesRegex(SubnetExchangeError, 'UNSUPPORTED_EXCHANGE_CONTRACT'):
                _ordinary(document, revision)


    def test_self_consistent_command_pins_cannot_change_merge_choices(self):
        def damage(document, files):
            command_row = next(row for row in document['materials'] if row['ref'] == self.value.command_ref.to_dict())
            command = json.loads(files[command_row['artifact_path']])
            command['requested_choices'][0]['choice'] = 'incoming'
            raw = canonical_bytes(command); files[command_row['artifact_path']] = raw
            command_row.update(bytes=len(raw), sha256=sha256(raw))
            for row in document['materials']:
                body = json.loads(files[row['artifact_path']])
                if body.get('schema_version') == 'rpnh/stored_plain_merge_public_pn/v1':
                    pin = next(pin for pin in body['dependencies'] if pin['ref'] == command_row['ref'])
                    pin.update(bytes=len(raw), sha256=sha256(raw))
                    pin['metadata']['size'] = len(raw)
                    pin['metadata']['reference_provenance']['publication']['size'] = len(raw)
                    projection = canonical_bytes(body); files[row['artifact_path']] = projection
                    row.update(bytes=len(projection), sha256=sha256(projection))
                    return
            self.fail('missing merge projection')
        with self.assertRaisesRegex(SubnetExchangeError, 'INTEGRITY_FAILED'):
            _exchange(self.damaged(damage))

    def test_revocation_during_export_is_not_hidden_by_plan_cache(self):
        from cpn.rpnh.registry.observer_access import revoke_observer_access
        with reader(self.w) as session:
            cut = session.capture_cut('source')
            plan = plan_subnet_export(session, root_revision_ref=self.value.revision.revision_ref, cuts={'source': cut})
            original = session.authorize_export
            def revoke_after_authorize(*args, **kwargs):
                original(*args, **kwargs)
                revoke_observer_access(self.w['gateway'], grant_ref=self.w['last_grant'].grant_ref,
                    command_id='late-revoke:' + uuid.uuid4().hex)
            with patch.object(session, 'authorize_export', revoke_after_authorize):
                with self.assertRaises(RegistryReadSessionError) as caught:
                    export_subnet(session, plan=plan, destination='review-local')
            self.assertEqual(caught.exception.code, 'ACCESS_CHANGED')

    def test_unknown_exchange_version_is_not_downgraded_to_generic_package(self):
        def damage(document, files): document['schema_version'] = 'rpnh/subnet_exchange/v999'
        with self.assertRaisesRegex(PackageError, 'INVALID_EXCHANGE_REFERENCE'):
            _exchange(self.damaged(damage))


    def reseal(self, manifest, files):
        for row in manifest['artifacts']:
            row.update(bytes=len(files[row['path']]), sha256=sha256(files[row['path']]))
        for row in manifest['provenance']:
            if row['relation'] == 'copied': row['origin_digest'] = sha256(files[row['artifact_path']])
        files['manifest.json'] = canonical_bytes(manifest)
        return preview_package(_zip(files, compression=zipfile.ZIP_DEFLATED))

    def plain_package(self, version, name, *, environment=False):
        original = self.package.package
        manifest = deepcopy(original.manifest)
        manifest['schema_version'] = version; manifest['package_id'] = name
        manifest.pop('subnet_exchange', None); manifest['dependencies'] = []
        manifest['artifacts'] = [row for row in manifest['artifacts'] if row['role'] in {'declaration', 'schema', 'license'}]
        wanted = {row['path'] for row in manifest['artifacts']}
        manifest['provenance'] = [row for row in manifest['provenance'] if row['artifact_path'] in wanted]
        files = {path: raw for path, raw in original.artifacts if path in wanted}
        if environment:
            raw = canonical_bytes({'schema_version': 'rpnh/environment_requirements/v1', 'entry_id': 'main',
                'python': {'requirement_id': 'python', 'implementation': 'cpython', 'version_specifier': '>=3.11'},
                'distributions': [{'requirement_id': 'rpnh', 'name': 'rpnh-harness', 'version_specifier': '>=0.1.0rc2',
                    'satisfies_host_requirement_ids': [row['requirement_id'] for row in manifest['requirements']]}],
                'system_requirements': [], 'tools': [], 'plugins': [], 'services': []})
            path = 'environment/requirements.json'; files[path] = raw
            manifest['entries'][0]['environment_requirements_path'] = path
            manifest['artifacts'].append({'path': path, 'media_type': 'application/json', 'role': 'document',
                'bytes': len(raw), 'sha256': sha256(raw), 'license_id': manifest['licenses'][0]['license_id'], 'disclosure': 'named_recipients'})
            manifest['provenance'].append({'artifact_path': path, 'relation': 'authored', 'origin_ref': None, 'origin_digest': None})
        return self.reseal(manifest, files)

    def test_selected_dependency_exchange_is_verified_with_shared_budget(self):
        dependency = self.package.package
        manifest = deepcopy(dependency.manifest)
        manifest['package_id'] = 'fixture/root-with-merge-dependency'
        manifest['dependencies'] = [{'dependency_id': 'merge', 'package_id': dependency.package_id,
            'manifest_digest': dependency.manifest_digest, 'version_range': None,
            'required': True, 'acquisition_hint': None}]
        root = self.reseal(manifest, dict(dependency.artifacts))
        selection = target(self.w)
        plan_subnet_import(root, target=selection, local_packages=[dependency])
        def corrupt(document, files):
            document['records'][-1]['command_id'] = 'forged-dependency'
        damaged = self.damaged(corrupt)
        manifest['dependencies'][0]['manifest_digest'] = damaged.manifest_digest
        root = self.reseal(manifest, dict(dependency.artifacts))
        with self.assertRaisesRegex(SubnetExchangeError, 'INTEGRITY_FAILED'):
            plan_subnet_import(root, target=selection, local_packages=[damaged])
        with self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
            plan_subnet_import(self.reseal({**manifest, 'dependencies': [{**manifest['dependencies'][0],
                'manifest_digest': dependency.manifest_digest}]}, dict(dependency.artifacts)),
                target=selection, local_packages=[dependency],
                limits=replace(DEFAULT_LIMITS, closure_members=1 + len(root.artifacts)))

    def test_versioned_schemas_and_mixed_resolution_preserve_semantics(self):
        from cpn.rpnh.collaboration.share_packages import (manifest_schema, package_preview_schema, package_resolution_lock_schema,
            PACKAGE_SCHEMA_V2, PACKAGE_SCHEMA_V3, PREVIEW_SCHEMA_V3, LOCK_SCHEMA_V3)
        from cpn.rpnh.collaboration.package_resolution import resolve_package
        from cpn.rpnh.collaboration.environment_requirements import read_environment_requirements
        from cpn.rpnh.collaboration.environment_contracts import EnvironmentContractError
        from jsonschema import Draft7Validator
        plain = self.plain_package('rpnh/share_package/v1', 'fixture/plain')
        env = self.plain_package(PACKAGE_SCHEMA_V2, 'fixture/environment', environment=True)
        manifest = deepcopy(self.package.package.manifest)
        manifest['dependencies'] = [{'dependency_id': name, 'package_id': item.package_id, 'manifest_digest': item.manifest_digest,
            'version_range': None, 'required': True, 'acquisition_hint': None} for name, item in [('plain', plain), ('env', env)]]
        package = self.reseal(manifest, dict(self.package.package.artifacts))
        lock = resolve_package(package, [plain, env], 'main')
        self.assertEqual(lock.to_dict()['schema_version'], LOCK_SCHEMA_V3)
        self.assertEqual({row['manifest_schema'] for row in lock.to_dict()['nodes']},
            {'rpnh/share_package/v1', PACKAGE_SCHEMA_V2, PACKAGE_SCHEMA_V3})
        Draft7Validator(manifest_schema(PACKAGE_SCHEMA_V3)).validate(package.manifest)
        Draft7Validator(package_preview_schema(PREVIEW_SCHEMA_V3)).validate(package.to_dict())
        Draft7Validator(package_resolution_lock_schema(LOCK_SCHEMA_V3)).validate(lock.to_dict())
        with self.assertRaises(EnvironmentContractError) as caught:
            read_environment_requirements(lock, [package, plain, env])
        self.assertEqual(caught.exception.code, 'ENVIRONMENT_REQUIREMENTS_UNDECLARED')
        lower = deepcopy(env.manifest)
        lower['dependencies'] = [{'dependency_id': 'newer', 'package_id': package.package_id,
            'manifest_digest': package.manifest_digest, 'version_range': None, 'required': True, 'acquisition_hint': None}]
        # Use the original v3 package without mixed dependencies to avoid a cycle.
        lower['dependencies'][0]['manifest_digest'] = self.package.package.manifest_digest
        with self.assertRaisesRegex(PackageError, 'UNSUPPORTED_PACKAGE_SCHEMA'):
            resolve_package(self.reseal(lower, dict(env.artifacts)), [self.package.package], 'main')

    def test_v3_pointer_is_exact_and_old_v1_reader_rejects(self):
        from cpn.rpnh.collaboration.package_preview import preview_package_v1
        with self.assertRaisesRegex(PackageError, 'UNSUPPORTED_PACKAGE_SCHEMA'):
            preview_package_v1(self.package.archive_bytes)
        manifest = deepcopy(self.package.package.manifest)
        manifest['subnet_exchange']['artifact_path'] = manifest['entries'][0]['declaration_path']
        with self.assertRaisesRegex(PackageError, 'INVALID_EXCHANGE_REFERENCE'):
            self.reseal(manifest, dict(self.package.package.artifacts))
        manifest = deepcopy(self.package.package.manifest)
        manifest['schema_version'] = 'rpnh/share_package/v1'; manifest.pop('subnet_exchange')
        downgraded = self.reseal(manifest, dict(self.package.package.artifacts))
        with self.assertRaisesRegex(SubnetExchangeError, 'UNSUPPORTED_EXCHANGE_CONTRACT'):
            _exchange(downgraded)


    def test_retained_prior_proof_copy_map_and_shared_budgets(self):
        dest = world(self.root / 'target', 'target')
        copied = import_subnet(dest['author'], plan=plan_subnet_import(self.package, target=target(dest)),
            command_id='prior-copy', expected_target_head=head(dest))
        package = export(dest, dest['author']._validate(copied.revision_ref))
        def mutate(change):
            def edit(document, files):
                root = next(row for row in document['records'] if row['revision_ref'] == document['root_revision_ref'])
                row = next(row for row in document['materials'] if row['ref'] == root['subnet_provenance_ref'])
                body = json.loads(files[row['artifact_path']]); change(body)
                raw = canonical_bytes(body); files[row['artifact_path']] = raw
                row.update(bytes=len(raw), sha256=sha256(raw))
            return edit
        def old_history(body):
            root = body['source_provenance']['root_revision_ref']
            next(row for row in body['source_provenance']['records'] if row['revision_ref'] == root)['command_id'] = 'forged-history'
        def mapping(body): body['origin_to_local_map'][0]['local_element_id'] = 'element:' + 'f' * 32
        def plan(body): body['plan_digest'] = '0' * 64
        def command(body): body['command_id'] = 'forged-command'
        unused_archive = base64.b64encode(self.plain_package('rpnh/share_package/v1', 'fixture/unused').archive_bytes).decode()
        def unused(body): body['dependency_archives_base64'].append(unused_archive)
        for change in (old_history, mapping, plan, command, unused):
            with self.subTest(change=change.__name__), self.assertRaisesRegex(SubnetExchangeError, 'INTEGRITY_FAILED'):
                _exchange(self.damaged(mutate(change), package))
        # Each outer envelope fits, but the retained original must be charged
        # to the same traversal budget rather than resetting it.
        outer_members = 1 + len(package.package.artifacts)
        expanded = len(package.package.manifest_bytes) + sum(len(raw) for _, raw in package.package.artifacts)
        for limits in (replace(DEFAULT_LIMITS, closure_members=outer_members),
                       replace(DEFAULT_LIMITS, closure_expanded_bytes=expanded),
                       replace(DEFAULT_LIMITS, dependency_depth=1)):
            with self.subTest(limits=limits), self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
                _exchange(package.package, limits)

    def test_seed_package_materials_survive_descendant_merge(self):
        seed = self.plain_package('rpnh/share_package/v2', 'fixture/seed', environment=True)
        manifest = deepcopy(seed.manifest); files = dict(seed.artifacts)
        path = 'fixtures/task.txt'; raw = b'Synthetic offline agent task input, retained exactly.\n'; files[path] = raw
        manifest['artifacts'].append({'path': path, 'media_type': 'text/plain', 'role': 'fixture', 'bytes': len(raw),
            'sha256': sha256(raw), 'license_id': manifest['licenses'][0]['license_id'], 'disclosure': 'named_recipients'})
        manifest['provenance'].append({'artifact_path': path, 'relation': 'authored', 'origin_ref': None, 'origin_digest': None})
        seed = self.reseal(manifest, files)
        source = world(self.root / 'copy-source', 'copy-source')
        imported = import_subnet(source['author'], plan=plan_subnet_import(seed, target=target(source)),
            command_id='seed', expected_target_head=head(source))
        base = source['author']._validate(imported.revision_ref)
        ids = {row['locator']: row['element_id'] for row in base.element_map['elements']}
        parents = []
        for side in ('left', 'right'):
            document = base.module.to_dict(); document['components'][0]['operations'][0]['config'] = {'value': side}
            parents.append(source['author'].publish(module=ModuleDeclaration.from_dict(document), element_ids=ids,
                parent_ref=base.revision.revision_ref, command_id=side))
        analyzer = PlainModuleMergeAnalyzer(source['gateway'], source['registration'], source['producer'])
        analysis = analyzer.analyze(local_ref=parents[0].revision.revision_ref, incoming_ref=parents[1].revision.revision_ref,
            base_ref=base.revision.revision_ref, command_id='seed-analysis')
        choices = [{'conflict_id': row['conflict_id'], 'choice': 'local', 'reason': 'Select explicit fixture result', 'delete_element_ids': []}
            for row in analysis.document['conflicts']]
        value = PlainModuleMergeAuthor(source['gateway'], source['registration'], source['producer']).publish(
            analysis_ref=analysis.analysis_ref, choices=choices, command_id='seed-merge')
        result = export(source, value)
        with self.assertRaisesRegex(SubnetExchangeError, 'LIMIT_EXCEEDED'):
            _exchange(result.package, replace(DEFAULT_LIMITS, dependency_depth=3))
        self.assertEqual(dict(result.package.artifacts)[path], raw)
        environment = seed.manifest['entries'][0]['environment_requirements_path']
        self.assertEqual(dict(result.package.artifacts)[environment], dict(seed.artifacts)[environment])
        for license_row in seed.manifest['licenses']:
            self.assertIn(license_row, result.package.manifest['licenses'])
        from cpn.rpnh.collaboration.environment_requirements import read_package_environment
        self.assertTrue(read_package_environment(result.package).requirements)
        receiver = world(self.root / 'receiver', 'receiver')
        local = import_subnet(receiver['author'], plan=plan_subnet_import(result, target=target(receiver)),
            command_id='seed-copy', expected_target_head=head(receiver))
        self.assertEqual(receiver['author']._validate(local.revision_ref).module.to_dict(), value.module.to_dict())


    def test_candidate_catalog_budget_is_checked_before_next_preview(self):
        from cpn.rpnh.collaboration import subnet_exchange as exchange
        preview = self.package.package
        expanded = len(preview.manifest_bytes) + sum(len(raw) for _, raw in preview.artifacts)
        for limits in (replace(DEFAULT_LIMITS, packages=1),
                       replace(DEFAULT_LIMITS, closure_bytes=len(preview.archive_bytes)),
                       replace(DEFAULT_LIMITS, closure_expanded_bytes=expanded)):
            with self.subTest(limits=limits), patch.object(exchange, 'preview_package', wraps=preview_package) as observed:
                with self.assertRaisesRegex(PackageError, 'PACKAGE_LIMIT_EXCEEDED'):
                    exchange._selected_packages(preview, [preview, preview], 'main', limits)
                self.assertEqual(observed.call_count, 1)


if __name__ == '__main__':
    unittest.main()
