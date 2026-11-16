"""DSH exact results and sustained bounded model history, entirely offline.

Real Owner/Registry/PN and real plugin subprocesses are used. The original
registered model port is scripted, and only unavailable AF_UNIX wakeup is
replaced by the existing pipe fixture. This does not certify native sockets
or a real provider API.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from multi_tool_offline_support import SessionPipeOwnerEventLoop
from multi_tool_dsh_reader_fixture import TEXT_UNIT, reader_plugin_definition
from test_dsh_multi_tool_backend import BatchEffect, selection, turn_request
from cpn.dsh.backend import DshBackend, MAX_BYTES, REVISION, declaration, registration
from cpn.dsh.message_codec import make_tool_result
from cpn.dsh.server import _managed_bindings
from cpn.plugins.managed_output import (
    MANAGED_OPERATION_RESULTS_SCHEMA, serialized_managed_json,
)
from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.llm_contracts import LLMInputResponseBytes
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.schema_catalog import canonical_json


LOCATOR_FIELDS = ('caller_execution_ref', 'caller_firing_ref',
                  'result_resource_ref', 'terminal_receipt_ref', 'call_id')


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def tool_call(provider_call_id, name='payload', **arguments):
    return {'id': provider_call_id, 'name': name,
            'arguments': json.dumps(arguments, ensure_ascii=False,
                                    separators=(', ', ': '))}


class RecordingEffect(BatchEffect):
    def __init__(self):
        self.frames = []

    def __call__(self, effect):
        self.frames.append(effect)
        return super().__call__(effect)


class ReaderScriptedPort:
    def __init__(self, selected, case):
        self.execution_policy = selected.as_registry_policy()
        self.case = case
        self.requests = []
        self.phase = 'initial'
        self.locators = {}
        self.full_text = {}
        self.history_round = 0
        self.archive_entries = []
        self.archive_arguments = None
        self.observed_pages = []
        self.cross_run = False

    def _reply(self, calls=None, text=''):
        document = {'protocol': 'llm_response_envelope/v1', 'text': text,
                    'tool_calls': calls or [],
                    'finish_reason': 'tool_calls' if calls else 'stop'}
        return LLMInputResponseBytes(canonical_json(document), status_code=None,
                                    external_request_id=f'offline-reader-{len(self.requests)}')

    @staticmethod
    def latest_results(request):
        messages = request['messages']
        start = next((i for i in range(len(messages) - 1, -1, -1)
                      if messages[i].get('role') == 'assistant'
                      and messages[i].get('tool_calls')), None)
        return [] if start is None else [message for message in messages[start + 1:]
                                         if message.get('role') == 'tool']

    @staticmethod
    def context_capsule(request):
        for message in request['messages']:
            if message.get('role') != 'user':
                continue
            try:
                value = json.loads(message.get('content', ''))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get('kind') == 'dsh_managed_history_context/v1':
                return value
        return None

    def request_once(self, attempt):
        request = json.loads(attempt.canonical_request_bytes)
        self.requests.append(request)
        results = self.latest_results(request)
        if self.cross_run:
            if self.phase == 'cross_initial':
                self.phase = 'cross_observed'
                return self._reply([tool_call(
                    'repeat', 'read_managed_output', **self.locators['repeat'],
                    offset_chars=0, max_bytes=999999)])
            self.case.assertEqual(len(results), 1)
            self.case.assertEqual(json.loads(results[0]['content']),
                                  {'error': 'managed_result_read_denied'})
            if self.phase == 'cross_observed':
                self.phase = 'cross_again'
                return self._reply([tool_call(
                    'repeat', 'read_managed_output', **self.locators['repeat'],
                    offset_chars=1, max_bytes=999999)])
            return self._reply(text='cross-run denied without body disclosure')
        if self.phase == 'initial':
            self.phase = 'large_results'
            calls = [tool_call('repeat', label='first', count=40000, tag=1.0),
                     tool_call('second', label='second', count=60000),
                     tool_call('known-failure', label='failed', count=1, fail=True)]
            # This noncanonical spelling must survive all history projections.
            calls[0]['arguments'] = '{ "label": "first", "count": 40000, "tag": 1e0 }'
            self.original_raw_arguments = calls[0]['arguments']
            return self._reply(calls)
        if self.phase == 'large_results':
            self.case.assertEqual([item['tool_call_id'] for item in results],
                                  ['repeat', 'second', 'known-failure'])
            for row, label, count in zip(results[:2], ('first', 'second'), (40000, 60000), strict=True):
                page = json.loads(row['content'])
                self.case.assertEqual(page['kind'], 'managed_operation_output_page/v1')
                self.case.assertTrue(page['truncated'])
                self.case.assertLessEqual(len(encoded(page)), 8192)
                process = int(re.search(r'"pid":([0-9]+)', page['content']).group(1))
                self.case.assertNotEqual(process, os.getpid())
                body = serialized_managed_json({'label': label, 'pid': process,
                                                'text': TEXT_UNIT * count})
                self.case.assertEqual(page['total_chars'], len(body))
                self.case.assertEqual(page['content'], body[:page['next_offset_chars']])
                self.locators[row['tool_call_id']] = {key: page[key] for key in LOCATOR_FIELDS}
                self.full_text[row['tool_call_id']] = body
                self.observed_pages.append(page)
            self.case.assertIn('error', json.loads(results[2]['content']))
            calls = []
            for call_id, body in self.full_text.items():
                for suffix, offset in (('middle', len(body) // 2), ('tail', len(body) - 2000)):
                    calls.append(tool_call(f'{call_id}-{suffix}', 'read_managed_output',
                                           **self.locators[call_id], offset_chars=offset,
                                           max_bytes=999999))
            self.phase = 'page_reads'
            return self._reply(calls)
        if self.phase == 'page_reads':
            self.case.assertEqual(len(results), 4)
            for row in results:
                page = json.loads(row['content'])
                body = self.full_text[page['call_id']]
                start = page['offset_chars']
                self.case.assertEqual(page['content'], body[start:start + len(page['content'])])
                self.case.assertLessEqual(len(encoded(page)), 8192)
                self.case.assertEqual({key: page[key] for key in LOCATOR_FIELDS},
                                      self.locators[page['call_id']])
                self.observed_pages.append(page)
            self.phase = 'history'
        if self.phase == 'history':
            if self.history_round < 6:
                label = f'history-{self.history_round}'
                self.history_round += 1
                return self._reply([tool_call('repeat', label=label, count=1)])
            capsule = self.context_capsule(request)
            self.case.assertIsNotNone(capsule)
            self.case.assertGreater(capsule['omitted_message_count'], 0)
            self.case.assertEqual(capsule['scope'], 'current_registered_run_only')
            self.archive_arguments = dict(capsule['read_arguments'], max_bytes=999999)
            self.phase = 'archive'
            return self._reply([tool_call('directory', 'read_managed_output',
                                         **self.archive_arguments)])
        if self.phase == 'archive':
            self.case.assertEqual(len(results), 1)
            page = json.loads(results[0]['content'])
            self.case.assertEqual(page['kind'], 'managed_operation_result_archive_page/v1')
            self.case.assertLessEqual(len(encoded(page)), 8192)
            self.archive_entries.extend(page['entries'])
            if page['next_offset'] is not None:
                self.archive_arguments['offset'] = page['next_offset']
                return self._reply([tool_call('directory', 'read_managed_output',
                                             **self.archive_arguments)])
            self.case.assertEqual(page['total_count'], 9)
            old = next(entry for entry in self.archive_entries
                       if all(entry.get(key) == self.locators['repeat'][key]
                              for key in LOCATOR_FIELDS))
            self.case.assertEqual(old['call_id'], 'repeat')
            self.case.assertEqual(sum(entry['call_id'] == 'repeat'
                                      for entry in self.archive_entries), 7)
            self.phase = 'old_read'
            return self._reply([tool_call('repeat', 'read_managed_output',
                                         **{key: old[key] for key in LOCATOR_FIELDS},
                                         offset_chars=len(self.full_text['repeat']) - 2000,
                                         max_bytes=999999)])
        self.case.assertEqual(self.phase, 'old_read')
        page = json.loads(results[0]['content'])
        self.case.assertEqual(page['content'], self.full_text['repeat'][-2000:])
        self.case.assertIsNone(page['next_offset_chars'])
        self.phase = 'complete'
        return self._reply(text='reader offline verified')

    def close(self):
        pass


class DshManagedResultReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(patch.stopall)
        entry = SimpleNamespace(name='dsh-reader-test', load=lambda: reader_plugin_definition)
        patch('cpn.plugins.catalog.metadata.entry_points', lambda *, group: [entry]).start()
        patch('cpn.dsh.backend.OwnerEventLoop', SessionPipeOwnerEventLoop).start()
        self.plugins = (self.root / 'plugins.json').resolve()
        self.plugins.write_text(json.dumps({
            'schema_version': 'rpnh/plugins/v1', 'plugins': [{
                'name': 'dsh_reader_test', 'entry_point': 'dsh-reader-test',
                'version': '1', 'config': {}, 'environment': [],
            }],
        }))
        original = selection(self.root)
        self.selected = replace(original,
            input_target=replace(original.input_target,
                context_window_tokens=16384, context_compaction_retained_tokens=2048),
            runtime_policy=replace(original.runtime_policy,
                context_tool_output_byte_limit=8192))
        self.effect = RecordingEffect()
        self.port = ReaderScriptedPort(self.selected, self)
        self.backend = self._make_backend(create=True)
        self.outcomes = []
        self.continued_from = None

    def _make_backend(self, *, create):
        return DshBackend(
            self.root / 'sessions', 'multi', self.effect, create=create,
            offline=False, selection=self.selected,
            plugin_config_path=self.plugins,
            managed_tools={'payload': 'dsh_reader_test/payload'},
            managed_tool_policy=ManagedSchedulerPolicy(max_in_flight=2),
            managed_result_reader=True, attempt_budget=None,
            input_port_factory=lambda *_args, **_kwargs: self.port)

    def tearDown(self):
        destination = os.environ.get('RPNH_MULTI_TOOL_EVIDENCE_DIR')
        attempts = self.root / 'sessions/multi/main/attempts'
        if not destination or not attempts.exists() or not list(attempts.iterdir()):
            return
        parent = Path(destination) / self._testMethodName
        parent.mkdir(parents=True, exist_ok=True)
        output = parent / f'attempt-{len(list(parent.iterdir())) + 1:03d}'
        output.mkdir()
        shutil.copytree(self.root / 'sessions/multi', output / 'registered-session')
        try:
            history = self.backend.history()
        except Exception as exc:
            history = {'read_error': f'{type(exc).__name__}: {exc}'}
        (output / 'actual-history.json').write_bytes(canonical_json(history))
        (output / 'actual-provider-requests.json').write_bytes(canonical_json(self.port.requests))
        (output / 'actual-effect-frames.json').write_bytes(canonical_json(self.effect.frames))
        (output / 'facts.json').write_bytes(canonical_json({
            'transport': 'original Owner dispatch; pipe wakeup replaces unavailable AF_UNIX',
            'provider': 'offline scripted registered model port; no real API',
            'scope': 'same-run exact product reader; prior-run body access denied',
            'continued_from_exact_session': self.continued_from,
            'outcomes': self.outcomes,
            'observed_pages': self.port.observed_pages,
            'archive_entries': self.port.archive_entries,
        }))

    def request(self, request_id='q1'):
        request = turn_request(self.selected)
        request['request_id'] = request_id
        request['messages'][0]['id'] = request_id
        request['policy']['tools'] = list(self.backend.managed_tool_names)
        return request

    def test_large_results_exact_read_archive_history_and_cross_run_denial(self):
        saved = os.environ.get('RPNH_DSH_READER_CONTINUE_FROM')
        if saved:
            # Revalidate every first-turn assertion from an already terminal
            # exact Registry, then execute the second main turn normally. This
            # optional session-only continuation avoids rerunning physical
            # tools when an assertion-only bug interrupted the test report.
            source = Path(saved)
            facts = json.loads((source / 'facts.json').read_text())
            first = facts['outcomes'][0]
            self.assertEqual(first['status'], 'terminal')
            shutil.rmtree(self.root / 'sessions/multi')
            shutil.copytree(source / 'registered-session', self.root / 'sessions/multi')
            self.backend = self._make_backend(create=False)
            history = self.backend.history()
            self.assertIsNone(history['active'])
            self.assertEqual(history['committed_history'][-1]['answer'], first['answer'])
            self.effect.frames = json.loads((source / 'actual-effect-frames.json').read_text())
            self.port.requests = json.loads((source / 'actual-provider-requests.json').read_text())
            self.port.observed_pages = facts['observed_pages']
            self.port.archive_entries = facts['archive_entries']
            self.port.locators = {page['call_id']: {key: page[key] for key in LOCATOR_FIELDS}
                                  for page in facts['observed_pages']}
            original = next(row for row in first['answer']['calls']
                            if row['ticket']['kind'] == 'tool')
            self.port.original_raw_arguments = original['arguments']['call']['raw_arguments']
            self.port.phase = 'complete'
            self.continued_from = str(source.resolve())
        else:
            first = self.backend.turn(self.request())
        self.outcomes.append(first)
        self.assertEqual(first['status'], 'terminal')
        self.assertEqual(first['answer']['text'], 'reader offline verified')
        self.assertEqual(self.port.phase, 'complete')
        calls = first['answer']['calls']
        models = [item for item in calls if item['ticket']['kind'] == 'model']
        tools = [item for item in calls if item['ticket']['kind'] == 'tool']
        self.assertGreaterEqual(len(models), 11)
        self.assertEqual(tools[0]['arguments']['call']['raw_arguments'], self.port.original_raw_arguments)
        self.assertEqual(tools[0]['arguments']['call']['arguments']['tag'], 1.0)
        self.assertTrue(all(set(item['arguments']) == {'route', 'step', 'request_input_ref'}
                            for item in models))
        self.assertTrue(all(set(item['observation']) == {'message', 'finish'} for item in models))
        self.assertGreaterEqual(sum(item['arguments']['call']['id'] == 'repeat'
                                    for item in tools), 8)
        model_frames = [frame for frame in self.effect.frames
                        if frame['ticket']['kind'] == 'model_request']
        self.assertTrue(all(len(encoded(frame)) < MAX_BYTES for frame in self.effect.frames))
        self.assertLess(len(encoded(model_frames[-1]['arguments']['messages'])),
                        len(encoded(first['answer']['messages'])))
        run = next((self.root / 'sessions/multi/main/attempts').iterdir())
        core = _RegistryCore(run, create=False, read_only=True, catalog=self.backend._catalog())
        products = []
        receipts = []
        for row in core.event_store.canonical_object_rows(object_type='resource_version/v1'):
            metadata = json.loads(row['metadata_json'])
            if metadata.get('content_schema_ref') == MANAGED_OPERATION_RESULTS_SCHEMA:
                products.append(json.loads(core.object_store.read_registered(core.get_version(row['version_id']))))
            if 'managed_plugin_call' in metadata.get('descriptors', {}):
                receipts.append(json.loads(core.object_store.read_registered(core.get_version(row['version_id']))))
        entries = [entry for product in products for entry in product['entries']]
        self.assertEqual(len(entries), 9)
        self.assertEqual(len([row for row in receipts if row['state'] == 'returned']), 8)
        self.assertEqual(len([row for row in receipts if row['state'] == 'failed']), 1)
        self.assertGreater(len(encoded(products[0])), 2 * 1024 * 1024)
        self.assertEqual(entries[0]['call']['raw_arguments'], self.port.original_raw_arguments)
        prior_messages = first['answer']['messages']
        self.port.cross_run = True
        self.port.phase = 'cross_initial'
        before = len(self.effect.frames)
        second = self.backend.turn(self.request('q2'))
        self.outcomes.append(second)
        self.assertEqual(second['status'], 'terminal')
        self.assertEqual(second['answer']['text'], 'cross-run denied without body disclosure')
        self.assertEqual(second['answer']['history_prefix_length'], len(prior_messages))
        second_tools = [row for row in second['answer']['calls'] if row['ticket']['kind'] == 'tool']
        self.assertEqual([row['arguments']['call']['id'] for row in second_tools], ['repeat', 'repeat'])
        self.assertEqual(len({row['observation']['message']['id'] for row in second_tools}), 2)
        second_model_frames = [frame for frame in self.effect.frames[before:]
                               if frame['ticket']['kind'] == 'model_request']
        for frame in second_model_frames:
            selected = frame['arguments']['messages']
            without_capsule = [message for message in selected
                               if message.get('id') not in {
                                   'dsh-managed-result-archive', 'dsh-managed-result-import'}]
            self.assertEqual(without_capsule[:len(prior_messages)], prior_messages)
        self.assertEqual(len(self.backend.history()['committed_history']), 2)

    def test_projection_retains_prefix_and_complete_batches_without_call_limit(self):
        request = self.request()
        messages = list(request['messages'])
        fake = '0' * 32
        archive = {'kind': 'managed_operation_result_archive/v1',
                   'reader': 'read_managed_output',
                   'archive_firing_ref': {'entity_type': 'transition_firing/v1',
                       'logical_id': 'transition_firing:' + fake,
                       'version_id': 'transition_firing_version:' + fake},
                   'result_resource_ref': {'resource_id': 'resource:' + fake,
                       'resource_version_id': 'resource_version:' + fake}}
        for step in range(180):
            call = {'type': 'tool-call', 'id': 'repeat', 'name': 'payload',
                    'arguments': '{ "label": "prior", "count": 1 }'}
            messages.extend([{'id': f'm{step}', 'role': 'assistant',
                'source': {'kind': 'model', **request['route']}, 'content': [call]},
                make_tool_result(message_id=f't{step}', call_id='repeat',
                    content=[{'type': 'text', 'text': 'x' * 2000}],
                    is_error=False, revision=REVISION)])
        state = {**request, 'messages': messages, 'step': 180,
                 'calls': [{'retained_original_record': 'x' * 10000}] * 2500,
                 'managed_result_archive': archive, 'history_prefix_length': 5}
        before = encoded(state)
        projected = self.backend._model_messages(state)
        self.assertEqual(encoded(state), before)
        self.assertEqual(projected[1:6], messages[:5])
        self.assertEqual(projected[-2:], messages[-2:])
        self.assertLess(len(projected), len(messages))
        calls = ({'id': 'repeat', 'name': 'payload', 'arguments': {'label': 'next', 'count': 1}},)
        self.assertTrue(self.backend._configured_batch_budget_fits(state, calls))
        huge_prefix = {'id': 'huge-prior', 'role': 'user', 'source': {'kind': 'user'},
                       'content': [{'type': 'text', 'text': 'x' * (MAX_BYTES // 8)}]}
        with self.assertRaisesRegex(ValueError, 'complete tool batch exceed'):
            self.backend._model_messages({**state, 'messages': [huge_prefix, *messages[-2:]],
                                          'history_prefix_length': 1})

    def test_reader_is_explicit_and_configured_snapshot_owns_limits(self):
        path = self.root / 'bindings.json'
        base = {'schema_version': 'rpnh/dsh_managed_bindings/v1',
                'tools': {'payload': 'dsh_reader_test/payload'}, 'admitted_effects': ['pure']}
        path.write_text(json.dumps({**base, 'managed_result_reader': True}))
        self.assertTrue(_managed_bindings(path)['managed_result_reader'])
        path.write_text(json.dumps({**base, 'managed_result_reader': 1}))
        with self.assertRaises(ValueError):
            _managed_bindings(path)
        net = compile_module(declaration(
            configured=True, managed_tools=self.backend.managed_tools,
            managed_result_reader=True, managed_result_output_bytes=4096,
            managed_history_retained_tokens=1234),
            registration(managed_tools=self.backend.managed_tools,
                         managed_result_reader=True))
        self.backend._select_registered_result_reader(SimpleNamespace(compiled=net))
        self.assertEqual(self.backend.managed_result_output_bytes, 4096)
        self.assertEqual(self.backend.managed_history_retained_tokens, 1234)
        operation = next(op for op in net.operations if op.declaration.name == 'dsh.tool')
        self.assertNotIn('interrupted', [outcome.name for outcome in operation.declaration.outcomes])
        self.assertEqual(len(operation.declaration.outcomes[0].products), 2)

    def test_known_failure_code_is_bounded_without_changing_original_evidence(self):
        fake = '0' * 32
        locator = {
            'caller_execution_ref': {'entity_type': 'operation_execution_lease/v1',
                'logical_id': 'operation_execution_lease:' + fake,
                'version_id': 'operation_execution_lease_version:' + fake},
            'caller_firing_ref': {'entity_type': 'transition_firing/v1',
                'logical_id': 'transition_firing:' + fake,
                'version_id': 'transition_firing_version:' + fake},
            'result_resource_ref': {'resource_id': 'resource:' + fake,
                'resource_version_id': 'resource_version:' + fake},
            'terminal_receipt_ref': {'resource_id': 'resource:' + fake,
                'resource_version_id': 'resource_version:' + fake},
            'call_id': 'repeat',
        }
        code = ('\\\"片段🙂' * 10000)
        call = {'id': 'repeat', 'name': 'payload', 'arguments': {}}
        evidence = {'outcome': 'failed', 'error': {'code': code},
                    'terminal_receipt_ref': locator['terminal_receipt_ref']}
        observation = {'value': {'error': code}, 'isError': True,
                       'managed_result': evidence}
        before = encoded(observation)
        entry = {'output': observation['value'], 'managed_result': evidence}
        projected = self.backend._bounded_result_observation(call, entry, locator, observation)
        self.assertEqual(encoded(observation), before)
        self.assertEqual(projected['managed_result'], evidence)
        self.assertTrue(projected['isError'])
        self.assertEqual(projected['value']['outcome'], 'failed')
        self.assertFalse(projected['value']['error_code_inline'])
        self.assertIsNone(projected['value']['reader'])
        self.assertLessEqual(len(encoded(projected['value'])), self.backend.managed_result_output_bytes)
        self.assertLessEqual(len(encoded(projected['message']['content'])),
                             3 * self.backend.managed_result_output_bytes)
        small = {**observation, 'value': {'error': 'handler_failed'}}
        self.assertEqual(self.backend._bounded_result_observation(
            call, entry, locator, small)['value'], small['value'])


if __name__ == '__main__':
    unittest.main()

