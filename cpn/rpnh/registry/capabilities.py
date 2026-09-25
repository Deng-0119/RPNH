"""Rebuildable capability facts; metadata discovery is not content access."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .identities import TypedId, new_id
from .invocations import InvocationLifecycle
from .models import PendingEvent, VersionRef
from .schema_catalog import canonical_json


class CapabilityError(RuntimeError):
    pass


class CapabilityService:
    def __init__(self, registry: Any) -> None:
        self.registry = registry

    def issue(self, *, target_invocation_id: TypedId,
              operation_binding_ref: VersionRef,
              resource_version_ids: Iterable[TypedId],
              revocation_condition: str, idempotency_key: str,
              activation_id: TypedId | None = None,
              parent_grant_id: TypedId | None = None) -> TypedId:
        versions = tuple(resource_version_ids)
        if not versions or any(item.kind != "resource_version" for item in versions):
            raise CapabilityError("capabilities bind one or more exact resource versions")
        if (target_invocation_id.kind != "invocation"
                or (activation_id is not None
                    and not isinstance(activation_id, TypedId))):
            raise CapabilityError("capability target requires exact invocation/optional activation identities")
        try:
            invocation_row = self.registry._logical_object(
                target_invocation_id, "invocation/v1")
        except Exception as exc:
            raise CapabilityError("capability target invocation is not registered") from exc
        invocation_ref = VersionRef(
            "invocation/v1", target_invocation_id,
            TypedId.parse(
                invocation_row["version_id"], expected="invocation_version"))
        try:
            context = InvocationLifecycle(self.registry).hydrate_context(invocation_ref)
            InvocationLifecycle(self.registry).revalidate_io(
                context, boundary="capability-issue")
        except Exception as exc:
            raise CapabilityError(
                "capability target invocation is not active") from exc
        activation = (context.authorization_lifetime_activation_ref
                      or context.activation_ref)
        if activation_id is not None and (
                activation is None or activation.entity_id != activation_id):
            raise CapabilityError("capability activation does not match target invocation")
        if operation_binding_ref != context.operation_binding_ref:
            raise CapabilityError(
                "capability operation binding does not match target invocation")
        lifetime_ref = context.own_transition_firing_ref or context.invocation_ref
        for version_id in versions:
            row = self.registry.event_store.object_row(version_id)
            if row is None or row["object_type"] != "resource_version/v1":
                raise CapabilityError(
                    f"capability resource version is not registered: {version_id}")
        if parent_grant_id is not None:
            try:
                self.registry._logical_object(parent_grant_id, "capability_grant/v1")
            except Exception as exc:
                raise CapabilityError("parent capability grant is not registered") from exc
        grant_id = new_id("grant")
        version_id = new_id("grant")
        payload = {
            "grant_id": str(grant_id), "target_invocation_id": str(target_invocation_id),
            "activation_id": str(activation_id) if activation_id is not None else None,
            "lifetime_ref": {
                "entity_type": lifetime_ref.entity_type,
                "logical_id": str(lifetime_ref.entity_id),
                "version_id": str(lifetime_ref.version_id),
            },
            "operation_binding_ref": {
                "entity_type": operation_binding_ref.entity_type,
                "logical_id": str(operation_binding_ref.entity_id),
                "version_id": str(operation_binding_ref.version_id),
            },
            "resource_version_ids": [str(item) for item in versions],
            "revocation_condition": revocation_condition,
            "parent_grant_id": str(parent_grant_id) if parent_grant_id else None,
        }
        transaction = self.registry.begin(
            idempotency_key=idempotency_key,
            task_round_id=context.task_round_ref.entity_id,
            net_instance_id=context.net_instance_ref.entity_id)
        transaction.prewrite(
            object_type="capability_grant/v1", logical_id=grant_id,
            version_id=version_id, payload=canonical_json(payload), metadata=payload,
            media_type="application/json", schema_ref="registry_v1/capability_grant/v1",
            producer_invocation_id=target_invocation_id)
        transaction.append(PendingEvent(
            event_type="capability_issued/v1", criticality="authoritative",
            stream_id=f"grant:{grant_id}", aggregate_id=str(grant_id),
            aggregate_type="capability_grant/v1", idempotency_key=idempotency_key,
            command_id=idempotency_key, payload=payload,
            payload_schema_ref="registry_v1/capability_issued/v1", task_control=True,
            producer_principal=str(context.principal_ref.entity_id),
            producer_invocation_id=target_invocation_id))
        transaction.commit()
        return grant_id

    def transition(self, grant_id: TypedId, action: str, *, payload: Mapping[str, Any],
                   idempotency_key: str) -> None:
        event_type = {
            "activate": "capability_activated/v1", "allow": "capability_allowed/v1",
            "deny": "capability_denied/v1", "revoke": "capability_revoked/v1",
        }.get(action)
        if event_type is None:
            raise CapabilityError(f"unknown capability action {action!r}")
        try:
            grant_row = self.registry._logical_object(grant_id, "capability_grant/v1")
            grant = self.registry.get_version(TypedId.parse(grant_row["version_id"], expected="grant"))
            target_id = TypedId.parse(grant.metadata["target_invocation_id"], expected="invocation")
            invocation_row = self.registry._logical_object(target_id, "invocation/v1")
            invocation_ref = VersionRef("invocation/v1", target_id,
                TypedId.parse(invocation_row["version_id"], expected="invocation_version"))
            context = InvocationLifecycle(self.registry).hydrate_context(invocation_ref)
        except Exception as exc:
            raise CapabilityError("capability transition grant/target invocation is not registered") from exc
        lifetime_ref = context.own_transition_firing_ref or context.invocation_ref
        expected_lifetime = {"entity_type": lifetime_ref.entity_type,
            "logical_id": str(lifetime_ref.entity_id), "version_id": str(lifetime_ref.version_id)}
        expected_binding = {"entity_type": context.operation_binding_ref.entity_type,
            "logical_id": str(context.operation_binding_ref.entity_id),
            "version_id": str(context.operation_binding_ref.version_id)}
        activation = context.authorization_lifetime_activation_ref or context.activation_ref
        if (grant.metadata.get("lifetime_ref") != expected_lifetime
                or grant.metadata.get("operation_binding_ref") != expected_binding
                or (grant.metadata.get("activation_id") is not None and (
                    activation is None
                    or grant.metadata["activation_id"] != str(activation.entity_id)))):
            raise CapabilityError("capability transition differs from its exact invocation lifetime/binding")
        body = {"grant_id": str(grant_id), **dict(payload)}
        transaction = self.registry.begin(idempotency_key=idempotency_key,
            task_round_id=context.task_round_ref.entity_id,
            net_instance_id=context.net_instance_ref.entity_id)
        transaction.append(PendingEvent(
            event_type=event_type, criticality="authoritative",
            stream_id=f"grant:{grant_id}", aggregate_id=str(grant_id),
            aggregate_type="capability_grant/v1", idempotency_key=idempotency_key,
            command_id=idempotency_key, payload=body,
            payload_schema_ref=f"registry_v1/{event_type}", task_control=True,
            producer_principal=str(context.principal_ref.entity_id),
            producer_invocation_id=target_id))
        transaction.commit()

    @staticmethod
    def subagent_scope(parent_versions: Iterable[TypedId],
                       configured_versions: Iterable[TypedId] | None,
                       *, delegation_depth: int) -> tuple[TypedId, ...]:
        if delegation_depth != 1:
            raise CapabilityError("off-net subagent delegation depth is exactly one")
        parent = tuple(parent_versions)
        configured = tuple(configured_versions) if configured_versions is not None else parent
        parent_set = set(parent)
        if any(item not in parent_set for item in configured):
            raise CapabilityError("subagent effective scope cannot exceed the parent scope")
        return configured
