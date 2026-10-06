"""Opt-in JSON-only local package preview and exact resolution commands."""
from __future__ import annotations

import argparse
import json
import sys

from .package_preview import preview_package
from .package_resolution import resolve_package
from .share_packages import PackageError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rpnh package", description="Inspect data-only ZIPs; never install or execute them")
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="preview one local ZIP without extraction or execution")
    preview.add_argument("archive")
    resolve = commands.add_parser("resolve", help="resolve an exact dependency lock from local ZIPs")
    resolve.add_argument("archive")
    resolve.add_argument("--local-package", action="append", default=[], metavar="ZIP")
    resolve.add_argument("--entry", default="main", metavar="ENTRY_ID")
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            value = preview_package(args.archive).to_dict()
        else:
            lock = resolve_package(args.archive, args.local_package, args.entry)
            # Emit the exact digest domain to stdout, no wrapper or extra newline.
            sys.stdout.write(lock.to_bytes().decode("ascii"))
            return 0
        print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
        return 0
    except PackageError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
