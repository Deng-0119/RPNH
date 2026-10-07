"""Portable author check: explicitly loads owner-selected trusted factories.

Run with python -I to check the installed wheel, not this directory's module.
No operation, model, Registry, HOST binding, or dependency install is performed.
"""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import sys
import tomllib
from importlib import metadata

from packaging.utils import canonicalize_name
from cpn.plugins import PluginDefinition, PluginResource, load_catalog
from cpn.plugins.catalog import inspect_plugins, read_config, read_plugin_metadata


def descriptor_diff(before, after, path=""):
    """Exact descriptor changes, without assigning compatibility judgments."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(before.keys() | after.keys())
                for change in descriptor_diff(before.get(key), after.get(key), path + "/" + key)]
    if before == after:
        return []
    return [{"path": path or "/", "before": before, "after": after}]


def check(source_root, config):
    source_root = Path(source_root).resolve()
    project = tomllib.loads((source_root / "pyproject.toml").read_text())["project"]
    source_inert = read_plugin_metadata((source_root / "rpnh_environment_plugins.json").read_text())
    selection = read_config(config)
    observation = inspect_plugins(selection)
    report = {"status": "FAIL", "inspection": observation, "source_root": str(source_root),
              "factory_loaded": "not_checked", "host_bound": "not_checked",
              "python": sys.executable, "descriptors": {}, "errors": []}
    errors = report["errors"]
    selected_eps = metadata.entry_points(group="rpnh.plugins")
    own_selections = [row for row in selection["plugins"]
                      if row["entry_point"] in project.get("entry-points", {}).get("rpnh.plugins", {})]
    if not own_selections:
        errors.append("plugins.json: no selection matches pyproject.toml rpnh.plugins entry points")
    for row in own_selections:
        matches = [ep for ep in selected_eps if ep.name == row["entry_point"]]
        if len(matches) != 1:
            errors.append("installed entry point missing/ambiguous: " + row["entry_point"])
            continue
        ep = matches[0]
        if (canonicalize_name(ep.dist.metadata["Name"]) != canonicalize_name(project["name"])
                or ep.dist.version != project["version"]):
            errors.append("pyproject.toml name/version differs from installed distribution")
        if ep.value != project["entry-points"]["rpnh.plugins"][ep.name]:
            errors.append("pyproject.toml entry point differs from installed entry_points.txt")
        installed_inert = read_plugin_metadata(ep.dist.read_text("rpnh_environment_plugins.json"))
        if installed_inert != source_inert:
            errors.append("rpnh_environment_plugins.json source differs from installed inert metadata (missing is unknown)")
        expected = {"plugin_id": row["name"], "version": row["version"],
                    "api_contract": "rpnh/plugin/v1", "entry_point": ep.name}
        if expected not in source_inert["plugins"]:
            errors.append("plugins.json identity/version differs from rpnh_environment_plugins.json")
        # This single-version sample deliberately releases its distribution and
        # PluginDefinition together. RPNH generally permits distinct versions.
        if project["version"] != row["version"]:
            errors.append("pyproject.toml version differs from selected plugin version (sample release policy)")
    if errors:
        return report
    report["factory_loaded"] = "attempted_trusted_load"
    try:
        catalog = load_catalog(selection)
    except Exception as exc:
        errors.append("PluginDefinition/config/dependency validation: " + type(exc).__name__ + ": " + str(exc))
        return report
    report["factory_loaded"] = "verified"
    for bound in catalog.plugins:
        definition = bound.definition
        assert isinstance(definition, PluginDefinition)
        assert all(isinstance(resource, PluginResource) for resource in definition.resources)
        report["descriptors"][definition.name] = definition.descriptor()
        selected = next(row for row in selection["plugins"] if row["name"] == definition.name)
        ep = next(ep for ep in selected_eps if ep.name == selected["entry_point"])
        installed_files = {Path(ep.dist.locate_file(item)).resolve() for item in ep.dist.files or ()}
        factory_module = sys.modules.get(ep.module)
        factory_file = getattr(factory_module, "__file__", None)
        if factory_file is None or Path(factory_file).resolve() not in installed_files:
            errors.append("factory import is outside installed wheel inventory; use python -I and a wheel install: " + ep.name)
        if selected in own_selections and factory_file is not None:
            # The exported sample uses a py-modules layout, including after an
            # author renames it. Do not silently validate stale installed code.
            source_file = source_root.joinpath(*ep.module.split(".")).with_suffix(".py")
            if not source_file.is_file() or source_file.read_bytes() != Path(factory_file).read_bytes():
                errors.append("source factory module differs from installed wheel; rebuild/reinstall: " + str(source_file))
        report.setdefault("loaded_sources", {})[definition.name] = {
            "factory": factory_file,
            "handlers": {op.name: inspect.getsourcefile(op.handler) for op in definition.operations},
        }
    report["catalog_digest"] = catalog.digest
    report["status"] = "FAIL" if errors else "PASS"
    report["note"] = "Trusted factory code executed; full descriptor/schema/resources validated. No operation or HOST binding. Version differences do not imply compatibility."
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="previous declaration report for exact descriptor diff")
    args = parser.parse_args(argv)
    # Refuse overwrite before loading trusted code.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            report = check(args.source_root, args.config or args.source_root / "plugins.json")
            if args.previous:
                before = json.loads(args.previous.read_text())["descriptors"]
                report["descriptor_changes"] = descriptor_diff(before, report["descriptors"])
        except (ValueError, OSError, KeyError) as exc:
            report = {"status": "FAIL", "errors": [str(exc)]}
        json.dump(report, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
