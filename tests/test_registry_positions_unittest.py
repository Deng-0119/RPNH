"""Original Registry/PN evidence for bounded local position observations.

The author methods exercise the real read-only provider and HTTP route parser
in process. The joined UI/AF_INET method is separate evidence. No Owner loop,
AF_UNIX substitute, browser, model, or external transport is used here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from material_availability_fixture import availability_world, publish_initial, disable
from material_location_fixture import location_world, publish_initial as publish_locations, binding_at, submit_location
from material_management_fixture import command_ref
from cpn.frontend.local_registry_browser import LocalRegistryBrowserProvider
from cpn.frontend.registry_browser import RegistryBrowserError, RegistryBrowserProvider, parse_query
from cpn.frontend.registry_positions import CAPABILITY
from cpn.frontend.server import handle_request
from cpn.rpnh.material_locations import run_location_operation
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.object_store import ObjectStore
from cpn.rpnh.registry.strict_contracts import ref_payload


def request(provider, action, **params):
    reply = handle_request(provider, 'GET', '/api/registry-browser?' + urlencode({'action': action, **params}))
    return reply.status, json.loads(reply.body)


def counts(core):
    with core.event_store.connection_scope() as db:
        return tuple(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
            for table in ('transactions', 'events', 'objects', 'relations',
                          'firing_temporary_members', 'firing_publications'))


def finish_location(test, world, key, *, action, name, expected=None, resource=None):
    accepted = submit_location(world, key, action=action, name=name, expected=expected, resource=resource)
    admitted = world.owner.admit('location.run', logical_tau=0, command_id=key + ':admit',
        owner_command_ref=command_ref(accepted))
    test.assertIsNotNone(admitted)
    active = world.owner.start(admitted, command_id=key + ':start')
    outputs = run_location_operation(world.owner, active)
    test.assertEqual(outputs.outputs, ())
    world.owner.succeed(outputs, command_id=key + ':success')
    receipt = world.owner.control.materials.read_receipt(session=world.session,
        material_command_ref=accepted['command_ref'])
    test.assertEqual((receipt['status'], receipt['result_kind']), ('PUBLISHED', 'location_changed'))
    return binding_at(world, name), receipt


class RegistryPositionsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

        def denied(*_args, **_kwargs):
            raise AssertionError('position observation checks cannot use external transport')

        self.addCleanup(patch.stopall)
        patch('socket.socket', denied).start()
        patch('urllib.request.urlopen', denied).start()

    @contextmanager
    def read_window(self, core, resources):
        before, epoch, ordinal = counts(core), core.event_store.writer_epoch, core.event_store.max_ordinal()
        versions = {resource.resource_version_id for resource in resources}
        original = ObjectStore.read_registered

        def no_business_body(store, prepared, *args, **kwargs):
            if prepared.version_id in versions:
                raise AssertionError('position observation read selected business bytes')
            return original(store, prepared, *args, **kwargs)

        with patch.object(ObjectStore, 'read_registered', no_business_body), patch.object(
                _RegistryCore, 'begin', side_effect=AssertionError('position observation attempted a Registry write')):
            yield
        self.assertEqual(counts(core), before)
        self.assertEqual(core.event_store.writer_epoch, epoch)
        self.assertEqual(core.event_store.max_ordinal(), ordinal)

    def pages(self, provider, action, **params):
        result = []
        cursor = None
        while True:
            status, page = request(provider, action, **params, **({'cursor': cursor} if cursor else {}))
            self.assertEqual(status, 200, page)
            result.append(page)
            cursor = page['next_cursor']
            if cursor is None:
                break
            self.assertLess(len(result), 20, 'bounded test history did not end')
        return result

    def test_native_cross_resource_chain_tombstone_pagination_and_current_disable(self):
        world = availability_world(Path(self.directory.name) / 'run')
        owner, core = world.owner, world.owner._core
        v1 = publish_initial(world, key='v1', value='private V1 business bytes')
        a1 = binding_at(world, 'documents/A')
        b, _ = finish_location(self, world, 'bind:B', action='bind', name='documents/B', resource=v1)
        v2 = publish_initial(world, key='v2', value='private V2 business bytes')
        a2 = binding_at(world, 'documents/A')
        self.assertNotEqual(v1.resource_id, v2.resource_id)
        producer1 = core.get_version(v1.resource_version_id).metadata['producer_ref']
        producer2 = core.get_version(v2.resource_version_id).metadata['producer_ref']
        self.assertNotEqual(producer1, producer2)
        a3, unbind_receipt = finish_location(self, world, 'unbind:A', action='unbind',
            name='documents/A', expected=a2)
        self.assertTrue(a3.tombstone)
        a4, rebind_receipt = finish_location(self, world, 'rebind:A', action='bind',
            name='documents/A', expected=a3, resource=v2)
        self.assertFalse(a4.tombstone)
        self.assertEqual(binding_at(world, 'documents/B'), b)
        provider = LocalRegistryBrowserProvider(core.run_dir, catalog=core.catalog)
        self.assertTrue(provider._core.read_only)
        with patch('cpn.frontend.registry_positions.PAGE_SIZE', 1), self.read_window(core, (v1, v2)):
            status, catalog = request(provider, 'catalog')
            self.assertEqual(status, 200, catalog)
            self.assertEqual(catalog['sources'][0]['position_query'], CAPABILITY)
            position_pages = self.pages(provider, 'positions', source_id=provider.source_id)
            self.assertEqual(len(position_pages), 2)
            positions = [entry for page in position_pages for entry in page['entries']]
            by_name = {entry['address']['opaque_name']: entry for entry in positions}
            self.assertEqual(set(by_name), {'documents/A', 'documents/B'})
            self.assertEqual(by_name['documents/A']['binding_ref'], ref_payload(a4.as_version_ref()))
            self.assertEqual(by_name['documents/B']['binding_ref'], ref_payload(b.as_version_ref()))
            handle = by_name['documents/A']['handle']
            history_pages = self.pages(provider, 'position_history', handle=handle)
            self.assertEqual(len(history_pages), 4)
            entries = [entry for page in history_pages for entry in page['entries']]
            exact_chain = [ref_payload(ref.as_version_ref()) for ref in (a4, a3, a2, a1)]
            self.assertEqual([entry['binding_ref'] for entry in entries], exact_chain)
            self.assertEqual([entry['previous_binding_ref'] for entry in entries], exact_chain[1:] + [None])
            self.assertEqual([entry['operation'] for entry in entries], ['bind', 'unbind', 'bind', 'bind'])
            self.assertEqual([entry['tombstoned'] for entry in entries], [False, True, False, False])
            r1 = {'resource_id': str(v1.resource_id), 'resource_version_id': str(v1.resource_version_id)}
            r2 = {'resource_id': str(v2.resource_id), 'resource_version_id': str(v2.resource_version_id)}
            self.assertEqual([entry['resource_ref'] for entry in entries], [r2, None, r2, r1])
            self.assertEqual(entries[3]['producer_invocation_ref'], producer1)
            self.assertEqual(entries[2]['producer_invocation_ref'], producer2)
            self.assertEqual(entries[1]['firing_ref'], unbind_receipt['firing_ref'])
            self.assertEqual(entries[0]['firing_ref'], rebind_receipt['firing_ref'])
            self.assertEqual([entry['availability'] for entry in entries], ['available', None, 'available', 'available'])
            source_cut = position_pages[0]['source_cut']
            for page in position_pages + history_pages:
                self.assertEqual(page['source_cut'], source_cut)
                self.assertEqual(page['availability_as_of'], core.event_store.max_ordinal())
                self.assertEqual(page['body_mode'], 'metadata_only')
                self.assertTrue(page['coverage']['canonical_only'])
                self.assertEqual(page['coverage']['scope'], 'task_scope_only')
                self.assertEqual(page['coverage']['has_more'], page['next_cursor'] is not None)
                self.assertLessEqual(len(page['entries']), 1)
                self.assertNotIn('private V1 business bytes', json.dumps(page))
                self.assertNotIn('private V2 business bytes', json.dumps(page))
            for entry in entries:
                self.assertEqual(entry['commit_ordinal'], core.event_store.canonical_object_publication_ordinal(
                    core.get_version(entry['binding_ref']['version_id']).version_id,
                    through_ordinal=source_cut['through_ordinal']))
                self.assertEqual(entry['entry_ref']['schema_version'], 'rpnh/collaboration/source_version_ref/v1')
                self.assertEqual(entry['entry_ref']['source_id'], provider.source_id)
                if entry['resource_ref'] is None:
                    self.assertIsNone(entry['resource_entry_ref'])
                else:
                    self.assertEqual(entry['resource_entry_ref'], {'schema_version':
                        'rpnh/collaboration/source_resource_ref/v1', 'source_id': provider.source_id,
                        'ref': entry['resource_ref']})
            # Object-version history remains scoped to V1's logical object;
            # the address chain is what legitimately connects its distinct V2.
            resource_pages = self.pages(provider, 'list', source_id=provider.source_id,
                entry_type='resource_version/v1')
            item = next(entry for page in resource_pages for entry in page['entries']
                if entry['entry_ref']['ref']['resource_version_id'] == str(v1.resource_version_id))
            status, versions = request(provider, 'versions', handle=item['handle'])
            self.assertEqual(status, 200, versions)
            self.assertFalse(versions['version_scope']['selects_current'])
            self.assertEqual([entry['entry_ref']['ref']['resource_version_id'] for entry in versions['entries']],
                [str(v1.resource_version_id)])
        print('positions: original distinct V1/V2 producers, exact bind/unbind/rebind chain and bounded history passed', flush=True)

        disable(world, [v1], 'disable:v1')
        after_disable = core.event_store.max_ordinal()
        self.assertGreater(after_disable, source_cut['through_ordinal'])
        with patch('cpn.frontend.registry_positions.PAGE_SIZE', 1), self.read_window(core, (v1, v2)):
            replayed = []
            for page in history_pages:
                status, replay = request(provider, 'position_history', handle=handle, cursor=page['page_cursor'])
                self.assertEqual(status, 200, replay)
                self.assertEqual(replay['source_cut'], source_cut)
                self.assertEqual(replay['head_binding_ref'], exact_chain[0])
                self.assertEqual(replay['availability_as_of'], after_disable)
                replayed.extend(replay['entries'])
            self.assertEqual([entry['binding_ref'] for entry in replayed], exact_chain)
            self.assertEqual([entry['availability'] for entry in replayed], ['available', None, 'available', 'disabled'])
            for page in position_pages:
                status, replay = request(provider, 'positions', source_id=provider.source_id, cursor=page['page_cursor'])
                self.assertEqual(status, 200, replay)
                self.assertEqual(replay['source_cut'], source_cut)
                self.assertEqual(replay['availability_as_of'], after_disable)
                entry, = replay['entries']
                self.assertEqual(entry['availability'], 'disabled' if entry['address']['opaque_name'] == 'documents/B' else 'available')
            self.assertTrue(all('availability_as_of' not in page['response']
                for page in provider._pages.values() if page.get('query') and page['response'] is not None))
            self.assertTrue(all('availability' not in entry
                for page in provider._pages.values() if page.get('query') and page['response'] is not None
                for entry in page['response']['entries']))
        self.assertEqual(binding_at(world, 'documents/A'), a4)
        self.assertEqual(binding_at(world, 'documents/B'), b)
        print('positions: original V1 disable reprojects old pages at one current cut; refs, writer epoch and six counts retained', flush=True)

    def test_handles_cursors_shared_budget_and_access_change_fail_closed(self):
        world = location_world(Path(self.directory.name) / 'run')
        resource = publish_locations(world)
        core = world.owner._core

        def provider(**kwargs):
            return LocalRegistryBrowserProvider(core.run_dir, catalog=core.catalog, **kwargs)

        with patch('cpn.frontend.registry_positions.PAGE_SIZE', 1), self.read_window(core, (resource,)):
            p = provider()
            status, page = request(p, 'positions', source_id=p.source_id)
            self.assertEqual(status, 200, page)
            position = page['entries'][0]
            status, wrong = request(p, 'record', handle=position['handle'])
            self.assertEqual((status, wrong), (409, {'error': 'stale_observation'}))
            self.assertEqual((p._pages, p._items), ({}, {}))

            p = provider()
            _, page = request(p, 'positions', source_id=p.source_id)
            status, wrong = request(p, 'list', source_id=p.source_id,
                entry_type='resource_version/v1', cursor=page['page_cursor'])
            self.assertEqual((status, wrong), (409, {'error': 'stale_observation'}))

            p = provider()
            _, page = request(p, 'positions', source_id=p.source_id)
            _, history = request(p, 'position_history', handle=page['entries'][0]['handle'])
            status, wrong = request(p, 'positions', source_id=p.source_id, cursor=history['page_cursor'])
            self.assertEqual((status, wrong), (409, {'error': 'stale_observation'}))

            p = provider()
            _, page = request(p, 'positions', source_id=p.source_id)
            other = provider()
            status, wrong = request(other, 'position_history', handle=page['entries'][0]['handle'])
            self.assertEqual((status, wrong), (409, {'error': 'stale_observation'}))

            p = provider()
            _, normal = request(p, 'list', source_id=p.source_id, entry_type='resource_version/v1')
            with patch('cpn.frontend.registry_positions.MAX_PAGES', len(p._pages)):
                status, limited = request(p, 'positions', source_id=p.source_id)
            self.assertEqual((status, limited), (413, {'error': 'scope_limit'}))
            self.assertEqual((p._pages, p._items), ({}, {}))

            p = provider()
            _, normal = request(p, 'list', source_id=p.source_id, entry_type='resource_version/v1')
            with patch('cpn.frontend.registry_positions.MAX_ITEMS', len(p._items)):
                status, limited = request(p, 'positions', source_id=p.source_id)
            self.assertEqual((status, limited), (413, {'error': 'scope_limit'}))

            p = provider()
            _, normal = request(p, 'list', source_id=p.source_id, entry_type='resource_version/v1')
            with patch('cpn.frontend.registry_positions.MAX_RETAINED_BYTES', p._retained_bytes):
                status, limited = request(p, 'positions', source_id=p.source_id)
            self.assertEqual((status, limited), (413, {'error': 'scope_limit'}))

            allowed = [True]

            def binding():
                if not allowed[0]:
                    raise RegistryBrowserError('access_changed')

            p = provider(binding=binding)
            _, page = request(p, 'positions', source_id=p.source_id)
            allowed[0] = False
            status, revoked = request(p, 'position_history', handle=page['entries'][0]['handle'])
            self.assertEqual((status, revoked), (403, {'error': 'access_changed'}))
            self.assertEqual((p._pages, p._items), ({}, {}))
        print('positions: cross-action/provider handles and cursors, shared page/item/byte limits, source revocation closed', flush=True)

    def test_closed_parser_and_shared_provider_do_not_infer_position_authority(self):
        self.assertEqual(parse_query('action=positions&source_id=s&cursor=c'),
            {'action': 'positions', 'source_id': 's', 'cursor': 'c'})
        self.assertEqual(parse_query('action=position_history&handle=h&cursor=c'),
            {'action': 'position_history', 'handle': 'h', 'cursor': 'c'})
        for query in ('action=positions', 'action=positions&source_id=s&address=documents/A',
                      'action=position_history&handle=h&source_id=s',
                      'action=position_history&handle=h&handle=other',
                      'action=position_history&handle=h&limit=100000'):
            with self.assertRaises(RegistryBrowserError):
                parse_query(query)
        # Negative capability check only. This deliberately has no shared read
        # grant/session machinery and is not evidence of positive shared reads.
        session = SimpleNamespace(describe=lambda: {'sources': [{'source_id': 'shared',
            'access_state': 'readable', 'capabilities': {'index_fields': {'resource_version/v1': ['summary']}}}]})
        shared = RegistryBrowserProvider(session)
        for action, params in (('positions', {'source_id': 'shared'}),
                               ('position_history', {'handle': 'unissued'})):
            status, rejected = request(shared, action, **params)
            self.assertEqual((status, rejected), (501, {'error': 'unsupported'}))
        self.assertEqual(handle_request(shared, 'POST', '/api/registry-browser?action=positions&source_id=shared').status, 405)


if __name__ == '__main__':
    unittest.main(verbosity=2)

