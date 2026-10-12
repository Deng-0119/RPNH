"""Run original input bytes through the existing Registry/PN with zero models."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from cpn.components.execution_services import ExecutionServices
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run

from .contracts import RULES, STEPS, schema_id
from .host import registration
from .tools import pack_blob, unpack_blob

ROOT = Path(__file__).parent
UPSTREAM = ROOT / "upstream"
INPUTS = {"source_a": ("users.json", "application/json"),
          "source_b": ("users.csv", "text/csv"),
          "source_c": ("users.parquet", "application/vnd.apache.parquet")}


def module():
    return ModuleDeclaration.from_json((ROOT / "module.json").read_text())


def input_values(data_dir):
    values = {}
    manifest = json.loads((UPSTREAM / "SOURCES.json").read_text())
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    for source, (name, media_type) in INPUTS.items():
        path = Path(data_dir) / source / name
        if path.stat().st_size > RULES["max_input_bytes"]:
            raise ValueError("input exceeds the declared byte limit")
        raw = path.read_bytes()
        relative = "multi-source-data-merger/environment/data/" + source + "/" + name
        if hashlib.sha256(raw).hexdigest() != expected[relative]:
            raise ValueError("input differs from the pinned original fixture: " + relative)
        values[source] = {"source": source,
            "upstream_url": "https://github.com/harbor-framework/terminal-bench-2/blob/" +
                RULES["upstream_commit"] + "/multi-source-data-merger/environment/data/" + source + "/" + name,
            "blob": pack_blob(raw, name, media_type)}
    return values


def create_owner(run_dir, *, data_dir):
    declaration = module()
    values = input_values(data_dir)
    inputs = {source: OwnerInput(schema_id(source + "_bytes"), canonical_json(value),
                                "Original input bytes: " + source) for source, value in values.items()}
    return start_run(declaration, registration(), run_dir=Path(run_dir),
        task_input=inputs["source_a"], entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(tuple(declaration.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), len(STEPS), 0, len(STEPS), 0),
        model_condition="multi-source-offline-zero-model",
        owner_statement="Deterministic HOST adaptation of TB2 original input bytes; not an agent benchmark",
        command_id="multi-source:fresh")


def inspect_registry(core):
    _, structure, marking = hydrate_module_runtime(core)
    resources, source_resources = [], {}
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        resource = core.get_version(row["version_id"])
        if not resource.metadata.get("descriptors", {}).get("tool_key", "").startswith(
                "example/tool_pipeline/multi_source/"):
            continue
        value = json.loads(core.object_store.read_registered(resource))
        resources.append({"ref": {"resource_id": str(resource.logical_id),
                                   "resource_version_id": str(resource.version_id)},
                          "metadata": dict(resource.metadata), "value": value})
        for source, ref in value["source_refs"].items():
            version = core.get_version(ref["resource_version_id"])
            source_resources[source] = {"ref": ref, "metadata": dict(version.metadata),
                "value": json.loads(core.object_store.read_registered(version))}
    firings = []
    for row in core.event_store.object_rows_by_type("transition_firing/v1"):
        record = core.event_store.ordered_firing_record(row["version_id"])
        firings.append({key: record[key] for key in (
            "firing_version_id", "state", "firing", "firing_completion", "ordered_event_records")})
    terminals = [dict(core.get_version(row["version_id"]).metadata)
                 for row in core.event_store.object_rows_by_type("run_terminal_evidence/v1")]
    final = None
    if terminals:
        ref = terminals[-1]["terminal_result_ref"]
        final = json.loads(core.object_store.read_registered(core.get_version(ref["version_id"])))
    return {"schema_version": "tool_pipeline/multi_source/evidence/v1",
        "actual_model_call_counts": list(core.event_store.actual_model_call_counts()),
        "terminal_evidence": terminals, "final": final, "resources": resources,
        "source_resources": source_resources, "firings": firings,
        "adopted_definition": structure.compiled.to_dict(),
        "original_verifier": {"status": "not_run", "reward": None},
        "checkpoint_ref": {"entity_type": marking.checkpoint_ref.entity_type,
                           "logical_id": str(marking.checkpoint_ref.entity_id),
                           "version_id": str(marking.checkpoint_ref.version_id)}}


def readback(run_dir):
    catalog = SchemaCatalog()
    registration().bind_schema_catalog(catalog)
    core = _RegistryCore(Path(run_dir), create=False, read_only=True, catalog=catalog)
    before = core.event_store.max_ordinal()
    evidence = inspect_registry(core)
    evidence["projection"] = project_registry_net(Path(run_dir), catalog=catalog)
    if core.event_store.max_ordinal() != before:
        raise AssertionError("read-only export mutated Registry")
    evidence["read_only_head_ordinal"] = before
    return evidence


def run_pipeline(run_dir, *, data_dir, max_in_flight=3, event_loop_factory=OwnerEventLoop):
    if max_in_flight not in (1, 2, 3):
        raise ValueError("one to three workers are supported")
    owner = create_owner(run_dir, data_dir=data_dir)
    loop = event_loop_factory(owner, Path(run_dir) / "owner.sock")
    try:
        services = ExecutionServices(owner=owner, event_loop=loop)
        with ThreadPoolExecutor(max_workers=max_in_flight) as pool:
            result = Orchestrator(owner=owner, event_loop=loop,
                prepare_dispatcher=services.prepare_dispatcher,
                submit_operation=pool.submit, max_in_flight=max_in_flight).run()
        evidence = readback(run_dir)
        evidence["stop_reason"] = result.stop_reason
        evidence["transport"] = ("native_owner_socket" if event_loop_factory is OwnerEventLoop
                                 else "explicit_test_transport")
        return evidence
    finally:
        loop.close()


def save_evidence(evidence, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    for key, name in ((None, "evidence.json"), ("projection", "projection.json"),
                      ("adopted_definition", "adopted-pn.json")):
        (output / name).write_text(json.dumps(evidence if key is None else evidence[key],
                                             indent=2, ensure_ascii=False) + "\n")
    if evidence["final"] is None:
        return []
    versions = {item["ref"]["resource_version_id"]: item for item in evidence["resources"]}
    files = []
    for name, ref in evidence["final"]["data"]["artifacts"].items():
        if name not in {"merged_users.parquet", "conflicts.json"}:
            raise ValueError("unexpected terminal artifact filename")
        item = versions[ref["resource_version_id"]]
        if item["ref"] != ref:
            raise ValueError("terminal artifact reference mismatch")
        blob = item["value"]["data"]["blob"]
        if blob["filename"] != name:
            raise ValueError("terminal filename differs from admitted artifact")
        raw = unpack_blob(blob)
        (output / name).write_bytes(raw)
        files.append({"filename": name, "sha256": hashlib.sha256(raw).hexdigest(), "ref": ref})
    (output / "artifact-references.json").write_text(json.dumps(files, indent=2) + "\n")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path,
                        default=UPSTREAM / "multi-source-data-merger/environment/data")
    parser.add_argument("--max-in-flight", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args(argv)
    if args.output_dir.exists() or (not args.readback and args.run_dir.exists()):
        parser.error("output, and fresh run directories, must be absent")
    evidence = readback(args.run_dir) if args.readback else run_pipeline(args.run_dir,
        data_dir=args.data_dir, max_in_flight=args.max_in_flight)
    files = save_evidence(evidence, args.output_dir)
    blockers = [item["value"]["data"] for item in evidence["resources"]
                if item["value"]["stage"] in {"blocked", "rejected"}]
    complete = bool(evidence["terminal_evidence"] and
                    evidence["terminal_evidence"][-1]["run_outcome"] == "complete")
    print(json.dumps({"status": "artifacts_complete_ungraded" if complete else "blocked_or_incomplete",
        "blockers": blockers, "artifacts": files, "original_verifier": "not_run",
        "actual_model_call_counts": evidence["actual_model_call_counts"]}, ensure_ascii=False))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
