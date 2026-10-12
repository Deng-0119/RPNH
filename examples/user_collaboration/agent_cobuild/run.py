"""Two controlled users co-build and adopt a real native two-agent protocol task.

No external model or service is contacted. Source authors and the local-process
model are deterministic programs; their evidence does not claim cognition.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid

from cpn.components.agent_loop.optional_host_bindings import make_optional_agent_host_bindings
from cpn.components.execution_services import ExecutionServices
from cpn.frontend.server import serve_projection
from cpn.llm_adapters import build_llm_input_port, load_llm_execution_selection
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.agent_tasks import TEXT_SCHEMA, _execution_route
from cpn.rpnh.collaboration import UserCollaborationHost, ClosedModuleAuthor, SourceQualifiedVersionRef
from cpn.rpnh.collaboration.materials import _elements
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.module import ModuleDeclaration
from cpn.rpnh.net_operations import prepare_replacement
from cpn.rpnh.registry._event_store.branch_publication import branch_command_key
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.user_collaboration_source import completed_text, strict_proposal_json
from cpn.rpnh.run import OwnerInput, start_run
from .contracts import module, contribution_document
from .host import registration, catalog, proposal_module
from .profile import write_profile
from .view import open_view, contribution_snapshot


def start(module, inputs, run_dir, role, *, cap, selection=None):
    bindings = None if selection is None else make_optional_agent_host_bindings(
        selection.input_target, workspace_policy=selection.runtime_policy.workspace,
        provider_backend_config=_execution_route(selection),
        transport_contract={"interaction_protocol_ref": "llm_request_envelope/v1",
                            "response_adapter_ref": "llm_response_envelope/v1"})
    return start_run(module, registration(), run_dir=run_dir,
        task_input=next(iter(inputs.values())), entry_inputs=inputs,
        budgets=ModuleBudgetDeclaration(tuple(module.to_dict()["budget_buckets"]),
            ("rpnh/module_declaration/v1",), cap, 0, cap, 0), catalog=catalog(),
        model_condition="agent-cobuild-deterministic-source" if selection is None else selection.input_target.model_condition,
        host_execution_bindings=bindings,
        owner_name=role, owner_statement="Controlled local agent contract collaboration; scripted protocol only",
        command_id="fresh:" + role)


def execute(owner, socket_path, *, workers, selection=None):
    loop = OwnerEventLoop(owner, socket_path)
    port = None
    try:
        if selection is not None:
            port = build_llm_input_port(selection, destination_run_root=owner._core.run_dir)
        services = ExecutionServices(owner=owner, event_loop=loop, llm_input_port=port)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            result = Orchestrator(owner=owner, event_loop=loop,
                prepare_dispatcher=services.prepare_dispatcher, submit_operation=pool.submit,
                max_in_flight=workers).run()
        if result.stop_reason != "terminal" or owner.terminal() is None:
            raise AssertionError("native execution did not reach terminal evidence: " + result.stop_reason)
        if selection is None and owner._core.event_store.actual_model_call_counts() != (0, 0):
            raise AssertionError("source program unexpectedly requested a model")
        return result
    finally:
        if port is not None: port.close()
        loop.close()


def terminal_payload(owner):
    terminal = owner._core.get_version(owner.terminal().version_id).metadata
    result = owner._core.get_version(terminal["terminal_result_ref"]["version_id"])
    return dict(terminal), json.loads(owner._core.object_store.read_registered(result))

def build_and_execute(root, selection, inputs_document):
    """Return live trusted HOST objects only for this process's official Viewer."""
    base = module()
    request = OwnerInput(TEXT_SCHEMA, canonical_json(canonical_json(inputs_document).decode()), "Synthetic task records")
    owner = start(base, {"request": request}, root / "target", "owner", cap=8, selection=selection)
    core, gateway = owner._core, owner.schema_gateway
    source_id = "agent-cobuild-owner-" + uuid.uuid4().hex
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
    for kind in ("curator", "auditor"):
        if current.revision.revision_ref != branch.head_revision_ref:
            raise AssertionError("contributor base must be the exact currently accepted Branch head")
        accepted_payload = canonical_json(current.module.to_dict())
        text = OwnerInput(TEXT_SCHEMA, canonical_json(accepted_payload.decode()), "Exact accepted agent-cobuild base")
        origin = start(proposal_module(kind), {"base": text}, root / kind, kind, cap=1)
        origin.schema_gateway.bind_source_identity(source_id="agent-cobuild-" + kind + "-" + uuid.uuid4().hex, command_id="source")
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
            reason="Strengthen " + kind + " task contract", command_id="propose:" + kind,
            **host.delivered_proposal_inputs(session, delivery))
        review = host.review(owner_session, proposal, disposition="approve",
            reason="Exact agent instruction/output contract change; no added tool authority", command_id="review:" + kind)
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
        raise AssertionError("curator proposal must retain the original base")
    if candidates[1].revision.parent_revision_refs != (candidates[0].revision.revision_ref,):
        raise AssertionError("auditor proposal must inherit the exact accepted curator revision")
    expected = contribution_document(contribution_document(base.to_dict(), "curator"), "auditor")
    if current.module.to_dict() != expected:
        raise AssertionError("adopted module must retain both exact contract contributions")
    plan = prepare_replacement(owner, current.module)
    adopt = host.decide(owner_session, project, operation="adopt", proposal_refs=(proposals[-1],), review_refs=(reviews[-1],),
        result_revision_ref=current.revision.revision_ref, expected_branch_ref=branch.branch_ref,
        expected_head_ref=branch.head_revision_ref, expected_runtime_ref=plan.base_net_ref,
        accepted_receipt_ref=branch_receipts[-1], command_id="adopt-combined-agent-cobuild")
    adopted = owner.control.edits.submit(plan.owner_command(), "owner-adopt",
                                        collaboration_action=host.action(owner_session, adopt))
    if adopted["status"] != "ADOPTED":
        raise AssertionError("owner did not adopt exact accepted agent-cobuild net")
    if hydrate_module_runtime(core)[0].net_ref == initial_net:
        raise AssertionError("owner adoption did not replace the initial runtime net")
    execute(owner, root / "o.sock", workers=1, selection=selection)
    terminal, final = terminal_payload(owner)
    if terminal["run_outcome"] != "complete":
        raise AssertionError("target task has no complete terminal")
    settled = core.event_store.list_events_by_type(("transition_firing_settled/v1",))
    if len(settled) != 2:
        raise AssertionError("target must settle two real agent nodes")
    return {"owner": owner, "host": host, "session": owner_session, "source_id": source_id,
        "contributors": contributors, "initial": initial, "candidates": candidates, "accepted_revision": current,
        "branch_receipts": branch_receipts, "adopt_receipt": host.receipt_ref("owner-adopt"),
        "terminal": terminal, "final": final, "session_facts": session_facts, "inputs": inputs_document}


def observe(owner):
    """Original records and payloads; local output requires disclosure review."""
    core = owner._core
    records = {}
    for kind in ("agent/v1", "executable_transition_binding/v1", "operation_binding/v1",
                 "operation_spec/v1", "agent_loop/v1", "agent_action/v2", "agent_action/v3",
                 "llm_call_spec/v2", "llm_call_spec/v3", "llm_invocation_spec/v1",
                 "llm_invocation_attempt/v1", "provider_attempt_spec/v1",
                 "run_terminal_evidence/v1", "invocation/v1", "firing_completion/v2"):
        records[kind] = [{"ref": {"entity_type": kind, "logical_id": r["logical_id"], "version_id": r["version_id"]},
                          "metadata": dict(core.get_version(r["version_id"]).metadata)}
                         for r in core.event_store.object_rows_by_type(kind)]
    resources = []
    for row in core.event_store.object_rows_by_type("resource_version/v1"):
        resource = core.get_version(row["version_id"])
        if (resource.metadata.get("media_type") != "application/json"
                and not resource.metadata.get("media_type", "").startswith("text/")):
            # Keep binary runtime material in its original Registry store.
            # It is neither an agent output nor a shareable evidence document.
            continue
        payload = core.object_store.read_registered(resource)
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            value = payload.decode("utf-8", errors="strict")
        resources.append({"ref": {"resource_id": str(resource.logical_id), "resource_version_id": str(resource.version_id)},
                          "metadata": dict(resource.metadata), "value": value})
    firings = [core.event_store.ordered_firing_record(row["version_id"])
               for row in core.event_store.object_rows_by_type("transition_firing/v1")]
    return {"run_dir": str(core.run_dir), "actual_model_call_counts": list(core.event_store.actual_model_call_counts()),
            "records": records, "resources": resources, "firings": firings,
            "events": [{"ordinal": e.ordinal, "event_id": str(e.event_id), "type": e.event_type,
                        "aggregate_id": e.aggregate_id, "occurred_at": e.occurred_at, "payload": dict(e.payload)}
                       for e in core.event_store.list_events()]}


def export_observations(world, output):
    owner, host, session = world["owner"], world["host"], world["session"]
    provider, reader = open_view(owner, host, session, world["source_id"])
    try:
        frame, contribution = contribution_snapshot(provider, owner._core)
        before = owner._core.event_store.max_ordinal()
        evidence = {"schema_version": "agent_cobuild/observation/v1",
            "validation_kind": "native_scripted_structure_and_protocol_not_cognition",
            "source_authors": "controlled_deterministic_programs_not_agents",
            "external_provider_calls": 0,
            "sessions": world["session_facts"],
            "role_actor_map": {"owner": session.actor, **{k: v["session"].actor for k, v in world["contributors"].items()}},
            "initial_base": world["initial"].revision.to_dict(),
            "initial_module": world["initial"].module.to_dict(),
            "collaboration_mode": "sequential_exact_base_proposals_and_protected_advances",
            "candidates": [{"revision": c.revision.to_dict(), "module": c.module.to_dict()} for c in world["candidates"]],
            "accepted_revision": world["accepted_revision"].revision.to_dict(),
            "accepted_module": world["accepted_revision"].module.to_dict(),
            "branch_receipts": [host.read_fact(session, ref) for ref in world["branch_receipts"]],
            "adopt_receipt": host.read_fact(session, world["adopt_receipt"]),
            "collaboration_facts": host.read_view(session),
            "sources": {kind: {"witness": item["witness"], "execution": observe(item["owner"])}
                        for kind, item in world["contributors"].items()},
            "target": observe(owner), "terminal": world["terminal"], "final": world["final"],
            "inputs": world["inputs"], "contribution_view": contribution,
            "read_cut": before, "browser_capture": "not_run"}
        if owner._core.event_store.max_ordinal() != before:
            raise AssertionError("evidence export mutated Registry")
        expected_actors = {v["session"].actor for v in world["contributors"].values()}
        observed = {c["actor"] for n in contribution["nodes"] for c in n["changes"]}
        if observed != expected_actors:
            raise AssertionError("Viewer must retain both actual contributors")
        output.mkdir()
        for name, value in (("evidence.json", evidence), ("dashboard.json", frame),
                            ("contributions.json", contribution), ("result.json", world["final"])):
            (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
        return provider, reader, evidence
    except BaseException:
        reader.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=Path(__file__).parent / "fixtures/input.json")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8775)
    args = parser.parse_args(argv)
    root = args.work_dir.resolve()
    if root.exists() or len(str(root / "o.sock").encode()) > 100:
        parser.error("choose an absent, short work directory for original AF_UNIX transport")
    root.mkdir(parents=True)
    selection = load_llm_execution_selection(write_profile(root / "profile"))
    reader = None
    try:
        world = build_and_execute(root, selection, json.loads(args.input.read_text()))
        provider, reader, evidence = export_observations(world, root / "export")
        print(json.dumps({"status": "NATIVE_COMPLETE_INDEPENDENT_CHECK_PENDING", "external_provider_calls": 0,
                          "scripted_call_counts": evidence["target"]["actual_model_call_counts"],
                          "agent_nodes": len({row["ref"]["logical_id"] for row in
                                              evidence["target"]["records"]["agent_loop/v1"]}),
                          "export_dir": str(root / "export")}), flush=True)
        if args.serve:
            serve_projection(provider, port=args.port, open_browser=False, show_resources=True)
        return 0
    finally:
        if reader is not None: reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
