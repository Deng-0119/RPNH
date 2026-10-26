"""Read-only owner edit receipts projected from the original Registry facts.

There is no receipt table, recovery queue or adoption authority here. Historical
success requires the original command/result/checkpoint/adoption closure.
"""
from __future__ import annotations

from .registry._event_store.adoption_reads import AdoptionPrefixReads
from .registry.event_store import RegistryCorruptError, _CANONICAL_EVENT_SQL, verified_adoption_prefix
from .registry.identities import TypedId
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


def _project(data, **fields):
    for key, value in fields.items():
        if key in data and canonical_json(data[key]) != canonical_json(value):
            raise RegistryCorruptError("owner edit receipt fields contradict the registered result")
    return {**data, **fields}


def read_edit_receipt(owner, command_id):
    """Read the latest recorded edit status without advancing or repairing it.

    The caller is the existing owner, including its exact task/principal filter.
    Control documents use the existing strict JSON reader. Adoption uses the
    normal prefix contract, which also supports workspace tar revisions. These
    are fixed read policies, never an unsupported-reader fallback or permission
    to execute/restore the historical graph.
    """
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("edit receipt requires one explicit edit_command_id")
    core = owner._core
    with core.event_store.connect() as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        reads = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, terminal=True)
        candidates = [(ref, body) for ref, body in owner.control.records("candidate", _reads=reads)
                      if body["command_id"] == command_id]
        results = [(ref, body) for ref, body in owner.control.records("result", _reads=reads)
                   if body["command_id"] == command_id]
        if not candidates:
            commands = [body for _, body in owner.control.records("command", _reads=reads)
                        if body["command_id"] == command_id]
            if commands or results:
                return {"status": "INCOMPLETE", "reason": "command or result exists without an edit candidate"}
            return {"status": "NOT_FOUND"}
        if len(candidates) != 1:
            raise RegistryCorruptError("owner edit command has conflicting immutable candidates")
        if not results:
            return {"status": "INCOMPLETE", "reason": "candidate has no recorded result"}
        adopted = [(ref, body) for ref, body in results if body["data"].get("status") == "ADOPTED"]
        if adopted and (len(adopted) != 1 or adopted[0][0] != results[-1][0]):
            raise RegistryCorruptError("owner edit has conflicting adoption results")
        result_ref, result = results[-1]
        data = result["data"]
        if data.get("status") not in {
                "REGISTERED", "DRAINING", "NEEDS_MARKING_DECISION", "CONFLICT", "RETRACTED", "ADOPTED"}:
            raise RegistryCorruptError("owner edit result has an unsupported status")
        receipt_ref = ref_payload(result_ref.as_version_ref())
        if data["status"] != "ADOPTED":
            return _project(data, result_ref=receipt_ref)
        rows = db.execute("SELECT e.* FROM events e WHERE e.task_id=? "
            "AND e.event_type='net_adopted/v1' AND e.command_id=? AND " + _CANONICAL_EVENT_SQL,
            (str(core.task_id), command_id)).fetchall()
        if len(rows) != 1:
            raise RegistryCorruptError("owner edit receipt lacks one canonical adoption")
        event = core.event_store._row_to_envelope(rows[0])
        payload = event.payload
        if (canonical_json(payload.get("owner_command_result_ref")) != canonical_json(receipt_ref)
                or canonical_json(payload.get("owner_principal_ref")) != canonical_json(ref_payload(owner.principal_ref))):
            raise RegistryCorruptError("owner edit receipt differs from exact result/owner authority")
        verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
            adoption_event_id=TypedId.parse(rows[0]["event_id"], expected="event"),
            _db=db)
        # Align the displayed original result with the existing event witness
        # using canonical bytes, including mappings the normal reader need not
        # expose. This is a receipt projection, not a new adoption validator.
        expected = {"command": "edit", "status": "ADOPTED",
            "base_net_ref": payload["supersedes_net_ref"],
            "candidate_net_ref": payload["net_instance_ref"],
            "predecessor_checkpoint_ref": payload["owner_predecessor_checkpoint_ref"],
            "checkpoint_ref": payload["owner_candidate_checkpoint_ref"],
            **{field: payload[field] for field in (
                "owner_command_ref", "candidate_ref", "owner_principal_ref", "control_authority_ref",
                "submission_checkpoint_ref", "token_mappings", "ordinary_retirements", "owner_input_mappings")}}
        if any(field not in data or canonical_json(data[field]) != canonical_json(value)
               for field, value in expected.items()):
            raise RegistryCorruptError("owner edit result differs from the canonical adoption witness")
        if (result["target"] != payload["net_instance_ref"]["logical_id"]
                or canonical_json(result["lineage"]) != canonical_json([
                    payload["owner_command_ref"], payload["candidate_ref"]])):
            raise RegistryCorruptError("owner edit result target/lineage differs from the adoption witness")
        return _project(data, net_ref=payload["net_instance_ref"], result_ref=receipt_ref,
            transaction_id=str(event.transaction_id))


__all__ = ("read_edit_receipt",)
