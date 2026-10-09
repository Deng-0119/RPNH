"""Exact DSH tool-message layouts. Recognizing a revision never admits execution."""
from __future__ import annotations
from copy import deepcopy
from collections.abc import Mapping

LEGACY_DSH_REVISION = 'ddefc45fbc7f8e46dd73185e68295696d1297887'
CANDIDATE_DSH_REVISION = '5badb15009ae1756c3afe0ae0cef1faafc290ccc'


class DshCodecError(ValueError):
    """A selected message does not satisfy its explicitly named dialect."""


def dialect_for_revision(revision):
    if revision == LEGACY_DSH_REVISION:
        return 3
    if revision == CANDIDATE_DSH_REVISION:
        return 4
    raise DshCodecError('unsupported exact DSH revision')


def _nonempty(value):
    return isinstance(value, str) and bool(value)


def decode_tool_result(message, revision, *, require_identity=False,
                       require_error=False):
    dialect = dialect_for_revision(revision)
    if (not isinstance(message, Mapping)
            or not isinstance(message.get('source'), Mapping)
            or message['source'].get('kind') != 'tool'
            or not _nonempty(message['source'].get('callId'))):
        raise DshCodecError('tool result requires a tool source')
    if require_identity and not _nonempty(message.get('id')):
        raise DshCodecError('tool result requires a message identity')
    if 'id' in message and not _nonempty(message['id']):
        raise DshCodecError('invalid tool message identity')
    if dialect == 3:
        content = message.get('content')
        if (message.get('role') != 'user' or 'toolCallId' in message
                or 'isError' in message or not isinstance(content, list)
                or len(content) != 1 or not isinstance(content[0], Mapping)
                or content[0].get('type') != 'tool-result'):
            raise DshCodecError('V3 requires exactly one nested tool-result')
        body = content[0]
    else:
        if message.get('role') != 'tool':
            raise DshCodecError('V4 requires a first-class tool-role result')
        body = message
    content = body.get('content')
    if (body.get('toolCallId') != message['source']['callId']
            or not isinstance(content, list)
            or any(not isinstance(block, Mapping)
                   or not _nonempty(block.get('type'))
                   or block['type'] == 'tool-result' for block in content)):
        raise DshCodecError('tool result content or correlation differs')
    if (('isError' in body and type(body['isError']) is not bool)
            or (require_error and type(body.get('isError')) is not bool)):
        raise DshCodecError('invalid tool result error flag')
    fields = {'id', 'role', 'source', 'content'}
    if dialect == 4:
        fields |= {'toolCallId', 'isError'}
    return deepcopy({
        **({'id': message['id']} if 'id' in message else {}),
        'source': dict(message['source']), 'callId': body['toolCallId'],
        'content': content,
        **({'isError': body['isError']} if 'isError' in body else {}),
        'messageExtensions': {k: v for k, v in message.items()
                              if k not in fields},
        'resultExtensions': ({k: v for k, v in body.items()
                              if k not in {'type', 'toolCallId', 'content',
                                           'isError'}} if dialect == 3 else {}),
    })


def encode_tool_result(value, revision):
    dialect = dialect_for_revision(revision)
    source = value.get('source')
    if (not _nonempty(value.get('callId')) or not isinstance(source, Mapping)
            or source.get('kind') != 'tool'
            or source.get('callId') != value['callId']):
        raise DshCodecError('tool result identity differs')
    message_reserved = {'id', 'role', 'source', 'content', 'toolCallId', 'isError'}
    result_reserved = {'type', 'toolCallId', 'content', 'isError'}
    if dialect == 4:
        result_reserved |= {'id', 'role', 'source'}
    if (not isinstance(value.get('messageExtensions'), Mapping)
            or not isinstance(value.get('resultExtensions'), Mapping)
            or message_reserved.intersection(value['messageExtensions'])
            or result_reserved.intersection(value['resultExtensions'])):
        raise DshCodecError('tool result extension collides with a reserved field')
    base = {**value.get('messageExtensions', {}),
            **({'id': value['id']} if 'id' in value else {}), 'source': source}
    result = {**value.get('resultExtensions', {}),
              'toolCallId': value['callId'], 'content': value['content'],
              **({'isError': value['isError']} if 'isError' in value else {})}
    encoded = ({**base, 'role': 'user',
                'content': [{**result, 'type': 'tool-result'}]}
               if dialect == 3 else {**base, **result, 'role': 'tool'})
    decode_tool_result(encoded, revision, require_identity='id' in value)
    return deepcopy(encoded)


def make_tool_result(*, message_id, call_id, content, is_error, revision):
    """Build one managed observation; error boolean remains mandatory."""
    if type(is_error) is not bool:
        raise DshCodecError('managed tool results require an explicit error flag')
    return encode_tool_result({
        'id': message_id, 'source': {'kind': 'tool', 'callId': call_id},
        'callId': call_id, 'content': content, 'isError': is_error,
        'messageExtensions': {}, 'resultExtensions': {},
    }, revision)
