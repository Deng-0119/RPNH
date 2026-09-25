"""Task-wide compare-and-append commands for shared Registry invariants."""

from __future__ import annotations

from typing import Any, Mapping

from .models import EventEnvelope, PendingEvent, VersionRef


class TaskControlLedger:
    """Task-wide compare-and-append commands for shared invariants.

    Phase 1 provides linearization only. Phase 3 supplies the approved cap and
    terminal-reserve admission policy; this class does not invent defaults.
    """

    def __init__(self, service: Any) -> None:
        self.service = service

    def stage_owner_net_adoption(self, transaction: Any, *, net_instance_ref: VersionRef,
                                supersedes_net_ref: VersionRef,
                                owner_provenance: Mapping[str, Any],
                                owner_principal_ref: VersionRef) -> None:
        """Stage the separate owner witness; the Core commit validates the bridge."""
        from .event_store import (
            RegistryConflict,
            _exact_ref_payload,
            validate_registered_net_closure,
        )
        from .owner_adoption import OWNER_FIELDS
        if (transaction.event_store is not self.service.event_store
                or set(owner_provenance) != set(OWNER_FIELDS)):
            raise RegistryConflict("owner adoption requires this Core transaction/exact provenance")
        closure = validate_registered_net_closure(
            self.service.event_store, self.service.catalog, net_instance_ref)
        payload = {field: closure[field] for field in (
            "team_design_root_ref", "llm_macro_net_ref",
            "node_refs", "operation_binding_refs", "output_binding_refs")}
        payload.update(net_instance_ref=_exact_ref_payload(net_instance_ref),
                       supersedes_net_ref=_exact_ref_payload(supersedes_net_ref), **dict(owner_provenance))
        transaction.append(PendingEvent(event_type="net_adopted/v1", criticality="authoritative",
            stream_id=f"task:{self.service.task_id}:control", aggregate_id=str(net_instance_ref.entity_id),
            aggregate_type="task_control", idempotency_key=transaction.idempotency_key,
            command_id=transaction.idempotency_key, payload=payload,
            payload_schema_ref="registry_v1/net_adopted/v1", task_control=True,
            producer_principal=str(owner_principal_ref.entity_id)))

    def append(self, *, event_type: str, aggregate_id: str,
               payload: Mapping[str, Any], idempotency_key: str) -> tuple[EventEnvelope, ...]:
        from .event_store import RegistryConflict
        if (event_type == "provider_attempt_reserved/v1"
                and self.service.event_store
                .current_task_recovery_manifest_row() is None):
            raise RegistryConflict(
                "provider call reservation requires a committed task recovery manifest")
        transaction = self.service.begin(idempotency_key=idempotency_key)
        transaction.append(PendingEvent(
            event_type=event_type, criticality="authoritative",
            stream_id=f"task:{self.service.task_id}:control",
            aggregate_id=aggregate_id, aggregate_type="task_control",
            idempotency_key=idempotency_key, command_id=idempotency_key,
            payload=dict(payload), payload_schema_ref=f"registry_v1/{event_type}",
            task_control=True))
        return transaction.commit()

    def adopt_net(self, *, net_instance_ref: VersionRef, idempotency_key: str,
                  supersedes_net_ref: VersionRef | None = None,
                  ) -> tuple[EventEnvelope, ...]:
        """Adopt one fully registered exact workflow-design/net closure.

        The Registry is append-only and grows throughout execution.  This
        command freezes only the exact candidate version and its explicit
        predecessor; it never treats a projection or object insertion order as
        active-net authority.
        """
        from .event_store import (
            RegistryConflict,
            _exact_ref_payload,
            validate_registered_net_closure,
            verified_adoption_head,
        )

        if (not isinstance(net_instance_ref, VersionRef)
                or net_instance_ref.entity_type != "net_instance/v1"
                or net_instance_ref.entity_id.kind != "net_instance"
                or net_instance_ref.version_id.kind != "net_instance_version"):
            raise RegistryConflict("net adoption requires an exact net_instance/v1 ref")
        closure = validate_registered_net_closure(
            self.service.event_store, self.service.catalog, net_instance_ref)
        payload = {
            "net_instance_ref": _exact_ref_payload(net_instance_ref),
            "team_design_root_ref": closure["team_design_root_ref"],
            "llm_macro_net_ref": closure["llm_macro_net_ref"],
            "node_refs": closure["node_refs"],
            "operation_binding_refs": closure["operation_binding_refs"],
            "output_binding_refs": closure["output_binding_refs"],
            "supersedes_net_ref": _exact_ref_payload(supersedes_net_ref),
        }
        existing = tuple(
            event for event in self.service.event_store.list_events()
            if event.idempotency_key == idempotency_key)
        if existing:
            adopted = [event for event in existing
                       if event.event_type == "net_adopted/v1"]
            if len(adopted) != 1 or dict(adopted[0].payload) != payload:
                raise RegistryConflict(
                    "net-adoption idempotency key was reused for different input")
            return existing
        adoptions = [
            event for event in self.service.event_store.list_events()
            if event.task_id == self.service.task_id
            and event.event_type == "net_adopted/v1"
        ]
        if adoptions:
            active = verified_adoption_head(
                self.service.event_store, self.service.catalog,
                self.service.task_id)
            if supersedes_net_ref != active or net_instance_ref == active:
                raise RegistryConflict(
                    "net adoption must exactly supersede the verified active exact version")
        elif supersedes_net_ref is not None:
            raise RegistryConflict(
                "initial net adoption cannot supersede a missing active net")
        return self.append(
            event_type="net_adopted/v1",
            aggregate_id=str(net_instance_ref.entity_id),
            payload=payload,
            idempotency_key=idempotency_key,
        )
