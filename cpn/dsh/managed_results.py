"""Historical DSH result display helpers; no receipt read authority.

Current DSH reads ordinary registered result products through the shared
managed_output service. These legacy pages remain display-only, and the retired
receipt-backed constructor explicitly rejects use without touching a Registry.
"""
from __future__ import annotations


from cpn.components.agent_loop.managed_output import (
    _fit_page, serialized_managed_json,
)
from cpn.plugins.api import canonical


READER_NAME = 'read_managed_output'
READER_KEY = 'rpnh/dsh/read-managed-output/v1'
PAGE_KIND = 'managed_operation_output_page/v1'
VISIBLE_RESULT_BYTES = 10_000
MAX_PAGE_BYTES = 65_536
READER_IDENTITY = {'implementation_id': 'rpnh.dsh.read_managed_output',
                   'revision': 'v1'}
READER_CONTRACTS = {'binding_protocol': READER_KEY, 'effect': 'registered_read'}
READER_INPUT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['session_id', 'request_id', 'call_id', 'terminal_receipt_ref'],
    'properties': {
        'session_id': {'type': 'string', 'minLength': 1},
        'request_id': {'type': 'string', 'minLength': 1},
        'call_id': {'type': 'string', 'minLength': 1},
        'terminal_receipt_ref': {
            'type': 'object', 'additionalProperties': False,
            'required': ['resource_id', 'resource_version_id'],
            'properties': {
                'resource_id': {'type': 'string',
                                'pattern': '^resource:[a-f0-9]{32}$'},
                'resource_version_id': {'type': 'string',
                                        'pattern': '^resource_version:[a-f0-9]{32}$'},
            },
        },
        'offset_chars': {'type': 'integer', 'minimum': 0},
        'max_bytes': {'type': 'integer', 'minimum': 1,
                      'maximum': MAX_PAGE_BYTES},
    },
}
READER_PROVIDER_DECLARATION = {
    'type': 'function', 'function': {
        'name': READER_NAME,
        'description': (
            'Read a page of a previously settled managed tool result from its '
            'exact locator. Never executes the original tool. Use the locator '
            'and next_offset_chars returned in a managed_operation_output_page/v1.'),
        'parameters': READER_INPUT_SCHEMA,
    },
}


class ManagedOutputReadDenied(ValueError):
    """A requested locator is outside this session's settled result history."""


def result_locator(session_id, request_id, call_id, receipt_ref):
    return {'session_id': session_id, 'request_id': request_id,
            'call_id': call_id, 'terminal_receipt_ref': dict(receipt_ref)}


def result_page(output, locator, *, offset_chars=0,
                max_bytes=VISIBLE_RESULT_BYTES):
    """Reuse the existing managed-result page fitter, with operation identity."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_PAGE_BYTES:
        raise ManagedOutputReadDenied('managed result page budget is outside its contract')
    text = serialized_managed_json(output)
    if type(offset_chars) is not int or not 0 <= offset_chars <= len(text):
        raise ManagedOutputReadDenied('managed result offset is outside the exact output')
    try:
        return _fit_page({
            'kind': PAGE_KIND, 'reader': READER_NAME, 'locator': dict(locator),
            'content': text[offset_chars:], 'offset_chars': offset_chars,
            'next_offset_chars': None, 'total_chars': len(text), 'truncated': False,
        }, max_bytes)
    except ValueError as exc:
        raise ManagedOutputReadDenied(str(exc)) from exc


def visible_output(output, locator):
    """Keep a small historical display or a bounded display page."""
    if len(canonical(output)) <= VISIBLE_RESULT_BYTES:
        return output
    return result_page(output, locator)


def project_history_wire(state):
    """Remove repeated cumulative messages only from the detached history DTO.

    Each original committed turn still owns its full answer.  Native DSH
    history consumes its call messages; the latest cumulative message list is
    retained for compatible read clients.  This projection is not read authority.
    """
    history = state['committed_history']
    projected = []
    for index, item in enumerate(history):
        if index == len(history) - 1:
            projected.append(item)
            continue
        projected.append({**item, 'answer': {
            **{key: value for key, value in item['answer'].items()
               if key != 'messages'},
            'messages_source': {'turn_ref': dict(item['turn_ref']),
                                'field': 'answer.messages'},
        }})
    return {**state, 'history_projection': {'kind': 'dsh_history_wire/v1'},
            'committed_history': projected}


class RegisteredDshOutputReader:
    """Reject the retired receipt-backed reader instead of granting authority.

    Current DSH uses plugins.managed_output.ManagedOperationOutputReader over
    registered ordinary result products. A legacy locator is display data and
    cannot be upgraded into that reader's execution and resource authority.
    """

    def __init__(self, backend):
        raise ManagedOutputReadDenied(
            'legacy DSH receipt readers are unavailable; use the registered '
            'managed operation result reader and its original product locator')
