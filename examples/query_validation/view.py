"""View a real observation Registry in the unchanged official read-only Viewer."""
import argparse
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import serve_projection
from .run import catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    serve_projection(RegistryDashboard(args.run_dir, catalog=catalog()),
                     host="127.0.0.1", port=0, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
