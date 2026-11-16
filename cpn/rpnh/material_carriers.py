"""Read exact literal-material carriers from original published execution facts.

The result is a bounded read projection for the original literal producer and
its workspace lineage. It is neither a purge authorization nor an exhaustive
inventory of arbitrary application archives. No paths are scanned or deleted.
"""
from __future__ import annotations

import json

from .file_execution_net import FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET
from .material_execution import is_literal_producer_spec
from .registry.event_store import RegistryConflict
from .registry.execution_child_closure import _definition
from .registry.identities import TypedId
from .registry.models import VersionRef
from .registry.object_store import ObjectIntegrityError
from .registry.publication import _version_from_payload
from .registry.resources import ResourceVersionRef
from .registry.strict_contracts import ref_payload
from .registry._event_store.collaboration_descriptors import (
    descriptor_store, exact_prepared, readable_descriptor,
)
from .registry._event_store.material_control import (
    canonical_rows, read_accepted_material_command, row_ref, reference,
)
from .registry._event_store.material_settlement import material_admission, terminal_result
from .workspace_settlement import _workspace_resource_state, _strict_workspace_path


class _CutObjects:
    """Reuse the original path-delta reader at one caller-owned SQLite cut."""
    def __init__(self, store, db, task_id):
        self.store, self.db, self.task_id = store, db, task_id
        self.object_store = descriptor_store(store)
        self.prepared = {}
        self.states = {}

    def exact(self, ref):
        key = ref["version_id"]
        if key not in self.prepared:
            self.prepared[key] = exact_prepared(self.db, self.object_store, self.task_id, ref)
        result = self.prepared[key]
        if (result.object_type != ref["entity_type"] or str(result.logical_id) != ref["logical_id"]):
            raise RegistryConflict("carrier reference differs from its original exact identity")
        return result

    def get_version(self, version_id):
        key = str(version_id)
        if key not in self.prepared:
            row = self.db.execute("SELECT * FROM objects WHERE version_id=?", (key,)).fetchone()
            if row is None:
                raise RegistryConflict("carrier workspace provenance is unavailable")
            self.exact(row_ref(row))
        return self.prepared[key]

    def document(self, ref):
        return readable_descriptor(self.object_store, self.exact(ref))

    def state(self, ref):
        key = ref["version_id"]
        if key not in self.states:
            self.states[key] = _workspace_resource_state(self, _version_from_payload(ref))
        return self.states[key]


def _fail(message):
    raise RegistryConflict("material carrier inspection: " + message)


def _entry(cut, ref, role, *, observe_payloads, **extra):
    item = cut.exact(ref)
    state, reason = "unknown", "BODY_NOT_OBSERVED"
    if observe_payloads:
        try:
            # Original bounded byte/size verification; no body is returned.
            cut.object_store.verify_registered(item)
            state, reason = "present", None
        except ObjectIntegrityError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                state, reason = "unexpected_missing", "NO_CANONICAL_PURGE_RESULT"
            else:
                state, reason = "unknown", "BODY_OBSERVATION_FAILED"
    result = {"carrier_ref": ref, "role": role, "carrier_state": state,
        "observation_reason": reason, "purge_authorized": False, **extra}
    if observe_payloads and state == "unexpected_missing" and role in {"semantic_output", "workspace_file"}:
        # This explicit observation reads original cleanup evidence only.
        # Default metadata inspection never enters it, including baseline
        # proofs called from the cleanup protocol itself.
        from .material_purge import inspect_purge_carrier_at
        try:
            facts = inspect_purge_carrier_at(cut.store, cut.db, cut.task_id, ref)
        except (RegistryConflict, ObjectIntegrityError, ValueError, KeyError, TypeError, OSError):
            result.update(carrier_state="unknown", observation_reason="PURGE_PROOF_UNAVAILABLE")
        else:
            result["purge_evidence"] = facts
            if any(item["state"] == "removed" for item in facts["results"]):
                result.update(carrier_state="purged", observation_reason="CANONICAL_PURGE_RESULT")
            elif facts["results"]:
                result.update(carrier_state="already_absent", observation_reason="CANONICAL_ABSENCE_OBSERVATION")
            elif any(item["status"] == "recovery_required" for item in facts["pending"]):
                result.update(carrier_state="unknown", observation_reason="PURGE_RECOVERY_REQUIRED")
    return result


def _current_workspace(cut, lineage_id):
    """Verify the original head row against its precise publication authority."""
    head = cut.db.execute("SELECT * FROM workspace_lineage_heads WHERE workspace_lineage_id=?",
                          (lineage_id,)).fetchone()
    if head is None:
        _fail("current workspace lineage authority is unavailable")
    ref = {"entity_type": "workspace_revision/v1", "logical_id": lineage_id,
           "version_id": head["workspace_revision_version_id"]}
    revision = cut.exact(ref)
    if (revision.metadata.get("settled") is not True
            or revision.metadata.get("payload_kind") != "full_workspace_tar"):
        _fail("current workspace head is not a settled workspace archive")
    row = cut.db.execute("SELECT transaction_id FROM objects WHERE version_id=?",
                         (ref["version_id"],)).fetchone()
    transaction = cut.db.execute("SELECT * FROM transactions WHERE transaction_id=?",
                                (head["transaction_id"],)).fetchone()
    if (row is None or transaction is None or transaction["status"] != "committed"
            or row["transaction_id"] != head["transaction_id"]):
        _fail("current workspace head differs from its exact publication transaction")
    material = json.loads(transaction["command_json"])
    roots = cut.db.execute("SELECT * FROM firing_publications WHERE state='PUBLISHED' "
        "AND published_transaction_id=? AND workspace_lineage_id=? AND workspace_revision_version_id=?",
        (head["transaction_id"], lineage_id, ref["version_id"])).fetchall()
    advances = [item for item in material.get("workspace_head_advances", [])
                if item.get("workspace_lineage_id") == lineage_id]
    if roots:
        if (len(roots) != 1 or advances
                or revision.metadata.get("disposition") not in ("committed", "merged")):
            _fail("current workspace head has ambiguous original publication authority")
        root = roots[0]
        completions = cut.db.execute("SELECT * FROM objects WHERE object_type='firing_completion/v2' "
            "AND transaction_id=?", (head["transaction_id"],)).fetchall()
        if len(completions) != 1:
            _fail("current workspace head lacks its original Success completion")
        completion = cut.document(row_ref(completions[0]))
        if (completion["workspace_revision_ref"] != ref
                or completion["operation_result_ref"]["version_id"] != root["operation_result_version_id"]
                or completion["transition_firing_ref"]["version_id"] != root["firing_version_id"]
                or completion["invocation_ref"]["version_id"] != root["invocation_version_id"]
                or completion["successor_checkpoint_ref"]["version_id"] != root["marking_checkpoint_version_id"]):
            _fail("current workspace head differs from its original Success closure")
        for exact in (completion["operation_result_ref"], completion["successor_checkpoint_ref"]):
            cut.exact(exact)
    elif advances:
        if (len(advances) != 1 or advances[0].get("workspace_revision_version_id") != ref["version_id"]
                or revision.metadata.get("parent_revision_ref", {}).get("version_id")
                != advances[0].get("expected_workspace_head_version_id")):
            _fail("current workspace head differs from its original advance command")
        advance = advances[0]
        cut.exact({"entity_type": advance["authority_entity_type"],
            "logical_id": advance["authority_logical_id"], "version_id": advance["authority_version_id"]})
    elif (revision.metadata.get("disposition") != "genesis"
            or revision.metadata.get("settled") is not True):
        _fail("current workspace head lacks its original advance or publication")
    return ref


def inspect_material_carriers(core, *, material_command_ref, observe_payloads=False):
    """Inspect one published literal command without producing Registry facts.

    Future confirmations must name their finite exact carrier refs. A later
    inspection is a new observation, never an extension of an earlier grant.
    The original candidate archive is explicitly retained as settlement proof
    and is read while validating that proof, even when observe_payloads=False.
    That flag controls only additional per-carrier byte/size observations.
    """
    if type(observe_payloads) is not bool:
        raise TypeError("observe_payloads must be an explicit bool")
    command_ref = reference(material_command_ref, "resource_version/v1")
    store = core.event_store
    with store.connect() as db:
        db.execute("BEGIN")
        return inspect_material_carriers_at(store, db, core.task_id,
            material_command_ref=command_ref, observe_payloads=observe_payloads)


def inspect_material_carriers_at(store, db, task_id, *, material_command_ref, observe_payloads=False):
    """Use the caller's original SQLite cut, including a final write-lock cut.

    The helper still validates original production/completion facts and does
    not apply current body policy to historical evidence verification.
    """
    if type(observe_payloads) is not bool:
        raise TypeError("observe_payloads must be an explicit bool")
    command_ref = reference(material_command_ref, "resource_version/v1")
    cut = _CutObjects(store, db, task_id)
    command = read_accepted_material_command(store, db, task_id, command_ref, current=False)
    if command["data"]["operation"] not in ("create_text", "replace_text"):
        _fail("only the declared literal-text carrier contract is supported")
    admissions = canonical_rows(store, db, task_id, object_type="firing_admission/v1",
        conditions="AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=?",
        parameters=(command_ref["version_id"],))
    if len(admissions) != 1:
        _fail("requires one published original material admission")
    raw = json.loads(admissions[0]["metadata_json"])
    firing_ref = raw["transition_firing_ref"]
    root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                       (firing_ref["version_id"],)).fetchone()
    if root is None or root["state"] != "PUBLISHED":
        _fail("provisional or rejected preparation is not a published material")
    admission, _command = material_admission(store, db, task_id, firing_ref)
    result_ref, result = terminal_result(store, db, task_id, admission, command, root)
    if result["result_kind"] != "saved" or len(result["output_refs"]) != 1:
        _fail("requires the exact original saved output")
    output_ref = result["output_refs"][0]
    closure = cut.document(result["completion_ref"])
    revision_ref = closure["workspace_revision_ref"]
    firing = cut.document(firing_ref)
    binding_ref = firing["operation_binding_ref"]
    binding = cut.document(binding_ref)
    spec = cut.document(binding["operation_spec_ref"])
    if not is_literal_producer_spec(spec):
        _fail("same type or derived_from does not establish the literal producer contract")
    mappings = db.execute("SELECT * FROM objects WHERE object_type='execution_terminal_mapping/v1' "
        "AND transaction_id=? ORDER BY rowid", (root["published_transaction_id"],)).fetchall()
    children = {}
    for row in mappings:
        mapping_ref = row_ref(row)
        mapping = cut.document(mapping_ref)
        instance = cut.document(mapping["execution_instance_ref"])
        definition = cut.document(instance["execution_net_definition_ref"])
        checkpoint = cut.document(mapping["execution_checkpoint_ref"])
        graph = _definition(definition)
        if (graph not in (FILE_MATERIALIZATION_NET, WORKSPACE_FINALIZATION_NET)
                or graph.definition_key in children
                or mapping["parent_business_firing_ref"] != firing_ref
                or mapping["parent_invocation_ref"] != admission["invocation_ref"]
                or instance["parent_business_firing_ref"] != firing_ref
                or instance["parent_invocation_ref"] != admission["invocation_ref"]
                or mapping["operation_result_ref"] != result["operation_result_ref"]
                or mapping["workspace_revision_ref"] != revision_ref
                or mapping["successor_business_checkpoint_ref"] != closure["successor_checkpoint_ref"]
                or mapping["business_outcome"] != "completed"
                or checkpoint["execution_checkpoint_ref"] != mapping["execution_checkpoint_ref"]
                or checkpoint["execution_instance_ref"] != mapping["execution_instance_ref"]
                or checkpoint["execution_net_definition_ref"] != instance["execution_net_definition_ref"]
                or checkpoint["status"] != "map_ready" or checkpoint["active_firing_refs"]
                or checkpoint["evidence_refs"] != mapping["evidence_refs"]):
            _fail("carrier mapping lacks its exact original map-ready Success closure")
        children[graph.definition_key] = (mapping_ref, mapping)
    if set(children) != {FILE_MATERIALIZATION_NET.definition_key, WORKSPACE_FINALIZATION_NET.definition_key}:
        _fail("literal producer requires both original execution mappings")
    file_mapping_ref, file_mapping = children[FILE_MATERIALIZATION_NET.definition_key]
    workspace_mapping_ref, workspace_mapping = children[WORKSPACE_FINALIZATION_NET.definition_key]
    pair = file_mapping["evidence_refs"]
    if len(pair) != 2 or pair[0] != output_ref or pair[1]["entity_type"] != "resource_version/v1":
        _fail("FILE evidence does not name the exact semantic/file pair")
    file_ref = pair[1]
    file_meta = cut.exact(file_ref).metadata
    path = _strict_workspace_path(file_meta.get("descriptors", {}).get("workspace_path", "")).as_posix()
    if (file_meta.get("origin_kind") != "workspace_write"
            or file_meta.get("producer_ref") != admission["invocation_ref"]
            or file_meta.get("origin", {}).get("primary_ref") != binding_ref
            or file_meta.get("descriptors", {}).get("content_role") != "workspace_file"):
        _fail("FILE resource differs from its exact original workspace producer")
    workspace_evidence = workspace_mapping["evidence_refs"]
    if len(workspace_evidence) != 1 or workspace_evidence[0]["entity_type"] != "workspace_revision_candidate/v1":
        _fail("workspace finalization lacks its exact original candidate")
    candidate_ref = workspace_evidence[0]
    candidate = cut.exact(candidate_ref).metadata
    raw_output = {"resource_id": output_ref["logical_id"], "resource_version_id": output_ref["version_id"]}
    raw_file = {"resource_id": file_ref["logical_id"], "resource_version_id": file_ref["version_id"]}
    deltas = [d for d in candidate["path_deltas"] if d["path"] == path]
    if (candidate["transition_firing_ref"] != firing_ref
            or candidate["invocation_ref"] != admission["invocation_ref"]
            or candidate["semantic_output_refs"] != [raw_output]
            or len(deltas) != 1 or deltas[0]["after_resource_ref"] != raw_file):
        _fail("candidate path mapping differs from the exact FILE evidence")
    file_resource = ResourceVersionRef(TypedId.parse(file_ref["logical_id"]), TypedId.parse(file_ref["version_id"]))
    if cut.state(revision_ref).get(path) != file_resource:
        _fail("published workspace does not carry the exact FILE resource")
    carriers = [
        _entry(cut, command["data"]["input_ref"], "dedicated_input", observe_payloads=observe_payloads,
            proof_refs=[command_ref, result_ref]),
        _entry(cut, output_ref, "semantic_output", observe_payloads=observe_payloads, proof_refs=[result_ref]),
        _entry(cut, file_ref, "workspace_file", observe_payloads=observe_payloads,
            path=path, proof_refs=[file_mapping_ref, candidate_ref]),
        _entry(cut, candidate_ref, "settlement_candidate_archive", observe_payloads=observe_payloads,
            path=path, retain_for_settlement_proof=True, proof_refs=[workspace_mapping_ref, result_ref]),
    ]
    revisions = canonical_rows(store, db, task_id, object_type="workspace_revision/v1",
        conditions="AND o.logical_id=?", parameters=(revision_ref["logical_id"],))
    for row in revisions:
        ref = row_ref(row)
        revision = cut.exact(ref)
        if (revision.metadata.get("settled") is not True
                or revision.metadata.get("payload_kind") != "full_workspace_tar"):
            continue
        state = cut.state(ref)
        if state.get(path) != file_resource:
            continue
        carriers.append(_entry(cut, ref, "workspace_archive", observe_payloads=observe_payloads,
            path=path, contains_other_files=len(state) > 1, proof_refs=[file_mapping_ref],
            exact_path_resource_ref=file_ref))
    current_ref = _current_workspace(cut, revision_ref["logical_id"])
    current = cut.state(current_ref).get(path)
    current_resource = None if current is None else ref_payload(current.as_version_ref())
    observed_at = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
    return {"protocol": "rpnh/material_carrier_inspection/v1", "task_ref": command["data"]["execution"]["task_ref"],
        "command_ref": command_ref, "output_ref": output_ref, "firing_ref": firing_ref,
        "observation_ordinal": observed_at, "carriers": carriers,
        "current_path_mapping": {"workspace_revision_ref": current_ref, "path": path,
            "resource_ref": current_resource, "still_maps_to_inspected_file": current == file_resource},
        "coverage": "literal_producer_and_its_workspace_lineage",
        "all_registry_carriers_proved": False, "purge_authorized": False,
        "availability_changed": False}

__all__ = ("inspect_material_carriers",)
