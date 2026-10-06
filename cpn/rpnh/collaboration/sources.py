"""Explicit local source registration through the existing trusted gateway."""

from __future__ import annotations

from dataclasses import dataclass
import json

from cpn.rpnh.registry._event_store.source_identity import (
    SOURCE_BINDING_TYPE, SOURCE_BINDING_SCHEMA, read_source_binding,
    source_binding_id, source_binding_version_id, source_command_key,
)
from cpn.rpnh.registry.identities import TypedId
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.object_store import ObjectIntegrityError
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload


@dataclass(frozen=True, slots=True)
class LocalSourceIdentity:
    source_id: str
    event_id: TypedId
    task_ref: VersionRef
    native_run_ref: VersionRef
    bootstrap_command_ref: VersionRef
    command_id: str
    writer_fencing_epoch: int


def get_local_source_identity(core) -> LocalSourceIdentity | None:
    """Read the explicit association; absent binding stays unbound."""
    with core.event_store.connect() as db:
        # The EventStore connection is normally autocommit. Keep binding,
        # stream head, and owner evidence at one read cut while a writer runs.
        db.execute("BEGIN")
        row = read_source_binding(db, core.catalog, core.task_id)
    if row is None:
        return None
    payload = json.loads(row["binding_metadata_json"])
    return LocalSourceIdentity(
        payload["source_id"], TypedId.parse(row["event_id"], expected="event"),
        _version_from_payload(payload["task_ref"]), _version_from_payload(payload["native_run_ref"]),
        _version_from_payload(payload["bootstrap_command_ref"]), payload["command_id"], row["writer_fencing_epoch"],
    )


def _bind_source_identity(core, *, task_ref: VersionRef, bootstrap_ref: VersionRef,
                          source_id: str, command_id: str) -> LocalSourceIdentity:
    """Called only by the trusted RegistryRegistrationGateway, never data IR."""
    if core.read_only:
        raise TypeError("read-only Registry cannot bind a source identity")
    run_ref = json.loads(core.event_store.get_meta("native_run_ref") or "null")
    payload = {
        "schema_version": SOURCE_BINDING_SCHEMA, "source_id": source_id,
        "binding_ref": ref_payload(VersionRef(SOURCE_BINDING_TYPE, source_binding_id(core.task_id),
                                               source_binding_version_id(core.task_id, command_id))),
        "task_ref": ref_payload(task_ref), "native_run_ref": run_ref,
        "bootstrap_command_ref": ref_payload(bootstrap_ref), "command_id": command_id,
    }
    core.catalog.validate_instance(SOURCE_BINDING_TYPE, category="object", instance=payload)
    key = source_command_key(command_id)
    tx = core.begin(idempotency_key=key)
    try:
        tx.prewrite(object_type=SOURCE_BINDING_TYPE, logical_id=source_binding_id(core.task_id),
                    version_id=source_binding_version_id(core.task_id, command_id),
                    payload=canonical_json(payload), metadata=payload, media_type="application/json",
                    schema_ref=SOURCE_BINDING_SCHEMA)
    except ObjectIntegrityError as exc:
        raise RegistryConflict("source binding command conflicts with immutable prior material") from exc
    tx.commit()
    result = get_local_source_identity(core)
    if result is None:
        raise RuntimeError("source registration committed without its binding fact")
    return result
