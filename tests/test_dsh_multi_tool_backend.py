"""Session offline checks: real Registry/PN/tools, scripted provider and pipe wakeup.

The pipe fixture replaces unavailable AF_UNIX only. It is not native DSH, an
actual model call, nor evidence that production socket transport works here.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from multi_tool_offline_support import SessionPipeOwnerEventLoop
from cpn.dsh.backend import DshBackend, REVISION
from cpn.dsh.message_codec import make_tool_result
from cpn.llm_adapters.config import LLMExecutionSelection
from cpn.plugins import PluginDefinition, PluginOperation
from cpn.plugins.api import PluginError, canonical
from cpn.plugins.managed_batch import ManagedOperationBatchService
from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy
from cpn.plugins.managed_tools import (
    ManagedPluginInvocationReconciliationRequired,
    ManagedPluginInvocationService, ManagedWorkerCompletion,
)
from cpn.rpnh.llm_contracts import LLMInputResponseBytes, LLMInputTarget
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.operations import OperationAuthorityError
from cpn.rpnh.registry.schema_catalog import canonical_json


def batch_tool(_context, arguments):
    """Tiny pure arithmetic; marker files are isolated test instrumentation."""
    root = Path(arguments['path'])
    label = arguments['label']
    started = time.monotonic_ns()
    (root / f'{label}-start.json').write_text(json.dumps({
        'time': started, 'pid': os.getpid()}))
    try:
        if arguments.get('overlap'):
            other = 'fast' if label == 'slow' else 'slow'
            deadline = time.monotonic() + 3
            while not (root / f'{other}-start.json').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('tools did not overlap')
                time.sleep(0.005)
        time.sleep(arguments.get('delay', 0))
        if arguments.get('fail'):
            raise RuntimeError('intentional known failure')
        return {'value': arguments['value'] * 2,
                'started': started, 'finished': time.monotonic_ns(),
                'pid': os.getpid()}
    finally:
        (root / f'{label}-end.json').write_text(json.dumps({
            'time': time.monotonic_ns(), 'pid': os.getpid()}))


def batch_plugin_definition():
    operation = PluginOperation('work', 'Double a number', {
            'type': 'object', 'additionalProperties': False,
            'required': ['path', 'label', 'value'],
            'properties': {
                'path': {'type': 'string'}, 'label': {'type': 'string'},
                'value': {'type': 'integer'}, 'overlap': {'type': 'boolean'},
                'delay': {'type': 'number'}, 'fail': {'type': 'boolean'},
            },
        }, {'type': 'object'}, batch_tool, effect='pure',
            timeout_seconds=10, max_result_bytes=2048)
    return PluginDefinition('multi_tool_test', '1', (
        operation, replace(operation, name='other'),
    ))


def selection(root):
    path = root / 'local-route.json'
    path.write_text(json.dumps({
        'schema_version': 'local_process_adapter_config/v1',
        'adapter_kind': 'local_process', 'model_condition': 'offline-multi',
        'argv': [sys.executable, '-c', 'raise SystemExit(1)'],
        'probe_argv': [sys.executable, '-c', 'raise SystemExit(1)'],
        'env': {}, 'inherit_env': [],
    }))
    return LLMExecutionSelection(
        LLMInputTarget('offline-multi', 64, 65536),
        'local_process', path.resolve(), 30)


def turn_request(selected):
    profile = {
        'schema_version': 'rpnh/dsh_execution_profile/v3',
        'profile': 'offline-multi', 'selection_id': 'local/offline-multi',
        'provider': 'local-test', 'provider_display_name': 'Offline scripted',
        'model_condition': selected.input_target.model_condition,
        'adapter_kind': selected.adapter_kind,
        'transport_kind': selected.as_registry_policy()['route_provenance'][0]['transport'],
        'timeout_seconds': selected.timeout_seconds,
        'max_output_tokens': selected.input_target.max_output_tokens,
        'max_response_bytes': selected.input_target.max_response_bytes,
        'reasoning_effort': selected.reasoning_effort,
        'supported_reasoning_efforts': list(selected.supported_reasoning_efforts),
        'default_reasoning_effort': selected.default_reasoning_effort,
    }
    return {
        'session_id': 'multi', 'request_id': 'q1', 'upstream_revision': REVISION,
        'route': {'provider': 'local-test', 'model': 'offline-multi'},
        'execution_profile': profile, 'data': [],
        'policy': {'allow_request': True, 'tools': ['work']},
        'messages': [{'role': 'user', 'id': 'q1', 'source': {'kind': 'user'},
                      'content': [{'type': 'text', 'text': 'run supplied tools'}]}],
    }


class ScriptedPort:
    def __init__(self, selected, calls):
        self.execution_policy = selected.as_registry_policy()
        self.tool_calls = calls
        self.requests = []

    def request_once(self, attempt):
        value = json.loads(attempt.canonical_request_bytes)
        self.requests.append(value)
        if len(self.requests) == 1:
            response = {'protocol': 'llm_response_envelope/v1', 'text': '',
                        'tool_calls': self.tool_calls, 'finish_reason': 'tool_calls'}
        else:
            results = [message for message in value['messages']
                       if message['role'] == 'tool']
            if [row['tool_call_id'] for row in results] != [row['id'] for row in self.tool_calls]:
                raise AssertionError('provider did not receive every original call result in order')
            response = {'protocol': 'llm_response_envelope/v1', 'text': 'done',
                        'tool_calls': [], 'finish_reason': 'stop'}
        return LLMInputResponseBytes(canonical_json(response), status_code=None,
                                     external_request_id=f'offline-{len(self.requests)}')

    def close(self):
        pass


class BatchEffect:
    """Scripted protocol boundary, not the real upstream DSH implementation."""
    def __call__(self, effect):
        ticket, arguments = effect['ticket'], effect['arguments']
        if ticket['kind'] == 'model_request':
            messages = []
            for message in arguments['messages']:
                text = ''.join(block.get('text', '') for block in message['content'])
                if message['role'] == 'assistant':
                    calls = [block for block in message['content']
                             if block['type'] == 'tool-call']
                    row = {'role': 'assistant', 'content': text}
                    if calls:
                        row['tool_calls'] = [{
                            'id': call['id'], 'type': 'function',
                            'function': {'name': call['name'], 'arguments': call['arguments']},
                        } for call in calls]
                elif message['role'] == 'tool':
                    row = {'role': 'tool', 'content': text,
                           'tool_call_id': message['toolCallId']}
                else:
                    row = {'role': 'user', 'content': text}
                messages.append(row)
            value = {'provider_request': {
                'protocol': 'registered_llm/v1', 'messages': messages,
                'tools': arguments['registered_tools'],
                'tool_choice': 'auto', 'placeholders': [],
            }}
        elif ticket['kind'] == 'model_response':
            response = arguments['provider_response']
            content = [{'type': 'tool-call', **call} for call in response['tool_calls']]
            if not content:
                content = [{'type': 'text', 'text': response['text']}]
            value = {
                'message': {'id': f"a-{arguments['step']}", 'role': 'assistant',
                            'source': {'kind': 'model', **arguments['route']},
                            'content': content},
                'chunks': [], 'finish': {'kind': 'tool-calls' if response['tool_calls'] else 'stop'},
                'wire_request': {},
            }
        else:
            raise AssertionError('managed tools must not execute through DSH effect transport')
        return {'ticket': ticket, 'value': value}


class DshBatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        entry = SimpleNamespace(name='multi-tool-test', load=lambda: batch_plugin_definition)
        self.addCleanup(patch.stopall)
        patch('cpn.plugins.catalog.metadata.entry_points', lambda *, group: [entry]).start()
        patch('cpn.dsh.backend.OwnerEventLoop', SessionPipeOwnerEventLoop).start()
        self.plugins = (self.root / 'plugins.json').resolve()
        self.plugins.write_text(json.dumps({
            'schema_version': 'rpnh/plugins/v1', 'plugins': [{
                'name': 'multi_tool_test', 'entry_point': 'multi-tool-test',
                'version': '1', 'config': {}, 'environment': [],
            }],
        }))
        self.selected = selection(self.root)

    def call(self, label, value=2, **arguments):
        return {'id': label, 'name': 'work',
                'arguments': json.dumps({'label': label, 'path': str(self.root),
                                         'value': value, **arguments}, ensure_ascii=False)}

    def backend(self, calls, policy=None, *, second_tool=False):
        port = ScriptedPort(self.selected, calls)
        backend = DshBackend(
            self.root / 'sessions', 'multi', BatchEffect(), create=True,
            offline=False, selection=self.selected,
            plugin_config_path=self.plugins,
            managed_tools={'work': 'multi_tool_test/work',
                           **({'other': 'multi_tool_test/other'} if second_tool else {})},
            managed_tool_policy=policy,
            input_port_factory=lambda *_args, **_kwargs: port)
        return backend, port

    def receipts(self, *, include_unsettled=False):
        run = next((self.root / 'sessions/multi/main/attempts').iterdir())
        core = _RegistryCore(run, create=False, read_only=True)
        result = []
        rows = (core.event_store.object_rows_by_type('resource_version/v1')
                if include_unsettled else
                core.event_store.canonical_object_rows(object_type='resource_version/v1'))
        for row in rows:
            metadata = json.loads(row['metadata_json'])
            if 'managed_plugin_call' in metadata.get('descriptors', {}):
                result.append(json.loads(core.object_store.read_registered(
                    core.get_version(row['version_id']))))
        return result

    def save_evidence(self, backend, outcome, **extra):
        destination = os.environ.get('RPNH_MULTI_TOOL_EVIDENCE_DIR')
        if not destination:
            return
        root = Path(destination) / self._testMethodName
        root.mkdir(parents=True, exist_ok=True)
        run = next((self.root / 'sessions/multi/main/attempts').iterdir())
        shutil.copytree(run, root / 'registered-run', dirs_exist_ok=True)
        facts = {
            'transport': 'test pipe wakeup; original Owner dispatch; no AF_UNIX certification',
            'provider': 'scripted offline port; no real API',
            'outcome': outcome, 'receipts': self.receipts(),
            'reconciliation_receipts': self.receipts(include_unsettled=True), **extra,
        }
        from cpn.rpnh.inspection import project_registry_net
        try:
            facts['petri_projection'] = project_registry_net(run, catalog=backend._catalog())
        except Exception as exc:
            facts['projection_error'] = str(exc)
        (root / 'facts.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2))

    def test_real_parallel_tools_order_failure_and_owner_scope(self):
        calls = [self.call('slow', 2, overlap=True, delay=0.2),
                 self.call('fast', 3, overlap=True), self.call('bad', fail=True)]
        calls[1]['name'] = 'other'
        backend, port = self.backend(calls, ManagedSchedulerPolicy(max_in_flight=2),
                                     second_tool=True)
        owner_thread = threading.get_ident()
        callback_threads = []
        original_prepare, original_finish = (ManagedPluginInvocationService.prepare,
                                              ManagedPluginInvocationService.finish)

        def prepare(service, *args, **kwargs):
            callback_threads.append(('prepare', threading.get_ident()))
            return original_prepare(service, *args, **kwargs)

        def finish(service, *args, **kwargs):
            callback_threads.append(('finish', threading.get_ident()))
            return original_finish(service, *args, **kwargs)

        scope_checks = []
        def before_dispatch(_owner, execution):
            if execution.operation.firing.transition_id != 'dsh.tool':
                return
            item = execution.operation.inputs[0]
            forged = json.loads(item.artifact.payload)
            forged['pending_tools'][0]['arguments']['value'] = 999
            with self.assertRaises(OperationAuthorityError):
                replace(item, artifact=replace(item.artifact,
                                               payload=canonical_json(forged)))
            forged['pending_tools'][0]['arguments']['value'] = 9
            same_length = canonical_json(forged)
            self.assertEqual(len(same_length), len(item.artifact.payload))
            changed_item = replace(item, artifact=replace(item.artifact,
                                                         payload=same_length))
            changed_execution = replace(
                execution, operation=replace(execution.operation, inputs=(changed_item,)))
            service = ManagedOperationBatchService(
                backend.managed_tool_service, backend._project_managed_batch)
            scope = service.bind(changed_execution)
            self.assertEqual(scope.calls[0].arguments['value'], 2)
            with self.assertRaises(PluginError):
                service.prepare(replace(scope), 0)
            service.release(scope)
            scope_checks.append(True)
        backend.before_dispatch = before_dispatch
        request = turn_request(self.selected)
        request['policy']['tools'] = list(backend.managed_tool_names)
        with patch.object(ManagedPluginInvocationService, 'prepare', prepare), \
                patch.object(ManagedPluginInvocationService, 'finish', finish):
            outcome = backend.turn(request)
        self.assertEqual(outcome['status'], 'terminal')
        self.assertEqual(outcome['answer']['status'], 'completed')
        self.assertEqual(len(port.requests), 2)
        records = [row for row in outcome['answer']['calls'] if row['ticket']['kind'] == 'tool']
        self.assertEqual([row['arguments']['call']['id'] for row in records], ['slow', 'fast', 'bad'])
        self.assertEqual([row['arguments']['call']['raw_arguments'] for row in records],
                         [row['arguments'] for row in calls])
        self.assertFalse(records[0]['observation']['isError'])
        self.assertFalse(records[1]['observation']['isError'])
        self.assertTrue(records[2]['observation']['isError'])
        slow, fast = (records[index]['observation']['value'] for index in (0, 1))
        self.assertLess(fast['finished'], slow['finished'])
        self.assertLess(slow['started'], fast['finished'])
        self.assertLess(fast['started'], slow['finished'])
        self.assertNotEqual(slow['pid'], os.getpid())
        self.assertNotEqual(fast['pid'], os.getpid())
        self.assertTrue(scope_checks)
        self.assertEqual(len(callback_threads), 6)
        self.assertEqual({thread for _kind, thread in callback_threads}, {owner_thread})
        terminal = {(row['call_id'], row['state']) for row in self.receipts()}
        self.assertTrue({('slow', 'returned'), ('fast', 'returned'), ('bad', 'failed')} <= terminal)
        events = []
        for label in ('slow', 'fast', 'bad'):
            events.extend((json.loads((self.root / f'{label}-{kind}.json').read_text())['time'], delta)
                          for kind, delta in (('start', 1), ('end', -1)))
        active, peak = 0, 0
        for _when, delta in sorted(events):
            active += delta
            peak = max(peak, active)
        self.assertEqual(peak, 2)
        self.save_evidence(backend, outcome, peak_physical_workers=peak,
                           callback_threads=callback_threads,
                           provider_requests=port.requests,
                           source_checks={'changed_length_rejected': True,
                                          'same_length_uses_original_registered_bytes': True})

    def test_default_serial_keeps_both_calls_and_original_results(self):
        backend, port = self.backend([self.call('first'), self.call('second', 5)])
        outcome = backend.turn(turn_request(self.selected))
        self.assertEqual(outcome['status'], 'terminal')
        records = [row for row in outcome['answer']['calls'] if row['ticket']['kind'] == 'tool']
        self.assertEqual([row['observation']['value']['value'] for row in records], [4, 10])
        self.assertLess(records[0]['observation']['value']['finished'],
                        records[1]['observation']['value']['started'])
        self.assertEqual(len(port.requests), 2)
        self.save_evidence(backend, outcome, provider_requests=port.requests)

    def test_unknown_keeps_success_receipt_and_stops_unstarted_sibling(self):
        backend, port = self.backend([
            self.call('good', delay=0.3), self.call('unknown'), self.call('unstarted')],
            ManagedSchedulerPolicy(max_in_flight=2))
        from cpn.plugins.managed_scheduler import execute_prepared_invocation

        def lose_observation(prepared, *, cancelled):
            if json.loads(prepared.receipt_material)['call_id'] == 'unknown':
                time.sleep(0.1)
                return ManagedWorkerCompletion(error_code='worker_observation_lost',
                                               observation_lost=True)
            return execute_prepared_invocation(prepared, cancelled=cancelled)

        with patch('cpn.plugins.managed_scheduler.execute_prepared_invocation', lose_observation):
            with self.assertRaises(ManagedPluginInvocationReconciliationRequired) as raised:
                backend.turn(turn_request(self.selected))
        self.assertEqual(raised.exception.evidence['call_id'], 'unknown')
        self.assertEqual(raised.exception.evidence['outcome'], 'outcome_unknown')
        self.assertEqual(len(port.requests), 1)
        self.save_evidence(backend, {
            'raised_exception': type(raised.exception).__name__,
            'message': str(raised.exception),
            'evidence': raised.exception.evidence,
        },
            lost_observation='injected worker completion; good sibling real process',
            provider_requests=port.requests)
        terminal = {(row['call_id'], row['state'])
                    for row in self.receipts(include_unsettled=True)}
        self.assertIn(('good', 'returned'), terminal)
        self.assertIn(('unknown', 'outcome_unknown'), terminal)
        self.assertFalse(any(call == 'unstarted' for call, _state in terminal))
        self.assertEqual(self.receipts(), [])
        self.assertEqual(backend.history()['committed_history'], [])

    def test_batch_budget_and_json_escape_bound(self):
        backend, _port = self.backend([self.call('a'), self.call('b')])
        calls = []
        for index, label in enumerate(('a', 'b')):
            raw = self.call(label)
            calls.append({**raw, 'raw_arguments': raw['arguments'],
                          'arguments': json.loads(raw['arguments'])})
        state = {**turn_request(self.selected), 'calls': [], 'step': 1,
                 'pending_tools': calls}
        with patch('cpn.dsh.backend.MAX_BYTES', 90_000):
            self.assertTrue(backend._configured_batch_budget_fits(state, calls[:1]))
            self.assertFalse(backend._configured_batch_budget_fits(state, calls))
        plugin, operation = backend.managed_tools.binding('work')
        with patch.object(type(backend.managed_tools), 'binding',
                          return_value=(plugin, replace(operation, max_result_bytes=16 * 1024 * 1024))):
            self.assertTrue(backend._configured_batch_budget_fits(state, calls[:1]))
            self.assertFalse(backend._configured_batch_budget_fits(state, calls))
        for text in ('é' * 100, '\0' * 100, '\\' * 100, '😀' * 100):
            body = canonical({'text': text})
            escaped = json.dumps(body.decode('utf-8'), ensure_ascii=True).encode('utf-8')
            self.assertLessEqual(len(escaped) - 2, 3 * len(body))


if __name__ == '__main__':
    unittest.main()

