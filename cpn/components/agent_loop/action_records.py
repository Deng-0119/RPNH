"""AgentLoop action records mechanics.

Methods operate on the owning mechanical lifecycle instance; they do not
own Registry state or create a second transaction coordinator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Mapping, Sequence

from cpn.rpnh.llm_contracts import LLMCallAttempt
from cpn.rpnh.registry.event_store import PendingEvent
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.models import TypedRelation, VersionRef
from cpn.rpnh.registry.operations import OperationExecutionBlockAuthority
from cpn.rpnh.registry.provider_calls import LLMCallV2, ProviderAttemptV2
from cpn.rpnh.registry.publication import (
    _ref_payload, _resource_from_payload, _stable_id, _version_from_payload,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, observe_registered_llm_response,
)

from .models import (
    AgentActionRecord, AgentContextOverlay, AgentLoopSnapshot,
    AgentLoopState, AgentToolCallFact, AgentTurnRecord,
    require_state_transition,
)


from .mechanical_contracts import (
    AgentLoopMechanicalLifecycleError,
    _LLM_FAILURE_DISPOSITIONS,
    _LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL,
    _LLM_SUBMISSION_STATES,
    _OWNER_INTERRUPTION_SUBMISSION_STATES,
    _RETRYABLE_LLM_FAILURE_DISPOSITIONS,
)


@dataclass(frozen=True, slots=True)
class AgentActionSettlementPlan:
    """Policy result consumed by the sole mechanical action transaction."""

    records: tuple[AgentActionRecord, ...]
    error_documents: Mapping[VersionRef, Mapping[str, Any]]
    written_resource_refs: tuple[ResourceVersionRef, ...]
    final_state: AgentLoopState
    terminal_reason: str | None = None
    terminal_payload: Mapping[str, Any] | None = None
    action_versions: Mapping[str, TypedId] | None = None
    successor_changes: Mapping[str, Any] | None = None


class ActionRecordsMechanicsMixin:
    @staticmethod
    def action_ref(action_id: str, idempotency_key: str) -> VersionRef:
        """Return the exact mechanical action identity for policy linkage."""
        if (not isinstance(action_id, str) or not action_id
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise TypeError("action identity requires action/key")
        return VersionRef(
            "agent_action/v2",
            TypedId.parse(action_id, expected="agent_action"),
            _stable_id("agent_action_version", action_id, idempotency_key))

    @staticmethod
    def _action_document(
            record: AgentActionRecord, *, action_ref: VersionRef,
            loop_ref: VersionRef, turn_ref: VersionRef) -> dict[str, Any]:
        document = {
            "agent_action_id": record.action_id,
            "agent_action_version_id": str(action_ref.version_id),
            "agent_action_ref": _ref_payload(action_ref),
            "agent_loop_ref": _ref_payload(loop_ref),
            "agent_turn_ref": _ref_payload(turn_ref),
            "turn_sequence": record.turn_sequence,
            "tool_call_ordinal": record.tool_call_ordinal,
            "tool_call_id": record.tool_call_id,
            "action_identity_kind": record.action_identity_kind,
            "action_identity_key": record.action_identity_key,
            "tool_name": record.tool_name,
            "raw_arguments": record.raw_arguments,
            "arguments": (dict(record.arguments)
                          if record.arguments is not None else None),
            "expected_revision": record.expected_revision,
            "state": record.state.value,
            "result_refs": [_ref_payload(ref) for ref in record.result_refs],
            "tool_error_ref": (_ref_payload(record.tool_error_ref)
                               if record.tool_error_ref is not None else None),
            "result_metadata": record.result_metadata,
        }
        if record.managed_action is not None:
            managed = dict(record.managed_action)
            output = managed.pop("output")
            error = managed.pop("error")
            document.pop("result_metadata")
            returned = managed.get("outcome") == "returned"
            document.update(
                managed, output=output, error=error,
                output_size_bytes=(
                    len(canonical_json(output)) if returned else 0),
                error_size_bytes=(
                    0 if returned else len(canonical_json(error))))
        return document

    def model_correctable_rejection(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            turn_sequence: int, prepared_action: Any, detail: str,
            idempotency_key: str,
            error_code: str = "arguments_invalid",
    ) -> tuple[AgentActionRecord, VersionRef, Mapping[str, Any]]:
        """Build one shared safe rejection fact for a prepared tool action."""
        validation = getattr(prepared_action, "validation", None)
        call = getattr(prepared_action, "tool_call", None)
        action_id = getattr(validation, "action_id", None)
        if (not isinstance(action_id, str) or not action_id
                or not isinstance(detail, str) or not detail
                or turn_ref.entity_type != "agent_turn/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "model-correctable rejection lacks exact action authority")
        action_ref = VersionRef(
            "agent_action/v2",
            TypedId.parse(action_id, expected="agent_action"),
            _stable_id("agent_action_version", action_id, idempotency_key))
        error_ref = VersionRef(
            "agent_tool_error/v1", new_id("agent_tool_error"),
            _stable_id("agent_tool_error_version", action_id,
                       idempotency_key))
        arguments = getattr(validation, "arguments", None)
        record = AgentActionRecord(
            action_id=action_id, loop_id=loop.loop_id,
            turn_sequence=turn_sequence,
            tool_call_ordinal=int(call.tool_call_ordinal),
            tool_call_id=call.tool_call_id,
            action_identity_kind=call.action_identity_kind,
            action_identity_key=call.action_identity_key,
            tool_name=call.tool_name, raw_arguments=call.raw_arguments,
            arguments=(dict(arguments)
                       if isinstance(arguments, Mapping) else None),
            expected_revision=loop.revision,
            state=AgentLoopState.ACTION_REJECTED,
            result_refs=(), tool_error_ref=error_ref,
            result_metadata=None)
        document = {
            "agent_tool_error_id": str(error_ref.entity_id),
            "agent_tool_error_version_id": str(error_ref.version_id),
            "agent_tool_error_ref": _ref_payload(error_ref),
            "agent_loop_ref": None,
            "agent_turn_ref": _ref_payload(turn_ref),
            "agent_action_ref": _ref_payload(action_ref),
            "operation_or_tool_identity": call.tool_name or "invalid_tool",
            "code": error_code, "error_code": error_code,
            "boundary": "validation",
            "classification": "model_correctable_invocation_mismatch",
            "detail": detail, "raw_arguments": call.raw_arguments,
            "consecutive_count": 1,
            "disposition": "return_to_same_agent_loop",
            "block_kind": None, "retry_not_before_utc": None,
            "same_agent_loop": True,
        }
        return record, action_ref, document

    def owner_interrupted_rejection(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            turn_sequence: int, prepared_action: Any,
            idempotency_key: str,
    ) -> tuple[AgentActionRecord, VersionRef, Mapping[str, Any]]:
        """Close one not-yet-settled action at the owner's checkpoint."""

        validation = getattr(prepared_action, "validation", None)
        call = getattr(prepared_action, "tool_call", None)
        action_id = getattr(validation, "action_id", None)
        if (not isinstance(action_id, str) or not action_id
                or turn_ref.entity_type != "agent_turn/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "owner interruption lacks exact action authority")
        action_ref = VersionRef(
            "agent_action/v2",
            TypedId.parse(action_id, expected="agent_action"),
            _stable_id(
                "agent_action_version", action_id, idempotency_key))
        error_ref = VersionRef(
            "agent_tool_error/v1", new_id("agent_tool_error"),
            _stable_id(
                "agent_tool_error_version", action_id, idempotency_key))
        arguments = getattr(validation, "arguments", None)
        record = AgentActionRecord(
            action_id=action_id, loop_id=loop.loop_id,
            turn_sequence=turn_sequence,
            tool_call_ordinal=int(call.tool_call_ordinal),
            tool_call_id=call.tool_call_id,
            action_identity_kind=call.action_identity_kind,
            action_identity_key=call.action_identity_key,
            tool_name=call.tool_name, raw_arguments=call.raw_arguments,
            arguments=(dict(arguments)
                       if isinstance(arguments, Mapping) else None),
            expected_revision=loop.revision,
            state=AgentLoopState.ACTION_REJECTED,
            result_refs=(), tool_error_ref=error_ref,
            result_metadata=None)
        document = {
            "agent_tool_error_id": str(error_ref.entity_id),
            "agent_tool_error_version_id": str(error_ref.version_id),
            "agent_tool_error_ref": _ref_payload(error_ref),
            "agent_loop_ref": None,
            "agent_turn_ref": _ref_payload(turn_ref),
            "agent_action_ref": _ref_payload(action_ref),
            "operation_or_tool_identity": call.tool_name or "invalid_tool",
            "code": "owner_interrupted",
            "error_code": "owner_interrupted",
            "boundary": "owner_interruption",
            "classification": "owner_interruption",
            "detail": (
                "Owner interruption invalidated this action before its "
                "effects became part of the resumable checkpoint."),
            "raw_arguments": call.raw_arguments,
            "consecutive_count": 1,
            "disposition": "close_at_owner_checkpoint",
            "block_kind": None,
            "retry_not_before_utc": None,
            "same_agent_loop": False,
        }
        return record, action_ref, document

    def commit_action_batch(
            self, *, tx: Any | None = None,
            idempotency_key: str | None = None,
            before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, turn_ref: VersionRef,
            turn_id: str, records: Sequence[AgentActionRecord],
            error_documents: Mapping[VersionRef, Mapping[str, Any]],
            terminal_reason: str | None = None,
            terminal_payload: Mapping[str, Any] | None = None,
            action_version: Callable[[AgentActionRecord], TypedId] | None = None,
            before_action_prewrite: Callable[[AgentActionRecord], None]
            | None = None,
            after_action_prewrite: Callable[[Any, AgentActionRecord, VersionRef], None]
            | None = None,
            finish_action_prewrite: Callable[[AgentActionRecord], None]
            | None = None,
            commit: bool = True,
    ) -> tuple[VersionRef, ...]:
        """Commit one ordered action/error batch and one successor Loop."""
        if tx is None:
            if not isinstance(idempotency_key, str) or not idempotency_key:
                raise AgentLoopMechanicalLifecycleError(
                    "action batch requires a transaction key")
            tx = self.core.begin(idempotency_key=idempotency_key)
        elif idempotency_key is not None:
            raise AgentLoopMechanicalLifecycleError(
                "action batch cannot receive both transaction and key")
        ordered = tuple(records)
        if (not ordered
                or tuple(item.tool_call_ordinal for item in ordered)
                != tuple(range(len(ordered)))
                or any(item.loop_id != before.loop_id for item in ordered)
                or any(item.turn_sequence != ordered[0].turn_sequence
                       for item in ordered)
                or after.loop_id != before.loop_id
                or after.revision != before.revision + 1
                or turn_ref.entity_type != "agent_turn/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "actions must settle once in provider order with one Loop")
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(before))
        self.prewrite_loop(tx, after)
        successor_ref = self.loop_ref(after)
        refs: list[VersionRef] = []
        for record in ordered:
            version_id = (action_version(record) if action_version is not None
                          else _stable_id(
                              "agent_action_version", record.action_id,
                              tx.idempotency_key))
            action_type = (
                "agent_action/v3"
                if record.managed_action is not None else "agent_action/v2")
            ref = VersionRef(
                action_type,
                TypedId.parse(record.action_id, expected="agent_action"),
                version_id)
            document = self._action_document(
                record, action_ref=ref, loop_ref=successor_ref,
                turn_ref=turn_ref)
            if before_action_prewrite is not None:
                before_action_prewrite(record)
            try:
                tx.prewrite(
                    object_type=ref.entity_type, logical_id=ref.entity_id,
                    version_id=ref.version_id, payload=canonical_json(document),
                    metadata=document, media_type="application/json",
                    schema_ref=f"registry_v1/{action_type}",
                    producer_invocation_id=before.invocation_ref.entity_id)
                if after_action_prewrite is not None:
                    after_action_prewrite(tx, record, ref)
            finally:
                if finish_action_prewrite is not None:
                    finish_action_prewrite(record)
            refs.append(ref)
        for error_ref, raw_document in error_documents.items():
            document = dict(raw_document)
            document["agent_loop_ref"] = _ref_payload(successor_ref)
            tx.prewrite(
                object_type=error_ref.entity_type,
                logical_id=error_ref.entity_id,
                version_id=error_ref.version_id,
                payload=canonical_json(document), metadata=document,
                media_type="application/json",
                schema_ref="registry_v1/agent_tool_error/v1",
                producer_invocation_id=before.invocation_ref.entity_id)
        for record in ordered:
            self._event(
                tx, event_type="agent_action_settled/v1",
                aggregate_id=before.loop_id, aggregate_type="agent_loop",
                producer_invocation_id=before.invocation_ref.entity_id,
                payload={
                    "agent_loop_id": before.loop_id,
                    "agent_turn_id": turn_id,
                    "agent_action_id": record.action_id,
                    "tool_call_ordinal": record.tool_call_ordinal,
                    "tool_call_id": record.tool_call_id,
                    "action_identity_kind": record.action_identity_kind,
                    "action_identity_key": record.action_identity_key,
                    "tool_name": record.tool_name,
                    "raw_arguments": record.raw_arguments,
                    "arguments": (dict(record.arguments)
                                  if record.arguments is not None else None),
                    "expected_revision": record.expected_revision,
                    "settlement": record.state.value,
                    "result_version_ids": [
                        str(ref.version_id) for ref in record.result_refs],
                    "tool_error_version_id": (
                        str(record.tool_error_ref.version_id)
                        if record.tool_error_ref is not None else None),
                    "revision": after.revision,
                })
        if after.state == AgentLoopState.COMPLETED:
            payload = {
                "agent_loop_id": after.loop_id,
                "state": after.state.value, "revision": after.revision,
                "adopted_candidate_version_id": (
                    str(after.adopted_candidate_ref.version_id)
                    if after.adopted_candidate_ref is not None else None),
                "disposition_version_id": (
                    str(after.disposition_ref.version_id)
                    if after.disposition_ref is not None else None),
                "llm_turns_used": after.llm_turns_used,
                "terminal_reason": terminal_reason,
                **dict(terminal_payload or {}),
            }
            self._event(
                tx, event_type="agent_loop_terminal/v1",
                aggregate_id=after.loop_id, aggregate_type="agent_loop",
                payload=payload,
                producer_invocation_id=after.invocation_ref.entity_id)
        if commit:
            tx.commit()
        return tuple(refs)

    def settle_action_batch(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            turn_id: str, records: Sequence[AgentActionRecord],
            error_documents: Mapping[VersionRef, Mapping[str, Any]],
            written_resource_refs: Sequence[ResourceVersionRef],
            final_state: AgentLoopState, idempotency_key: str,
            terminal_reason: str | None = None,
            terminal_payload: Mapping[str, Any] | None = None,
            stage: Callable[[Any], None] | None = None,
            action_version: Callable[[AgentActionRecord], TypedId] | None = None,
            after_action_prewrite: Callable[
                [Any, AgentActionRecord, VersionRef], None] | None = None,
    ) -> tuple[AgentLoopSnapshot, tuple[AgentActionRecord, ...]]:
        """Build and commit the sole successor for one policy-evaluated turn."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,))
        ordered = tuple(records)
        resources = tuple(written_resource_refs)
        if (not ordered
                or final_state not in {
                    AgentLoopState.WAITING_FOR_LLM,
                    AgentLoopState.COMPLETED,
                }
                or any(record.expected_revision != current.revision
                       for record in ordered)
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in resources)
                or len(set(resources)) != len(resources)):
            raise AgentLoopMechanicalLifecycleError(
                "action settlement lacks one exact policy-evaluated batch")
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            revision=current.revision + 1,
            state=final_state,
            written_resource_refs=resources)
        tx = self.core.begin(idempotency_key=idempotency_key)
        if stage is not None:
            stage(tx)
        refs = self.commit_action_batch(
            tx=tx, before=current, after=after, turn_ref=turn_ref,
            turn_id=turn_id, records=ordered,
            error_documents=error_documents,
            terminal_reason=terminal_reason,
            terminal_payload=terminal_payload,
            action_version=action_version,
            after_action_prewrite=after_action_prewrite,
            commit=False)
        tx.commit()
        committed = self.hydrate_loop(self.loop_ref(after))
        return committed, tuple(self.hydrate_action(ref) for ref in refs)

    def evaluate_and_settle_action_batch(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            turn_id: str, idempotency_key: str,
            evaluate_policy: Callable[
                [Any, AgentLoopSnapshot], AgentActionSettlementPlan],
            after_action_prewrite: Callable[
                [Any, AgentActionRecord, VersionRef], None] | None = None,
    ) -> tuple[AgentLoopSnapshot, tuple[AgentActionRecord, ...]]:
        """Run injected policy inside the sole mechanical settlement tx."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,
                                  AgentLoopState.ACTION_PENDING))
        if (turn_ref.entity_type != "agent_turn/v1"
                or self.turn_ref_for(
                    current.loop_id, current.next_turn_sequence - 1)
                != turn_ref
                or not callable(evaluate_policy)):
            raise AgentLoopMechanicalLifecycleError(
                "action policy lacks its exact current turn")
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(current))
        plan = evaluate_policy(tx, current)
        if not isinstance(plan, AgentActionSettlementPlan):
            raise AgentLoopMechanicalLifecycleError(
                "action policy returned no mechanical settlement plan")
        records = tuple(plan.records)
        resources = tuple(plan.written_resource_refs)
        if (not records
                or plan.final_state not in {
                    AgentLoopState.WAITING_FOR_LLM,
                    AgentLoopState.COMPLETED,
                    AgentLoopState.ACTION_APPLIED,
                    AgentLoopState.ACTION_REJECTED,
                    AgentLoopState.WAITING_RESOURCE,
                }
                or any(record.expected_revision != current.revision
                       for record in records)
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in resources)
                or len(set(resources)) != len(resources)):
            raise AgentLoopMechanicalLifecycleError(
                "action policy returned an invalid settlement plan")
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            revision=current.revision + 1,
            state=plan.final_state,
            written_resource_refs=resources)
        versions = dict(plan.action_versions or {})
        if versions and set(versions) != {record.action_id
                                         for record in records}:
            raise AgentLoopMechanicalLifecycleError(
                "action versions do not cover the exact settlement batch")
        refs = self.commit_action_batch(
            tx=tx, before=current, after=after, turn_ref=turn_ref,
            turn_id=turn_id, records=records,
            error_documents=plan.error_documents,
            terminal_reason=plan.terminal_reason,
            terminal_payload=plan.terminal_payload,
            action_version=(
                (lambda record: versions[record.action_id])
                if versions else None),
            after_action_prewrite=after_action_prewrite,
            commit=False)
        tx.commit()
        committed = self.hydrate_loop(self.loop_ref(after))
        return committed, tuple(self.hydrate_action(ref) for ref in refs)

    def begin_action_policy_transaction(
            self, loop: AgentLoopSnapshot, *, idempotency_key: str,
            transaction_scope: Mapping[str, Any] | None = None,
    ) -> tuple[Any, AgentLoopSnapshot]:
        """Open the sole transaction in which a policy evaluates one batch."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,
                                  AgentLoopState.ACTION_PENDING))
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise AgentLoopMechanicalLifecycleError(
                "action policy transaction requires an idempotency key")
        scope = dict(transaction_scope or {})
        tx = self.core.begin(idempotency_key=idempotency_key, **scope)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(current))
        return tx, current

    def finish_action_policy_transaction(
            self, *, tx: Any, current: AgentLoopSnapshot,
            turn_ref: VersionRef, turn_id: str,
            plan: AgentActionSettlementPlan,
            before_action_prewrite: Callable[[AgentActionRecord], None]
            | None = None,
            after_action_prewrite: Callable[
                [Any, AgentActionRecord, VersionRef], None] | None = None,
            finish_action_prewrite: Callable[[AgentActionRecord], None]
            | None = None,
    ) -> tuple[AgentLoopSnapshot, tuple[AgentActionRecord, ...]]:
        """Commit policy effects with the sole mechanical action successor."""
        if not isinstance(plan, AgentActionSettlementPlan):
            raise AgentLoopMechanicalLifecycleError(
                "action policy returned no mechanical settlement plan")
        records = tuple(plan.records)
        resources = tuple(plan.written_resource_refs)
        if (not records
                or plan.final_state not in {
                    AgentLoopState.WAITING_FOR_LLM,
                    AgentLoopState.COMPLETED,
                    AgentLoopState.ACTION_APPLIED,
                    AgentLoopState.ACTION_REJECTED,
                    AgentLoopState.WAITING_RESOURCE,
                }
                or any(not isinstance(ref, ResourceVersionRef)
                       for ref in resources)
                or len(set(resources)) != len(resources)):
            raise AgentLoopMechanicalLifecycleError(
                "action policy returned an invalid settlement plan")
        expected_revision = (
            current.revision - 1
            if current.state == AgentLoopState.ACTION_PENDING
            else current.revision)
        if any(record.expected_revision != expected_revision
               for record in records):
            raise AgentLoopMechanicalLifecycleError(
                "action records differ from their admitted Loop revision")
        changes = dict(plan.successor_changes or {})
        if {"loop_id", "loop_version_id", "revision", "state",
                "written_resource_refs"}.intersection(changes):
            raise AgentLoopMechanicalLifecycleError(
                "action policy cannot replace mechanical successor fields")
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, tx.idempotency_key)),
            revision=current.revision + 1,
            state=plan.final_state,
            written_resource_refs=resources,
            **changes)
        versions = dict(plan.action_versions or {})
        if versions and set(versions) != {
                record.action_id for record in records}:
            raise AgentLoopMechanicalLifecycleError(
                "action versions do not cover the exact settlement batch")
        refs = self.commit_action_batch(
            tx=tx, before=current, after=after, turn_ref=turn_ref,
            turn_id=turn_id, records=records,
            error_documents=plan.error_documents,
            terminal_reason=plan.terminal_reason,
            terminal_payload=plan.terminal_payload,
            action_version=(
                (lambda record: versions[record.action_id])
                if versions else None),
            before_action_prewrite=before_action_prewrite,
            after_action_prewrite=after_action_prewrite,
            finish_action_prewrite=finish_action_prewrite,
            commit=False)
        tx.commit()
        committed = self.hydrate_loop(self.loop_ref(after))
        return committed, tuple(self.hydrate_action(ref) for ref in refs)

    def begin_monitored_action(
            self, *, loop: AgentLoopSnapshot, turn_ref: VersionRef,
            record: AgentActionRecord, idempotency_key: str,
    ) -> tuple[AgentLoopSnapshot, AgentActionRecord, VersionRef]:
        """Create and commit the sole ACTION_PENDING successor."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.TURN_STORED,))
        if (record.expected_revision != current.revision
                or record.state != AgentLoopState.ACTION_PENDING):
            raise AgentLoopMechanicalLifecycleError(
                "pending action differs from its admitted Loop revision")
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            state=AgentLoopState.ACTION_PENDING,
            revision=current.revision + 1)
        return self.commit_pending_action(
            before=current, after=after, turn_ref=turn_ref,
            record=record,
            action_version=_stable_id(
                "agent_action_version", record.action_id, idempotency_key),
            idempotency_key=idempotency_key)

    def commit_pending_action(
            self, *, before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, turn_ref: VersionRef,
            record: AgentActionRecord, action_version: TypedId,
            idempotency_key: str,
    ) -> tuple[AgentLoopSnapshot, AgentActionRecord, VersionRef]:
        """Commit the sole monitored action as ACTION_PENDING before execution."""
        if (before.state != AgentLoopState.TURN_STORED
                or after.state != AgentLoopState.ACTION_PENDING
                or record.state != AgentLoopState.ACTION_PENDING
                or record.loop_id != before.loop_id
                or after.loop_id != before.loop_id
                or after.revision != before.revision + 1
                or turn_ref.entity_type != "agent_turn/v1"):
            raise AgentLoopMechanicalLifecycleError(
                "monitored action start requires one exact pending successor")
        action_ref = VersionRef(
            "agent_action/v2",
            TypedId.parse(record.action_id, expected="agent_action"),
            action_version)
        document = self._action_document(
            record, action_ref=action_ref,
            loop_ref=self.loop_ref(after), turn_ref=turn_ref)

        def stage(tx: Any) -> None:
            self.prewrite_object(
                tx, action_ref, document,
                producer_invocation_id=before.invocation_ref.entity_id)

        committed = self.commit_loop_successor(
            before, after, idempotency_key=idempotency_key, stage=stage)
        return committed, self.hydrate_action(action_ref), action_ref

    def hydrate_action(self, action_ref: VersionRef) -> AgentActionRecord:
        """Read one exact immutable action through the shared declared view."""
        if (not isinstance(action_ref, VersionRef)
                or action_ref.entity_type not in {
                    "agent_action/v2", "agent_action/v3"}):
            raise TypeError("action history requires agent_action/v2-or-v3")
        value = dict(self.kernel._exact_object(
            action_ref, expected_type=action_ref.entity_type).metadata)
        if value.get("agent_action_ref") != _ref_payload(action_ref):
            raise AgentLoopMechanicalLifecycleError(
                "action history differs from its exact immutable ref")
        if action_ref.entity_type == "agent_action/v3":
            returned = value.get("outcome") == "returned"
            expected_output_size = (
                len(canonical_json(value.get("output"))) if returned else 0)
            expected_error_size = (
                0 if returned else len(canonical_json(value.get("error"))))
            if (value.get("output_size_bytes") != expected_output_size
                    or value.get("error_size_bytes") != expected_error_size):
                raise AgentLoopMechanicalLifecycleError(
                    "managed action stored JSON sizes differ from canonical bytes")
        return AgentActionRecord(
            action_id=str(value["agent_action_id"]),
            loop_id=str(value["agent_loop_ref"]["logical_id"]),
            turn_sequence=int(value["turn_sequence"]),
            tool_call_ordinal=int(value["tool_call_ordinal"]),
            tool_call_id=value["tool_call_id"],
            action_identity_kind=str(value["action_identity_kind"]),
            action_identity_key=str(value["action_identity_key"]),
            tool_name=value["tool_name"], raw_arguments=value["raw_arguments"],
            arguments=value["arguments"],
            expected_revision=int(value["expected_revision"]),
            state=AgentLoopState(str(value["state"])),
            result_refs=tuple(
                VersionRef(
                    str(item["entity_type"]),
                    TypedId.parse(str(item["logical_id"])),
                    TypedId.parse(str(item["version_id"])))
                for item in value["result_refs"]),
            tool_error_ref=(
                VersionRef(
                    str(value["tool_error_ref"]["entity_type"]),
                    TypedId.parse(str(value["tool_error_ref"]["logical_id"])),
                    TypedId.parse(str(value["tool_error_ref"]["version_id"])))
                if isinstance(value.get("tool_error_ref"), Mapping) else None),
            result_metadata=(
                value.get("result_metadata")
                if action_ref.entity_type == "agent_action/v2" else
                ({
                    "kind": "managed_native_plugin_result/v1",
                    "output": value["output"],
                    "terminal_receipt_ref": value[
                        "terminal_receipt_ref"],
                } if value["outcome"] == "returned" else None)),
            managed_action=(
                None if action_ref.entity_type == "agent_action/v2" else {
                    name: value[name] for name in (
                        "provider_name", "registration_key", "selector",
                        "plugin_catalog_digest", "binding_digest", "effect",
                        "outcome", "request_admission_receipt_ref",
                        "admitted_at_utc", "started_receipt_ref",
                        "terminal_receipt_ref", "model_visible_result_ref",
                        "non_delivery_reason", "output", "error")
                } | {"max_result_bytes": value["max_result_bytes"]}))

    def latest_settled_action(
            self, *, action_id: str, turn_ref: VersionRef,
            ordinal: int) -> tuple[VersionRef, AgentActionRecord]:
        """Resolve a stable action identity without using canonical-head views."""
        logical_id = TypedId.parse(action_id, expected="agent_action")
        rows = tuple(
            row for action_type in ("agent_action/v2", "agent_action/v3")
            if (row := self.core.event_store.latest_object_row(
                logical_id, object_type=action_type)) is not None)
        if len(rows) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "action history lacks exactly one v2-or-v3 immutable object")
        row, = rows
        ref = VersionRef(
            str(row["object_type"]), logical_id,
            TypedId.parse(str(row["version_id"]),
                          expected="agent_action_version"))
        document = json.loads(str(row["metadata_json"]))
        if (document.get("agent_turn_ref") != _ref_payload(turn_ref)
                or document.get("tool_call_ordinal") != ordinal
                or document.get("state") == "ACTION_PENDING"):
            raise AgentLoopMechanicalLifecycleError(
                "action history lacks one exact settled action")
        return ref, self.hydrate_action(ref)


__all__ = ["ActionRecordsMechanicsMixin", "AgentActionSettlementPlan"]
