"""Fresh native three-actor collaboration; no model, external API or fallback.

Run in the user's existing compatible environment. Every contributor has an
independent original task/principal and produces its proposal as successful stock
TEXT. The protected target Branch and owner edit gate create their own receipts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid

from cpn.frontend.server import serve_projection
from cpn.rpnh.agent_tasks import TEXT_SCHEMA
from cpn.rpnh.collaboration import (
    UserCollaborationHost, ClosedModuleAuthor,
    SourceQualifiedVersionRef, user_collaboration_schema_data)
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement
from cpn.rpnh.registry._event_store.branch_publication import branch_command_key
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.user_collaboration_source import completed_text, strict_proposal_json
from cpn.rpnh.run import OwnerInput, start_run
from examples.tool_pipeline.contracts import schema_id
from examples.tool_pipeline.run import fixture, make_orchestrator, inspect_registry
from .business import CONTRIBUTIONS
from .declarations import base_module, proposal_module
from .host import registration
from .view import open_view, contribution_snapshot
from .source_identity import source_identity


def collaboration_catalog():
    schemas, types, paths = user_collaboration_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


def start(module, inputs, run_dir, role, *, cap):
    return start_run(module, registration(), run_dir=run_dir,
        task_input=next(iter(inputs.values())), entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), cap, 0, cap, 0),
        catalog=collaboration_catalog(), model_condition="native-billing-deterministic-zero-model",
        owner_name=role, owner_statement="Controlled local synthetic billing collaboration",
        command_id="fresh:" + role)


def execute(owner, socket_path, *, workers):
    # Do not substitute pipes, direct products, socket monkeypatches or fake receipts.
    loop = OwnerEventLoop(owner, socket_path)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            result = make_orchestrator(owner, loop, pool, max_in_flight=workers).run()
        if result.stop_reason != "terminal" or owner.terminal() is None:
            raise AssertionError("native execution did not reach canonical terminal evidence")
        if owner._core.event_store.actual_model_call_counts() != (0, 0):
            raise AssertionError("unexpected model accounting")
        return result
    finally:
        loop.close()


def terminal_payload(owner):
    terminal = owner._core.get_version(owner.terminal().version_id).metadata
    result = owner._core.get_version(terminal["terminal_result_ref"]["version_id"])
    return dict(terminal), json.loads(owner._core.object_store.read_registered(result))


def build_and_execute(root):
    """Return live trusted HOST objects only for this process's official Viewer."""
    identity = source_identity()
    base = base_module()
    inputs = {kind: OwnerInput(schema_id("source_" + kind), canonical_json(fixture(kind + ".json")),
                               "Published synthetic electricity " + kind) for kind in ("usage", "tariff")}
    owner = start(base, inputs, root / "target", "owner", cap=10)
    core, gateway = owner._core, owner.schema_gateway
    source_id = "billing-owner-" + uuid.uuid4().hex
    gateway.bind_source_identity(source_id=source_id, command_id="source")
    host = UserCollaborationHost(core, gateway)
    owner_session = host.create_session(origin_core=core, principal_ref=owner.principal_ref, command_id="owner-session")
    producer = SourceQualifiedVersionRef(source_id, owner.principal_ref)
    author = ClosedModuleAuthor(gateway, owner.registration, producer)
    namespace = uuid.uuid4()
    elements = {path: "element:" + uuid.uuid5(namespace, path).hex for path in _elements(base)}
    initial = author.publish(module=base, element_ids=elements, command_id="initial-base")
    branch = gateway.create_author_branch(head_revision_ref=initial.revision.revision_ref, command_id="shared-branch")
    initial_net = hydrate_module_runtime(core)[0].net_ref
    project = host.protect(owner_session, branch_ref=branch.branch_ref, runtime_net_ref=initial_net, command_id="protect")

    contributors, proposals, reviews, candidates, branch_receipts = {}, [], [], [], []
    current = initial
    for kind in ("usage", "tariff"):
        if current.revision.revision_ref != branch.head_revision_ref:
            raise AssertionError("contributor base must be the exact currently accepted Branch head")
        accepted_payload = canonical_json(current.module.to_dict())
        text = OwnerInput(TEXT_SCHEMA, canonical_json(accepted_payload.decode()), "Exact accepted billing base")
        origin = start(proposal_module(kind), {"base": text}, root / kind, kind, cap=1)
        origin.schema_gateway.bind_source_identity(source_id="billing-" + kind + "-" + uuid.uuid4().hex, command_id="source")
        session = host.create_session(origin_core=origin._core, principal_ref=origin.principal_ref, command_id=kind + "-session")
        invitation = host.invite(owner_session, project, subject_session_ref=session.ref, role="propose", command_id="invite:" + kind)
        host.join(session, invitation, command_id="join:" + kind)
        execute(origin, root / (kind[0] + ".sock"), workers=1)
        if len(origin._core.event_store.list_events_by_type(("transition_firing_settled/v1",))) != 1:
            raise AssertionError("each independent proposal must have one native settled firing")
        _, raw_document, witness = completed_text(origin._core)
        document = strict_proposal_json(raw_document)
        candidate = author.publish(module=ModuleDeclaration.from_dict(document), element_ids=elements,
            parent_ref=current.revision.revision_ref, command_id="candidate:" + kind)
        delivery = host.ingest_completed_text(session, origin_core=origin._core, command_id="deliver:" + kind)
        proposal = host.submit_proposal(session, project, payload=document,
            base_revision_ref=current.revision.revision_ref, expected_branch_ref=branch.branch_ref,
            expected_head_ref=branch.head_revision_ref, candidate_revision_ref=candidate.revision.revision_ref,
            reason="Bound " + kind + " input per interval", command_id="propose:" + kind,
            **host.delivered_proposal_inputs(session, delivery))
        review = host.review(owner_session, proposal, disposition="approve",
            reason="Exact bounded-input change on the shared published fixture", command_id="review:" + kind)
        contributors[kind] = {"owner": origin, "session": session, "delivery": delivery, "proposal": proposal, "witness": witness}
        proposals.append(proposal); reviews.append(review); candidates.append(candidate)
        decision = host.decide(owner_session, project, operation="advance", proposal_refs=(proposal,),
            review_refs=(review,), result_revision_ref=candidate.revision.revision_ref,
            expected_branch_ref=branch.branch_ref, expected_head_ref=branch.head_revision_ref,
            command_id="accept:" + kind)
        command = "advance:" + kind
        with host.command(host.action(owner_session, decision), branch_command_key(command)):
            branch = gateway.advance_author_branch(expected_branch_version_ref=branch.branch_ref,
                expected_head_revision_ref=branch.head_revision_ref, expected_stream_head=branch.sequence,
                next_revision_ref=candidate.revision.revision_ref, command_id=command)
        branch_receipts.append(host.receipt_ref(branch_command_key(command)))
        current = candidate

    session_facts = [host.read_fact(owner_session, s.ref) for s in
                     (owner_session, *(v["session"] for v in contributors.values()))]
    if len({canonical_json(f["data"]["origin_principal_ref"]) for f in session_facts}) != 3:
        raise AssertionError("three distinct original principals are required")
    if len({canonical_json(f["data"]["origin_task_ref"]) for f in session_facts}) != 3:
        raise AssertionError("three distinct original tasks are required")
    if len({canonical_json(f["self_ref"]) for f in session_facts}) != 3:
        raise AssertionError("three distinct original sessions are required")
    if len({f["data"]["actor"] for f in session_facts}) != 3:
        raise AssertionError("three distinct original actors are required")

    if candidates[0].revision.parent_revision_refs != (initial.revision.revision_ref,):
        raise AssertionError("usage proposal must retain the original base")
    if candidates[1].revision.parent_revision_refs != (candidates[0].revision.revision_ref,):
        raise AssertionError("tariff proposal must inherit the exact accepted usage revision")
    for kind, choice in CONTRIBUTIONS.items():
        operation, = next(c for c in current.module.components if c.name == choice["component"]).operations
        if dict(operation.config) != {"max_integer": choice["max_integer"]}:
            raise AssertionError("accepted billing net lost a contributor's business bound")
    plan = prepare_replacement(owner, current.module)
    adopt = host.decide(owner_session, project, operation="adopt", proposal_refs=(proposals[-1],), review_refs=(reviews[-1],),
        result_revision_ref=current.revision.revision_ref, expected_branch_ref=branch.branch_ref,
        expected_head_ref=branch.head_revision_ref, expected_runtime_ref=plan.base_net_ref,
        accepted_receipt_ref=branch_receipts[-1], command_id="adopt-combined-billing")
    adopted = owner.control.edits.submit(plan.owner_command(), "owner-adopt",
                                        collaboration_action=host.action(owner_session, adopt))
    if adopted["status"] != "ADOPTED":
        raise AssertionError("owner did not adopt exact accepted billing net")
    if hydrate_module_runtime(core)[0].net_ref == initial_net:
        raise AssertionError("owner adoption did not replace the initial runtime net")
    execute(owner, root / "o.sock", workers=2)
    terminal, final = terminal_payload(owner)
    if terminal["run_outcome"] != "complete" or final["data"]["report"] != fixture("expected-report.json"):
        raise AssertionError("native result differs from the existing independent billing oracle")
    settled = core.event_store.list_events_by_type(("transition_firing_settled/v1",))
    if len(settled) != 10:
        raise AssertionError("complete billing PN must settle exactly ten business operations")
    return {"owner": owner, "host": host, "session": owner_session, "source_id": source_id,
        "contributors": contributors, "initial": initial, "candidates": candidates, "accepted_revision": current,
        "branch_receipts": branch_receipts, "adopt_receipt": host.receipt_ref("owner-adopt"),
        "terminal": terminal, "final": final, "session_facts": session_facts, "source_identity": identity}


def complete_business_observation(owner):
    """Extend the original filtered billing export with the two adapter products."""
    core = owner._core
    observation = inspect_registry(core)
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        resource = core.get_version(row["version_id"])
        if resource.metadata.get("descriptors", {}).get("example") != "native_billing_collaboration":
            continue
        observation["resources"].append({
            "ref": {"resource_id": str(resource.logical_id), "resource_version_id": str(resource.version_id)},
            "metadata": dict(resource.metadata), "value": json.loads(core.object_store.read_registered(resource))})
    if len(observation["resources"]) != 12:
        raise AssertionError("complete billing export must include all twelve native business products")
    return observation


def export_observations(world, output):
    """Disposable derived records, never consumed as permission or runtime facts."""
    owner, host, session = world["owner"], world["host"], world["session"]
    provider, reader = open_view(owner, host, session, world["source_id"])
    try:
        frame, contribution = contribution_snapshot(provider, owner._core)
        expected_actors = {v["session"].actor for v in world["contributors"].values()}
        observed = {c["actor"] for n in contribution["nodes"] for c in n["changes"]}
        if observed != expected_actors:
            raise AssertionError("Viewer did not derive exactly both recorded proposer contributions")
        if not any(a["role"] == "adopted" and a["actor"] == session.actor for a in contribution["actions"]):
            raise AssertionError("Viewer is missing the separate original owner adoption action")
        before = owner._core.event_store.max_ordinal()
        evidence = {"schema_version": "native_billing_collaboration/observation/v1",
            "status": "native_execution_and_contribution_read_passed_browser_capture_pending",
            "source_identity": world["source_identity"],
            "actual_model_call_counts": list(owner._core.event_store.actual_model_call_counts()),
            "sessions": world["session_facts"],
            "contributor_executions": {kind: {
                "terminal": terminal_payload(item["owner"])[0],
                "actual_model_call_counts": list(item["owner"]._core.event_store.actual_model_call_counts()),
                "settled_firings": len(item["owner"]._core.event_store.list_events_by_type(("transition_firing_settled/v1",))),
                "successful_text_witness": item["witness"]}
                for kind, item in world["contributors"].items()},
            "role_actor_map": {"owner": session.actor, **{k: v["session"].actor for k, v in world["contributors"].items()}},
            "initial_base": world["initial"].revision.to_dict(),
            "collaboration_mode": "sequential_exact_base_proposals_and_protected_advances",
            "candidates": [c.revision.to_dict() for c in world["candidates"]],
            "accepted_revision": world["accepted_revision"].revision.to_dict(),
            "branch_receipts": [host.read_fact(session, ref) for ref in world["branch_receipts"]],
            "adopt_receipt": host.read_fact(session, world["adopt_receipt"]),
            "collaboration_facts": host.read_view(session),
            "terminal": world["terminal"], "final": world["final"],
            "business": complete_business_observation(owner), "contribution_view": contribution,
            "read_cut": before, "browser_capture": "not_run"}
        if owner._core.event_store.max_ordinal() != before:
            raise AssertionError("evidence export mutated Registry")
        output.mkdir()
        for name, value in (("evidence.json", evidence), ("dashboard.json", frame),
                            ("contributions.json", contribution), ("report.json", world["final"]["data"]["report"])):
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return provider, reader, evidence
    except BaseException:
        reader.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="fresh short writable directory; private Registry evidence")
    parser.add_argument("--serve", action="store_true", help="keep this trusted HOST bound to the official Viewer")
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args(argv)
    root = args.work_dir.resolve()
    if root.exists():
        parser.error("work directory must be absent; each invocation creates new canonical identities")
    if len(str(root / "u.sock").encode()) > 100:
        parser.error("choose a shorter work directory for the native AF_UNIX socket")
    root.mkdir(parents=True)
    reader = None
    try:
        world = build_and_execute(root)
        provider, reader, evidence = export_observations(world, root / "export")
        print(json.dumps({"status": evidence["status"], "total_cny": world["final"]["data"]["report"]["total_cny"],
            "total_kwh": world["final"]["data"]["report"]["total_kwh"],
            "business_settlements": 10, "proposal_settlements": 2,
            "actual_model_call_counts": [0, 0], "export_dir": str(root / "export")}), flush=True)
        if args.serve:
            serve_projection(provider, port=args.port, open_browser=False, show_resources=True)
        return 0
    except OSError as exc:
        print(json.dumps({"status": "BLOCKED", "error": type(exc).__name__ + ": " + str(exc),
            "note": "Native transport was not replaced. Retain partial original records; do not reuse this work directory."}), flush=True)
        return 2
    finally:
        if reader is not None:
            reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
