"""Run the fixed explicit tool PN without a provider, model or network request."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from cpn.components.execution_services import ExecutionServices
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.harness import Harness
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from .contracts import schema_id
from .host import registration

ROOT = Path(__file__).parent
MODEL_CONDITION = "tool-pipeline-offline-zero-model"


def module():
    return ModuleDeclaration.from_json((ROOT / "module.json").read_text())


def fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text())


def create_owner(run_dir, *, usage=None, tariff=None, selected_registration=None):
    declaration = module()
    values = {"usage": fixture("usage.json") if usage is None else usage,
              "tariff": fixture("tariff.json") if tariff is None else tariff}
    inputs = {key: OwnerInput(schema_id("source_" + key), canonical_json(value),
                              "Synthetic electricity " + key) for key, value in values.items()}
    return start_run(declaration, selected_registration or registration(), run_dir=Path(run_dir),
        task_input=inputs["usage"], entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(tuple(declaration.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 10, 0, 10, 0),
        model_condition=MODEL_CONDITION, owner_statement="Offline synthetic billing tool PN",
        command_id="tool-pipeline:fresh")


def make_harness(owner, loop, pool, *, max_in_flight=2):
    services = ExecutionServices(owner=owner, event_loop=loop)
    return Harness(owner=owner, event_loop=loop, prepare_dispatcher=services.prepare_dispatcher,
                   submit_operation=pool.submit, max_in_flight=max_in_flight)


def inspect_registry(core):
    """Derived read-only evidence; it never decides which operation may execute."""
    executable, structure, marking = hydrate_module_runtime(core)
    resources = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        resource = core.get_version(row["version_id"])
        if not resource.metadata.get("descriptors", {}).get("tool_key", "").startswith("example/tool_pipeline/"):
            continue
        value = json.loads(core.object_store.read_registered(resource))
        resources.append({"ref": {"resource_id": str(resource.logical_id),
                                   "resource_version_id": str(resource.version_id)},
                          "metadata": dict(resource.metadata), "value": value})
    source_resources = {}
    for item in resources:
        if item["value"]["stage"] not in {"usage_raw", "tariff_raw"}:
            continue
        for ref in item["metadata"]["reference_provenance"]["derived_from_refs"]:
            source = core.get_version(ref["version_id"])
            source_resources[ref["version_id"]] = {
                "ref": {"resource_id": str(source.logical_id), "resource_version_id": str(source.version_id)},
                "metadata": dict(source.metadata),
                "value": json.loads(core.object_store.read_registered(source))}
    firings = []
    for row in core.event_store.object_rows_by_type("transition_firing/v1"):
        record = core.event_store.ordered_firing_record(row["version_id"])
        firings.append({key: record[key] for key in (
            "firing_version_id", "state", "firing", "firing_completion", "ordered_event_records")})
    terminals = [dict(core.get_version(row["version_id"]).metadata)
                 for row in core.event_store.object_rows_by_type("run_terminal_evidence/v1")]
    # Never infer success from a JSON "validated" flag or Harness.goal_reached.
    final = None
    if terminals:
        ref = terminals[-1]["terminal_result_ref"]
        final = json.loads(core.object_store.read_registered(core.get_version(ref["version_id"])))
    return {"schema_version": "tool_pipeline/evidence/v1",
        "actual_model_call_counts": list(core.event_store.actual_model_call_counts()),
        "terminal_evidence": terminals, "final": final, "resources": resources,
        "source_resources": list(source_resources.values()), "firings": firings,
        "adopted_definition": structure.compiled.to_dict(),
        "events": [{"ordinal": event.ordinal, "event_id": str(event.event_id),
                    "type": event.event_type, "aggregate_id": event.aggregate_id,
                    "occurred_at": event.occurred_at, "payload": dict(event.payload)}
                   for event in core.event_store.list_events()],
        "checkpoint_ref": {"entity_type": marking.checkpoint_ref.entity_type,
                           "logical_id": str(marking.checkpoint_ref.entity_id),
                           "version_id": str(marking.checkpoint_ref.version_id)}}


def readback(run_dir):
    """Rebuild all business evidence from Registry, with no former runner/cache."""
    catalog = SchemaCatalog()
    registration().bind_schema_catalog(catalog)
    core = _RegistryCore(Path(run_dir), create=False, read_only=True, catalog=catalog)
    before = core.event_store.max_ordinal()
    evidence = inspect_registry(core)
    evidence["projection"] = project_registry_net(Path(run_dir), catalog=catalog)
    if core.event_store.max_ordinal() != before:
        raise AssertionError("read-only evidence export mutated Registry")
    return evidence


def run_pipeline(run_dir, *, usage=None, tariff=None, max_in_flight=2,
                 selected_registration=None, event_loop_factory=OwnerEventLoop):
    if max_in_flight not in {1, 2}:
        raise ValueError("this example supports one or two Harness workers")
    owner = create_owner(run_dir, usage=usage, tariff=tariff, selected_registration=selected_registration)
    loop = event_loop_factory(owner, Path(run_dir) / "owner.sock")
    try:
        with ThreadPoolExecutor(max_workers=max_in_flight) as pool:
            result = make_harness(owner, loop, pool, max_in_flight=max_in_flight).exact_execute()
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
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    for key, filename in (("adopted_definition", "adopted-pn.json"), ("projection", "projection.json"),
                          ("final", "report.json")):
        if evidence.get(key) is not None:
            (output / filename).write_text(json.dumps(evidence[key], indent=2, ensure_ascii=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--usage", type=Path, default=ROOT / "fixtures/usage.json")
    parser.add_argument("--tariff", type=Path, default=ROOT / "fixtures/tariff.json")
    parser.add_argument("--max-in-flight", type=int, choices=(1, 2), default=2)
    parser.add_argument("--readback", action="store_true", help="read-only export of an existing run")
    args = parser.parse_args(argv)
    if args.output_dir.exists() or (not args.readback and args.run_dir.exists()):
        parser.error("output directory, and a fresh run directory, must be absent")
    try:
        evidence = readback(args.run_dir) if args.readback else run_pipeline(args.run_dir,
            usage=json.loads(args.usage.read_text()), tariff=json.loads(args.tariff.read_text()),
            max_in_flight=args.max_in_flight)
    except OSError as exc:
        print(json.dumps({"status": "BLOCKED", "error": type(exc).__name__ + ": " + str(exc),
                          "note": "No transport fallback was used. Retain this run for diagnosis."}))
        return 2
    save_evidence(evidence, args.output_dir)
    terminal = evidence["terminal_evidence"]
    success = bool(terminal and terminal[-1]["run_outcome"] == "complete")
    print(json.dumps({"status": "PASS" if success else "REJECTED_OR_INCOMPLETE",
        "total_cny": evidence["final"]["data"]["report"]["total_cny"] if success else None,
        "actual_model_call_counts": evidence["actual_model_call_counts"],
        "output_dir": str(args.output_dir)}))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
