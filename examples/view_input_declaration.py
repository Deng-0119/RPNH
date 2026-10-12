"""Open the controlled input examples' declared or actual Registry Petri net."""
import argparse
import importlib
import json

from cpn.frontend.server import serve_projection
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.inspection import project_compiled_net


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", choices=("approval_workflow", "a2a_quote"))
    parser.add_argument("--run-dir", help="Existing original run Registry; omit for declaration only")
    parser.add_argument("--json", action="store_true", help="Print projection without opening HTTP")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    contracts = importlib.import_module("examples." + args.example + ".contracts")
    host = importlib.import_module("examples." + args.example + ".host")
    if args.run_dir:
        from cpn.frontend.dashboard import RegistryDashboard
        module = ".app" if args.example == "approval_workflow" else ".native"
        catalog = importlib.import_module("examples." + args.example + module).catalog()
        host.registration().bind_schema_catalog(catalog)
        provider = RegistryDashboard(args.run_dir, catalog=catalog)
        projection = lambda: provider.dashboard()["net"]
    else:
        compiled = compile_module(contracts.module(), host.registration())
        projection = lambda: project_compiled_net(compiled, source={"mode": "initial_configured"})
        provider = projection
    if args.json:
        print(json.dumps(projection(), ensure_ascii=False))
    else:
        serve_projection(provider, host="127.0.0.1", port=0, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
