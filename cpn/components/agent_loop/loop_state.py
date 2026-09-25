"""AgentLoop loop state mechanics.

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


class LoopStateMechanicsMixin:
    @staticmethod
    def loop_ref(loop: AgentLoopSnapshot) -> VersionRef:
        if not isinstance(loop, AgentLoopSnapshot):
            raise TypeError("AgentLoop mechanics require an AgentLoopSnapshot")
        return VersionRef(
            "agent_loop/v1", TypedId.parse(loop.loop_id, expected="agent_loop"),
            TypedId.parse(loop.loop_version_id, expected="agent_loop_version"))

    @staticmethod
    def successor_loop_ref(
            loop: AgentLoopSnapshot, idempotency_key: str,
    ) -> VersionRef:
        """Return the exact next mechanical Loop identity for policy linkage."""
        if (not isinstance(loop, AgentLoopSnapshot)
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise TypeError("Loop successor identity requires loop/key")
        return VersionRef(
            "agent_loop/v1",
            TypedId.parse(loop.loop_id, expected="agent_loop"),
            _stable_id("agent_loop_version", loop.loop_id,
                       loop.revision + 1, idempotency_key))

    @staticmethod
    def loop_document(loop: AgentLoopSnapshot) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for item in fields(loop):
            value = getattr(loop, item.name)
            if isinstance(value, VersionRef):
                value = _ref_payload(value)
            elif isinstance(value, ResourceVersionRef):
                value = _resource_payload(value)
            elif isinstance(value, AgentLoopState):
                value = value.value
            elif isinstance(value, tuple):
                value = [
                    _resource_payload(ref)
                    if isinstance(ref, ResourceVersionRef) else ref
                    for ref in value]
            document[item.name] = value
        document["agent_loop_id"] = document.pop("loop_id")
        document["agent_loop_version_id"] = document.pop(
            "loop_version_id")
        document["agent_loop_ref"] = _ref_payload(
            LoopStateMechanicsMixin.loop_ref(loop))
        return document

    @staticmethod
    def _event(
            tx: Any, *, event_type: str, aggregate_id: str,
            aggregate_type: str, payload: Mapping[str, Any],
            producer_invocation_id: TypedId) -> None:
        stream_kind = {
            "agent_loop": "agent-loop",
            "llm_invocation_attempt": "llm-attempt",
        }.get(aggregate_type, aggregate_type.replace("_", "-"))
        tx.append(PendingEvent(
            event_type=event_type, criticality="authoritative",
            stream_id=f"{stream_kind}:{aggregate_id}",
            aggregate_id=aggregate_id, aggregate_type=aggregate_type,
            idempotency_key=tx.idempotency_key,
            command_id=tx.idempotency_key, payload=dict(payload),
            payload_schema_ref="registry_v1/" + event_type,
            task_control=True,
            producer_invocation_id=producer_invocation_id))

    def prewrite_loop(self, tx: Any, loop: AgentLoopSnapshot) -> None:
        document = self.loop_document(loop)
        ref = self.loop_ref(loop)
        tx.prewrite(
            object_type=ref.entity_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(document),
            metadata=document, media_type="application/json",
            schema_ref="registry_v1/agent_loop/v1",
            producer_invocation_id=loop.invocation_ref.entity_id)

    def hydrate_loop(self, loop_ref: VersionRef) -> AgentLoopSnapshot:
        """Hydrate one exact immutable Loop object through the shared view."""
        if (not isinstance(loop_ref, VersionRef)
                or loop_ref.entity_type != "agent_loop/v1"):
            raise TypeError("AgentLoop hydration requires agent_loop/v1")
        value = dict(self.kernel._exact_object(
            loop_ref, expected_type="agent_loop/v1").metadata)
        if value.pop("agent_loop_ref", None) != _ref_payload(loop_ref):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop bytes differ from their exact immutable ref")
        value["loop_id"] = value.pop("agent_loop_id")
        value["loop_version_id"] = value.pop("agent_loop_version_id")
        resource_fields = {
            "tool_catalog_ref", "cap_review_resource_ref",
        }
        version_fields = {
            "invocation_ref", "operation_binding_ref", "owner_ref",
            "current_candidate_ref", "adopted_candidate_ref",
            "disposition_ref", "workspace_binding_ref",
            "workspace_lineage_ref", "workspace_base_revision_ref",
            "workspace_revision_ref",
        }
        for key in resource_fields:
            raw = value.get(key)
            if isinstance(raw, Mapping):
                value[key] = _resource_from_payload(raw)
        for key in version_fields:
            raw = value.get(key)
            if isinstance(raw, Mapping):
                value[key] = _version_from_payload(raw)
        value["written_resource_refs"] = tuple(
            _resource_from_payload(item)
            for item in value.get("written_resource_refs", ()))
        value["workspace_changed_paths"] = tuple(
            value.get("workspace_changed_paths", ()))
        value["workspace_deleted_paths"] = tuple(
            value.get("workspace_deleted_paths", ()))
        value["state"] = AgentLoopState(value["state"])
        return AgentLoopSnapshot(**value)

    def latest_loop(self, loop_id: str) -> AgentLoopSnapshot | None:
        logical_id = TypedId.parse(loop_id, expected="agent_loop")
        row = self.core.event_store.latest_object_row(
            logical_id, object_type="agent_loop/v1")
        if row is None:
            return None
        return self.hydrate_loop(VersionRef(
            "agent_loop/v1", logical_id,
            TypedId.parse(str(row["version_id"]),
                          expected="agent_loop_version")))

    def loop_for_invocation(
            self, invocation_ref: VersionRef,
    ) -> AgentLoopSnapshot | None:
        if (not isinstance(invocation_ref, VersionRef)
                or invocation_ref.entity_type != "invocation/v1"):
            raise TypeError("AgentLoop lookup requires invocation/v1")
        identities: set[str] = set()
        for row in self.core.event_store.canonical_object_rows(
                object_type="agent_loop/v1"):
            value = json.loads(str(row["metadata_json"]))
            if value.get("invocation_ref") == _ref_payload(invocation_ref):
                identities.add(str(row["logical_id"]))
        if len(identities) > 1:
            raise AgentLoopMechanicalLifecycleError(
                "invocation has multiple canonical AgentLoops")
        return (self.latest_loop(next(iter(identities)))
                if identities else None)

    def current_loop(self, loop: AgentLoopSnapshot) -> AgentLoopSnapshot:
        """Require one supplied snapshot to be the exact canonical Loop head."""
        ref = self.loop_ref(loop)
        row = self.core.event_store.latest_object_row(
            ref.entity_id, object_type=ref.entity_type)
        if row is None or str(row["version_id"]) != str(ref.version_id):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop command does not name its current head")
        current = self.hydrate_loop(ref)
        if current != loop:
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop snapshot differs from committed Registry bytes")
        return current

    def require_current_revision(
            self, loop: AgentLoopSnapshot, *, expected_revision: int | None = None,
            allowed_states: Sequence[AgentLoopState] = (),
    ) -> AgentLoopSnapshot:
        """Return the exact current head after shared revision/state checks."""
        current = self.current_loop(loop)
        if (expected_revision is not None
                and (isinstance(expected_revision, bool)
                     or not isinstance(expected_revision, int)
                     or current.revision != expected_revision)):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop command crossed its expected revision")
        states = tuple(allowed_states)
        if (states
                and (any(not isinstance(state, AgentLoopState)
                         for state in states)
                     or current.state not in states)):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop command is unavailable from its current state")
        return current

    def commit_loop_successor(
            self, before: AgentLoopSnapshot, after: AgentLoopSnapshot, *,
            idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
            allow_same_state: bool = False,
            transaction_scope: Mapping[str, Any] | None = None,
            transaction: Any | None = None,
    ) -> AgentLoopSnapshot:
        """Commit the sole generic Loop successor and optional sibling facts."""
        if (not isinstance(idempotency_key, str) or not idempotency_key
                or after.loop_id != before.loop_id
                or after.revision != before.revision + 1
                or after.loop_version_id == before.loop_version_id):
            raise AgentLoopMechanicalLifecycleError(
                "Loop successor requires one new revision and identity")
        if before.state == after.state:
            if not allow_same_state:
                raise AgentLoopMechanicalLifecycleError(
                    "same-state Loop successor requires an explicit segment")
        else:
            require_state_transition(before.state, after.state)
        if transaction is not None and transaction_scope:
            raise AgentLoopMechanicalLifecycleError(
                "Loop successor cannot open and reuse a transaction")
        tx = (transaction if transaction is not None else self.core.begin(
            idempotency_key=idempotency_key,
            **dict(transaction_scope or {})))
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(before))
        if stage is not None:
            stage(tx)
        self.prewrite_loop(tx, after)
        for event_type, aggregate_id, aggregate_type, payload in events:
            self._event(
                tx, event_type=event_type, aggregate_id=aggregate_id,
                aggregate_type=aggregate_type, payload=payload,
                producer_invocation_id=before.invocation_ref.entity_id)
        tx.commit()
        return self.hydrate_loop(self.loop_ref(after))

    def transition_loop(
            self, loop: AgentLoopSnapshot, target: AgentLoopState, *,
            idempotency_key: str, allow_same_state: bool = False,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
            **changes: Any,
    ) -> AgentLoopSnapshot:
        """Build and commit a generic state/cap/resource successor."""
        forbidden = {"loop_id", "loop_version_id", "revision", "state"}
        if forbidden.intersection(changes):
            raise AgentLoopMechanicalLifecycleError(
                "policy changes cannot replace mechanical Loop identity")
        version = _stable_id(
            "agent_loop_version", loop.loop_id, loop.revision + 1,
            idempotency_key)
        existing = self.core.event_store.object_row(version)
        if existing is not None:
            committed = self.hydrate_loop(VersionRef(
                "agent_loop/v1",
                TypedId.parse(loop.loop_id, expected="agent_loop"),
                version))
            if (committed.revision != loop.revision + 1
                    or committed.state != target):
                raise AgentLoopMechanicalLifecycleError(
                    "Loop transition replay resolved another successor")
            return committed
        current = self.current_loop(loop)
        updated = replace(
            current, loop_version_id=str(version),
            revision=current.revision + 1, state=target, **changes)
        return self.commit_loop_successor(
            current, updated, idempotency_key=idempotency_key, stage=stage,
            events=events, allow_same_state=allow_same_state)

    def commit_start(
            self, loop: AgentLoopSnapshot, *, idempotency_key: str,
            workspace: tuple[VersionRef, Mapping[str, Any]] | None = None,
            validate_before_commit: Callable[[Any], None] | None = None,
    ) -> VersionRef:
        """Commit one NEW Loop and its optional firing workspace atomically."""
        if loop.state != AgentLoopState.NEW or loop.revision != 0:
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop start requires the prepared NEW revision zero")
        tx = self.core.begin(idempotency_key=idempotency_key)
        if validate_before_commit is not None:
            tx.validate_before_commit(validate_before_commit)
        if workspace is not None:
            workspace_ref, workspace_document = workspace
            if workspace_ref.entity_type != "workspace_binding/v1":
                raise AgentLoopMechanicalLifecycleError(
                    "AgentLoop workspace start requires workspace_binding/v1")
            tx.prewrite(
                object_type=workspace_ref.entity_type,
                logical_id=workspace_ref.entity_id,
                version_id=workspace_ref.version_id,
                payload=canonical_json(dict(workspace_document)),
                metadata=dict(workspace_document),
                media_type="application/json",
                schema_ref="registry_v1/workspace_binding/v1",
                producer_invocation_id=loop.invocation_ref.entity_id)
        self.prewrite_loop(tx, loop)
        self._event(
            tx, event_type="agent_loop_started/v1",
            aggregate_id=loop.loop_id, aggregate_type="agent_loop",
            producer_invocation_id=loop.invocation_ref.entity_id,
            payload={
                "agent_loop_id": loop.loop_id,
                "agent_loop_version_id": loop.loop_version_id,
                "invocation_version_id": str(loop.invocation_ref.version_id),
                "owner_version_id": str(loop.owner_ref.version_id),
                "state": "NEW", "revision": 0,
                "llm_turn_budget": loop.llm_turn_budget,
                "model_condition": loop.model_condition,
                "tool_catalog_ref": _resource_payload(loop.tool_catalog_ref),
            })
        tx.commit()
        return self.loop_ref(loop)

    def continue_after_text_turn(
            self, *, loop: AgentLoopSnapshot, turn: AgentTurnRecord,
            expected_revision: int, idempotency_key: str,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> AgentLoopSnapshot:
        current = self.require_current_revision(
            loop, expected_revision=expected_revision,
            allowed_states=(AgentLoopState.TURN_STORED,))
        exact = self.hydrate_current_turn(
            current, read_response=read_response)
        if exact != turn or turn.tool_calls or turn.finish_reason == "length":
            raise AgentLoopMechanicalLifecycleError(
                "text continuation lacks its exact zero-action turn")
        return self.transition_loop(
            current, AgentLoopState.WAITING_FOR_LLM,
            idempotency_key=idempotency_key)

    def continue_after_length_interruption(
            self, *, loop: AgentLoopSnapshot, interruption: Any,
            expected_revision: int, idempotency_key: str,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> AgentLoopSnapshot:
        current = self.require_current_revision(
            loop, expected_revision=expected_revision,
            allowed_states=(AgentLoopState.TURN_STORED,))
        sequence = getattr(interruption, "sequence", None)
        exact = self.hydrate_length_interruption(
            current, sequence=sequence, read_response=read_response)
        recorded = tuple(
            event for event in self.turn_events(current)
            if event.payload.get("sequence") == sequence)
        if exact != interruption or recorded:
            raise AgentLoopMechanicalLifecycleError(
                "length continuation lacks its unexecuted interruption")
        return self.transition_loop(
            current, AgentLoopState.WAITING_FOR_LLM,
            idempotency_key=idempotency_key)

    def continue_role_segment(
            self, *, loop: AgentLoopSnapshot, new_turn_budget: int,
            idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
            transaction_scope: Mapping[str, Any] | None = None,
            **changes: Any,
    ) -> AgentLoopSnapshot:
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,))
        if {"loop_id", "loop_version_id", "revision", "state",
                "llm_turn_budget"}.intersection(changes):
            raise AgentLoopMechanicalLifecycleError(
                "role policy cannot replace mechanical continuation fields")
        if (isinstance(new_turn_budget, bool)
                or not isinstance(new_turn_budget, int)
                or new_turn_budget <= current.llm_turn_budget):
            raise AgentLoopMechanicalLifecycleError(
                "role continuation must extend the registered turn budget")
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            revision=current.revision + 1,
            llm_turn_budget=new_turn_budget, **changes)
        return self.commit_cap_continuation(
            before=current, after=after, idempotency_key=idempotency_key,
            stage=stage, events=events,
            transaction_scope=transaction_scope)

    def close_at_cap(
            self, *, loop: AgentLoopSnapshot, terminal_reason: str,
            idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            terminal_payload: Mapping[str, Any] | None = None,
            terminal_state: AgentLoopState = AgentLoopState.COMPLETED,
            transaction_scope: Mapping[str, Any] | None = None,
            evaluate_policy: Callable[[Any], Mapping[str, Any]] | None = None,
            terminal_aggregate_id: str | None = None,
            terminal_aggregate_type: str = "agent_loop",
            **changes: Any,
    ) -> AgentLoopSnapshot:
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,))
        if {"loop_id", "loop_version_id", "revision", "state"}.intersection(
                changes):
            raise AgentLoopMechanicalLifecycleError(
                "cap policy cannot replace mechanical terminal fields")
        if (not isinstance(terminal_reason, str) or not terminal_reason
                or terminal_state not in {
                    AgentLoopState.COMPLETED, AgentLoopState.EXHAUSTED}):
            raise AgentLoopMechanicalLifecycleError(
                "cap closure requires an exact terminal reason")
        tx = None
        if evaluate_policy is not None:
            tx = self.core.begin(
                idempotency_key=idempotency_key,
                **dict(transaction_scope or {}))
            tx.validate_before_commit(
                lambda _view: self._validate_current_loop(current))
            policy_changes = dict(evaluate_policy(tx))
            if {"loop_id", "loop_version_id", "revision", "state"}.intersection(
                    policy_changes):
                raise AgentLoopMechanicalLifecycleError(
                    "cap policy cannot replace mechanical terminal fields")
            changes = {**changes, **policy_changes}
        after = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            revision=current.revision + 1,
            state=terminal_state, **changes)
        aggregate_id = terminal_aggregate_id or current.loop_id
        if (not isinstance(aggregate_id, str) or not aggregate_id
                or not isinstance(terminal_aggregate_type, str)
                or not terminal_aggregate_type):
            raise AgentLoopMechanicalLifecycleError(
                "cap closure requires an exact terminal aggregate")
        event = ("agent_loop_terminal/v1", aggregate_id,
                 terminal_aggregate_type, {
            "agent_loop_id": current.loop_id,
            "state": after.state.value,
            "revision": after.revision,
            "adopted_candidate_version_id": (
                str(after.adopted_candidate_ref.version_id)
                if after.adopted_candidate_ref is not None else None),
            "disposition_version_id": (
                str(after.disposition_ref.version_id)
                if after.disposition_ref is not None else None),
            "llm_turns_used": after.llm_turns_used,
            "terminal_reason": terminal_reason,
            **dict(terminal_payload or {}),
        })
        if tx is None:
            return self.commit_cap_continuation(
                before=current, after=after, idempotency_key=idempotency_key,
                stage=stage, events=(event,),
                transaction_scope=transaction_scope)
        if stage is not None:
            stage(tx)
        committed = self.commit_loop_successor(
            current, after, idempotency_key=idempotency_key,
            events=(event,), transaction=tx)
        return committed

    def commit_cap_continuation(
            self, *, before: AgentLoopSnapshot,
            after: AgentLoopSnapshot, idempotency_key: str,
            stage: Callable[[Any], None] | None = None,
            events: Sequence[tuple[str, str, str, Mapping[str, Any]]] = (),
            transaction_scope: Mapping[str, Any] | None = None,
    ) -> AgentLoopSnapshot:
        if (before.state != AgentLoopState.WAITING_FOR_LLM
                or after.state not in {
                    AgentLoopState.WAITING_FOR_LLM,
                    AgentLoopState.COMPLETED,
                    AgentLoopState.EXHAUSTED}
                or (after.state == AgentLoopState.WAITING_FOR_LLM
                    and after.llm_turn_budget <= before.llm_turn_budget)):
            raise AgentLoopMechanicalLifecycleError(
                "cap continuation is not one segment extension/closure")
        return self.commit_loop_successor(
            before, after, idempotency_key=idempotency_key, stage=stage,
            events=events,
            allow_same_state=(after.state == before.state),
            transaction_scope=transaction_scope)


__all__ = ["LoopStateMechanicsMixin"]


from collections.abc import Mapping
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import stat

from cpn.rpnh.executable_net import load_compiled_net
from cpn.rpnh.llm_contracts import LLMCallAttempt, LLMInputTarget
from cpn.rpnh.response_protocol import (
    PublishedLLMResponse, canonicalize_llm_response_payload,
    observe_registered_llm_response,
)
from cpn.rpnh.registry.errors import (
    ResourceIntegrityFault, ResourcePayloadSchemaViolation,
    StaleAuthorityHead, UnauthorizedResourceDelivery,
)
from cpn.rpnh.registry.firing_authority import canonical_invocation
from cpn.rpnh.registry.identities import TypedId, new_id
from cpn.rpnh.registry.invocations import InvocationLifecycle
from cpn.rpnh.registry.agent_resource_broker import (
    AgentLoopResourceRequest, prepare_agent_resource_request,
    stage_resumed_agent_resource_lifecycle,
)
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.operation_execution import verify_operation_execution
from cpn.rpnh.registry.publication import (
    _append_direct_resource_version_publication, _direct_resource_metadata,
    _provider_request_resource_metadata, _ref_payload, _registry_type_catalog_ref,
    _resource_from_payload, _version_from_payload, _stable_id,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resource_verification import verify_resource
from cpn.rpnh.registry.resources import (
    AcknowledgeResourceDelivery, AddressBindingIntent,
    AgentLoopResourceGrantAuthority, AgentLoopResourceLifecycleAuthority,
    AuthorizeResourceRelease, PetriOutputOrigin, PrepareResourceDelivery,
    PublishResource, ResourceAddress, ResourceVersionRef,
    UnbindResourceAddress, WorkspaceWriteOrigin,
)
from cpn.rpnh.registry.schema_catalog import TypeDefinition, canonical_json

from .compact import build_replacement_history, reduce_tool_messages, should_compact
from .request_envelope_materialization import materialize_agent_request_envelope
from .models import (
    AgentActionRecord, AgentLoopSnapshot, AgentLoopState, AgentTurnRecord,
    LocatedAgentInput,
)
from .service import (
    AgentLengthInterruptionRecord, AgentLoopRegistryPort,
    CompletedAgentContextCompaction, PreparedAgentContextCompaction,
    ParentOwnedDelegatedSubtaskLengthReplay, ParentOwnedDelegatedSubtaskRequest,
    ParentOwnedDelegatedSubtaskResult, ParentOwnedDelegatedSubtaskToolStep,
    PreparedAgentLLMTurn, PreparedAgentTurnContext,
    PreparedParentOwnedDelegatedSubtaskCall, StartAgentLoopCommand,
    _PreparedParentOwnedDelegatedSubtaskContext,
)
from .delegated_history import compact_delegated_subtask_history_after_length
from .tool_catalog import (
    TOOL_ARGUMENT_SCHEMAS, AgentToolCatalog, build_agent_tool_catalog,
    derive_atomic_subtask_tools, parse_agent_tool_catalog,
)
from .tool_validation import (
    AgentToolSyntaxError, ValidatedAgentToolAction,
    validate_agent_tool_observation,
)
from .tool_projection import (
    bounded_agent_action_output_projection, bounded_agent_text_search_projection,
)
from cpn.components.tool_executors import (
    ExecutionEnvironmentIdentity, NumericalToolProfile,
    query_execution_environment_resources,
)
from .action_execution import (
    EXECUTION_PROVENANCE_DOCUMENT, EXECUTION_PROVENANCE_SCHEMA,
    OPTIONAL_TOOL_BINDINGS, OptionalAgentCapabilityUnavailable,
)


def loop_ref(loop):
    return LoopStateMechanicsMixin.loop_ref(loop)


def loop_document(loop):
    return LoopStateMechanicsMixin.loop_document(loop)

class LoopStateExecutionMixin:
    def start_agent_loop_v1(self, command):
        if not isinstance(command, StartAgentLoopCommand):
            raise TypeError("optional start requires StartAgentLoopCommand")
        context = canonical_invocation(self.core, self.kernel, command.invocation_ref).context
        binding, _, operation = self._declared(context)
        if (command.operation_binding_ref != context.operation_binding_ref
                or command.owner_ref != _version_from_payload(binding["principal_ref"])
                or command.model_condition != self._target(context).model_condition
                or command.llm_turn_budget != operation.declaration.config["resource_bounds"]["max_tool_turns"]
                or command.registered_tool_catalog_ref != self._catalog(context, command.tool_catalog)):
            raise ResourceIntegrityFault("optional loop start differs from registered authority")
        loop = self.mechanical_lifecycle.latest_loop(command.loop_id)
        if loop is not None:
            if loop.invocation_ref != command.invocation_ref:
                raise ResourceIntegrityFault("optional loop identity belongs to another invocation")
            return loop
        template_ref = _version_from_payload(binding["workspace_binding_ref"])
        template = self.kernel._exact_object(template_ref, expected_type="workspace_binding/v1").metadata
        lineage_ref = _version_from_payload(
            template["workspace_lineage_ref"])
        admission = self.kernel._exact_object(
            context.admission_marking_checkpoint_ref,
            expected_type="marking_checkpoint/v1").metadata
        base_refs = tuple(
            _version_from_payload(value)
            for value in admission["workspace_revision_refs"]
            if value.get("logical_id") == str(lineage_ref.entity_id))
        if len(base_refs) != 1:
            raise ResourceIntegrityFault(
                "firing admission lacks one workspace lineage head")
        base_ref, = base_refs
        workspace_ref = VersionRef("workspace_binding/v1", new_id("workspace_binding"), new_id("workspace_binding_version"))
        workspace = dict(template, workspace_binding_id=str(workspace_ref.entity_id),
            workspace_binding_version_id=str(workspace_ref.version_id), binding_kind="firing_view",
            template_binding_ref=_ref_payload(template_ref), invocation_ref=_ref_payload(context.invocation_ref),
            transition_firing_ref=_ref_payload(context.own_transition_firing_ref),
            base_revision_ref=_ref_payload(base_ref),
            allowed_root=(
                "workspace/views/"
                + context.own_transition_firing_ref.version_id.value))
        loop = AgentLoopSnapshot(command.loop_id, str(new_id("agent_loop_version")), command.invocation_ref,
            command.operation_binding_ref, AgentLoopState.NEW, 0, 0, command.llm_turn_budget, 0,
            command.model_condition, command.registered_tool_catalog_ref, None, None, None, command.owner_ref,
            workspace_binding_ref=workspace_ref,
            workspace_lineage_ref=lineage_ref,
            workspace_base_revision_ref=base_ref)
        committed_ref = self.mechanical_lifecycle.commit_start(
            loop, idempotency_key=command.idempotency_key,
            workspace=(workspace_ref, workspace))
        return self.hydrate_agent_loop_v1(committed_ref)

    def hydrate_agent_loop_v1(self, ref):
        return self.mechanical_lifecycle.hydrate_loop(ref)

    def _current(self, loop):
        try:
            self.mechanical_lifecycle.current_loop(loop)
        except Exception as exc:
            raise StaleAuthorityHead(
                "optional loop revision is not its exact committed head") from exc
        self._context(loop)
        return loop

    def _transition(self, loop, state, *, idempotency_key, **changes):
        self._current(loop)
        return self.mechanical_lifecycle.transition_loop(
            loop, state, idempotency_key=idempotency_key, **changes)

    def mark_agent_loop_waiting_v1(self, loop, *, expected_revision, idempotency_key):
        if expected_revision != loop.revision or loop.state != AgentLoopState.NEW:
            raise StaleAuthorityHead("optional initial wait requires its exact NEW revision")
        return self._transition(loop, AgentLoopState.WAITING_FOR_LLM, idempotency_key=idempotency_key)

    def continue_after_text_turn_v1(self, loop, turn, *, expected_revision, idempotency_key):
        return self.mechanical_lifecycle.continue_after_text_turn(
            loop=loop, turn=turn, expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            read_response=lambda ref: self.kernel._read_firing_registered(
                self._context(loop), ref))

    def agent_loop_segment_role_v1(self, execution, loop):
        self._execution(execution, loop)
        _, _, operation = self._declared(self._context(loop))
        return operation.declaration.config["agent_loop_role"]

    def task_model_call_handoff_required_v1(self, execution):
        execution = self._execution(execution)
        # Use the existing exact Registry accounting, not a local counter.
        manifest = self.core.get_version(self.ledger.recovery_manifest_ref().version_id).metadata
        counts = self.core.event_store.actual_model_call_counts()
        return counts[0] >= manifest["ordinary_global_cap"]

    def handoff_agent_llm_turn_cap_v1(self, execution, loop, *, idempotency_key):
        self._execution(execution, loop)
        return self.mechanical_lifecycle.close_at_cap(
            loop=loop, terminal_reason="llm_turn_cap",
            idempotency_key=idempotency_key)

    def handoff_agent_task_model_call_cap_v1(self, execution, loop, *, idempotency_key):
        self._execution(execution, loop)
        return self.mechanical_lifecycle.close_at_cap(
            loop=loop, terminal_reason="task_model_call_cap",
            idempotency_key=idempotency_key)

    def continue_agent_loop_role_segment_v1(self, execution, loop, *, idempotency_key):
        execution = self._execution(execution, loop)
        _binding, _compiled, operation = self._declared(
            execution.operation.canonical.context)
        role = operation.declaration.config.get("agent_loop_role")
        increment = operation.declaration.config.get(
            "turn_budget_extension")
        if (role not in {"critic", "finalization_reviewer"}
                or isinstance(increment, bool)
                or not isinstance(increment, int) or increment < 1):
            raise OptionalAgentCapabilityUnavailable(
                "same-role continuation requires a registered positive "
                "turn_budget_extension")
        return self.mechanical_lifecycle.continue_role_segment(
            loop=loop,
            new_turn_budget=loop.llm_turn_budget + increment,
            idempotency_key=idempotency_key)

    def continue_after_incomplete_length_turn_v1(self, loop, turn, *, expected_revision, idempotency_key):
        return self.mechanical_lifecycle.continue_after_length_interruption(
            loop=loop, interruption=turn,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            read_response=lambda ref: self.kernel._read_firing_registered(
                self._context(loop), ref))
