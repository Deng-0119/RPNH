"""Stable real-process plugin fixture for exact DSH result-reader evidence.

Keep implementation separate from assertions so changing a test does not
change this registered operation's plugin identity after a saved run.
"""
from __future__ import annotations

import os

from cpn.plugins import PluginDefinition, PluginOperation


TEXT_UNIT = '片段"\\\n🙂'


def reader_payload(_context, arguments):
    if arguments.get('fail'):
        raise RuntimeError('intentional known reader-fixture failure')
    return {'label': arguments['label'],
            'text': TEXT_UNIT * arguments['count'], 'pid': os.getpid()}


def reader_plugin_definition():
    return PluginDefinition('dsh_reader_test', '1', (
        PluginOperation('payload', 'Return deterministic Unicode test content', {
            'type': 'object', 'additionalProperties': False,
            'required': ['label', 'count'],
            'properties': {
                'label': {'type': 'string'},
                'count': {'type': 'integer', 'minimum': 0, 'maximum': 200000},
                'fail': {'type': 'boolean'}, 'tag': {'type': 'number'},
            },
        }, {'type': 'object'}, reader_payload, effect='pure',
            timeout_seconds=10, max_result_bytes=16 * 1024 * 1024),
    ))

