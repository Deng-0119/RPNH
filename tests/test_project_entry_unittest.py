"""Real Registry and owner-control checks, with explicitly synthetic proposals.

Does not claim native source TEXT execution, browser use, real humans or task
completion. The separate native validation attempt was blocked at AF_UNIX.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from cpn.rpnh.agent_tasks import AgentStage,build_agent_task_module,TEXT_SCHEMA
from cpn.rpnh.run import OwnerInput
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.registry.resource_service import _publish_private_system
from cpn.rpnh.registry.resources import PublishResource,PrivateSystemOrigin
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.collaboration.project_local import initialize,LocalProjectApplication,OwnerProjectViewer
from cpn.rpnh.collaboration.project_entry import parse_request,run_console


class RequestTests(unittest.TestCase):
    def test_strict_json_and_frame_limits(self):
        self.assertEqual(parse_request(b'{"op":"status"}'),{'op':'status'})
        for raw in (b'{"op":"status","op":"fact"}', b'{"x":NaN}',b'{"x":1e999}',
                    b'{"x":-1e999}',b'['*10000+b']'*10000,b'\xff',b'x'*300001):
            with self.subTest(raw=raw[:30]),self.assertRaises((ValueError,TypeError)):
                parse_request(raw)
        out=BytesIO()
        run_console(None,BytesIO(b'x'*300001+b'\n{"op":"status"}\n'),out)
        self.assertEqual(len(out.getvalue().splitlines()),1)


class ProjectProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory(prefix='project-entry-check-')
        cls.root=Path(cls.temp.name)/'project'
        cls.module=build_agent_task_module([AgentStage('worker','Inspect the operator input and report exact evidence.')])
        cls.initial=initialize(cls.root,cls.module,{'request':OwnerInput(TEXT_SCHEMA,canonical_json('Controlled protocol check'),'test input')},2,read_closure_limit=16 * 1024 * 1024)
        cls.app=LocalProjectApplication(cls.root)
        cls.owner=cls.app.endpoints['owner']
        cls.branch=cls.initial['branch']
        cls.base=cls.branch['head_revision_ref']
        cls.qualified_materials=[cls.initial['initial_revision'][key] for key in
            ('definition_ref','element_mapping_ref','boundary_mapping_ref','host_requirements_ref')]
        for index in (1,2):
            route='contributor-'+str(index)
            subject=cls.initial['routes'][route]['session']
            cls.app.process('owner',{'op':'grant-read','subject_session':subject,'command_id':'grant-'+str(index),
                'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
                'record_types':['collaboration_net_revision/v1'], 'material_refs':cls.qualified_materials,
                'export_refs':[], 'export_destinations':[]})
            invitation=cls.app.process('owner',{'op':'invite','subject_session':subject,'role':'propose','command_id':'invite-'+str(index)})
            cls.app.process(route,{'op':'join','invitation':invitation,'command_id':'join-'+str(index)})

    @classmethod
    def tearDownClass(cls):
        cls.app.close_readers()
        cls.temp.cleanup()

    def test_01_bound_routes_and_selected_reads(self):
        before=self.app.owner._core.event_store.max_ordinal()
        for key in ('actor','session','origin_core','registry_root','principal','role'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.app.process('contributor-1',{'op':'status',key:'owner'})
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),before)
        self.assertNotEqual(self.app.endpoints['contributor-1'].session.actor,self.app.endpoints['contributor-2'].session.actor)
        actual=self.app.process('contributor-1',{'op':'module','revision':self.base})
        self.assertEqual(actual['module'],self.module.to_dict())
        with self.assertRaises(RegistryConflict):
            self.app.process('contributor-1',{'op':'invite','subject_session':self.initial['routes']['owner']['session'],
                'role':'propose','command_id':'fake-owner'})
        with self.assertRaises(RegistryConflict):
            self.app.host.resume_session(self.owner.session.ref,origin_core=self.app.owners['contributor-1']._core,
                principal_ref=self.app.owners['contributor-1'].principal_ref)
        with self.assertRaises(PermissionError):
            self.app.process('contributor-1',{'op':'grant-read'})

    def fixture_proposal(self,route,document,key):
        endpoint=self.app.endpoints[route]
        ids=self.owner.dispatch({'op':'module','revision':self.base})['element_ids']
        candidate=self.owner._author().publish(module=ModuleDeclaration.from_dict(document),element_ids=ids,
            parent_ref=self.owner._qualified(self.base),command_id='synthetic-candidate-'+key)
        gateway=self.app.owner.schema_gateway
        text=_publish_private_system(self.app.owner._core,gateway._task_ref,PublishResource(
            origin=PrivateSystemOrigin(gateway._bootstrap_ref),payload=canonical_json(document),media_type='text/plain',
            content_schema_ref=None,summary='Synthetic protocol fixture only; not a human/native contribution',
            lifetime_ref=gateway._bootstrap_ref,idempotency_key='fixture-text-'+key))
        proposal=self.app.host.submit_proposal(endpoint.session,self.app.project,source_text_ref=text,
            origin_text={'source_id':self.app.host._binding['source_id'],'ref':ref_payload(text.as_version_ref())},
            execution_refs={'producer_ref':gateway._bootstrap_ref},payload=document,
            base_revision_ref=self.owner._qualified(self.base),expected_branch_ref=self.owner._qualified(self.branch['branch_ref'],'collaboration_branch/v1'),
            expected_head_ref=self.owner._qualified(self.base),candidate_revision_ref=candidate.revision.revision_ref,
            command_id='fixture-proposal-'+key,synthetic_fixture=True)
        return ref_payload(proposal),candidate

    def test_02_original_merge_acceptance_and_separate_adoption(self):
        left=deepcopy(self.module.to_dict());right=deepcopy(left)
        left['name']='PreservedOwnerTaskName'
        left['components'][0]['operations'][0]['config']['node_synopsis']='Left precise task instruction'
        right['components'][0]['operations'][0]['config']['node_synopsis']='Right precise task instruction'
        p1,c1=self.fixture_proposal('contributor-1',left,'left')
        p2,c2=self.fixture_proposal('contributor-2',right,'right')
        reviews=[self.app.process('owner',{'op':'review','proposal':p,'disposition':'approve','reason':'Exact synthetic protocol candidate',
                    'command_id':'review-'+str(i)}) for i,p in enumerate((p1,p2))]
        before_runtime=self.app.owner.snapshot()['net_ref']
        analysis=self.app.process('owner',{'op':'analyze','base':self.base,
            'local':c1.revision.revision_ref.to_dict(),'incoming':c2.revision.revision_ref.to_dict(),'command_id':'analysis'})
        self.assertTrue(analysis['document']['conflicts'])
        choices=[{'conflict_id':r['conflict_id'],'choice':'incoming','reason':'Select the right exact task instruction',
                  'delete_element_ids':[]} for r in analysis['document']['conflicts']]
        merged=self.app.process('owner',{'op':'merge','analysis':analysis['analysis'],'choices':choices,'command_id':'merge'})
        self.assertEqual(len(merged['revision']['parent_revision_refs']),2)
        self.assertEqual(merged['module']['name'],left['name'])
        request={'op':'accept','revision':merged['revision']['revision_ref'],'branch':self.branch['branch_ref'],
                 'head':self.base,'proposals':[p1,p2],'reviews':reviews,'command_id':'accept'}
        accepted=self.app.process('owner',request)
        self.assertEqual(self.app.owner.snapshot()['net_ref'],before_runtime)
        count=self.app.owner._core.event_store.max_ordinal()
        self.assertEqual(self.app.process('owner',request),accepted)
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),count)
        before_plan=self.app.owner._core.event_store.max_ordinal()
        selection=self.app.process('owner',{'op':'read-plan','revision':merged['revision']['revision_ref'],'include_runtime':True})
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),before_plan)
        self.assertTrue(selection['material_refs'])
        print('CLOSURE_METRICS', json.dumps({key: selection[key] for key in ('selected_body_bytes','max_selected_body_bytes','selected_body_count','material_ref_count','revision_count')}))
        self.app.process('owner',{'op':'grant-read','subject_session':ref_payload(self.owner.session.ref),
            'command_id':'owner-selected-merge-read','expires_at':(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
            'record_types':selection['record_types'],'material_refs':selection['material_refs'],
            'export_refs':selection['export_refs'],'export_destinations':['package-review']})
        reader=self.owner.reader
        merged_ref=self.owner._qualified(merged['revision']['revision_ref'])
        projection=reader.read_public_projection(entry_ref=merged_ref,at_cut=reader.capture_cut(merged_ref.source_id))
        self.assertEqual(projection['record']['producer_contract'],'rpnh/plain_merge_public_pn/v1')
        self.app.process('owner',{'op':'grant-read','subject_session':ref_payload(self.app.endpoints['contributor-2'].session.ref),
            'command_id':'incomplete-merge-scope','expires_at':(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
            'record_types':selection['record_types'],'material_refs':[], 'export_refs':[], 'export_destinations':[]})
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        limited=self.app.endpoints['contributor-2'].reader
        with self.assertRaises(RegistryReadSessionError):
            limited.read_public_projection(entry_ref=merged_ref,at_cut=limited.capture_cut(merged_ref.source_id))
        snapshot=self.app.process('owner',{'op':'runtime-status'})
        adopt={'op':'adopt','receipt':accepted['receipt'],'branch':accepted['branch']['branch_ref'],
            'head':accepted['branch']['head_revision_ref'],'runtime':snapshot['net_ref'],
            'marking_mapping':{r['token_ref']['version_id']:r['place'] for r in snapshot['marking']},
            'retire_token_refs':[],'owner_inputs':{},'command_id':'adopt'}
        adopted=self.app.process('owner',adopt)
        self.assertEqual(adopted['result']['status'],'ADOPTED')
        self.assertIn('receipt',adopted)
        self.assertNotEqual(self.app.owner.snapshot()['net_ref'],before_runtime)
        count=self.app.owner._core.event_store.max_ordinal()
        replay=self.app.process('owner',adopt)
        self.assertEqual(replay['status'],'ADOPTED')
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),count)
        with self.assertRaises(RegistryConflict):
            self.app.process('owner',{**adopt,'marking_mapping':{}})

    def test_024_settled_artifact_plan(self):
        before=self.app.owner._core.event_store.max_ordinal()
        plan=self.app.process('owner', {'op':'artifacts-plan'})
        self.assertEqual(plan['checkpoint_ref'], self.app.owner.snapshot()['checkpoint_ref'])
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),before)
        self.assertEqual(plan['files'], [])  # Initial fixture has no business execution.
        with self.assertRaises(RegistryConflict):
            self.app.process('contributor-1', {'op':'artifacts-plan'})
        with self.assertRaises(ValueError):
            self.app.process('owner', {'op':'artifact-read','checkpoint':plan['checkpoint_ref'],
                'workspace':{}, 'path':'../../private'})

    def test_024b_scoped_artifact_bytes_component(self):
        # Component check uses an existing exact Module body, not a claimed
        # executed workspace. Full settled business-file binding remains unrun.
        import hashlib
        from cpn.rpnh.collaboration.project_artifacts import _read_scoped_body
        endpoint=self.app.endpoints['contributor-1']
        reference=self.qualified_materials[0]
        prepared=self.app.owner._core.get_version(reference['ref']['resource_version_id'])
        payload=self.app.owner._core.object_store.read_registered(prepared)
        row={'resource_ref':reference,'sha256':hashlib.sha256(payload).hexdigest(),'byte_count':len(payload)}
        self.assertEqual(_read_scoped_body(endpoint,row)['bytes'],payload)
        wrong={**row,'sha256':'0'*64}
        with self.assertRaises(RegistryConflict):
            _read_scoped_body(endpoint,wrong)
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        # Exact ungranted body: current registered whole task declaration.
        from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
        executable=hydrate_module_runtime(self.app.owner._core)[0]
        from cpn.rpnh.collaboration.registry_typed_readers import qualify
        net=self.app.owner._core.get_version(executable.net_ref.version_id).metadata
        ref=net['team_net_declaration_resource_ref']
        if 'resource_id' not in ref:
            ref={'resource_id':ref['logical_id'],'resource_version_id':ref['version_id']}
        denied={**row,'resource_ref':qualify(self.app.host._binding['source_id'],ref)}
        with self.assertRaises(RegistryReadSessionError):
            _read_scoped_body(endpoint,denied)

    def test_025_official_viewer_pure_routes(self):
        from cpn.frontend.dashboard import RegistryDashboard
        from cpn.frontend.server import handle_request
        self.owner.viewer=RegistryDashboard(self.app.owner._core.run_dir,catalog=self.app.owner._core.catalog)
        provider=OwnerProjectViewer(self.owner)
        for path in ('/api/v1/net','/api/v1/dashboard'):
            with self.subTest(path=path):
                response=handle_request(provider,'GET',path)
                self.assertEqual(response.status,200,response.body[:500])
        self.assertEqual(handle_request(provider,'POST','/api/v1/net').status,405)

    def test_03_revoke_and_reopen_do_not_revive_reader(self):
        endpoint=self.app.endpoints['contributor-1']
        observer=endpoint.session._observer
        self.app.process('owner',{'op':'revoke-read','grant':ref_payload(observer.grant_ref),'command_id':'revoke-reader'})
        before=self.app.owner._core.event_store.max_ordinal()
        with self.assertRaises(RegistryConflict):
            self.app.process('contributor-1',{'op':'status'})
        self.assertEqual(self.app.owner._core.event_store.max_ordinal(),before)
        self.app.process('owner',{'op':'stop-host','command_id':'stop-test'})
        reopened=LocalProjectApplication(self.root)
        try:
            self.assertEqual(reopened.endpoints['owner'].session.actor,self.owner.session.actor)
            self.assertEqual(reopened.endpoints['contributor-1'].session.actor,endpoint.session.actor)
            before=reopened.owner._core.event_store.max_ordinal()
            with self.assertRaises(RegistryConflict):
                reopened.process('contributor-1',{'op':'status'})
            self.assertEqual(reopened.owner._core.event_store.max_ordinal(),before)
            reopened.process('owner',{'op':'stop-host','command_id':'stop-reopened'})
        finally:
            reopened.close_readers()


class ImportedProjectReadPlanTests(unittest.TestCase):
    def test_imported_project_scope_exports_package_and_requires_provenance_body(self):
        import base64
        from cpn.rpnh.collaboration.package_preview import preview_package
        from cpn.rpnh.collaboration.registry_read_contracts import RegistryReadSessionError
        seed_path = Path(__file__).resolve().parents[1] / 'examples/project_collaboration/scenario/generated/seed-project.zip'
        seed = preview_package(seed_path.read_bytes())
        artifacts = dict(seed.artifacts)
        entry = seed.manifest['entries'][0]
        module = ModuleDeclaration.from_dict(json.loads(artifacts[entry['declaration_path']]))
        inputs = {key: OwnerInput(value['schema_id'], canonical_json(value['value']), 'Original package fixture')
                  for key, value in json.loads(artifacts['fixtures/inputs.json']).items()}
        with TemporaryDirectory(prefix='project-import-read-plan-') as directory:
            root = Path(directory) / 'project'
            initial = initialize(root, module, inputs, 1, package=seed,
                                 read_closure_limit=16 * 1024 * 1024)
            app = LocalProjectApplication(root)
            try:
                revision = initial['branch']['head_revision_ref']
                provenance = initial['import_result']['provenance_ref']
                before = app.owner._core.event_store.max_ordinal()
                plan = app.process('owner', {'op': 'read-plan', 'revision': revision, 'include_runtime': False})
                self.assertEqual(app.owner._core.event_store.max_ordinal(), before)
                self.assertIn(provenance, plan['material_refs'])
                self.assertIn(provenance, plan['export_refs'])
                self.assertIn('resource_version/v1', plan['record_types'])
                grant = {'op': 'grant-read', 'subject_session': initial['routes']['owner']['session'],
                         'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                         'record_types': plan['record_types'], 'export_refs': plan['export_refs'],
                         'export_destinations': ['local-receiver']}
                app.process('owner', {**grant, 'command_id': 'without-provenance',
                    'material_refs': [ref for ref in plan['material_refs'] if ref != provenance]})
                request = {'op': 'download', 'revision': revision, 'destination': 'local-receiver'}
                with self.assertRaises(RegistryReadSessionError) as denied:
                    app.process('owner', request)
                self.assertEqual(denied.exception.code, 'MATERIAL_ACCESS_NOT_GRANTED')
                app.process('owner', {**grant, 'command_id': 'complete-import-scope',
                                      'material_refs': plan['material_refs']})
                downloaded = app.process('owner', request)
                exported = preview_package(base64.b64decode(downloaded['archive_base64'], validate=True))
                self.assertEqual(dict(exported.artifacts)['fixtures/inputs.json'], artifacts['fixtures/inputs.json'])
            finally:
                app.process('owner', {'op': 'stop-host', 'command_id': 'stop-import-scope-test'})
                app.close_readers()


if __name__=='__main__':
    unittest.main()
