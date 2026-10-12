"""Observe supplied SQL through Registry/PetriNet; no solver or grader is run."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

from cpn.components.execution_services import ExecutionServices
from cpn.frontend.dashboard import RegistryDashboard
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.inspection import project_registry_net
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.run import OwnerInput, start_run
from .contracts import schema_id
from .host import registration
from .tools import PINNED_DATABASE_SHA256, sql_value

ROOT = Path(__file__).parent


def module():
    return ModuleDeclaration.from_json((ROOT / "module.json").read_text())


def catalog():
    result = SchemaCatalog()
    registration().bind_schema_catalog(result)
    return result


def database_value(path, *, condition, query_timeout_s=120):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if condition == "tb2_pinned_database" and digest != PINNED_DATABASE_SHA256:
        raise ValueError("DB hash does not match the pinned Terminal-Bench task")
    return {"path": str(path), "sha256": digest, "condition": condition,
            "query_timeout_s": query_timeout_s}


def readback(run_dir):
    selected_catalog = catalog()
    core = _RegistryCore(Path(run_dir), create=False, read_only=True, catalog=selected_catalog)
    before = core.event_store.max_ordinal()
    resources = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        resource = core.get_version(row["version_id"])
        if not resource.metadata.get("descriptors", {}).get("tool_key", "").startswith("example/query_validation/"):
            continue
        resources.append({"ref": {"resource_id": str(resource.logical_id), "resource_version_id": str(resource.version_id)},
            "metadata": dict(resource.metadata), "value": json.loads(core.object_store.read_registered(resource))})
    terminals = [dict(core.get_version(row["version_id"]).metadata)
        for row in core.event_store.object_rows_by_type("run_terminal_evidence/v1")]
    final = None
    if terminals:
        ref = terminals[-1]["terminal_result_ref"]
        final = json.loads(core.object_store.read_registered(core.get_version(ref["version_id"])))
    firings = []
    for row in core.event_store.object_rows_by_type("transition_firing/v1"):
        record = core.event_store.ordered_firing_record(row["version_id"])
        firings.append({key: record[key] for key in (
            "firing_version_id", "state", "firing", "firing_completion", "ordered_event_records")})
    final_sql = next((item for item in resources if item["value"]["stage"] == "final_sql"), None)
    dashboard = RegistryDashboard(Path(run_dir), catalog=selected_catalog).dashboard()
    if core.event_store.max_ordinal() != before:
        raise AssertionError("read-only readback changed Registry")
    return {"schema_version": "query_validation/evidence/v1", "original_verifier": "not_run", "benchmark_pass": None,
        "actual_model_call_counts": list(core.event_store.actual_model_call_counts()),
        "terminal_evidence": terminals, "final": final, "final_sql_resource": final_sql, "resources": resources,
        "firings": firings, "projection": project_registry_net(Path(run_dir), catalog=selected_catalog),
        "dashboard": dashboard, "verified_head_ordinal": before}


def run_observation(run_dir, *, database, original_sql, candidate_sql, event_loop_factory=OwnerEventLoop):
    declaration = module()
    values = {"database": database, "original_sql": sql_value(original_sql), "candidate_sql": sql_value(candidate_sql)}
    inputs = {key: OwnerInput(schema_id(key), canonical_json(value), "SQL observation " + key)
              for key, value in values.items()}
    owner = start_run(declaration, registration(), run_dir=Path(run_dir),
        task_input=inputs["database"], entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(tuple(declaration.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), 3, 0, 3, 0),
        model_condition="query-validation-zero-model", owner_statement="Read-only SQL observation; original verifier not run",
        command_id="query-validation:fresh")
    loop = event_loop_factory(owner, Path(run_dir) / "owner.sock")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            services = ExecutionServices(owner=owner, event_loop=loop)
            result = Orchestrator(owner=owner, event_loop=loop,
                prepare_dispatcher=services.prepare_dispatcher, submit_operation=pool.submit, max_in_flight=1).run()
        evidence = readback(run_dir)
        evidence["stop_reason"] = result.stop_reason
        evidence["transport"] = ("native_owner_socket" if event_loop_factory is OwnerEventLoop
                                 else "explicit_test_transport")
        return evidence
    finally:
        loop.close()


def save_evidence(evidence, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for filename, value in (("evidence.json", evidence), ("projection.json", evidence["projection"]),
                            ("dashboard.json", evidence["dashboard"]), ("report.json", evidence["final"])):
        (output / filename).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    final_sql = evidence.get("final_sql_resource")
    if final_sql is not None:
        (output / "sol.sql").write_bytes(final_sql["value"]["data"]["sql"].encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--original-sql", type=Path)
    parser.add_argument("--candidate-sql", type=Path)
    parser.add_argument("--query-timeout", type=float, default=120)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args(argv)
    if args.output_dir.exists() or (not args.readback and args.run_dir.exists()):
        parser.error("output directory and fresh run directory must be absent")
    if not args.readback and not all((args.database, args.original_sql, args.candidate_sql)):
        parser.error("provide database, original SQL and independently authored candidate SQL")
    try:
        evidence = readback(args.run_dir) if args.readback else run_observation(args.run_dir,
            database=database_value(args.database, condition="tb2_pinned_database", query_timeout_s=args.query_timeout),
            original_sql=args.original_sql.read_text(encoding="utf-8"),
            candidate_sql=args.candidate_sql.read_text(encoding="utf-8"))
        save_evidence(evidence, args.output_dir)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc), "original_verifier": "not_run"}))
        return 2
    complete = bool(evidence["terminal_evidence"] and evidence["terminal_evidence"][-1]["run_outcome"] == "complete")
    equal = complete and evidence["final"]["data"]["equivalent_to_original"]
    print(json.dumps({"status": "EQUIVALENCE_OBSERVED" if equal else "MISMATCH_OR_INCOMPLETE",
        "original_verifier": "not_run", "benchmark_pass": None, "output_dir": str(args.output_dir)}))
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
