"""Author the shipped inert v2 package with the real installed native demo SDK.

Receiver users consume native-add-v2.zip directly. This optional author tool
rebuilds exactly that package; it never launches an operation or creates a run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

from cpn.plugins.catalog import load_catalog
from cpn.plugins.host import COMPONENT_KEY, TERMINAL_KEY, schema_key
from cpn.plugins.runtime import build_plugin_module, plugin_registration
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package
from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256

ROOT = Path(__file__).resolve().parent


def build(destination: Path):
    configuration = json.loads((ROOT / "plugins.json").read_text())
    catalog = load_catalog(configuration)
    module = build_plugin_module(catalog, "demo/add")
    registration = plugin_registration(catalog)
    requirements = [
        {"requirement_id": "component", "kind": "component", "contract_id": COMPONENT_KEY,
         "required": True, "effects": [], "data_classes": []},
        {"requirement_id": "executor", "kind": "executor", "contract_id": catalog.operation_key("demo/add"),
         "required": True, "effects": [], "data_classes": []},
        {"requirement_id": "finish", "kind": "terminal", "contract_id": TERMINAL_KEY,
         "required": True, "effects": [], "data_classes": []},
    ]
    environment = {
        "schema_version": "rpnh/environment_requirements/v1", "entry_id": "main",
        "python": {"requirement_id": "python", "implementation": "cpython", "version_specifier": ">=3.11,<4"},
        "distributions": [
            {"requirement_id": "harness", "name": "rpnh-harness", "version_specifier": ">=0.1.0rc2,<1",
             "satisfies_host_requirement_ids": ["component", "executor", "finish"]},
            {"requirement_id": "demo", "name": "rpnh-native-demo", "version_specifier": "==0.3.0",
             "satisfies_host_requirement_ids": ["executor"]},
        ],
        "system_requirements": [], "tools": [], "services": [],
        "plugins": [{"requirement_id": "demo-plugin", "plugin_id": "demo", "api_contract": "rpnh/plugin/v1",
                     "version_specifier": "==0.3.0", "distribution_requirement_id": "demo",
                     "satisfies_host_requirement_ids": ["executor"]}],
    }
    payloads = {
        "declarations/main.json": canonical_bytes(module.to_dict()),
        "environment/requirements.json": canonical_bytes(environment),
        "licenses/LICENSE.txt": (ROOT / "LICENSE.txt").read_bytes(),
    }
    for index, schema_id in enumerate(module.required_schemas):
        payloads[f"schemas/schema-{index}.json"] = canonical_bytes(registration.declaration("schema", schema_id)["schema"])
    artifacts = []
    for path, payload in sorted(payloads.items()):
        role = "declaration" if path.startswith("declarations/") else "schema" if path.startswith("schemas/") else "license" if path.startswith("licenses/") else "document"
        media = "application/schema+json" if role == "schema" else "text/plain" if role == "license" else "application/json"
        artifacts.append({"path": path, "media_type": media, "bytes": len(payload), "sha256": sha256(payload),
                          "role": role, "license_id": "mit", "disclosure": "public"})
    manifest = {
        "schema_version": "rpnh/share_package/v2", "package_id": "examples/native-add", "version": "1.0.0",
        "entries": [{"entry_id": "main", "kind": "closed_module", "declaration_path": "declarations/main.json",
                     "declaration_schema": "rpnh/module_declaration/v1", "environment_requirements_path": "environment/requirements.json",
                     "input_schema_ids": [schema_key(catalog, "demo/add", "input")],
                     "output_schema_ids": [schema_key(catalog, "demo/add", "output")],
                     "completion_contract": {"success_exit": "result", "failure_exits": [],
                                             "acceptor_requirement_ids": ["finish"], "open_obligations": []}}],
        "artifacts": artifacts,
        "origin": {"repository_url": "https://github.com/Deng-0119/RPNH", "commit": None,
                   "publisher_claim": "Public RPNH native-add reuse example", "source_refs": []},
        "provenance": [{"artifact_path": row["path"], "relation": "authored", "origin_ref": None, "origin_digest": None} for row in artifacts],
        "dependencies": [],
        "compatibility": {"declaration_schemas": ["rpnh/module_declaration/v1"], "runtime_contracts": [],
                          "host_contracts": [row["contract_id"] for row in requirements]},
        "requirements": requirements, "policy_surface": [],
        "licenses": [{"license_id": "mit", "expression": "MIT", "text_paths": ["licenses/LICENSE.txt"], "notice_paths": []}],
        "disclosure": {"classification": "public", "intended_audience": "Anyone trying local package reuse",
                       "excluded_categories": ["secrets", "local_bindings", "runtime_evidence"]},
    }
    payloads["manifest.json"] = canonical_bytes(manifest)
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "native-add-v2.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(payloads.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    preview_package(archive_path)
    (destination / "native-add-v2.lock.json").write_bytes(resolve_package(archive_path).to_bytes())
    owner_input = {"schema_id": schema_key(catalog, "demo/add", "input"),
                   "payload": {"left": 2, "right": 3}, "summary": "Add the supplied integers 2 and 3"}
    owner_request = {
        "task_input": owner_input, "entry_inputs": {"request": owner_input}, "resource_inputs": {}, "inventory_input": None,
        "budgets": {"budget_buckets": module.to_dict()["budget_buckets"], "protocol_versions": ["rpnh/module_declaration/v1"],
                    "ordinary_global_cap": 1, "terminal_quota": 0, "task_total_hard_cap": 1, "finalization_budget": 0},
        "model_condition": "native-plugin-no-model", "owner_statement": "Run this selected native-add package once with the displayed inputs",
        "owner_name": "Local package example user", "command_id": "package-reuse-native-add",
    }
    (destination / "owner-request.json").write_text(json.dumps(owner_request, indent=2) + "\n")
    return archive_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="author output directory; must be absent")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output directory must be absent")
    print(build(args.output))
