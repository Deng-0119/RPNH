"""Bounded non-retrying stdio transport; only the owner thread writes Registry."""
from __future__ import annotations
import argparse
import fcntl
import json
from pathlib import Path
import queue
import sys
import threading
from uuid import uuid4
from .backend import DshBackend, PROTOCOL, MAX_BYTES


def _attempt_budget(value):
    if value == 'unmetered':
        return None
    try:
        budget = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            'attempt budget must be a positive integer or unmetered') from exc
    if budget < 1 or str(budget) != value:
        raise argparse.ArgumentTypeError(
            'attempt budget must be a positive integer or unmetered')
    return budget


def _managed_bindings(path):
    try:
        with path.open(encoding='utf-8') as stream:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate JSON key')
                    result[key] = value
                return result
            document = json.load(
                stream, object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError('nonfinite JSON')))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError('managed bindings must be readable exact JSON') from exc
    if (not isinstance(document, dict)
            or set(document) != {
                'schema_version', 'tools', 'admitted_effects'}
            or document.get('schema_version') != 'rpnh/dsh_managed_bindings/v1'
            or not isinstance(document.get('tools'), dict)
            or not document['tools']
            or not isinstance(document.get('admitted_effects'), list)):
        raise ValueError('invalid managed bindings document')
    return document


def main():
    p = argparse.ArgumentParser(description='RPNH-backed DSH owner (private stdio)')
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--session', required=True)
    p.add_argument('--create', action='store_true')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--execution-path', type=Path)
    p.add_argument('--plugin-config', type=Path)
    p.add_argument('--managed-tool', action='append', default=[])
    p.add_argument('--managed-bindings', type=Path)
    p.add_argument('--attempt-budget', type=_attempt_budget, default=48)
    args = p.parse_args()
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', args.session):
        p.error('invalid session id')
    if args.plugin_config is not None and not args.plugin_config.is_absolute():
        p.error('--plugin-config must be absolute')
    if args.managed_tool and args.managed_bindings is not None:
        p.error('--managed-tool and --managed-bindings are mutually exclusive')
    if args.managed_bindings is not None and not args.managed_bindings.is_absolute():
        p.error('--managed-bindings must be absolute')
    managed_tools = {}
    for item in args.managed_tool:
        name, separator, selector = item.partition('=')
        if (not separator or not name or not selector
                or name in managed_tools):
            p.error('--managed-tool requires a unique NAME=PLUGIN/OPERATION')
        managed_tools[name] = selector
    if args.managed_bindings is not None:
        try:
            document = _managed_bindings(args.managed_bindings)
        except ValueError as exc:
            p.error(str(exc))
        managed_tools = document['tools']
        admitted_effects = tuple(document['admitted_effects'])
    else:
        admitted_effects = ('pure',)
    if (args.plugin_config is None) != (not managed_tools):
        p.error('--plugin-config and managed tools are required together')
    if managed_tools and args.execution_path is None:
        p.error('managed tools require configured provider execution')
    session_root = args.root / args.session
    session_root.mkdir(parents=True, exist_ok=True)
    lease = open(session_root / 'dsh-owner.lock', 'a+b')
    try:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('session already has a DSH owner')
    inbox, replies = queue.Queue(), queue.Queue()
    write_lock = threading.Lock()
    backend = None
    def write(value):
        payload = json.dumps({'protocol': PROTOCOL, **value}, allow_nan=False, separators=(',', ':')).encode()
        if len(payload) > MAX_BYTES:
            raise ValueError('outbound frame exceeds size limit')
        with write_lock:
            sys.stdout.buffer.write(payload + b'\n')
            sys.stdout.buffer.flush()
    def reader():
        try:
            while True:
                line = sys.stdin.buffer.readline(MAX_BYTES + 2)
                if not line:
                    break
                if len(line) > MAX_BYTES or not line.endswith(b'\n'):
                    raise ValueError('invalid or oversized frame')
                value = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                if value.get('protocol') != PROTOCOL:
                    raise ValueError('protocol version differs')
                if value.get('kind') == 'cancel':
                    if backend is not None:
                        backend.cancel()
                elif value.get('kind') == 'effect-result':
                    replies.put(value)
                else:
                    inbox.put(value)
        except Exception as exc:
            inbox.put({'kind':'reader-error','error':str(exc)})
        finally:
            replies.put(None)
            inbox.put(None)
    def effect(request):
        correlation = uuid4().hex
        write({'kind':'effect', 'id':correlation, **request})
        result = replies.get(timeout=120)
        if result is None:
            raise RuntimeError('capability host disconnected; execution outcome unknown')
        if result.get('id') != correlation:
            raise ValueError('response correlation differs; refusing to consume another request')
        return result['result']
    backend = DshBackend(
        args.root, args.session, effect, create=args.create,
        execution_config_path=args.execution_path,
        plugin_config_path=args.plugin_config,
        managed_tools=(managed_tools if managed_tools else None),
        managed_tool_admitted_effects=admitted_effects,
        attempt_budget=args.attempt_budget,
        offline=(True if args.offline else False))
    threading.Thread(target=reader, daemon=True).start()
    write({'kind':'ready', 'session_id':args.session})
    try:
        while True:
            command = inbox.get()
            if command is None:
                break
            cid = command.get('id')
            try:
                if command.get('kind') != 'command' or not isinstance(cid, str):
                    raise ValueError('invalid command envelope')
                method = command.get('method')
                if method == 'history':
                    result = backend.history()
                elif method == 'turn':
                    result = backend.turn(command['request'])
                elif method == 'resume':
                    result = backend.resume()
                elif method == 'close':
                    if backend.history()['active']:
                        raise ValueError('active request must be stopped/drained before close')
                    write({'kind':'reply','id':cid,'result':{'closed':True}})
                    break
                else:
                    raise ValueError('unsupported managed command')
                write({'kind':'reply','id':cid,'result':result})
            except Exception as exc:
                write({'kind':'reply','id':cid,'error':{'type':type(exc).__name__, 'message':str(exc)}})
    finally:
        lease.close()

if __name__ == '__main__':
    main()
