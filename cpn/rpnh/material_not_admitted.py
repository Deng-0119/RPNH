"""Owner closure for an accepted material command with no admission.

Only original current actor/execution/target failures may close a command.
Closing an editor, cancelling a read, unavailable capacity and unknown writes
are not command cancellation or terminal evidence. This port performs no tool
work and makes no automatic retry after a changed precondition.
"""
from __future__ import annotations

from .registry.event_store import RegistryConflict
from .registry.publication import _version_from_payload
from .registry._event_store.material_control import read_accepted_material_command, reference
from .registry._event_store.material_not_admitted import (
    RESULT_PROTOCOL, assert_unadmitted, current_reason, read_not_admitted,
    receipt_view, transaction_key,
)


def close_not_admitted(owner, *, material_command_ref):
    """Return a proved original result; never create a substitute firing."""
    from .run import RunOwner
    if not isinstance(owner, RunOwner):
        raise TypeError("not_admitted closure requires the original RunOwner")
    command_ref = reference(material_command_ref, "resource_version/v1")
    core = owner._core
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        command = read_accepted_material_command(core.event_store, db, core.task_id,
                                                  command_ref, current=False)
        terminal = read_not_admitted(core.event_store, db, core.task_id, command_ref)
        if terminal is not None:
            return receipt_view(command, command_ref, terminal)
        assert_unadmitted(core.event_store, db, core.task_id, command_ref)
        reason = current_reason(core.event_store, db, core.task_id, command_ref)
    key = transaction_key(command_ref, reason["code"])
    tx = core.begin(idempotency_key=key)
    data = {"protocol": RESULT_PROTOCOL, "result_kind": "not_admitted", "command_ref": command_ref,
        "rejection_transaction_id": str(tx.transaction_id), "error": reason}
    try:
        owner.control.publish("result", command["command_id"], command["target"], data,
            lineage=(_version_from_payload(command_ref),), transaction=tx,
            suffix="material-not-admitted:" + key)
        tx.commit()
    except RegistryConflict:
        # A competing exact terminal may have committed after the read cut.
        # Reconstruct it; all other conflicts retain their original meaning.
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            terminal = read_not_admitted(core.event_store, db, core.task_id, command_ref)
            if terminal is not None:
                return receipt_view(command, command_ref, terminal)
        raise
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        terminal = read_not_admitted(core.event_store, db, core.task_id, command_ref)
        if terminal is None:
            raise RegistryConflict("not_admitted commit has no exact original result")
        return receipt_view(command, command_ref, terminal)


__all__ = ("close_not_admitted",)
