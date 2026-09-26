"""Common companion CLI; selection is separate from the shared display."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Callable

from cpn.frontend.server import handle_request, serve_projection
from ._registry import RegistryViewBinding


def run_cli(host: str, binder: Callable[..., RegistryViewBinding], argv=None) -> int:
    parser = argparse.ArgumentParser(description=f'Read an existing RPNH {host} Registry; never start or resume it.')
    parser.add_argument('--root', type=Path, required=True)
    identity = 'thread-id' if host == 'codex' else 'session-id'
    parser.add_argument('--' + identity, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--turn', default='latest', help='active, latest or positive ordinal')
    selection.add_argument('--task-id' if host == 'codex' else '--request-id')
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--describe', action='store_true', help='Print the exact pinned binding without starting HTTP')
    output.add_argument('--json', action='store_true', help='Print the standard v1 projection without starting HTTP')
    output.add_argument('--view', action='store_true', help='Run the shared blocking, loopback-only viewer')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--show-resources', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--max-checkpoints', type=int, default=2048)
    parser.add_argument('--max-firings', type=int, default=2000)
    parser.add_argument('--presentation', type=Path, help='Optional exact-topology display metadata; no execution changes')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    kwargs = {'turn': args.turn}
    kwargs['task_id' if host == 'codex' else 'request_id'] = getattr(args, 'task_id' if host == 'codex' else 'request_id')
    try:
        binding = binder(args.root, getattr(args, identity.replace('-', '_')), **kwargs)
        if args.describe:
            print(json.dumps(binding.describe(), ensure_ascii=False, indent=2))
            return 0
        if args.json:
            response = handle_request(binding, 'GET', '/api/v1/net')
            if response.status != 200:
                raise ValueError(f'projection failed with HTTP status {response.status}')
            print(response.body.decode('utf-8'))
            return 0
        from cpn.frontend.dashboard import RegistryDashboard, load_presentation
        provider = RegistryDashboard(binding.run_dir, catalog=binding.catalog,
                                     binding=binding,
                                     presentation=load_presentation(args.presentation),
                                     max_checkpoints=args.max_checkpoints,
                                     max_firings=args.max_firings)
        return serve_projection(provider, port=args.port, open_browser=not args.no_open,
                                show_resources=args.show_resources)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        parser.exit(2, f'PN view binding refused: {error}\n')
