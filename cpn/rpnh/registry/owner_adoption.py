"""Drained OWNER commands adopt a registered graph, never a fictitious firing.

The execution owner supplies allocated ordinary token refs and the marking
choice. This bridge owns only the atomic checkpoint/adoption/control successor.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Mapping

from .identities import TypedId
from .models import PendingEvent, VersionRef
from .publication import _ref_payload, _resource_from_payload, _version_from_payload
from .resources import PublishResource, ResourceVersionRef
from .schema_catalog import canonical_json

if TYPE_CHECKING:
    from ._registry import _RegistryCore
    from .transaction import RegistryTransaction


@dataclass(frozen=True, slots=True)
class OwnerTokenMapping:
    old_token_ref: VersionRef
    new_token_ref: VersionRef


@dataclass(frozen=True, slots=True)
class OrdinaryRetirement:
    old_token_ref: VersionRef


@dataclass(frozen=True, slots=True)
class OwnerInputMapping:
    owner_resource_ref: ResourceVersionRef
    source_ref: VersionRef
    place: str
    new_token_ref: VersionRef


@dataclass(frozen=True, slots=True)
class OwnerAdoptionRequest:
    command_id: str
    owner_command_ref: ResourceVersionRef
    candidate_ref: ResourceVersionRef
    owner_principal_ref: VersionRef
    control_authority_ref: VersionRef
    base_net_ref: VersionRef
    submission_checkpoint_ref: VersionRef
    candidate_net_ref: VersionRef
    predecessor_checkpoint_ref: VersionRef
    checkpoint_ref: VersionRef
    epoch: int
    next_token_id: int
    attempts: tuple[Mapping[str, Any], ...]
    token_refs: tuple[VersionRef, ...]
    workspace_revision_refs: tuple[VersionRef, ...]
    token_mappings: tuple[OwnerTokenMapping, ...]
    ordinary_retirements: tuple[OrdinaryRetirement, ...]
    owner_input_mappings: tuple[OwnerInputMapping, ...] = ()


@dataclass(frozen=True, slots=True)
class OwnerAdoptionStaged:
    checkpoint_ref: VersionRef
    owner_command_result_ref: ResourceVersionRef
    transaction_id: TypedId


OWNER_FIELDS = (
    "owner_command_ref", "owner_command_result_ref", "candidate_ref",
    "owner_principal_ref", "control_authority_ref", "submission_checkpoint_ref",
    "owner_predecessor_checkpoint_ref", "owner_candidate_checkpoint_ref",
    "petri_structure_delta", "token_mappings", "ordinary_retirements",
    "owner_input_mappings",
)


def _require_editable_run(authority: Mapping[str, Any]) -> None:
    from .event_store import RegistryConflict
    if authority["status"] != "running" or authority["terminal_evidence_ref"] is not None:
        raise RegistryConflict("owner adoption requires a running run with no terminal evidence")


def _require_mapped_new_tokens(new_refs: set[bytes], mapped_new: set[bytes]) -> None:
    from .event_store import RegistryConflict
    if new_refs != mapped_new:
        raise RegistryConflict("owner adoption cannot seed unmapped tokens without registered owner inputs")


def _terminal_source_places(compiled) -> set[str]:
    names = {f"{terminal.source.component}.{terminal.source.port}"
        for terminal in (compiled.source.terminal, *compiled.source.terminal_alternatives)}
    return {port.place for port in compiled.ports if port.name in names}


def _require_place_content_schema(place, schema_id: str) -> None:
    from .event_store import RegistryConflict
    if schema_id not in place.admitted_schemas:
        raise RegistryConflict("owner token resource schema is outside the declared place variants")


def owner_command_document(request: OwnerAdoptionRequest) -> dict[str, Any]:
    """Exact immutable submission contract; its checkpoint is provenance only."""
    data = {"command": "edit",
        "base_net_ref": _ref_payload(request.base_net_ref),
        "submission_checkpoint_ref": _ref_payload(request.submission_checkpoint_ref),
        "candidate_net_ref": _ref_payload(request.candidate_net_ref),
        "candidate_ref": _ref_payload(request.candidate_ref.as_version_ref()),
        "owner_principal_ref": _ref_payload(request.owner_principal_ref),
        "control_authority_ref": _ref_payload(request.control_authority_ref)}
    return {"kind": "command", "command_id": request.command_id,
        "source": _ref_payload(request.owner_principal_ref),
        "target": str(request.candidate_net_ref.entity_id), "data": data,
        "lineage": [_ref_payload(request.candidate_ref.as_version_ref())]}


def owner_result_document(request: OwnerAdoptionRequest) -> dict[str, Any]:
    """This ADOPTED product is staged, not visible success before commit."""
    command = owner_command_document(request)
    data = {**command["data"], "status": "ADOPTED",
        "owner_command_ref": _ref_payload(request.owner_command_ref.as_version_ref()),
        "predecessor_checkpoint_ref": _ref_payload(request.predecessor_checkpoint_ref),
        "checkpoint_ref": _ref_payload(request.checkpoint_ref),
        "owner_input_mappings": [{"owner_resource_ref": _ref_payload(m.owner_resource_ref.as_version_ref()),
            "source_ref": _ref_payload(m.source_ref), "place": m.place,
            "new_token_ref": _ref_payload(m.new_token_ref)} for m in request.owner_input_mappings],
        "token_mappings": [{"old_token_ref": _ref_payload(m.old_token_ref),
                            "new_token_ref": _ref_payload(m.new_token_ref)}
                           for m in request.token_mappings],
        "ordinary_retirements": [{"old_token_ref": _ref_payload(r.old_token_ref),
                                  "source_checkpoint_ref": _ref_payload(request.predecessor_checkpoint_ref)}
                                 for r in request.ordinary_retirements]}
    return {**command, "kind": "result", "data": data,
        "lineage": [_ref_payload(request.owner_command_ref.as_version_ref()),
                    _ref_payload(request.candidate_ref.as_version_ref())]}


def stage_owner_adoption(core: _RegistryCore, transaction: RegistryTransaction,
                         request: OwnerAdoptionRequest, *,
                         result: PublishResource) -> OwnerAdoptionStaged:
    """Stage in an existing owner transaction; caller commits once.

    Candidate net/command/tokens must already be ordinary registered authority.
    The result must use the normal PrivateSystemOrigin bootstrap route. No new
    registration, admission pause, mapping policy or operation effect lives here.
"""
    from ._registry import _RegistryCore
    from .event_store import RegistryConflict, validate_registered_net_closure
    from .resource_service import _ResourceServiceKernel, _publish_private_system
    from .run_authority import stage_run_execution_checkpoint
    if (not isinstance(core, _RegistryCore) or core.read_only
            or not isinstance(request, OwnerAdoptionRequest)
            or transaction.event_store is not core.event_store
            or transaction.task_id != core.task_id
            or transaction.net_instance_id is not None or transaction.task_round_id is not None
            or transaction.idempotency_key != request.command_id):
        raise TypeError("owner adoption requires exact owner Core/transaction/request")
    if (not request.command_id or not isinstance(result, PublishResource)
            or result.idempotency_key != request.command_id):
        raise TypeError("owner adoption requires a separate command and Resource product")
    if json.loads(result.payload) != owner_result_document(request):
        raise RegistryConflict("owner command result bytes differ from the adoption request")
    from .run_authority import current_run_execution_authority
    _require_editable_run(current_run_execution_authority(core, _ResourceServiceKernel(core))[1])
    old_net = validate_registered_net_closure(
        core.event_store, core.catalog, request.base_net_ref)
    net = validate_registered_net_closure(
        core.event_store, core.catalog, request.candidate_net_ref)
    from ..executable_net import load_compiled_net
    from ..petri_primitives import (
        derive_petri_structure_delta, verify_petri_structure_delta,
    )
    old_declaration = _resource_from_payload(
        old_net["team_net_declaration_resource_ref"])
    candidate_declaration = _resource_from_payload(
        net["team_net_declaration_resource_ref"])
    old_compiled = load_compiled_net(json.loads(core.object_store.read_registered(
        core.get_version(old_declaration.resource_version_id))))
    candidate_compiled = load_compiled_net(json.loads(core.object_store.read_registered(
        core.get_version(candidate_declaration.resource_version_id))))
    structure_delta = derive_petri_structure_delta(
        old_compiled.symbolic, candidate_compiled.symbolic)
    verify_petri_structure_delta(
        old_compiled.symbolic, candidate_compiled.symbolic, structure_delta)
    task_ref = _version_from_payload(_ResourceServiceKernel(core)._exact_object(
        _version_from_payload(net["team_design_root_ref"]),
        expected_type="team_design_root/v1").metadata["task_ref"])
    result_ref = _publish_private_system(core, task_ref, result, transaction=transaction)
    checkpoint = {"marking_checkpoint_ref": _ref_payload(request.checkpoint_ref),
        "net_instance_ref": _ref_payload(request.candidate_net_ref),
        "team_design_root_ref": net["team_design_root_ref"],
        "epoch": request.epoch, "next_token_id": request.next_token_id,
        "attempts": [dict(a) for a in request.attempts],
        "token_refs": sorted((_ref_payload(ref) for ref in request.token_refs), key=canonical_json),
        "settled": True,
        "previous_checkpoint_ref": _ref_payload(request.predecessor_checkpoint_ref),
        "settlement_delta_ref": None, "transition_firing_refs": [],
        "workspace_revision_refs": [_ref_payload(ref) for ref in request.workspace_revision_refs],
        "owner_command_ref": _ref_payload(request.owner_command_ref.as_version_ref()),
        "owner_command_result_ref": _ref_payload(result_ref.as_version_ref())}
    transaction.prewrite(object_type="marking_checkpoint/v1",
        logical_id=request.checkpoint_ref.entity_id, version_id=request.checkpoint_ref.version_id,
        payload=canonical_json(checkpoint), metadata=checkpoint, media_type="application/json",
        schema_ref="registry_v1/marking_checkpoint/v1")
    payload = {key: value for key, value in checkpoint.items() if key not in {
        "marking_checkpoint_ref", "epoch", "next_token_id", "attempts", "token_refs"}}
    payload["checkpoint_ref"] = _ref_payload(request.checkpoint_ref)
    transaction.append(PendingEvent(event_type="marking_checkpoint_committed/v1",
        criticality="authoritative", stream_id=f"marking:{request.candidate_net_ref.entity_id}",
        aggregate_id=str(request.candidate_net_ref.entity_id), aggregate_type="marking_checkpoint",
        idempotency_key=request.command_id, command_id=request.command_id, payload=payload,
        payload_schema_ref="registry_v1/marking_checkpoint_committed/v1", task_control=True,
        producer_principal=str(request.owner_principal_ref.entity_id)))
    provenance = {"owner_command_ref": checkpoint["owner_command_ref"],
        "owner_command_result_ref": checkpoint["owner_command_result_ref"],
        "candidate_ref": _ref_payload(request.candidate_ref.as_version_ref()),
        "owner_principal_ref": _ref_payload(request.owner_principal_ref),
        "control_authority_ref": _ref_payload(request.control_authority_ref),
        "submission_checkpoint_ref": _ref_payload(request.submission_checkpoint_ref),
        "owner_predecessor_checkpoint_ref": checkpoint["previous_checkpoint_ref"],
        "owner_candidate_checkpoint_ref": _ref_payload(request.checkpoint_ref),
        "petri_structure_delta": structure_delta.to_dict(),
        "token_mappings": owner_result_document(request)["data"]["token_mappings"],
        "ordinary_retirements": owner_result_document(request)["data"]["ordinary_retirements"],
        "owner_input_mappings": owner_result_document(request)["data"]["owner_input_mappings"]}
    core.task_control.stage_owner_net_adoption(transaction,
        net_instance_ref=request.candidate_net_ref, supersedes_net_ref=request.base_net_ref,
        owner_provenance=provenance, owner_principal_ref=request.owner_principal_ref)
    stage_run_execution_checkpoint(core, _ResourceServiceKernel(core), transaction,
        request.checkpoint_ref, idempotency_key=request.command_id,
        declaration_ref=request.candidate_ref.as_version_ref(),
        declaration_schema_ref="rpnh/executable_net/v1",
        mutable_stage_ref=request.candidate_ref.as_version_ref())
    return OwnerAdoptionStaged(request.checkpoint_ref, result_ref, transaction.transaction_id)


def validate_owner_adoption(store, catalog, db, *, task_id, transaction_id,
                            pending, objects, events, relations) -> None:
    """Commit-snapshot gate. Ordinary sibling settlements are not submission CAS."""
    from .event_store import (RegistryConflict, _exact_object_metadata,
        _CANONICAL_EVENT_SQL, validate_registered_net_closure,
        verified_adoption_head, verified_checkpoint_head)
    from .object_store import ObjectStore
    p = pending.payload
    if any(field.startswith(("repair_", "growth_")) for field in p):
        raise RegistryConflict("owner command adoption cannot borrow repair/growth provenance")
    new = {str(item.version_id): item for item in objects}
    def exact(value, expected, *, staged=False):
        ref = _version_from_payload(value)
        if ref.entity_type != expected:
            raise RegistryConflict("owner adoption reference type differs")
        item = new.get(str(ref.version_id)) if staged else None
        if item is not None:
            if item.object_type != expected or item.logical_id != ref.entity_id:
                raise RegistryConflict("owner adoption staged reference differs")
            return ref, dict(item.metadata)
        metadata = _exact_object_metadata(store, ref, expected_type=expected, db=db)
        if not db.execute("SELECT 1 FROM objects o JOIN events e ON e.event_id=o.published_event_id "
                f"WHERE o.version_id=? AND {_CANONICAL_EVENT_SQL}", (str(ref.version_id),)).fetchone():
            raise RegistryConflict("owner adoption requires ordinary canonical Registry authority")
        return ref, metadata
    def document(ref):
        return json.loads(ObjectStore(store.path.parent / "objects", catalog, read_only=True)
            .path_for_version(ref.version_id).read_bytes())
    try:
        base, _ = exact(p["supersedes_net_ref"], "net_instance/v1")
        candidate, _ = exact(p["net_instance_ref"], "net_instance/v1")
        if verified_adoption_head(store, catalog, task_id, _db=db) != base or candidate == base:
            raise RegistryConflict("CONFLICT: owner command base active NET changed")
        old_head = verified_checkpoint_head(store, catalog, task_id, base, _db=db)
        predecessor, old = exact(p["owner_predecessor_checkpoint_ref"], "marking_checkpoint/v1")
        if predecessor != old_head:
            raise RegistryConflict("owner adoption requires the latest drained predecessor checkpoint")
        if db.execute("SELECT 1 FROM firing_publications WHERE state='PROVISIONAL' LIMIT 1").fetchone():
            raise RegistryConflict("owner adoption requires no active PROVISIONAL firing")
        if any(item.producer_invocation_id is not None for item in (*objects, *events)):
            raise RegistryConflict("owner adoption cannot publish through a firing")
        if any(e.event_type in {"firing_admitted/v1", "transition_firing_settled/v1",
                "structural_growth_adopted/v1", "checkpoint_repair_committed/v1",
                "run_terminal_evidence_committed/v1"} for e in events):
            raise RegistryConflict("owner command cannot synthesize firing/repair/growth/terminal effects")
        if any(item.object_type in {"transition_firing/v1", "invocation/v1", "marking_delta/v1",
                "run_terminal_evidence/v1", "firing_completion/v2"} for item in objects):
            raise RegistryConflict("owner adoption cannot manufacture firing/settlement/terminal authority")
        old_net = validate_registered_net_closure(store, catalog, base, _db=db)
        net = validate_registered_net_closure(store, catalog, candidate, _db=db)
        _, old_root = exact(old_net["team_design_root_ref"], "team_design_root/v1")
        _, root = exact(net["team_design_root_ref"], "team_design_root/v1")
        if root["task_ref"] != old_root["task_ref"] or root["task_ref"]["logical_id"] != str(task_id):
            raise RegistryConflict("owner adoption net roots cross task authority")
        for field in ("team_design_root_ref", "llm_macro_net_ref",
                      "node_refs", "operation_binding_refs", "output_binding_refs"):
            if p[field] != net[field]:
                raise RegistryConflict("owner adoption fact differs from candidate closure")
        principal, _ = exact(p["owner_principal_ref"], "principal/v1")
        authority_ref, authority = exact(p["control_authority_ref"], "user_authority_decision/v1")
        if (authority["status"] != "effective" or authority["user_principal_ref"] != p["owner_principal_ref"]
                or root["owner_principal_ref"] != p["owner_principal_ref"]
                or old_root["owner_principal_ref"] != p["owner_principal_ref"]
                or root["task_ref"] not in authority["governed_artifact_refs"]
                or pending.producer_principal != str(principal.entity_id)
                or authority["authority_kind"] not in {"scope", "architecture"}):
            raise RegistryConflict("owner command lacks registered effective owner control authority")
        if (root["run_ref"] != old_root["run_ref"]
                or p["control_authority_ref"] not in root["artifact_refs"]):
            raise RegistryConflict("owner control authority differs from candidate root/run authority")
        command_ref, command_meta = exact(p["owner_command_ref"], "resource_version/v1")
        candidate_ref, candidate_meta = exact(p["candidate_ref"], "resource_version/v1")
        result_ref, result_meta = exact(p["owner_command_result_ref"], "resource_version/v1", staged=True)
        submission, submitted = exact(p["submission_checkpoint_ref"], "marking_checkpoint/v1")
        if (submitted["net_instance_ref"] != p["supersedes_net_ref"]
                or not db.execute("SELECT 1 FROM events e WHERE e.task_id=? AND "
                    "e.event_type='marking_checkpoint_committed/v1' AND "
                    "json_extract(e.payload_json,'$.checkpoint_ref.version_id')=? AND "
                    f"{_CANONICAL_EVENT_SQL}", (str(task_id), str(submission.version_id))).fetchone()):
            raise RegistryConflict("submission checkpoint is not committed base-net provenance")
        declaration_ref = _resource_from_payload(net["team_net_declaration_resource_ref"])
        if candidate_ref != declaration_ref.as_version_ref():
            raise RegistryConflict("candidate Resource is not the registered Module declaration")
        for ref, metadata in ((command_ref, command_meta), (candidate_ref, candidate_meta), (result_ref, result_meta)):
            if (metadata["task_ref"] != root["task_ref"]
                    or metadata["resource_id"] != str(ref.entity_id)
                    or metadata["resource_version_id"] != str(ref.version_id)):
                raise RegistryConflict("owner command/candidate/result Resource crosses exact task authority")
        checkpoint_ref, checkpoint = exact(p["owner_candidate_checkpoint_ref"], "marking_checkpoint/v1", staged=True)
        if (str(checkpoint_ref.version_id) not in new or str(result_ref.version_id) not in new
                or db.execute("SELECT 1 FROM objects WHERE version_id IN (?,?)",
                    (str(checkpoint_ref.version_id), str(result_ref.version_id))).fetchone()):
            raise RegistryConflict("owner result and checkpoint must be fresh SAMEtransaction publications")
        command_data = {"command": "edit", "base_net_ref": p["supersedes_net_ref"],
            "submission_checkpoint_ref": p["submission_checkpoint_ref"],
            "candidate_net_ref": p["net_instance_ref"], "candidate_ref": p["candidate_ref"],
            "owner_principal_ref": p["owner_principal_ref"], "control_authority_ref": p["control_authority_ref"]}
        command = {"kind": "command", "command_id": pending.command_id,
            "source": p["owner_principal_ref"], "target": str(candidate.entity_id),
            "data": command_data, "lineage": [p["candidate_ref"]]}
        if document(command_ref) != command:
            raise RegistryConflict("registered owner command bytes differ from exact control request")
        result_data = {**command_data, "status": "ADOPTED", "owner_command_ref": p["owner_command_ref"],
            "predecessor_checkpoint_ref": p["owner_predecessor_checkpoint_ref"],
            "checkpoint_ref": p["owner_candidate_checkpoint_ref"], "token_mappings": p["token_mappings"],
            "ordinary_retirements": p["ordinary_retirements"], "owner_input_mappings": p["owner_input_mappings"]}
        expected_result = {**command, "kind": "result", "data": result_data,
            "lineage": [p["owner_command_ref"], p["candidate_ref"]]}
        for metadata, kind in ((command_meta, "command"), (result_meta, "result")):
            if (metadata["content_schema_ref"] != "rpnh/owner_control/v1"
                    or metadata["descriptors"].get("owner_control_kind") != kind
                    or metadata["descriptors"].get("command_id") != pending.command_id
                    or metadata["origin_kind"] != "private_system"):
                raise RegistryConflict("owner command/result lacks ordinary registered control Resource origin")
        for metadata, lineage in ((command_meta, command["lineage"]), (result_meta, expected_result["lineage"])):
            if metadata["reference_provenance"]["derived_from_refs"] != sorted(lineage, key=canonical_json):
                raise RegistryConflict("owner command/result lineage differs from ordinary Resource provenance")
        if any(e.command_id != pending.command_id or e.producer_principal != str(principal.entity_id)
               for e in events if e.event_type == "marking_checkpoint_committed/v1"):
            raise RegistryConflict("owner checkpoint command/principal differs from NET adoption")
        if document(result_ref) != expected_result or document(checkpoint_ref) != checkpoint:
            raise RegistryConflict("owner result/checkpoint bytes differ from exact adoption")
        commits = [e for e in events if e.event_type == "marking_checkpoint_committed/v1"]
        adoptions = [e for e in events if e.event_type == "net_adopted/v1"]
        if (len(adoptions) != 1 or len(commits) != 1
                or commits[0].payload.get("checkpoint_ref") != p["owner_candidate_checkpoint_ref"]
                or checkpoint["net_instance_ref"] != p["net_instance_ref"]
                or checkpoint["team_design_root_ref"] != net["team_design_root_ref"]
                or checkpoint["previous_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or checkpoint["settlement_delta_ref"] is not None or checkpoint["transition_firing_refs"] != []
                or any(checkpoint.get(f) != p[f] for f in ("owner_command_ref", "owner_command_result_ref"))):
            raise RegistryConflict("owner adoption requires one exact successor checkpoint/NET pair")
        if checkpoint["workspace_revision_refs"] != old.get("workspace_revision_refs", []):
            raise RegistryConflict("owner adoption cannot discard workspace authority")
        for value in checkpoint["workspace_revision_refs"]:
            exact(value, "workspace_revision/v1")
        _validate_tokens(store, catalog, db, checkpoint, old, old_net, net, root, p, exact, document)
        authorities = [o for o in objects if o.object_type == "run_execution_authority/v1"]
        if (len(authorities) != 1
                or authorities[0].metadata["latest_checkpoint_ref"] != p["owner_candidate_checkpoint_ref"]):
            raise RegistryConflict("owner adoption requires SAMEtransaction run-execution-checkpoint successor")
        rows = db.execute("SELECT o.logical_id FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id WHERE o.object_type='run_execution_authority/v1' AND "
            f"{_CANONICAL_EVENT_SQL}").fetchall()
        if not rows or any(str(o.logical_id) != rows[0]["logical_id"] for o in authorities):
            raise RegistryConflict("owner adoption run authority must preserve its exact lineage")
        previous_authority = db.execute("SELECT o.metadata_json FROM objects o JOIN events e "
            "ON e.event_id=o.published_event_id WHERE o.object_type='run_execution_authority/v1' AND "
            f"{_CANONICAL_EVENT_SQL} ORDER BY e.ordinal DESC LIMIT 1").fetchone()
        current = json.loads(previous_authority["metadata_json"])
        _require_editable_run(current)
        successor = dict(authorities[0].metadata)
        source_fields = {"run_execution_authority_ref", "latest_checkpoint_ref",
                         "declaration_ref", "declaration_schema_ref"}
        if (current["latest_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or successor["declaration_ref"] != p["candidate_ref"]
                or successor["declaration_schema_ref"] != "rpnh/executable_net/v1"
                or candidate_meta["content_schema_ref"] != successor["declaration_schema_ref"]
                or {k:v for k,v in successor.items() if k not in source_fields}
                != {k:v for k,v in current.items() if k not in source_fields}):
            raise RegistryConflict("owner adoption cannot reinterpret accumulated run authority")
        successor_ref = _version_from_payload(successor["run_execution_authority_ref"])
        stage_relations = [r for r in relations if r.relation_type == "derived_from"
            and r.source == successor_ref and r.metadata.get("authority_role") == "mutable_stage"]
        if len(stage_relations) != 1 or stage_relations[0].target != candidate_ref:
            raise RegistryConflict("owner adoption requires exact prospective mutable-stage relation")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RegistryConflict("owner adoption exact command/checkpoint authority is malformed") from exc


def verified_owner_adoption_fields(store, catalog, db, event, *, _prefix_reads=None):
    """Read the sole owner witness, requiring its SAMEtransaction checkpoint/result."""
    from .event_store import RegistryCorruptError, _exact_object_metadata, _CANONICAL_EVENT_SQL
    from .object_store import ObjectStore
    from ..executable_net import load_compiled_net
    from ..petri_primitives import (
        derive_petri_structure_delta, verify_petri_structure_delta,
    )
    p = event.payload
    if _prefix_reads is not None and getattr(_prefix_reads, "terminal", False):
        from ._event_store.adoption_reads import AdoptionPrefixReads
        if (type(_prefix_reads) is not AdoptionPrefixReads or _prefix_reads.db is not db
                or _prefix_reads.store is not store or _prefix_reads.catalog is not catalog
                or _prefix_reads.task_id != event.task_id):
            raise TypeError("terminal owner witness requires its fixed same-cut reader")
        return _verified_terminal_owner_adoption(store, catalog, db, event, _prefix_reads)
    object_store = ObjectStore(store.path.parent / "objects", catalog, read_only=True)
    if _prefix_reads is not None:
        from ._event_store.adoption_reads import AdoptionPrefixReads
        if (type(_prefix_reads) is not AdoptionPrefixReads or _prefix_reads.db is not db
                or _prefix_reads.store is not store or _prefix_reads.task_id != event.task_id):
            raise TypeError("owner prefix witness requires its fixed same-cut reader")
    def metadata(store, ref, *, expected_type=None, db=None):
        if _prefix_reads is not None:
            return _prefix_reads.metadata(ref, expected_type)
        return _exact_object_metadata(store, ref, expected_type=expected_type, db=db)
    def document(ref):
        if _prefix_reads is not None:
            return _prefix_reads.document(ref)
        return json.loads(object_store.path_for_version(ref.version_id).read_bytes())
    def compiled_document(ref):
        if _prefix_reads is not None:
            return _prefix_reads.compiled(ref)
        return load_compiled_net(document(ref))
    try:
        if not all(field in p for field in OWNER_FIELDS):
            raise RegistryCorruptError("owner adoption witness is incomplete")
        for field, expected in (("owner_command_ref", "resource_version/v1"),
                ("owner_command_result_ref", "resource_version/v1"), ("candidate_ref", "resource_version/v1"),
                ("owner_principal_ref", "principal/v1"), ("control_authority_ref", "user_authority_decision/v1"),
                ("submission_checkpoint_ref", "marking_checkpoint/v1"),
                ("owner_predecessor_checkpoint_ref", "marking_checkpoint/v1"),
                ("owner_candidate_checkpoint_ref", "marking_checkpoint/v1")):
            metadata(store, _version_from_payload(p[field]), expected_type=expected, db=db)
        rows = db.execute("SELECT e.* FROM events e WHERE e.task_id=? AND "
            "e.event_type='marking_checkpoint_committed/v1' AND e.transaction_id=? AND "
            f"{_CANONICAL_EVENT_SQL}", (str(event.task_id), str(event.transaction_id))).fetchall()
        if len(rows) != 1:
            raise RegistryCorruptError("owner adoption lacks one SAMEtransaction checkpoint commit")
        if _prefix_reads is not None:
            _prefix_reads.event(rows[0])
        commit = json.loads(rows[0]["payload_json"])
        checkpoint_ref = _version_from_payload(p["owner_candidate_checkpoint_ref"])
        checkpoint = metadata(store, checkpoint_ref,
            expected_type="marking_checkpoint/v1", db=db)
        result_ref = _version_from_payload(p["owner_command_result_ref"])
        for ref in (checkpoint_ref, result_ref):
            row = db.execute("SELECT transaction_id FROM objects WHERE version_id=?",
                (str(ref.version_id),)).fetchone()
            if row is None or row["transaction_id"] != str(event.transaction_id):
                raise RegistryCorruptError("owner checkpoint/result is not SAMEtransaction authority")
        if (commit["checkpoint_ref"] != p["owner_candidate_checkpoint_ref"]
                or commit["net_instance_ref"] != p["net_instance_ref"]
                or commit["previous_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or checkpoint["previous_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or checkpoint["net_instance_ref"] != p["net_instance_ref"]
                or checkpoint["settlement_delta_ref"] is not None or checkpoint["transition_firing_refs"] != []
                or any(commit.get(f) != p[f] or checkpoint.get(f) != p[f]
                       for f in ("owner_command_ref", "owner_command_result_ref"))):
            raise RegistryCorruptError("owner adoption differs from exact checkpoint bridge")
        result = document(result_ref)
        if (result["kind"] != "result" or result["source"] != p["owner_principal_ref"]
                or result["command_id"] != event.command_id
                or result["data"]["status"] != "ADOPTED"
                or result["data"]["owner_command_ref"] != p["owner_command_ref"]
                or result["data"]["base_net_ref"] != p["supersedes_net_ref"]
                or result["data"]["candidate_net_ref"] != p["net_instance_ref"]
                or result["data"]["checkpoint_ref"] != p["owner_candidate_checkpoint_ref"]
                or result["data"]["predecessor_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or result["data"]["owner_input_mappings"] != p["owner_input_mappings"]):
            raise RegistryCorruptError("owner adoption result bytes differ from exact bridge")
        old_net = metadata(store,
            _version_from_payload(p["supersedes_net_ref"]),
            expected_type="net_instance/v1", db=db)
        candidate_net = metadata(store,
            _version_from_payload(p["net_instance_ref"]),
            expected_type="net_instance/v1", db=db)
        old_declaration = _resource_from_payload(
            old_net["team_net_declaration_resource_ref"])
        candidate_declaration = _resource_from_payload(
            candidate_net["team_net_declaration_resource_ref"])
        old_compiled = compiled_document(old_declaration.as_version_ref())
        candidate_compiled = compiled_document(candidate_declaration.as_version_ref())
        structure_delta = derive_petri_structure_delta(
            old_compiled.symbolic, candidate_compiled.symbolic)
        verify_petri_structure_delta(
            old_compiled.symbolic, candidate_compiled.symbolic, structure_delta)
        if p["petri_structure_delta"] != structure_delta.to_dict():
            raise RegistryCorruptError(
                "owner adoption Petri structure delta differs from registered declarations")
        return {field: p[field] for field in OWNER_FIELDS}
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RegistryCorruptError("owner adoption witness is malformed") from exc


def _verified_terminal_owner_adoption(store, catalog, db, event, reads):
    """Reclose the original commit gate at its historical adoption position.

    This mode is consumed only by terminal history. No historical head is made
    current and no caller-supplied proof bag or execution callback is accepted.
    """
    from .event_store import RegistryConflict, RegistryCorruptError, _CANONICAL_EVENT_SQL
    from ._event_store.net_lineage import validate_registered_net_closure
    from ..petri_primitives import derive_petri_structure_delta, verify_petri_structure_delta
    from .errors import TerminalReadIncomplete
    p, transaction = event.payload, str(event.transaction_id)
    def same(left, right):
        return canonical_json(left) == canonical_json(right)
    def require(condition, message):
        if not condition:
            raise RegistryCorruptError(message)
    def exact(value, expected, *, staged=False):
        ref = _version_from_payload(value)
        return ref, reads.metadata(ref, expected)
    def checkpoint_commit(value, *, before=None):
        ref, checkpoint = exact(value, "marking_checkpoint/v1")
        rows = db.execute("SELECT e.* FROM events e WHERE e.task_id=? AND e.event_type='marking_checkpoint_committed/v1' "
            "AND json_extract(e.payload_json,'$.checkpoint_ref.version_id')=? AND " + _CANONICAL_EVENT_SQL,
            (str(event.task_id), str(ref.version_id))).fetchall()
        require(len(rows) == 1, "owner checkpoint has no unique canonical commit")
        row = rows[0]
        reads.event(row)
        expected = {key: value for key, value in checkpoint.items() if key not in {
            "marking_checkpoint_ref", "epoch", "next_token_id", "attempts", "token_refs"}}
        expected["checkpoint_ref"] = value
        payload = json.loads(row["payload_json"])
        settlement_id = payload.pop("settlement_event_id", None)
        require(same(payload, expected), "owner checkpoint commit differs from exact descriptor")
        if checkpoint["settlement_delta_ref"] is not None:
            require(len(checkpoint["transition_firing_refs"]) == 1 and settlement_id is not None,
                "owner historical settled checkpoint lacks exact firing/event linkage")
            firing_ref = _version_from_payload(checkpoint["transition_firing_refs"][0])
            record = store.ordered_firing_record(firing_ref.version_id, _db=db)
            require(record["state"] == "PUBLISHED" and same(record["successor_checkpoint"], checkpoint),
                "owner historical checkpoint differs from original firing settlement")
            for role, field, kind in (("firing", "transition_firing_ref", "transition_firing/v1"),
                    ("admission_checkpoint", "marking_checkpoint_ref", "marking_checkpoint/v1"),
                    ("successor_checkpoint", "marking_checkpoint_ref", "marking_checkpoint/v1"),
                    ("marking_delta", "marking_delta_ref", "marking_delta/v1"),
                    ("firing_completion", "firing_completion_ref", "firing_completion/v2")):
                document = record[role]
                _, metadata = exact(document[field], kind)
                require(same(document, metadata), "owner historical settlement descriptor bytes differ")
            exact(checkpoint["previous_checkpoint_ref"], "marking_checkpoint/v1")
            settlements = []
            for member in record["events"]:
                member_row = db.execute("SELECT * FROM events WHERE event_id=?", (str(member.event_id),)).fetchone()
                reads.event(member_row)
                if member.event_type == "transition_firing_settled/v1":
                    settlements.append(member)
            require(len(settlements) == 1 and str(settlements[0].event_id) == settlement_id
                and str(settlements[0].transaction_id) == row["transaction_id"],
                "owner historical checkpoint has a foreign settlement event")
            result_ref = _version_from_payload(settlements[0].payload["operation_result_ref"])
            reads.metadata(result_ref, "operation_result/v1")
            require(reads.publication(result_ref)["transaction_id"] == row["transaction_id"],
                "owner historical operation result is not atomic with settlement")
        else:
            require(settlement_id is None, "owner checkpoint bridge fabricates a settlement event")
        require(checkpoint["marking_checkpoint_ref"] == value, "owner checkpoint exact self ref differs")
        publication = reads.publication(ref)
        require(publication["transaction_id"] == row["transaction_id"], "owner checkpoint object and commit have different transactions")
        terminal = reads.transaction(row["transaction_id"])
        require(before is None or terminal["ordinal"] < before, "owner historical checkpoint was not committed before adoption")
        return checkpoint, row
    def prior(value, expected):
        ref, body = exact(value, expected)
        row = reads.publication(ref, ordinary=True)
        require(reads.transaction(row["transaction_id"])["ordinal"] < event.ordinal,
            "owner prerequisite was not canonically published before adoption")
        return ref, body
    def private_control(ref, body, kind, lineage):
        source = _version_from_payload(body["origin"]["primary_ref"])
        prior(_ref_payload(source), "bootstrap_command/v1")
        require(body["content_schema_ref"] == "rpnh/owner_control/v1"
            and body["descriptors"].get("owner_control_kind") == kind
            and body["descriptors"].get("command_id") == event.command_id
            and body["origin_kind"] == "private_system" and body["origin"]["kind"] == "private_system"
            and body["origin"]["secondary_ref"] == _ref_payload(source)
            and body["producer_ref"] == _ref_payload(source)
            and body["reference_provenance"]["producer_invocation_ref"] is None
            and body["reference_provenance"]["operation_binding_ref"] is None
            and same(body["reference_provenance"]["derived_from_refs"], sorted(lineage, key=canonical_json)),
            "owner control Resource origin/command/lineage differs")
    try:
        require(all(field in p for field in OWNER_FIELDS), "owner adoption witness is incomplete")
        require(not any(field.startswith(("repair_", "growth_")) for field in p), "owner adoption borrows another bridge")
        row = db.execute("SELECT * FROM events WHERE event_id=?", (str(event.event_id),)).fetchone()
        reads.event(row)
        from .event_store import fact_event_envelope
        require(row["transaction_id"] == transaction and event.ordinal == row["ordinal"]
            and same(fact_event_envelope(store._row_to_envelope(row)), fact_event_envelope(event)),
            "owner adoption differs from canonical event")
        transaction_row = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (transaction,)).fetchone()
        require(transaction_row is not None and transaction_row["idempotency_key"] == event.command_id,
            "owner adoption transaction differs from exact command key")
        batch = db.execute("SELECT * FROM events WHERE transaction_id=? ORDER BY ordinal", (transaction,)).fetchall()
        for member in batch:
            reads.event(member)
        adoptions = [member for member in batch if member["event_type"] == "net_adopted/v1"]
        commits = [member for member in batch if member["event_type"] == "marking_checkpoint_committed/v1"]
        require(len(adoptions) == len(commits) == 1 and adoptions[0]["event_id"] == str(event.event_id),
            "owner adoption lacks a unique same-transaction checkpoint/adoption pair")
        require(not any(member["producer_invocation_id"] is not None for member in batch)
            and not any(member["event_type"] in {"firing_admitted/v1", "transition_firing_settled/v1",
                "structural_growth_adopted/v1", "checkpoint_repair_committed/v1", "run_terminal_evidence_committed/v1"}
                for member in batch), "owner adoption manufactures firing/repair/terminal effects")
        objects = db.execute("SELECT * FROM objects WHERE transaction_id=?", (transaction,)).fetchall()
        require(not any(item["producer_invocation_id"] is not None or item["object_type"] in {
            "transition_firing/v1", "invocation/v1", "marking_delta/v1", "run_terminal_evidence/v1", "firing_completion/v2"}
            for item in objects), "owner adoption manufactures firing/settlement authority")
        base, _ = prior(p["supersedes_net_ref"], "net_instance/v1")
        candidate, _ = prior(p["net_instance_ref"], "net_instance/v1")
        require(base != candidate, "owner adoption reuses its source net")
        old_net = validate_registered_net_closure(store, catalog, base, _db=db, _memo={}, _prefix_reads=reads)
        net = validate_registered_net_closure(store, catalog, candidate, _db=db, _memo={}, _prefix_reads=reads)
        _, old_root = exact(old_net["team_design_root_ref"], "team_design_root/v1")
        _, root = exact(net["team_design_root_ref"], "team_design_root/v1")
        require(root["task_ref"] == old_root["task_ref"] and root["task_ref"]["logical_id"] == str(event.task_id)
            and root["run_ref"] == old_root["run_ref"], "owner net roots cross exact task/run authority")
        for field in ("team_design_root_ref", "llm_macro_net_ref", "node_refs", "operation_binding_refs", "output_binding_refs"):
            require(same(p[field], net[field]), "owner adoption differs from complete registered candidate closure")
        principal, _ = prior(p["owner_principal_ref"], "principal/v1")
        _, authority = prior(p["control_authority_ref"], "user_authority_decision/v1")
        require(authority["status"] == "effective" and authority["authority_kind"] in {"scope", "architecture"}
            and authority["user_principal_ref"] == p["owner_principal_ref"]
            and root["owner_principal_ref"] == old_root["owner_principal_ref"] == p["owner_principal_ref"]
            and root["task_ref"] in authority["governed_artifact_refs"]
            and p["control_authority_ref"] in root["artifact_refs"]
            and event.producer_principal == str(principal.entity_id), "owner command lacks exact registered control authority")
        command_ref, command_meta = prior(p["owner_command_ref"], "resource_version/v1")
        candidate_ref, candidate_meta = prior(p["candidate_ref"], "resource_version/v1")
        result_ref, result_meta = exact(p["owner_command_result_ref"], "resource_version/v1")
        require(candidate_ref == _resource_from_payload(net["team_net_declaration_resource_ref"]).as_version_ref(),
            "owner candidate differs from registered declaration")
        for body in (command_meta, candidate_meta, result_meta):
            require(body["task_ref"] == root["task_ref"], "owner control Resource crosses exact task")
        submitted, submitted_event = checkpoint_commit(p["submission_checkpoint_ref"], before=event.ordinal)
        old, old_event = checkpoint_commit(p["owner_predecessor_checkpoint_ref"], before=event.ordinal)
        require(submitted["net_instance_ref"] == old["net_instance_ref"] == p["supersedes_net_ref"]
            and submitted_event["ordinal"] <= old_event["ordinal"] and old["settled"] is True,
            "owner submission/predecessor is not drained source-net provenance")
        historical_heads = db.execute("SELECT e.* FROM events e WHERE e.task_id=? AND e.event_type='marking_checkpoint_committed/v1' "
            "AND json_extract(e.payload_json,'$.net_instance_ref.version_id')=? AND e.ordinal<? AND "
            + _CANONICAL_EVENT_SQL + " ORDER BY e.ordinal DESC", (str(event.task_id), str(base.version_id), batch[0]["ordinal"])).fetchall()
        require(bool(historical_heads) and historical_heads[0]["event_id"] == old_event["event_id"],
            "owner predecessor was not the latest historical drained checkpoint")
        # Reconstruct whether every publication open at adoption had settled
        # before it, rather than trusting today's PUBLISHED flag alone.
        for publication in db.execute("SELECT * FROM firing_publications").fetchall():
            opened_rows = db.execute("SELECT * FROM events WHERE transaction_id=? AND event_type='transaction_committed/v1'",
                (publication["opened_transaction_id"],)).fetchall()
            require(len(opened_rows) == 1 and type(opened_rows[0]["ordinal"]) is int,
                "firing publication opening transaction is malformed")
            if opened_rows[0]["ordinal"] >= batch[0]["ordinal"]:
                continue
            reads.transaction(publication["opened_transaction_id"])
            require(publication["state"] == "PUBLISHED" and publication["published_transaction_id"] is not None,
                "owner adoption occurred with an active provisional firing")
            published = reads.transaction(publication["published_transaction_id"])
            require(published["ordinal"] < batch[0]["ordinal"], "owner adoption preceded an already-open firing settlement")
        checkpoint, checkpoint_event = checkpoint_commit(p["owner_candidate_checkpoint_ref"])
        checkpoint_ref = _version_from_payload(p["owner_candidate_checkpoint_ref"])
        for ref in (checkpoint_ref, result_ref):
            publication = reads.publication(ref, ordinary=True)
            require(publication["transaction_id"] == transaction, "owner checkpoint/result lacks same-transaction publication")
        require(checkpoint_event["transaction_id"] == transaction and checkpoint["settled"] is True
            and checkpoint["net_instance_ref"] == p["net_instance_ref"]
            and checkpoint["team_design_root_ref"] == net["team_design_root_ref"]
            and checkpoint["previous_checkpoint_ref"] == p["owner_predecessor_checkpoint_ref"]
            and checkpoint["settlement_delta_ref"] is None and checkpoint["transition_firing_refs"] == []
            and checkpoint["epoch"] == old["epoch"]
            and same(checkpoint["workspace_revision_refs"], old.get("workspace_revision_refs", []))
            and all(checkpoint.get(field) == p[field] for field in ("owner_command_ref", "owner_command_result_ref"))
            and checkpoint_event["command_id"] == event.command_id
            and checkpoint_event["producer_principal"] == str(principal.entity_id),
            "owner adoption checkpoint changes exact source/epoch/workspace/control authority")
        for value in checkpoint["workspace_revision_refs"]:
            exact(value, "workspace_revision/v1")
        request = OwnerAdoptionRequest(event.command_id, ResourceVersionRef(command_ref.entity_id, command_ref.version_id),
            ResourceVersionRef(candidate_ref.entity_id, candidate_ref.version_id), principal,
            _version_from_payload(p["control_authority_ref"]), base, _version_from_payload(p["submission_checkpoint_ref"]),
            candidate, _version_from_payload(p["owner_predecessor_checkpoint_ref"]), checkpoint_ref,
            checkpoint["epoch"], checkpoint["next_token_id"], tuple(checkpoint["attempts"]),
            tuple(_version_from_payload(value) for value in checkpoint["token_refs"]),
            tuple(_version_from_payload(value) for value in checkpoint["workspace_revision_refs"]),
            tuple(OwnerTokenMapping(_version_from_payload(value["old_token_ref"]), _version_from_payload(value["new_token_ref"]))
                for value in p["token_mappings"]),
            tuple(OrdinaryRetirement(_version_from_payload(value["old_token_ref"])) for value in p["ordinary_retirements"]),
            tuple(OwnerInputMapping(ResourceVersionRef(_version_from_payload(value["owner_resource_ref"]).entity_id,
                    _version_from_payload(value["owner_resource_ref"]).version_id), _version_from_payload(value["source_ref"]),
                    value["place"], _version_from_payload(value["new_token_ref"])) for value in p["owner_input_mappings"]))
        command, result = owner_command_document(request), owner_result_document(request)
        require(same(reads.document(command_ref), command) and same(reads.document(result_ref), result),
            "owner command/result complete bytes differ from exact adoption")
        for field in ("token_mappings", "ordinary_retirements", "owner_input_mappings"):
            require(same(result["data"][field], p[field]), "owner adoption full mapping/retirement/input request differs")
        private_control(command_ref, command_meta, "command", command["lineage"])
        private_control(result_ref, result_meta, "result", result["lineage"])
        old_compiled = reads.compiled(_resource_from_payload(old_net["team_net_declaration_resource_ref"]).as_version_ref())
        compiled = reads.compiled(candidate_ref)
        delta = derive_petri_structure_delta(old_compiled.symbolic, compiled.symbolic)
        verify_petri_structure_delta(old_compiled.symbolic, compiled.symbolic, delta)
        require(same(p["petri_structure_delta"], delta.to_dict()), "owner adoption structural delta differs")
        sources, targets = reads.owner_mapping_indexes()
        transfer_rows = [(m["new_token_ref"], [m["old_token_ref"]]) for m in p["token_mappings"]]
        transfer_rows += [(m["new_token_ref"], [m["owner_resource_ref"], m["source_ref"]]) for m in p["owner_input_mappings"]]
        for mapping in p["token_mappings"]:
            require(sources.get(canonical_json(mapping["old_token_ref"])) == [str(event.event_id)],
                "owner source occurrence has multiple committed outgoing mappings")
        require(checkpoint["next_token_id"] == old["next_token_id"] + len(transfer_rows),
            "owner mapping next-token counter differs from original allocator")
        for offset, (target, parents) in enumerate(transfer_rows):
            _, target_body = exact(target, "petri_token/v1")
            require(target_body["token_id"] == old["next_token_id"] + offset,
                "owner mapping token identity differs from original allocator order")
            require(targets.get(canonical_json(target)) == [str(event.event_id)], "owner target occurrence has multiple committed adopters")
            publication = reads.publication(_version_from_payload(target), ordinary=True)
            mapping_transaction = publication["transaction_id"]
            mapping_batch = db.execute("SELECT * FROM transactions WHERE transaction_id=?", (mapping_transaction,)).fetchone()
            require(mapping_batch is not None and mapping_batch["idempotency_key"] == event.command_id + ":owner-mapping",
                "owner mapping transaction has a foreign command key")
            require(mapping_transaction != transaction and publication["command_id"] == event.command_id + ":owner-mapping"
                and reads.transaction(mapping_transaction)["ordinal"] < batch[0]["ordinal"],
                "owner mapping token is not the original pre-adoption command transaction")
            relations = db.execute("SELECT r.* FROM relations r JOIN events e ON e.event_id=r.published_event_id "
                "WHERE r.relation_type='derived_from' AND json_extract(r.source_json,'$.version_id')=? AND "
                + _CANONICAL_EVENT_SQL, (target["version_id"],)).fetchall()
            def relation_ref(value):
                return {"entity_type": value["entity_type"], "logical_id": value["entity_id"], "version_id": value["version_id"]}
            matches = []
            for relation in relations:
                body = reads.relation(relation, ordinary=True)
                if body["metadata"].get("owner_mapping") is True:
                    require(body["strength"] == "strong" and same(body["metadata"], {"owner_mapping": True})
                        and relation["transaction_id"] == mapping_transaction and relation_ref(body["source"]) == target,
                        "owner mapping relation differs from canonical allocator publication")
                    matches.append(canonical_json(relation_ref(body["target"])))
            require(len(matches) == len(parents) and sorted(matches) == sorted(map(canonical_json, parents)),
                "owner mapping lacks unique exact allocator lineage relations")
        _validate_tokens(store, catalog, db, checkpoint, old, old_net, net, root, p, exact, reads.document,
            _terminal_reads=reads)
        authorities = [item for item in objects if item["object_type"] == "run_execution_authority/v1"]
        require(len(authorities) == 1, "owner adoption lacks one same-transaction run successor")
        authority_rows = db.execute("SELECT o.*,e.ordinal FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type='run_execution_authority/v1' AND " + _CANONICAL_EVENT_SQL + " ORDER BY e.ordinal").fetchall()
        require(len({item["logical_id"] for item in authority_rows}) == 1,
            "owner adoption changes the run authority lineage")
        predecessors = [item for item in authority_rows if item["ordinal"] < batch[0]["ordinal"]]
        require(bool(predecessors), "owner adoption lacks original run authority")
        previous = predecessors[-1]
        current_ref = VersionRef("run_execution_authority/v1", TypedId.parse(previous["logical_id"]), TypedId.parse(previous["version_id"]))
        successor_ref = VersionRef("run_execution_authority/v1", TypedId.parse(authorities[0]["logical_id"]), TypedId.parse(authorities[0]["version_id"]))
        current, successor = reads.metadata(current_ref), reads.metadata(successor_ref)
        require(reads.publication(successor_ref, ordinary=True)["transaction_id"] == transaction
            and current["run_execution_authority_ref"] == _ref_payload(current_ref)
            and successor["run_execution_authority_ref"] == _ref_payload(successor_ref)
            and current["task_ref"] == root["task_ref"] and current["run_ref"] == root["run_ref"]
            and current["status"] == successor["status"] == "running"
            and current["terminal_evidence_ref"] is None and successor["terminal_evidence_ref"] is None
            and current["latest_checkpoint_ref"] == p["owner_predecessor_checkpoint_ref"]
            and successor["latest_checkpoint_ref"] == p["owner_candidate_checkpoint_ref"]
            and successor["declaration_ref"] == p["candidate_ref"]
            and successor["declaration_schema_ref"] == candidate_meta["content_schema_ref"] == "rpnh/executable_net/v1",
            "owner run successor changes exact task/run/checkpoint/status authority")
        changed = {"run_execution_authority_ref", "latest_checkpoint_ref", "declaration_ref", "declaration_schema_ref"}
        require(same({k: v for k, v in current.items() if k not in changed},
            {k: v for k, v in successor.items() if k not in changed}), "owner adoption reinterprets accumulated run authority")
        relations = db.execute("SELECT * FROM relations WHERE relation_type='derived_from' "
            "AND json_extract(source_json,'$.version_id')=? AND json_extract(metadata_json,'$.authority_role')='mutable_stage'",
            (str(successor_ref.version_id),)).fetchall()
        require(len(relations) == 1, "owner successor lacks unique mutable-stage relation")
        relation = reads.relation(relations[0], ordinary=True)
        require(relations[0]["transaction_id"] == transaction and relation["strength"] == "strong"
            and relation["source"] == {"entity_type": successor_ref.entity_type, "entity_id": str(successor_ref.entity_id), "version_id": str(successor_ref.version_id)}
            and relation["target"] == {"entity_type": candidate_ref.entity_type, "entity_id": str(candidate_ref.entity_id), "version_id": str(candidate_ref.version_id)},
            "owner mutable-stage relation differs from exact candidate")
        return {field: p[field] for field in OWNER_FIELDS}
    except FileNotFoundError as exc:
        raise RegistryCorruptError("owner terminal witness registered payload is absent") from exc
    except OSError as exc:
        raise TerminalReadIncomplete("STORAGE_READ_UNAVAILABLE", "owner terminal witness could not be read") from exc
    except (KeyError, TypeError, ValueError, RegistryConflict) as exc:
        raise RegistryCorruptError("owner terminal witness violates the original adoption gate") from exc


def _validate_tokens(store, catalog, db, checkpoint, old, old_net, net, root, p, exact, document, *, _terminal_reads=None):
    from .event_store import RegistryConflict
    from ..executable_net import load_compiled_net
    from ..petri_primitives import (
        derive_petri_structure_delta, verify_petri_structure_delta,
    )
    from ..runtime_net import RuntimeNet
    from ..marking import validate_typed_marking_state
    from .module_runtime import hydrate_module_resource_plan
    from .resources import (AttemptCounterAuthority, PetriTokenState, PetriLeaseClaim,
                           PetriContinuation, PetriOverrideWarning)
    from .owner_mapping import registered_owner_input, ordinary_retirement_reason, transfer_consumer, token_state
    from .event_store import _CANONICAL_EVENT_SQL
    def lineage(source, target):
        rows = db.execute("SELECT r.source_json,r.target_json FROM relations r JOIN events e "
            "ON e.event_id=r.published_event_id WHERE r.relation_type='derived_from' AND "
            "r.strength='strong' AND json_extract(r.source_json,'$.version_id')=? AND "
            f"{_CANONICAL_EVENT_SQL}", (source["version_id"],)).fetchall()
        def relation_ref(value):
            value = json.loads(value)
            return {"entity_type": value["entity_type"], "logical_id": value["entity_id"], "version_id": value["version_id"]}
        if not any(relation_ref(r["source_json"]) == source and relation_ref(r["target_json"]) == target for r in rows):
            raise RegistryConflict("owner transfer lacks exact ordinary registered derived_from lineage")
    # Resource-plan hydration needs a Core reader only; it publishes nothing.
    from ._registry import _RegistryCore
    core = (_RegistryCore(store.path.parent.parent, create=False, read_only=True, catalog=catalog)
            if _terminal_reads is None else None)
    candidate = _version_from_payload(p["net_instance_ref"])
    root_ref = _version_from_payload(net["team_design_root_ref"])
    declaration = _resource_from_payload(net["team_net_declaration_resource_ref"])
    compiled = (load_compiled_net(document(declaration.as_version_ref())) if _terminal_reads is None
                else _terminal_reads.compiled(declaration.as_version_ref()))
    plan = (hydrate_module_resource_plan(core, compiled, candidate, net, root_ref, root, declaration)
            if _terminal_reads is None else _terminal_reads.resource_plan(compiled, candidate, net, root_ref, root, declaration))
    structure = RuntimeNet(compiled, net_ref=candidate, resource_plan=plan)
    old_refs = {canonical_json(ref): ref for ref in old["token_refs"]}
    new_refs = {canonical_json(ref): ref for ref in checkpoint["token_refs"]}
    if _terminal_reads is not None and (len(old_refs) != len(old["token_refs"])
            or len(new_refs) != len(checkpoint["token_refs"])):
        raise RegistryConflict("owner checkpoint repeats an exact token occurrence")
    mapped_old, mapped_new, retired = set(), set(), set()
    preserved = ("resource_ref", "work_resource_ref", "kind", "verdict", "continuation",
                 "lease_identity_ref", "lease_claims", "override_warning")
    old_declaration = _resource_from_payload(old_net["team_net_declaration_resource_ref"])
    old_compiled = (load_compiled_net(document(old_declaration.as_version_ref())) if _terminal_reads is None
                    else _terminal_reads.compiled(old_declaration.as_version_ref()))
    structure_delta = derive_petri_structure_delta(
        old_compiled.symbolic, compiled.symbolic)
    verify_petri_structure_delta(
        old_compiled.symbolic, compiled.symbolic, structure_delta)
    if p["petri_structure_delta"] != structure_delta.to_dict():
        raise RegistryConflict(
            "owner adoption Petri structure delta differs from registered declarations")
    old_places = {place.name: place for place in old_compiled.symbolic.places}
    places = {place.name: place for place in compiled.symbolic.places}
    for mapping in p["token_mappings"]:
        _, source = exact(mapping["old_token_ref"], "petri_token/v1")
        _, target = exact(mapping["new_token_ref"], "petri_token/v1")
        a, b = canonical_json(mapping["old_token_ref"]), canonical_json(mapping["new_token_ref"])
        if (source["petri_token_ref"] != mapping["old_token_ref"]
                or a not in old_refs or b not in new_refs or a in mapped_old or b in mapped_new
                or source["net_instance_ref"] != p["supersedes_net_ref"]
                or source["epoch"] != old["epoch"] or source["consumed_by"] is not None
                or any((source[f] != target[f] if _terminal_reads is None
                        else canonical_json(source[f]) != canonical_json(target[f])) for f in preserved)
                or target["place"] not in places or source["place"] not in old_places
                or old_places[source["place"]].token_kind != places[target["place"]].token_kind
                or old_places[source["place"]].reusable != places[target["place"]].reusable
                or target["producer"] is not None
                or target["consumer"] != transfer_consumer(structure, target["place"])):
            raise RegistryConflict("owner token mapping loses or repeats exact ordinary source authority")
        lineage(mapping["new_token_ref"], mapping["old_token_ref"])
        identities = (target["lease_identity_ref"], *(c["lease_identity_ref"] for c in target["lease_claims"]))
        for value in identities:
            if (value is not None and value["entity_type"] == "logical_artifact_slot/v1"
                    and _version_from_payload(value) not in plan.slot_refs.values()):
                raise RegistryConflict("NEEDS_MARKING_DECISION: NET-scoped slot lease cannot preserve old exact identity")
        mapped_old.add(a); mapped_new.add(b)
    terminal_places = _terminal_source_places(old_compiled)
    for retirement in p["ordinary_retirements"]:
        a = canonical_json(retirement["old_token_ref"])
        _, source = exact(retirement["old_token_ref"], "petri_token/v1")
        if (a not in old_refs or a in mapped_old or a in retired
                or source["petri_token_ref"] != retirement["old_token_ref"]
                or source["net_instance_ref"] != p["supersedes_net_ref"]
                or source["epoch"] != old["epoch"] or source["consumed_by"] is not None
                or retirement["source_checkpoint_ref"] != p["owner_predecessor_checkpoint_ref"]
                or source["place"] not in old_places or old_places[source["place"]].reusable
                or source["place"] in terminal_places
                or source["lease_identity_ref"] is not None or source["lease_claims"]
                or source["kind"] is not None or source["continuation"] is not None
                or source["work_resource_ref"] is not None
                or ordinary_retirement_reason(token_state(
                    _version_from_payload(retirement["old_token_ref"]), source), old_places[source["place"]], terminal_places)):
            raise RegistryConflict("owner retirement lacks ordinary source linkage or discards unresolved authority")
        if source["resource_ref"] is not None:
            ref = _resource_from_payload(source["resource_ref"])
            _, metadata = exact(_ref_payload(ref.as_version_ref()), "resource_version/v1")
            if metadata["task_ref"] != root["task_ref"]:
                raise RegistryConflict("ordinary retirement source Resource crosses task authority")
        retired.add(a)
    if mapped_old | retired != set(old_refs):
        raise RegistryConflict("owner mapping/ordinary retirement must account for every old occurrence")
    candidate_terminals = _terminal_source_places(compiled)
    input_new = set()
    for mapping in p["owner_input_mappings"]:
        resource_ref = _version_from_payload(mapping["owner_resource_ref"])
        ref = ResourceVersionRef(resource_ref.entity_id, resource_ref.version_id)
        exact(mapping["owner_resource_ref"], "resource_version/v1")
        exact(mapping["source_ref"], "bootstrap_command/v1")
        _, token = exact(mapping["new_token_ref"], "petri_token/v1")
        b = canonical_json(mapping["new_token_ref"])
        if (b not in new_refs or b in mapped_new or b in input_new or mapping["place"] not in places
                or mapping["place"] in candidate_terminals or places[mapping["place"]].token_kind != "data"
                or places[mapping["place"]].colours
                or token["place"] != mapping["place"] or token["producer"] is not None
                or token["consumer"] != transfer_consumer(structure, token["place"])
                or token["resource_ref"] != {"resource_id": str(ref.resource_id), "resource_version_id": str(ref.resource_version_id)}
                or any(token[f] is not None for f in ("work_resource_ref", "kind", "verdict", "continuation", "lease_identity_ref", "override_warning"))
                or token["lease_claims"]):
            raise RegistryConflict("owner input mapping fabricates or repeats token/terminal/unsettled authority")
        if _terminal_reads is None:
            registered_owner_input(core, ref, task_ref=root["task_ref"], root=root,
                source_ref=_version_from_payload(mapping["source_ref"]))
        else:
            _terminal_reads.owner_input(ref, task_ref=root["task_ref"], root=root,
                source_ref=_version_from_payload(mapping["source_ref"]))
        lineage(mapping["new_token_ref"], mapping["owner_resource_ref"])
        lineage(mapping["new_token_ref"], mapping["source_ref"])
        input_new.add(b)
    # No candidate fresh M0 seeds: only transferred or explicit registered inputs.
    _require_mapped_new_tokens(set(new_refs), mapped_new | input_new)
    states = []
    for value in checkpoint["token_refs"]:
        ref, token = exact(value, "petri_token/v1")
        catalog.validate_instance("petri_token/v1", category="object", instance=token)
        if (token["petri_token_ref"] != value or token["net_instance_ref"] != p["net_instance_ref"]
                or token["epoch"] != checkpoint["epoch"] or token["consumed_by"] is not None
                or token["place"] not in places):
            raise RegistryConflict("owner marking has foreign/unsettled token authority")
        def resource(value, *, check_place=False):
            if value is None: return None
            resource_ref = _resource_from_payload(value)
            _, metadata = exact(_ref_payload(resource_ref.as_version_ref()), "resource_version/v1")
            if metadata["task_ref"] != root["task_ref"]:
                raise RegistryConflict("owner token resource crosses task authority")
            if check_place:
                _require_place_content_schema(places[token["place"]], metadata["content_schema_ref"])
            return resource_ref
        for value in (token["lease_identity_ref"], *(c["lease_identity_ref"] for c in token["lease_claims"])):
            if value is not None: exact(value, value["entity_type"])
        states.append(PetriTokenState(token_ref=ref, token_id=token["token_id"], place=token["place"],
            epoch=token["epoch"], producer=token["producer"], consumer=token["consumer"],
            resource_ref=resource(token["resource_ref"], check_place=True), work_resource_ref=resource(token["work_resource_ref"]),
            kind=token["kind"], consumed_by=token["consumed_by"], verdict=token["verdict"],
            override_warning=None if token["override_warning"] is None else PetriOverrideWarning(**token["override_warning"]),
            continuation=None if token["continuation"] is None else PetriContinuation(
                token["continuation"]["round"], resource(token["continuation"]["source_ref"])),
            lease_identity_ref=None if token["lease_identity_ref"] is None else _version_from_payload(token["lease_identity_ref"]),
            lease_claims=tuple(PetriLeaseClaim(_version_from_payload(c["lease_identity_ref"]),
                resource(c["expected_resource_ref"]), c["access_mode"], c.get("staging_place")) for c in token["lease_claims"])))
    validate_typed_marking_state(structure, epoch=checkpoint["epoch"], next_token_id=checkpoint["next_token_id"],
        attempts=tuple(AttemptCounterAuthority(a["transition_id"], a["highest_issued"]) for a in checkpoint["attempts"]),
        tokens=tuple(sorted(states, key=lambda t: t.token_id)), require_token_refs=True)
    capacities = {place.name: place.capacity for place in compiled.symbolic.places}
    for place, capacity in capacities.items():
        if capacity is not None and sum(t.place == place for t in states) > capacity:
            raise RegistryConflict("owner marking exceeds declared local place capacity")


__all__ = ("OwnerAdoptionRequest", "OwnerAdoptionStaged", "OwnerTokenMapping", "OrdinaryRetirement", "OwnerInputMapping",
           "owner_command_document", "owner_result_document", "stage_owner_adoption")
