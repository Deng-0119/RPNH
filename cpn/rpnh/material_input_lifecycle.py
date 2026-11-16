"""Original Owner input-protocol abandonment; never delete body bytes."""
from __future__ import annotations

from .registry.event_store import RegistryConflict
from .registry.publication import _version_from_payload
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload
from .registry.user_collaboration import check_handle
from .registry._event_store.material_input_lifecycle import (
    ACTION, PROTOCOL, validate_params, transaction_key, _original_preparation,
    assert_unused, read_abandonment, abandonment_receipt,
)
from .registry._event_store.material_control import require_session


def abandon_material_input(owner, params, command_id, *, collaboration_action):
    validate_params(params)
    core = owner._core
    action = check_handle(core.event_store, collaboration_action)
    expected = {"owner_command": ACTION, "command_id": command_id, "params": params}
    if (command_id != transaction_key(params["input_ref"]) or action.kind != ACTION
            or action.creation != canonical_json(expected)):
        raise RegistryConflict("input abandonment requires its exact stable HOST action")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        require_session(core.event_store, db, core.task_id, action.session)
        _original, _receipt, input_key, preparation = _original_preparation(
            core.event_store, db, core.task_id, params["input_ref"])
        if (input_key != params["input_command_id"] or preparation["actor"] != action.session.actor
                or preparation["session_ref"] != ref_payload(action.session.ref)):
            raise RegistryConflict("input abandonment differs from its preparation actor or identity")
        terminal = read_abandonment(core.event_store, db, core.task_id, params["input_ref"])
        if terminal is not None:
            return abandonment_receipt(terminal)
        assert_unused(core.event_store, db, core.task_id, params["input_ref"])
    tx = core.begin(idempotency_key=command_id)
    tx.bind_collaboration_action(action)
    owner.control.publish("result", command_id, None,
        {"protocol": PROTOCOL, "input_ref": params["input_ref"], "input_command_id": input_key,
         "status": "ABANDONED", "abandonment_transaction_id": str(tx.transaction_id),
         "user_action": action.material()},
        lineage=(_version_from_payload(params["input_ref"]),), transaction=tx,
        descriptors={"owner_abandoned_input_version_id": params["input_ref"]["version_id"]},
        suffix="material-input-abandon")
    tx.commit()
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        terminal = read_abandonment(core.event_store, db, core.task_id, params["input_ref"])
        if terminal is None:
            raise RegistryConflict("input abandonment commit has no exact original result")
        return abandonment_receipt(terminal)


__all__ = ("abandon_material_input",)
