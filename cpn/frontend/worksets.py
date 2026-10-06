"""Read-only N02 projection of real Workset and ordinary PN facts."""
from __future__ import annotations

import json
import sqlite3

from cpn.rpnh.collaboration.worksets import WORKSET, ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, RECONCILIATION, qualified
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.schema_catalog import canonical_text
from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor, _canonical_closure
from cpn.rpnh.registry._event_store.source_identity import read_source_binding


_NORMAL_ROOT_TERMINAL = "collaboration_root_terminal/v2"
_NORMAL_CHILD_PROFILE = "execution-v1-normal-only"


def workset_view(core, *, source_readers=None):
    """One local snapshot; no payload, registration, reconciliation or scheduling.

    Physical source coverage is deliberately not inferred from target receipts.
    The target has recorded references, not authority to scan a source Registry.
    """
    if not core.read_only:
        raise TypeError("Workset Viewer requires a read-only Registry")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        cut = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        binding = read_source_binding(db, core.catalog, core.task_id)
        if binding is None:
            return {"schema_version": "rpnh/workset_view/v1", "source_id": None,
                    "capture_cut": cut, "coverage": "not_registered", "current": [], "history": []}
        source = json.loads(binding["binding_metadata_json"])["source_id"]
        records = []
        normal_roots = {}
        for row in db.execute("SELECT o.*,e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type IN (?,?,?,?,?,?) ORDER BY e.ordinal",
            (WORKSET, ACCEPTANCE, CONTRIBUTION, ROOT_TERMINAL, RECONCILIATION, _NORMAL_ROOT_TERMINAL)):
            if not _canonical_closure(db, core.task_id, transaction_id=row["transaction_id"],
                    members=(("object", row["version_id"]), ("event", row["published_event_id"]))):
                continue
            doc = exact_descriptor(db, core.object_store, core.task_id, {
                "entity_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"]})
            if row["object_type"] == _NORMAL_ROOT_TERMINAL:
                from cpn.rpnh.collaboration.root_terminals import read_root_terminal_snapshot
                verified = read_root_terminal_snapshot(core, db, doc["record_ref"])
                seal_ref = doc["body"]["required_child_seal_ref"]
                seal = verified["seal"]
                if (verified["root"] != doc or doc["record_ref"]["source_id"] != source
                        or doc["body"]["closure_profile"] != _NORMAL_CHILD_PROFILE
                        or seal_ref["source_id"] != source or seal["source_id"] != source
                        or seal_ref["ref"] != seal["execution_child_seal_ref"]
                        or seal_ref["ref"]["entity_type"] != "execution_child_seal/v1"
                        or seal["closure_profile"] != _NORMAL_CHILD_PROFILE
                        or type(verified["verified_at_cut"]) is not int or verified["verified_at_cut"] != cut):
                    raise ValueError("normal RootTerminal closure differs from the Workset snapshot")
                normal_roots[row["version_id"]] = verified
            records.append((row, doc))
        by_ref = {doc["record_ref"]["ref"]["version_id"]: doc for _row, doc in records}
        worksets = [(row, doc) for row, doc in records if row["object_type"] == WORKSET]
        latest = {}
        for row, doc in worksets:
            terminal_ref = doc["body"]["terminal_ref"]
            if terminal_ref is not None:
                kind = terminal_ref["ref"]["entity_type"]
                if kind not in {ROOT_TERMINAL, _NORMAL_ROOT_TERMINAL}:
                    raise ValueError("Workset terminal type is unsupported")
                if kind == _NORMAL_ROOT_TERMINAL:
                    verified = normal_roots.get(terminal_ref["ref"]["version_id"])
                    if (verified is None or verified["root"]["record_ref"] != terminal_ref
                            or verified["workset"] != doc):
                        raise ValueError("Workset normal RootTerminal closure is unavailable")
            latest[row["logical_id"]] = (row, doc)
        current = []
        marking_ref, available_tokens = _current_marking(core, db)
        for row, doc in latest.values():
            body = doc["body"]
            acceptances = [by_ref[ref["ref"]["version_id"]] for ref in body["acceptances"].values()
                           if ref["ref"]["version_id"] in by_ref]
            contributions = [by_ref[ref["ref"]["version_id"]] for ref in body["contributions"].values()
                             if ref["ref"]["version_id"] in by_ref]
            acceptance_complete = len(acceptances) == len(body["acceptances"])
            contribution_complete = bool(contributions) and len(contributions) == len(body["contributions"])
            terminal = None if body["terminal_ref"] is None else by_ref.get(body["terminal_ref"]["ref"]["version_id"])
            root_evidence = None
            if terminal is not None:
                for candidate in db.execute("SELECT * FROM objects WHERE object_type='run_terminal_evidence/v1'"):
                    if not _canonical_closure(db, core.task_id, transaction_id=candidate["transaction_id"],
                            members=(("object", candidate["version_id"]), ("event", candidate["published_event_id"]))):
                        continue
                    value = exact_descriptor(db, core.object_store, core.task_id, {
                        "entity_type": "run_terminal_evidence/v1", "logical_id": candidate["logical_id"], "version_id": candidate["version_id"]})
                    if (value["terminal_result_ref"] == terminal["body"]["output_resource_ref"]
                            and value["final_checkpoint_ref"] == terminal["body"]["checkpoint_ref"]):
                        root_evidence = value["terminal_evidence_ref"]
            a_versions = {a["record_ref"]["ref"]["version_id"] for a in acceptances}
            reconciliations = [value for other, value in records if other["object_type"] == RECONCILIATION
                and value["body"]["acceptance_ref"]["ref"]["version_id"] in a_versions]
            proofs = {}
            for value in reconciliations:
                proof = {key: value["body"][key] for key in
                         ("physical_delivery_ref", "logical_delivery_ref", "acceptance_ref", "original_outcome")}
                key = canonical_text(proof["physical_delivery_ref"])
                existing = proofs.get(key)
                if existing is not None and any(existing[name] != value for name, value in proof.items()):
                    raise ValueError("physical reconciliation facts conflict")
                if existing is None:
                    proofs[key] = {**proof, "reconciliation_refs": [value["record_ref"]]}
                else:
                    existing["reconciliation_refs"].append(value["record_ref"])
            physical, physical_cuts, physical_complete = _physical_observation(acceptances, source_readers or {})
            current.append({"workset_ref": doc["record_ref"], "state": body["state"],
                "generation": body["generation"], "collection_version": body["collection_version"],
                "sequence": body["sequence"], "requirements_ref": body["requirements_ref"],
                "input_binding_ref": body["input_binding_ref"], "expected_slots": body["expected_slots"],
                "workset_sealed": body["state"] in {"sealed", "completed"},
                "required_child_seal_ref": body["required_child_seal_ref"],
                "acceptance_count": len(acceptances) if acceptance_complete else None,
                "contribution_count": len(contributions) if contribution_complete else None,
                "acceptance_coverage": "complete" if acceptance_complete else "not_provided",
                "contribution_coverage": "complete" if contribution_complete else "not_provided",
                "occurrence_count": len({canonical_text(a["body"]["occurrence_ref"]) for a in acceptances}) if acceptance_complete else None,
                "available_occurrence_count": (sum(canonical_text(a["body"]["occurrence_ref"]) in available_tokens for a in acceptances)
                    if acceptance_complete and available_tokens is not None else None),
                "availability_checkpoint_ref": marking_ref,
                "physical_attempt_count": len(physical) if physical_complete else None,
                "physical_coverage": "complete_named_deliveries" if physical_complete else "source_not_observed",
                "physical_source_cuts": physical_cuts, "physical_attempts": physical,
                "acceptances": [{"acceptance_ref": value["record_ref"],
                    "target_workset_id": value["body"]["target_workset_id"],
                    "collection_version": value["body"]["collection_version"],
                    "input_binding_ref": value["body"]["input_binding_ref"],
                    "logical_delivery_ref": value["body"]["logical_delivery_ref"],
                    "initial_physical_delivery_ref": value["body"]["physical_delivery_ref"],
                    "occurrence_ref": value["body"]["occurrence_ref"], "slot": value["body"]["slot"],
                    "availability": ("not_provided" if available_tokens is None or not acceptance_complete else "available"
                        if canonical_text(value["body"]["occurrence_ref"]) in available_tokens else "absent_from_current_marking")}
                    for value in acceptances],
                "contributions": [{"contribution_ref": value["record_ref"],
                    "acceptance_ref": value["body"]["acceptance_ref"], "occurrence_ref": value["body"]["occurrence_ref"],
                    "slot": value["body"]["slot"]} for value in contributions],
                "recorded_physical_attempts": list(proofs.values()),
                "root_terminal_ref": body["terminal_ref"], "root_terminal_evidence_ref": root_evidence,
                "cancel_status": "not_provided", "stop_status": "not_provided"})
            if normal_roots:
                closure = None
                if terminal is not None and terminal["record_ref"]["ref"]["entity_type"] == _NORMAL_ROOT_TERMINAL:
                    verified = normal_roots[terminal["record_ref"]["ref"]["version_id"]]
                    closure = {"profile": terminal["body"]["closure_profile"],
                        "root_terminal_ref": terminal["record_ref"],
                        "seal_ref": terminal["body"]["required_child_seal_ref"],
                        "verified_at_cut": verified["verified_at_cut"]}
                current[-1]["root_child_closure"] = closure
        history = [{"workset_ref": doc["record_ref"], "sequence": doc["body"]["sequence"],
            "collection_version": doc["body"]["collection_version"], "action": doc["body"]["action"],
            "state_at_commit": doc["body"]["state"], "recorded_ordinal": row["ordinal"],
            "is_current": latest[row["logical_id"]][0]["version_id"] == row["version_id"]} for row, doc in worksets]
        return {"schema_version": "rpnh/workset_view/v2" if normal_roots else "rpnh/workset_view/v1",
                "source_id": source, "capture_cut": cut,
                "coverage": "complete_local_worksets", "current": current, "history": history}


def _current_marking(core, db):
    """Same-cut counterpart of the original sole run-authority reader."""
    try:
        rows = [row for row in db.execute(
            "SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type='run_execution_authority/v1' ORDER BY e.ordinal DESC")
            if _canonical_closure(db, core.task_id, transaction_id=row["transaction_id"],
                members=(("object", row["version_id"]), ("event", row["published_event_id"])))]
        if not rows or len({row["logical_id"] for row in rows}) != 1:
            return None, None
        row = rows[0]
        TypedId.parse(row["logical_id"], expected="run_execution_authority")
        TypedId.parse(row["version_id"], expected="run_execution_authority_version")
        selected = {"entity_type": row["object_type"], "logical_id": row["logical_id"], "version_id": row["version_id"]}
        authority = exact_descriptor(db, core.object_store, core.task_id, selected)
        pointer = db.execute("SELECT value FROM registry_meta WHERE key='native_run_ref'").fetchone()
        if pointer is None:
            return None, None
        run_ref = json.loads(pointer[0])
        if run_ref["entity_type"] != "native_run_identity/v1":
            return None, None
        run = exact_descriptor(db, core.object_store, core.task_id, run_ref)
        task = exact_descriptor(db, core.object_store, core.task_id, run["task_ref"])
        if (authority["run_execution_authority_ref"] != selected or authority["run_ref"] != run_ref
                or authority["task_ref"] != run["task_ref"] or run["run_id"] != run_ref["logical_id"]
                or run["run_version_id"] != run_ref["version_id"] or task["task_id"] != str(core.task_id)
                or task["task_id"] != run["task_ref"]["logical_id"] or task["task_version_id"] != run["task_ref"]["version_id"]):
            return None, None
        reference = authority["latest_checkpoint_ref"]
        checkpoint = exact_descriptor(db, core.object_store, core.task_id, reference)
        net = exact_descriptor(db, core.object_store, core.task_id, checkpoint["net_instance_ref"])
        root = exact_descriptor(db, core.object_store, core.task_id, checkpoint["team_design_root_ref"])
        declaration = net["team_net_declaration_resource_ref"]
        if (checkpoint["marking_checkpoint_ref"] != reference or net["net_instance_ref"] != checkpoint["net_instance_ref"]
                or net["team_design_root_ref"] != checkpoint["team_design_root_ref"] or root["task_ref"] != run["task_ref"]
                or authority["declaration_ref"] != {"entity_type": "resource_version/v1", "logical_id": declaration["resource_id"],
                                                  "version_id": declaration["resource_version_id"]}):
            return None, None
        if db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' AND net_version_id=?",
                      (checkpoint["net_instance_ref"]["version_id"],)).fetchone():
            return reference, None
        return reference, {canonical_text(ref) for ref in checkpoint["token_refs"]}
    except (KeyError, ValueError, RuntimeError, TypeError, sqlite3.Error, OSError):
        return None, None


def _physical_observation(acceptances, readers):
    """Named trusted local read-only sources; independent per-source cuts."""
    observed, cuts = [], {}
    complete = bool(acceptances)
    grouped = {}
    for acceptance in acceptances:
        delivery = acceptance["body"]["logical_delivery_ref"]
        grouped.setdefault(delivery["source_id"], {})[canonical_text(delivery)] = delivery
    for source_id, deliveries in grouped.items():
        core = readers.get(source_id)
        if core is None or not core.read_only:
            complete = False
            continue
        try:
            with core.event_store.connect() as db:
                db.execute("BEGIN")
                binding = read_source_binding(db, core.catalog, core.task_id)
                if binding is None or json.loads(binding["binding_metadata_json"])["source_id"] != source_id:
                    raise ValueError("physical observation source identity changed")
                cuts[source_id] = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
                for delivery in deliveries.values():
                    exact_descriptor(db, core.object_store, core.task_id, delivery["ref"])
                latest, attempted = {}, set()
                for row in db.execute("SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
                                      "WHERE o.object_type='resource_delivery/v1' ORDER BY e.ordinal"):
                    metadata = json.loads(row["metadata_json"])
                    if metadata.get("purpose") not in deliveries or metadata.get("boundary") != "parent_receipt":
                        continue
                    attempted.add(row["logical_id"])
                    if not _canonical_closure(db, core.task_id, transaction_id=row["transaction_id"],
                            members=(("object", row["version_id"]), ("event", row["published_event_id"]))):
                        complete = False
                        continue
                    if metadata.get("state") not in {"acknowledged", "unknown", "failed"}:
                        continue
                    ref = VersionRef("resource_delivery/v1", TypedId.parse(row["logical_id"]), TypedId.parse(row["version_id"]))
                    from cpn.rpnh.collaboration.delivery_evidence import read_delivery_evidence
                    logical, _exported, _payload, actual = read_delivery_evidence(core, db,
                        deliveries[metadata["purpose"]], qualified(source_id, ref), terminal_required=True)
                    latest[row["logical_id"]] = {"physical_delivery_ref": qualified(source_id, ref),
                        "logical_delivery_ref": deliveries[actual["purpose"]], "outcome": actual["state"]}
                observed.extend(latest.values())
                if set(latest) != attempted:
                    complete = False
        except (ValueError, RuntimeError, sqlite3.Error, OSError):
            complete = False
            cuts.pop(source_id, None)
    return observed, cuts, complete
