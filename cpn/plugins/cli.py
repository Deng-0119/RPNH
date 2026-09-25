"""Native owner-facing plugin entry; no provider profile is needed for pure tools."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .api import PluginError
from .catalog import load_catalog, read_config
from .runtime import build_plugin_module, plugin_registration, run_plugin


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rpnh plugins")
    parser.add_argument("--config", type=Path, help="owner-selected plugin JSON (or RPNH_PLUGIN_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    build = sub.add_parser("build")
    build.add_argument("operation")
    run = sub.add_parser("run")
    run.add_argument("operation")
    run.add_argument("--input", type=Path, required=True, help="schema-valid JSON request file")
    run.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    catalog = load_catalog(read_config(args.config))
    if args.command == "list":
        value = {"catalog_digest": catalog.digest, "operations": catalog.describe()}
    elif args.command == "check":
        from cpn.rpnh.compiler import compile_module
        registration = plugin_registration(catalog)
        for operation in catalog.describe():
            compile_module(build_plugin_module(catalog, operation["selector"]), registration)
        value = {"valid": True, "operation_count": len(catalog.describe()), "catalog_digest": catalog.digest,
                 "note": "Configuration/compilation only; no provider or plugin operation executed."}
    elif args.command == "build":
        from cpn.rpnh.compiler import compile_module
        module = build_plugin_module(catalog, args.operation)
        compile_module(module, plugin_registration(catalog))
        value = module.to_dict()
    else:
        with args.input.open(encoding="utf-8") as source:
            arguments = json.load(source)
        value = run_plugin(catalog, args.operation, arguments, run_dir=args.run_dir.resolve())
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if args.command != "run" or value["terminal_evidence_ref"] is not None else 1
