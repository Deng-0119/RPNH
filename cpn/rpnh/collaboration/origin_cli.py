"""Thin, one-session product-origin NDJSON transport over the public reader."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import sys

from ..registry.schema_catalog import SchemaCatalog, canonical_json
from .read_host_config import open_read_host_session
from .references import (
    SOURCE_RESOURCE_REF_SCHEMA, SOURCE_VERSION_REF_SCHEMA,
    SourceQualifiedResourceRef, SourceQualifiedVersionRef,
)
from .registry_read_contracts import RegistryReadSessionError
from .schema_catalog import collaboration_schema_data
from .share_packages import strict_json


MAX_ROOT_BYTES = 64 * 1024
_RELATIONS = ('producer_execution', 'start_inputs', 'claims')
# An exception's message, cause, path, or arbitrary code is never a diagnostic.
_SAFE_CODES = frozenset({
    'INVALID_ARGUMENTS', 'INVALID_ROOT', 'INVALID_QUERY', 'UNSUPPORTED_RELATION',
    'INVALID_HOST_CONFIGURATION', 'UNSUPPORTED_HOST_PLATFORM',
    'INTEGRITY_FAILED', 'NOT_PRESENT_AT_CUT', 'ROOT_ORIGIN_UNSUPPORTED',
    'UNSUPPORTED_SETTLEMENT_SHAPE', 'LIMIT_EXCEEDED', 'ACCESS_CHANGED',
    'SOURCE_UNAVAILABLE', 'NOT_DISCLOSED', 'SESSION_EXPIRED', 'SESSION_CLOSED',
    'READ_FAILED', 'UNSUPPORTED_ENTRY_TYPE', 'UNSUPPORTED_READER_VERSION',
    'AUTHORITY_NOT_CONFIGURED', 'CURSOR_MISMATCH', 'INVALID_CUT',
    'OUTPUT_FAILED', 'INTERRUPTED',
})


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise RegistryReadSessionError('INVALID_ARGUMENTS')


def _parser():
    parser = _Parser(
        prog='rpnh origin',
        allow_abbrev=False,
        description=(
            'Read one exact product origin through a trusted read-HOST. '
            'Emit one unchanged canonical page per NDJSON line on stdout.'),
        epilog=(
            'One process owns one read session and fixed cut; continuations are '
            'internal only. No external cursor, resume file, material read, or '
            'writer access. Exit 0 means the terminal page was flushed; any '
            'nonzero exit means an incomplete stream, even if earlier lines '
            'were delivered. Stderr contains fixed safe error codes. '
            'Root JSON is strict, bounded to 64 KiB, and must be an exact '
            'source-qualified resource or operation_result/v1 reference; '
            'names, paths, latest, bare IDs, and inferred sources are rejected.'),
    )
    parser.add_argument('--read-host-config', type=Path, required=True,
                        metavar='HOST.json', help='existing owner-approved local read-HOST configuration')
    parser.add_argument('--root', type=Path, required=True, metavar='ROOT.json',
                        help='strict source-qualified reference JSON; duplicate/unknown fields rejected')
    parser.add_argument('--include', action='append', choices=_RELATIONS,
                        metavar='RELATION', help=(
                            'repeat for an explicit unique selection containing producer_execution; '
                            'default: producer_execution, start_inputs, claims'))
    parser.add_argument('--page-size', type=int, metavar='N', help=(
        'positive integer; maximum min(100, HOST maximum), default min(20, maximum)'))
    return parser


def _load_root(path):
    """Bound the file read before strict decoding and fixed-schema validation."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RegistryReadSessionError('INVALID_ROOT')
            if info.st_size > MAX_ROOT_BYTES:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                payload = stream.read(MAX_ROOT_BYTES + 1)
            if len(payload) > MAX_ROOT_BYTES:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
        finally:
            os.close(descriptor)
        document = strict_json(payload, path='origin-root')
        if type(document) is not dict:
            raise ValueError
        schemas, types, paths = collaboration_schema_data()
        catalog = SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)
        if document.get('schema_version') == SOURCE_RESOURCE_REF_SCHEMA:
            return SourceQualifiedResourceRef.from_dict(document, catalog=catalog)
        if document.get('schema_version') == SOURCE_VERSION_REF_SCHEMA:
            root = SourceQualifiedVersionRef.from_dict(document, catalog=catalog)
            if (root.ref.entity_type == 'operation_result/v1'
                    and root.ref.entity_id.kind == 'operation_result'
                    and root.ref.version_id.kind == 'operation_result_version'):
                return root
        raise ValueError
    except RegistryReadSessionError:
        raise
    except Exception:
        raise RegistryReadSessionError('INVALID_ROOT') from None


def _canonical_size(value, remaining):
    """Measure the public JSON page without constructing a second page/buffer."""
    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise RegistryReadSessionError('LIMIT_EXCEEDED')

    before = remaining
    if value is None:
        charge(4)
    elif type(value) is bool:
        charge(4 if value else 5)
    elif type(value) is int:
        charge(len(str(value)))
    elif type(value) is str:
        charge(2)
        for char in value:
            charge(2 if char in '\\"\b\f\n\r\t' else
                   6 if ord(char) < 32 or 127 <= ord(char) <= 65535 else
                   12 if ord(char) > 65535 else 1)
    elif type(value) in (list, dict):
        charge(2 + max(0, len(value) - 1))
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise RegistryReadSessionError('READ_FAILED')
                charge(_canonical_size(key, remaining))
                charge(1)
                charge(_canonical_size(item, remaining))
        else:
            for item in value:
                charge(_canonical_size(item, remaining))
    else:
        raise RegistryReadSessionError('READ_FAILED')
    return before - remaining


def _serialize_page(page, limit):
    # The page already passed the reader's response budget. Independently bound
    # this adapter's current transport allocation before canonical serialization.
    expected = _canonical_size(page, limit)
    encoded = canonical_json(page)
    if len(encoded) != expected or len(encoded) > limit:
        raise RegistryReadSessionError('LIMIT_EXCEEDED')
    return encoded


def _write_all(sink, payload):
    with memoryview(payload) as remaining:
        offset = 0
        while offset < len(remaining):
            count = sink.write(remaining[offset:])
            if type(count) is not int or not 0 < count <= len(remaining) - offset:
                raise RegistryReadSessionError('OUTPUT_FAILED')
            offset += count


def _stream(session, root, include, page_size, sink):
    cut = session.capture_cut(root.source_id)
    cursor = None
    while True:
        page = session.query_product_origin_v1(
            root, cut, include=include, page_size=page_size, cursor=cursor)
        cursor = page['continuation']
        try:
            encoded = _serialize_page(page, session.limits.max_response_bytes)
        except Exception:
            # A current authority failure wins over a pending transport error.
            session.final_recheck((root.source_id,))
            raise
        session.final_recheck((root.source_id,))
        try:
            _write_all(sink, encoded)
            _write_all(sink, b'\n')
            sink.flush()
        except Exception:
            raise RegistryReadSessionError('OUTPUT_FAILED') from None
        # Never retain delivered pages or serialized bytes across the next query.
        del encoded, page
        if cursor is None:
            return


def main(argv=None):
    """Run without exposing arbitrary exception strings through the outer CLI."""
    session = None
    try:
        args = _parser().parse_args(argv)
        if args.include is not None and (
                len(set(args.include)) != len(args.include)
                or 'producer_execution' not in args.include):
            raise RegistryReadSessionError('INVALID_QUERY')
        if args.page_size is not None:
            if args.page_size < 1:
                raise RegistryReadSessionError('INVALID_QUERY')
            if args.page_size > 100:
                raise RegistryReadSessionError('LIMIT_EXCEEDED')
        root = _load_root(args.root)
        try:
            session = open_read_host_session(args.read_host_config)
            _canonical_size(root.to_dict(), session.limits.max_query_bytes)
            # Write directly to the binary raw stream when available. There is
            # then no failed buffered write for interpreter shutdown to retry.
            binary = sys.stdout.buffer
            sink = getattr(binary, 'raw', binary)
            _stream(session, root, args.include, args.page_size, sink)
        finally:
            if session is not None:
                session.close()
        return 0
    except KeyboardInterrupt:
        code, status = 'INTERRUPTED', 130
    except Exception as exc:
        code = exc.code if isinstance(exc, RegistryReadSessionError) else 'READ_FAILED'
        if type(code) is not str or code not in _SAFE_CODES:
            code = 'READ_FAILED'
        status = 2
    try:
        print('rpnh origin: ' + code, file=sys.stderr)
    except Exception:
        pass
    return status


if __name__ == '__main__':
    raise SystemExit(main())
