"""Bounded frames for one already-produced owner reply, never execution state.

The private command correlation remains the only transport identity. Fragments
are emitted in order and have no retry, cache, Registry read, or dispatch path.
Old clients opt out and keep the original single-frame size failure.
"""
from __future__ import annotations

import json


REPLY_FRAGMENT_SCHEMA = 'rpnh/dsh-reply-fragment/v1'


def _encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      separators=(',', ':')).encode('ascii')


def encode_reply_frames(correlation, result, *, protocol, max_bytes,
                        accept_fragments=False):
    """Yield whole JSON frame bytes; consumers append their existing newline.

    Result JSON is serialized once before the first frame is yielded. Its
    characters are ASCII JSON, so nesting a fragment in a JSON string expands
    each character by at most two bytes. The complete wrapper, including the
    longest offset/null fields, is reserved before selecting a chunk size.
    """
    if not isinstance(correlation, str) or not correlation:
        raise ValueError('reply requires an original command correlation')
    if not isinstance(protocol, str) or not protocol:
        raise ValueError('reply requires its transport protocol')
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('reply frame budget must be a positive integer')
    if type(accept_fragments) is not bool:
        raise ValueError('reply fragment negotiation must be boolean')
    raw = _encode(result)
    prefix = _encode({'protocol': protocol, 'kind': 'reply',
                      'id': correlation})[:-1] + b',"result":'
    if len(prefix) + len(raw) + 1 <= max_bytes:
        yield prefix + raw + b'}'
        return
    if not accept_fragments:
        raise ValueError('outbound frame exceeds size limit')
    text = raw.decode('ascii')
    total = len(text)

    def frame(offset, end, content):
        return {'protocol': protocol, 'kind': 'reply-fragment',
                'id': correlation, 'fragment': {
                    'schema_version': REPLY_FRAGMENT_SCHEMA,
                    'offset_chars': offset, 'next_offset_chars': end,
                    'total_chars': total, 'content': content}}

    overhead = max(len(_encode(frame(total, total, ''))),
                   len(_encode(frame(total, None, ''))))
    chunk_chars = (max_bytes - overhead) // 2
    if chunk_chars < 1:
        raise ValueError('reply frame budget cannot carry one fragment character')
    for offset in range(0, total, chunk_chars):
        end = min(total, offset + chunk_chars)
        encoded = _encode(frame(offset, end if end < total else None,
                                text[offset:end]))
        if len(encoded) > max_bytes:
            raise AssertionError('reply fragment exceeded its complete-frame bound')
        yield encoded
