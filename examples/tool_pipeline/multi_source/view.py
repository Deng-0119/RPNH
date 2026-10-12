"""Open the original read-only Viewer on this example's actual Registry."""
import argparse
from cpn.frontend.dashboard import RegistryDashboard
from cpn.frontend.server import serve_projection
from cpn.rpnh.registry.schema_catalog import SchemaCatalog
from .host import registration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    catalog = SchemaCatalog()
    registration().bind_schema_catalog(catalog)
    provider = RegistryDashboard(args.run_dir, catalog=catalog)
    serve_projection(provider, host="127.0.0.1", port=0, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
