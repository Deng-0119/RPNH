"""AgentLoop turn records mechanics.

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


class TurnRecordsMechanicsMixin:
    def commit_provider_turn(
            self, *, before: AgentLoopSnapshot, after: AgentLoopSnapshot,
            attempt: Any, response_ref: ResourceVersionRef,
            response_size: int, turn_ref: VersionRef,
            tool_call_count: int, idempotency_key: str,
            stage_response: Callable[[Any], None],
            length_interrupted: bool = False,
            stage_provider_completion: Callable[[Any], None] | None = None,
            provider_completion: Mapping[str, Any] | None = None,
    ) -> None:
        """Publish response, turn fact and exactly one successor Loop."""
        if (before.state != AgentLoopState.WAITING_FOR_LLM
                or after.state != AgentLoopState.TURN_STORED
                or after.revision != before.revision + 1
                or after.loop_id != before.loop_id
                or attempt.invocation_ref.entity_type != "llm_invocation_spec/v1"
                or attempt.attempt_ref.entity_type
                != "llm_invocation_attempt/v1"
                or turn_ref.entity_type != "agent_turn/v1"
                or isinstance(response_size, bool) or response_size < 1
                or isinstance(tool_call_count, bool) or tool_call_count < 0):
            raise AgentLoopMechanicalLifecycleError(
                "provider turn command is not one exact Loop successor")
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(before))
        stage_response(tx)
        if stage_provider_completion is not None:
            stage_provider_completion(tx)
        if provider_completion is not None:
            completion = dict(provider_completion)
            provider_attempt_id = completion.get("provider_attempt_id")
            if (stage_provider_completion is not None
                    or not isinstance(provider_attempt_id, str)
                    or not provider_attempt_id):
                raise AgentLoopMechanicalLifecycleError(
                    "provider completion must have one mechanical owner")
            self._event(
                tx, event_type="provider_attempt_completed/v1",
                aggregate_id=provider_attempt_id,
                aggregate_type="provider_attempt", payload=completion,
                producer_invocation_id=before.invocation_ref.entity_id)
        self.prewrite_loop(tx, after)
        registered = {
            "llm_invocation_ref": _ref_payload(attempt.invocation_ref),
            "llm_invocation_attempt_ref": _ref_payload(attempt.attempt_ref),
            "response_resource_ref": _resource_payload(response_ref),
            "response_size": response_size,
        }
        producer = before.invocation_ref.entity_id
        self._event(
            tx, event_type="llm_response_registered/v1",
            aggregate_id=str(attempt.attempt_ref.entity_id),
            aggregate_type="llm_invocation_attempt", payload=registered,
            producer_invocation_id=producer)
        if length_interrupted:
            self._event(
                tx, event_type="llm_invocation_interrupted/v1",
                aggregate_id=str(attempt.attempt_ref.entity_id),
                aggregate_type="llm_invocation_attempt",
                producer_invocation_id=producer,
                payload={
                    "agent_loop_id": before.loop_id,
                    "turn_sequence": before.next_turn_sequence,
                    **registered, "finish_reason": "length",
                    "revision": after.revision,
                })
        else:
            self._event(
                tx, event_type="llm_invocation_succeeded/v1",
                aggregate_id=str(attempt.attempt_ref.entity_id),
                aggregate_type="llm_invocation_attempt",
                producer_invocation_id=producer,
                payload={
                    **{key: value for key, value in registered.items()
                       if key != "response_size"},
                    "agent_turn_ref": _ref_payload(turn_ref),
                    "turn_sequence": before.next_turn_sequence,
                })
            self._event(
                tx, event_type="agent_turn_recorded/v2",
                aggregate_id=before.loop_id, aggregate_type="agent_loop",
                producer_invocation_id=producer,
                payload={
                    "agent_loop_id": before.loop_id,
                    "agent_turn_id": str(turn_ref.entity_id),
                    "agent_turn_version_id": str(turn_ref.version_id),
                    "sequence": before.next_turn_sequence,
                    **registered,
                    "response_protocol_ref": "llm_response_envelope/v1",
                    "tool_call_count": tool_call_count,
                    "revision": after.revision,
                })
        tx.commit()

    def record_provider_turn(
            self, *, loop: AgentLoopSnapshot, attempt: LLMCallAttempt,
            response_ref: ResourceVersionRef, response_size: int,
            turn_ref: VersionRef, tool_call_count: int,
            idempotency_key: str,
            stage_response: Callable[[Any], None],
            length_interrupted: bool = False,
            stage_provider_completion: Callable[[Any], None] | None = None,
            provider_completion: Mapping[str, Any] | None = None,
    ) -> AgentLoopSnapshot:
        """Validate one exact attempt and commit its sole Loop successor."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,))
        self._validate_attempt_for_loop(
            current, attempt, invocation_kinds=("normal_turn",))
        sequence = current.next_turn_sequence
        stored = replace(
            current,
            loop_version_id=str(_stable_id(
                "agent_loop_version", current.loop_id,
                current.revision + 1, idempotency_key)),
            state=AgentLoopState.TURN_STORED,
            revision=current.revision + 1,
            next_turn_sequence=(sequence if length_interrupted
                                else sequence + 1),
            llm_turns_used=(current.llm_turns_used if length_interrupted
                            else current.llm_turns_used + 1))
        self.commit_provider_turn(
            before=current, after=stored, attempt=attempt,
            response_ref=response_ref, response_size=response_size,
            turn_ref=turn_ref, tool_call_count=tool_call_count,
            idempotency_key=idempotency_key,
            stage_response=stage_response,
            length_interrupted=length_interrupted,
            stage_provider_completion=stage_provider_completion,
            provider_completion=provider_completion)
        return self.hydrate_loop(self.loop_ref(stored))

    def turn_events(self, loop: AgentLoopSnapshot) -> tuple[Any, ...]:
        """Return immutable recorded-turn events for one Loop in commit order."""
        if not isinstance(loop, AgentLoopSnapshot):
            raise TypeError("turn history requires an AgentLoop snapshot")
        return tuple(self.core.event_store.list_events_by_aggregate(
            loop.loop_id, event_types=("agent_turn_recorded/v2",)))

    def latest_context_overlay(
            self, loop: AgentLoopSnapshot,
    ) -> AgentContextOverlay | None:
        """Hydrate the newest completed v3 replacement-history overlay."""
        if not isinstance(loop, AgentLoopSnapshot):
            raise TypeError("context overlay requires an AgentLoop snapshot")
        candidates: list[tuple[int, AgentContextOverlay]] = []
        for row in self.core.event_store.object_rows_by_type(
                "agent_context_compaction/v3"):
            value = json.loads(str(row["metadata_json"]))
            raw_loop_ref = value.get("agent_loop_ref")
            if (not isinstance(raw_loop_ref, Mapping)
                    or raw_loop_ref.get("logical_id") != loop.loop_id):
                continue
            ref = VersionRef(
                "agent_context_compaction/v3",
                TypedId.parse(str(row["logical_id"]),
                              expected="agent_context_compaction"),
                TypedId.parse(str(row["version_id"]),
                              expected="agent_context_compaction_version"))
            exact = dict(self.kernel._exact_object(
                ref, expected_type="agent_context_compaction/v3").metadata)
            if exact != value or exact.get(
                    "agent_context_compaction_ref") != _ref_payload(ref):
                raise AgentLoopMechanicalLifecycleError(
                    "context overlay differs from its immutable object")
            capsule = exact.get("replacement_history", [None])[0]
            revision = (capsule.get("loop_revision")
                        if isinstance(capsule, Mapping) else None)
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise AgentLoopMechanicalLifecycleError(
                    "context overlay lacks its completed Loop revision")
            candidates.append((revision, AgentContextOverlay(
                compaction_ref=ref,
                loop_id=loop.loop_id,
                trigger_reason=str(exact["trigger_reason"]),
                first_turn_sequence=int(exact["first_turn_sequence"]),
                last_turn_sequence=int(exact["last_turn_sequence"]),
                covered_turn_refs=tuple(
                    _version_from_payload(item)
                    for item in exact["covered_turn_refs"]),
                replacement_history=tuple(exact["replacement_history"]),
                llm_invocation_ref=_version_from_payload(
                    exact["llm_invocation_ref"]),
                llm_invocation_attempt_ref=_version_from_payload(
                    exact["llm_invocation_attempt_ref"]),
            )))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        if (len(candidates) > 1
                and candidates[-2][0] == candidates[-1][0]):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop has multiple context overlays at one revision")
        overlay = candidates[-1][1]
        events = self.turn_events(loop)
        covered = tuple(
            self.turn_ref_from_event(event)
            for event in events
            if int(event.payload["sequence"])
            <= overlay.last_turn_sequence)
        if covered != overlay.covered_turn_refs:
            raise AgentLoopMechanicalLifecycleError(
                "context overlay does not cover one exact turn prefix")
        return overlay

    def prior_turn_refs(
            self, loop: AgentLoopSnapshot,
    ) -> tuple[VersionRef, ...]:
        """Return the exact contiguous committed turn prefix for one Loop."""
        current = self.current_loop(loop)
        by_sequence: dict[int, VersionRef] = {}
        for event in self.turn_events(current):
            sequence = event.payload.get("sequence")
            if (isinstance(sequence, bool) or not isinstance(sequence, int)
                    or sequence < 0 or sequence in by_sequence):
                raise AgentLoopMechanicalLifecycleError(
                    "AgentLoop turn history is not one exact prefix")
            by_sequence[sequence] = self.turn_ref_from_event(event)
        if tuple(sorted(by_sequence)) != tuple(
                range(current.next_turn_sequence)):
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop turn history is not contiguous")
        return tuple(by_sequence[index]
                     for index in range(current.next_turn_sequence))

    @staticmethod
    def turn_ref_from_event(event: Any) -> VersionRef:
        payload = getattr(event, "payload", None)
        if (not isinstance(payload, Mapping)
                or "agent_turn_id" not in payload
                or "agent_turn_version_id" not in payload):
            raise AgentLoopMechanicalLifecycleError(
                "turn event lacks exact immutable identity")
        return VersionRef(
            "agent_turn/v1",
            TypedId.parse(str(payload["agent_turn_id"]),
                          expected="agent_turn"),
            TypedId.parse(str(payload["agent_turn_version_id"]),
                          expected="agent_turn_version"))

    def turn_ref_for(self, loop_id: str, sequence: int) -> VersionRef:
        events = tuple(
            event for event in self.core.event_store.list_events_by_aggregate(
                loop_id, event_types=("agent_turn_recorded/v2",))
            if event.payload.get("agent_loop_id") == loop_id
            and event.payload.get("sequence") == sequence)
        if len(events) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop lacks one exact recorded turn for the sequence")
        return self.turn_ref_from_event(events[0])

    def hydrate_turn_event(
            self, loop: AgentLoopSnapshot, event: Any, *,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> AgentTurnRecord:
        """Reconstruct one turn only from its event and firing-local bytes."""
        data = getattr(event, "payload", None)
        if (not isinstance(data, Mapping)
                or data.get("agent_loop_id") != loop.loop_id):
            raise AgentLoopMechanicalLifecycleError(
                "turn event belongs to another AgentLoop")
        response_ref = _resource_from_payload(data["response_resource_ref"])
        payload = read_response(response_ref)
        if not isinstance(payload, bytes):
            raise AgentLoopMechanicalLifecycleError(
                "turn response reader returned no immutable bytes")
        authority = PublishedLLMResponse(
            response_resource_ref=response_ref,
            llm_invocation_attempt_ref=_version_from_payload(
                data["llm_invocation_attempt_ref"]),
            llm_invocation_ref=_version_from_payload(
                data["llm_invocation_ref"]),
            model_condition=loop.model_condition,
            size=int(data["response_size"]),
            response_protocol_ref=str(data["response_protocol_ref"]))
        observed = observe_registered_llm_response(payload, authority)
        facts = tuple(AgentToolCallFact(
            item.tool_call_ordinal, item.tool_call_id, item.tool_name,
            item.raw_arguments, item.syntax_errors,
            item.action_identity_kind, item.action_identity_key)
            for item in observed.tool_calls)
        return AgentTurnRecord(
            turn_id=str(data["agent_turn_id"]), loop_id=loop.loop_id,
            sequence=int(data["sequence"]),
            llm_invocation_ref=authority.llm_invocation_ref,
            llm_invocation_attempt_ref=(
                authority.llm_invocation_attempt_ref),
            response_resource_ref=response_ref,
            response_size=len(payload),
            response_protocol_ref=authority.response_protocol_ref,
            text_present=json.loads(payload).get("text") is not None,
            tool_calls=facts, finish_reason=observed.finish_reason,
            recorded_revision=int(data["revision"]))

    def hydrate_turn(
            self, turn_ref: VersionRef, *,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> AgentTurnRecord:
        """Hydrate one exact event-backed turn without a composition view."""
        if (not isinstance(turn_ref, VersionRef)
                or turn_ref.entity_type != "agent_turn/v1"):
            raise TypeError("turn hydration requires agent_turn/v1")
        events = tuple(self.core.event_store.agent_turn_events_for_ref(
            turn_ref))
        if len(events) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "turn lacks one authoritative recorded event")
        event = events[0]
        if self.turn_ref_from_event(event) != turn_ref:
            raise AgentLoopMechanicalLifecycleError(
                "turn event differs from its exact immutable ref")
        loop_id = str(event.payload.get("agent_loop_id"))
        revision = event.payload.get("revision")
        rows = tuple(
            row for row in self.core.event_store.object_rows_by_logical(
                loop_id, object_type="agent_loop/v1")
            if json.loads(str(row["metadata_json"])).get("revision")
            == revision)
        if len(rows) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "turn lacks its exact Loop revision")
        loop = self.hydrate_loop(VersionRef(
            "agent_loop/v1",
            TypedId.parse(loop_id, expected="agent_loop"),
            TypedId.parse(str(rows[0]["version_id"]),
                          expected="agent_loop_version")))
        turn = self.hydrate_turn_event(
            loop, event, read_response=read_response)
        if turn.turn_id != str(turn_ref.entity_id):
            raise AgentLoopMechanicalLifecycleError(
                "hydrated turn differs from its exact identity")
        return turn

    def hydrate_current_turn(
            self, loop: AgentLoopSnapshot, *,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> AgentTurnRecord:
        current = self.current_loop(loop)
        if (current.state != AgentLoopState.TURN_STORED
                or current.next_turn_sequence < 1):
            raise AgentLoopMechanicalLifecycleError(
                "current turn requires exact TURN_STORED authority")
        matches = tuple(
            event for event in self.turn_events(current)
            if event.payload.get("sequence")
            == current.next_turn_sequence - 1)
        if len(matches) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "stored Loop lacks one exact immutable turn")
        turn = self.hydrate_turn_event(
            current, matches[0], read_response=read_response)
        if turn.recorded_revision != current.revision:
            raise AgentLoopMechanicalLifecycleError(
                "stored turn differs from its Loop revision")
        return turn

    def hydrate_length_interruption(
            self, loop: AgentLoopSnapshot, *, sequence: int,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> object | None:
        """Hydrate the sole exact response-length interruption for a slot."""
        current = self.current_loop(loop)
        if (isinstance(sequence, bool) or not isinstance(sequence, int)
                or sequence < 0):
            raise TypeError("length interruption sequence is invalid")
        matches = tuple(
            event for event in self.core.event_store.list_events_by_type(
                ("llm_invocation_interrupted/v1",))
            if (event.payload.get("agent_loop_id") == current.loop_id
                and event.payload.get("turn_sequence") == sequence))
        if not matches:
            return None
        if len(matches) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "semantic slot has multiple length interruptions")
        value = matches[0].payload
        invocation_ref = _version_from_payload(value["llm_invocation_ref"])
        attempt_ref = _version_from_payload(
            value["llm_invocation_attempt_ref"])
        response_ref = _resource_from_payload(value["response_resource_ref"])
        invocation = self.exact_object_document(
            invocation_ref, expected_type="llm_invocation_spec/v1")
        attempt = self.exact_object_document(
            attempt_ref, expected_type="llm_invocation_attempt/v1")
        payload = read_response(response_ref)
        authority = PublishedLLMResponse(
            response_ref, attempt_ref, invocation_ref,
            str(invocation["model_condition"]), int(value["response_size"]),
            "llm_response_envelope/v1")
        observed = observe_registered_llm_response(payload, authority)
        if (invocation.get("invocation_kind") != "normal_turn"
                or invocation.get("agent_loop_ref", {}).get("logical_id")
                != current.loop_id
                or invocation.get("turn_sequence") != sequence
                or attempt.get("llm_invocation_ref")
                != _ref_payload(invocation_ref)
                or len(payload) != int(value["response_size"])
                or observed.finish_reason != "length"):
            raise AgentLoopMechanicalLifecycleError(
                "length interruption differs from immutable response authority")
        from .service import AgentLengthInterruptionRecord
        return AgentLengthInterruptionRecord(
            loop_id=current.loop_id, sequence=sequence,
            llm_invocation_ref=invocation_ref,
            llm_invocation_attempt_ref=attempt_ref,
            response_resource_ref=response_ref,
            response_size=int(value["response_size"]),
            model_condition=str(invocation["model_condition"]),
            recorded_revision=int(value["revision"]))

    def prepare_turn_actions(
            self, loop: AgentLoopSnapshot, turn: AgentTurnRecord, *,
            read_response: Callable[[ResourceVersionRef], bytes],
            tool_argument_schemas: Mapping[str, Mapping[str, Any]],
            timing_origin_ns: int | None = None,
    ) -> tuple[object, ...]:
        """Parse one immutable turn into the shared ordered action DTOs."""
        exact = self.hydrate_current_turn(
            loop, read_response=read_response)
        if exact != turn or turn.finish_reason == "length":
            raise AgentLoopMechanicalLifecycleError(
                "action parsing requires the exact usable stored turn")
        observation = observe_registered_llm_response(
            read_response(turn.response_resource_ref), PublishedLLMResponse(
                turn.response_resource_ref,
                turn.llm_invocation_attempt_ref,
                turn.llm_invocation_ref, loop.model_condition,
                turn.response_size, turn.response_protocol_ref))
        from cpn.components.tool_executors import (
            prepare_registered_agent_actions,
        )
        return prepare_registered_agent_actions(
            loop_id=loop.loop_id, turn_sequence=turn.sequence,
            expected_revision=loop.revision,
            tool_calls=observation.tool_calls,
            tool_argument_schemas=tool_argument_schemas,
            timing_origin_ns=timing_origin_ns)

    def _validate_current_loop(self, expected: AgentLoopSnapshot) -> None:
        row = self.core.event_store.latest_object_row(
            TypedId.parse(expected.loop_id, expected="agent_loop"),
            object_type="agent_loop/v1")
        if row is None or str(row["version_id"]) != expected.loop_version_id:
            raise AgentLoopMechanicalLifecycleError(
                "AgentLoop transaction crossed its committed head")

    def prewrite_object(
            self, tx: Any, ref: VersionRef,
            document: Mapping[str, Any], *,
            producer_invocation_id: TypedId,
    ) -> None:
        """Prewrite one schema-named mechanical object without policy lookup."""
        payload = dict(document)
        self.core.catalog.validate_instance(
            ref.entity_type, category="object", instance=payload)
        tx.prewrite(
            object_type=ref.entity_type, logical_id=ref.entity_id,
            version_id=ref.version_id, payload=canonical_json(payload),
            metadata=payload, media_type="application/json",
            schema_ref="registry_v1/" + ref.entity_type,
            producer_invocation_id=producer_invocation_id)

    def commit_request_authority(
            self, *, idempotency_key: str,
            stage_request: Callable[[Any], None],
            loop: AgentLoopSnapshot | None = None,
            request_ref: ResourceVersionRef | None = None,
            verify_replay: Callable[[ResourceVersionRef], None] | None = None,
    ) -> ResourceVersionRef | None:
        """Commit one policy-prepared logical request through the shared writer."""
        if (not isinstance(idempotency_key, str) or not idempotency_key
                or not callable(stage_request)):
            raise AgentLoopMechanicalLifecycleError(
                "request authority requires one prepared transaction callback")
        if loop is not None:
            self.require_current_revision(
                loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,
                                      AgentLoopState.COMPACTING,
                                      AgentLoopState.TURN_STORED))
        if request_ref is not None:
            if not isinstance(request_ref, ResourceVersionRef):
                raise TypeError("request authority requires a resource ref")
            if self.core.event_store.object_row(
                    request_ref.resource_version_id) is not None:
                if verify_replay is None:
                    raise AgentLoopMechanicalLifecycleError(
                        "request replay requires exact immutable verification")
                verify_replay(request_ref)
                return request_ref
        tx = self.core.begin(idempotency_key=idempotency_key)
        if loop is not None:
            tx.validate_before_commit(
                lambda _view: self._validate_current_loop(loop))
        stage_request(tx)
        tx.commit()
        if request_ref is not None:
            if self.core.event_store.object_row(
                    request_ref.resource_version_id) is None:
                raise AgentLoopMechanicalLifecycleError(
                    "request commit did not publish its declared authority")
            if verify_replay is not None:
                verify_replay(request_ref)
        return request_ref

    def exact_object_document(
            self, ref: VersionRef, *, expected_type: str,
    ) -> dict[str, Any]:
        if (not isinstance(ref, VersionRef)
                or ref.entity_type != expected_type):
            raise TypeError("exact object hydration has the wrong type")
        value = dict(self.kernel._exact_object(
            ref, expected_type=expected_type).metadata)
        reference_key = {
            "llm_invocation_spec/v1": "llm_invocation_ref",
            "llm_invocation_attempt/v1": "llm_invocation_attempt_ref",
        }.get(expected_type)
        if (reference_key is not None
                and value.get(reference_key) != _ref_payload(ref)):
            raise AgentLoopMechanicalLifecycleError(
                "immutable object bytes differ from their exact identity")
        return value

    def commit_prepared_attempt(
            self, *, loop: AgentLoopSnapshot, invocation_ref: VersionRef,
            invocation_document: Mapping[str, Any],
            attempt_ref: VersionRef,
            attempt_document: Mapping[str, Any],
            producer_invocation_id: TypedId,
            idempotency_key: str,
            provider_attempt_ref: VersionRef | None = None,
    ) -> None:
        """Commit neutral request-attempt identity and optional provider link."""
        if (invocation_ref.entity_type != "llm_invocation_spec/v1"
                or attempt_ref.entity_type != "llm_invocation_attempt/v1"
                or invocation_document.get("llm_invocation_ref")
                != _ref_payload(invocation_ref)
                or attempt_document.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt_ref)
                or attempt_document.get("llm_invocation_ref")
                != _ref_payload(invocation_ref)):
            raise AgentLoopMechanicalLifecycleError(
                "prepared attempt documents cross their exact identities")
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(loop))
        self.prewrite_object(
            tx, invocation_ref, invocation_document,
            producer_invocation_id=producer_invocation_id)
        self.prewrite_object(
            tx, attempt_ref, attempt_document,
            producer_invocation_id=producer_invocation_id)
        if provider_attempt_ref is not None:
            if provider_attempt_ref.entity_type != "provider_attempt_spec/v1":
                raise AgentLoopMechanicalLifecycleError(
                    "neutral attempt provider link has the wrong type")
            tx.relate(TypedRelation(
                new_id("relation"), "derived_from", attempt_ref,
                provider_attempt_ref),
                producer_invocation_id=producer_invocation_id)
        self._event(
            tx, event_type="llm_invocation_attempt_reserved/v1",
            aggregate_id=str(attempt_ref.entity_id),
            aggregate_type="llm_invocation_attempt",
            payload=dict(attempt_document),
            producer_invocation_id=producer_invocation_id)
        tx.commit()

    def reserve_invocation_attempt(
            self, *, loop: AgentLoopSnapshot,
            invocation_ref: VersionRef,
            invocation_document: Mapping[str, Any],
            producer_invocation_id: TypedId,
            reservation_class: str | None,
            finalization_scope: str | None,
            model_condition: str,
            canonical_request_bytes: bytes,
            max_response_bytes: int,
            maximum_attempts: int,
            idempotency_key: str,
            provider_attempt_ref: VersionRef | None = None,
    ) -> LLMCallAttempt:
        """Reserve the contiguous next neutral attempt for one logical call."""
        current = self.require_current_revision(
            loop, allowed_states=(AgentLoopState.WAITING_FOR_LLM,
                                  AgentLoopState.COMPACTING,
                                  AgentLoopState.TURN_STORED))
        if (not isinstance(invocation_ref, VersionRef)
                or invocation_ref.entity_type != "llm_invocation_spec/v1"
                or invocation_document.get("llm_invocation_ref")
                != _ref_payload(invocation_ref)
                or invocation_document.get("agent_loop_ref")
                != _ref_payload(self.loop_ref(current))
                or invocation_document.get("model_condition")
                != model_condition
                or model_condition != current.model_condition
                or not isinstance(canonical_request_bytes, bytes)
                or not canonical_request_bytes
                or isinstance(max_response_bytes, bool)
                or not isinstance(max_response_bytes, int)
                or max_response_bytes < 1
                or isinstance(maximum_attempts, bool)
                or not isinstance(maximum_attempts, int)
                or not 1 <= maximum_attempts <= 3):
            raise AgentLoopMechanicalLifecycleError(
                "attempt reservation differs from its current logical call")
        existing_invocation = self.core.event_store.object_row(
            invocation_ref.version_id)
        if existing_invocation is not None:
            if self.exact_object_document(
                    invocation_ref,
                    expected_type="llm_invocation_spec/v1") \
                    != dict(invocation_document):
                raise AgentLoopMechanicalLifecycleError(
                    "invocation replay differs from its immutable authority")

        attempts: list[dict[str, Any]] = []
        for row in self.core.event_store.object_rows_by_type(
                "llm_invocation_attempt/v1"):
            value = json.loads(str(row["metadata_json"]))
            if value.get("llm_invocation_ref") == _ref_payload(
                    invocation_ref):
                attempts.append(value)
        attempts.sort(key=lambda value: int(value["attempt_ordinal"]))
        if len(attempts) > maximum_attempts:
            raise AgentLoopMechanicalLifecycleError(
                "attempt ledger exceeds its registered bound")
        if tuple(value.get("attempt_ordinal") for value in attempts) \
                != tuple(range(len(attempts))):
            raise AgentLoopMechanicalLifecycleError(
                "attempt ledger is not contiguous")

        prior_ref: VersionRef | None = None
        if attempts:
            prior = attempts[-1]
            prior_ref = _version_from_payload(
                prior["llm_invocation_attempt_ref"])
            terminal = tuple(
                event for event in
                self.core.event_store.list_events_by_aggregate(
                    str(prior_ref.entity_id))
                if event.event_type in {
                    "llm_invocation_failed/v1",
                    "llm_invocation_interrupted/v1",
                    "llm_invocation_owner_interrupted/v1",
                    "llm_invocation_succeeded/v1",
                })
            if not terminal:
                # The open attempt is immutable.  Replaying its reservation
                # must return that exact fact, not rebuild it with itself as
                # its predecessor.
                return LLMCallAttempt(
                    invocation_ref, prior_ref, len(attempts) - 1,
                    model_condition, canonical_request_bytes,
                    max_response_bytes)
            elif terminal[-1].event_type != "llm_invocation_failed/v1":
                raise AgentLoopMechanicalLifecycleError(
                    "settled successful/interrupted call cannot retry")
            elif terminal[-1].payload.get("next_attempt_allowed") is True:
                ordinal = len(attempts)
                if ordinal >= maximum_attempts:
                    raise AgentLoopMechanicalLifecycleError(
                        "attempt ledger exhausted its registered bound")
                attempt_ref = VersionRef(
                    "llm_invocation_attempt/v1",
                    _stable_id("llm_invocation_attempt",
                               invocation_ref.version_id, ordinal),
                    _stable_id("llm_invocation_attempt_version",
                               invocation_ref.version_id, ordinal))
            else:
                raise AgentLoopMechanicalLifecycleError(
                    "failed call does not authorize another attempt")
        else:
            ordinal = 0
            attempt_ref = VersionRef(
                "llm_invocation_attempt/v1",
                _stable_id("llm_invocation_attempt",
                           invocation_ref.version_id, ordinal),
                _stable_id("llm_invocation_attempt_version",
                           invocation_ref.version_id, ordinal))
        attempt_document = {
            "llm_invocation_attempt_id": str(attempt_ref.entity_id),
            "llm_invocation_attempt_version_id": str(attempt_ref.version_id),
            "llm_invocation_attempt_ref": _ref_payload(attempt_ref),
            "llm_invocation_ref": _ref_payload(invocation_ref),
            "attempt_ordinal": ordinal,
            "prior_attempt_ref": (
                _ref_payload(prior_ref) if prior_ref is not None else None),
            "reservation_class": reservation_class,
            "finalization_scope": finalization_scope,
        }
        if self.core.event_store.object_row(attempt_ref.version_id) is None:
            self.commit_prepared_attempt(
                loop=current, invocation_ref=invocation_ref,
                invocation_document=invocation_document,
                attempt_ref=attempt_ref,
                attempt_document=attempt_document,
                producer_invocation_id=producer_invocation_id,
                idempotency_key=f"{idempotency_key}:attempt:{ordinal}",
                provider_attempt_ref=provider_attempt_ref)
        elif self.exact_object_document(
                attempt_ref,
                expected_type="llm_invocation_attempt/v1") \
                != attempt_document:
            raise AgentLoopMechanicalLifecycleError(
                "attempt replay differs from its immutable authority")
        return LLMCallAttempt(
            invocation_ref, attempt_ref, ordinal, model_condition,
            canonical_request_bytes, max_response_bytes)

    def require_raw_return(
            self, attempt_ref: VersionRef, *,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> tuple[Any, ResourceVersionRef, bytes]:
        """Resolve the one Core raw-return fact for a neutral attempt."""
        if (not isinstance(attempt_ref, VersionRef)
                or attempt_ref.entity_type
                != "provider_attempt_spec/v1"):
            raise TypeError(
                "raw-return authority requires provider_attempt_spec/v1")
        events = tuple(self.core.event_store.list_events_by_aggregate(
            str(attempt_ref.entity_id), event_types=(
                "provider_attempt_submission_observed/v1",)))
        if len(events) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "provider attempt lacks one exact raw return")
        response_ref = _resource_from_payload(
            events[0].payload["response_resource_ref"])
        payload = read_response(response_ref)
        if not isinstance(payload, bytes) or not payload:
            raise AgentLoopMechanicalLifecycleError(
                "raw return lacks registered immutable bytes")
        return events[0], response_ref, payload

    @staticmethod
    def _relation_ref(raw: Mapping[str, Any]) -> VersionRef:
        return VersionRef(
            str(raw["entity_type"]),
            TypedId.parse(str(raw["entity_id"])),
            TypedId.parse(str(raw["version_id"])))

    def provider_attempt_for_invocation_attempt(
            self, attempt_ref: VersionRef,
    ) -> ProviderAttemptV2:
        """Hydrate the sole provider attempt linked to a neutral attempt."""
        attempt_document = self.exact_object_document(
            attempt_ref, expected_type="llm_invocation_attempt/v1")
        invocation_ref = _version_from_payload(
            attempt_document["llm_invocation_ref"])
        invocation_document = self.exact_object_document(
            invocation_ref, expected_type="llm_invocation_spec/v1")
        context = InvocationLifecycle(self.core).hydrate_context(
            _version_from_payload(invocation_document["invocation_ref"]))
        firing_ref = context.own_transition_firing_ref
        if firing_ref is None:
            raise AgentLoopMechanicalLifecycleError(
                "neutral attempt lacks exact firing authority")
        view = self.core.event_store.firing_view(
            firing_version_id=firing_ref.version_id,
            invocation_version_id=context.invocation_ref.version_id)
        targets: list[VersionRef] = []
        for row in self.core.event_store.relation_rows_for_view(
                view, version_id=attempt_ref.version_id,
                relation_type="derived_from", endpoint="source"):
            source = self._relation_ref(json.loads(row["source_json"]))
            target = self._relation_ref(json.loads(row["target_json"]))
            if (source == attempt_ref
                    and target.entity_type == "provider_attempt_spec/v1"):
                targets.append(target)
        if len(targets) != 1:
            raise AgentLoopMechanicalLifecycleError(
                "neutral attempt lacks one exact provider attempt linkage")
        provider_ref = targets[0]
        provider = self.exact_object_document(
            provider_ref, expected_type="provider_attempt_spec/v1")
        call_ref = _version_from_payload(provider["llm_call_ref"])
        call = self.exact_object_document(
            call_ref, expected_type="llm_call_spec/v2")
        hydrated_call = LLMCallV2(
            call_ref.entity_id, call_ref.version_id,
            _version_from_payload(call["invocation_ref"]),
            _version_from_payload(call["operation_binding_ref"]),
            call["agent_loop_ref"], call["turn_sequence"],
            _resource_from_payload(call["request_resource_ref"]),
            _version_from_payload(call["terminal_delivery_ref"]),
            _resource_from_payload(call["semantic_prompt_resource_ref"]),
            _resource_from_payload(call["llm_execution_target_ref"]),
            call["backend"], call["model"],
            _resource_from_payload(call["transport_contract_ref"]),
            call["interaction_protocol_ref"], call["response_adapter_ref"],
            _resource_from_payload(call["tool_catalog_ref"]),
            tuple(_version_from_payload(raw)
                  for raw in call["prior_turn_refs"]),
            call["timeout_seconds"], call["max_response_bytes"])
        return ProviderAttemptV2(
            provider_ref.entity_id, provider_ref.version_id, hydrated_call,
            provider["reservation_class"], provider["finalization_scope"])

    def raw_return_for_invocation_attempt(
            self, attempt_ref: VersionRef, *,
            read_response: Callable[[ResourceVersionRef], bytes],
    ) -> tuple[ProviderAttemptV2, Any, ResourceVersionRef, bytes]:
        provider = self.provider_attempt_for_invocation_attempt(attempt_ref)
        event, response_ref, payload = self.require_raw_return(
            provider.ref, read_response=read_response)
        return provider, event, response_ref, payload

    def commit_llm_failure(
            self, *, loop: AgentLoopSnapshot, attempt: Any,
            disposition: str, next_attempt_allowed: bool,
            idempotency_key: str, failure_code: str | None = None,
            submission_state: str | None = None,
            events: Sequence[
                tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> None:
        """Persist one neutral failed-attempt disposition."""
        self.current_loop(loop)
        if (not isinstance(disposition, str) or not disposition
                or not isinstance(next_attempt_allowed, bool)
                or (failure_code is None) != (submission_state is None)):
            raise AgentLoopMechanicalLifecycleError(
                "LLM failure disposition is incomplete")
        payload: dict[str, Any] = {
            "llm_invocation_ref": _ref_payload(attempt.invocation_ref),
            "llm_invocation_attempt_ref": _ref_payload(
                attempt.attempt_ref),
            "attempt_ordinal": attempt.attempt_ordinal,
            "disposition": disposition,
            "next_attempt_allowed": next_attempt_allowed,
        }
        if failure_code is not None:
            payload.update(
                failure_code=failure_code,
                submission_state=submission_state)
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(loop))
        for event_type, aggregate_id, aggregate_type, event_payload in events:
            self._event(
                tx, event_type=event_type, aggregate_id=aggregate_id,
                aggregate_type=aggregate_type, payload=event_payload,
                producer_invocation_id=loop.invocation_ref.entity_id)
        self._event(
            tx, event_type="llm_invocation_failed/v1",
            aggregate_id=str(attempt.attempt_ref.entity_id),
            aggregate_type="llm_invocation_attempt", payload=payload,
            producer_invocation_id=loop.invocation_ref.entity_id)
        tx.commit()

    def record_invocation_owner_interruption(
            self, *, loop: AgentLoopSnapshot, attempt: LLMCallAttempt,
            submission_state: str, idempotency_key: str,
            invocation_kinds: Sequence[str],
            allowed_states: Sequence[AgentLoopState],
    ) -> None:
        """Atomically close one cancelled physical call without a model result."""

        current = self.require_current_revision(
            loop, allowed_states=allowed_states)
        if (not isinstance(attempt, LLMCallAttempt)
                or submission_state not in _OWNER_INTERRUPTION_SUBMISSION_STATES
                or not isinstance(idempotency_key, str)
                or not idempotency_key):
            raise AgentLoopMechanicalLifecycleError(
                "LLM owner interruption is incomplete")
        invocation = self.exact_object_document(
            attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1")
        attempt_document = self.exact_object_document(
            attempt.attempt_ref,
            expected_type="llm_invocation_attempt/v1")
        provider = self.provider_attempt_for_invocation_attempt(
            attempt.attempt_ref)
        if (invocation.get("agent_loop_ref")
                != _ref_payload(self.loop_ref(current))
                or invocation.get("invocation_kind")
                not in tuple(invocation_kinds)
                or invocation.get("invocation_ref")
                != _ref_payload(current.invocation_ref)
                or attempt_document.get("llm_invocation_ref")
                != _ref_payload(attempt.invocation_ref)
                or attempt_document.get("llm_invocation_attempt_ref")
                != _ref_payload(attempt.attempt_ref)
                or provider.call.invocation_ref != current.invocation_ref
                or provider.call.operation_binding_ref
                != current.operation_binding_ref):
            raise AgentLoopMechanicalLifecycleError(
                "LLM owner interruption differs from its current authority")
        payload = {
            "provider_attempt_id": str(provider.attempt_id),
            "llm_invocation_ref": _ref_payload(attempt.invocation_ref),
            "llm_invocation_attempt_ref": _ref_payload(attempt.attempt_ref),
            "provider_attempt_ref": _ref_payload(provider.ref),
            "llm_call_ref": _ref_payload(provider.call.ref),
            "invocation_ref": _ref_payload(current.invocation_ref),
            "submission_state": submission_state,
        }
        tx = self.core.begin(idempotency_key=idempotency_key)
        tx.validate_before_commit(
            lambda _view: self._validate_current_loop(current))
        self._event(
            tx, event_type="provider_attempt_owner_interrupted/v1",
            aggregate_id=str(provider.attempt_id),
            aggregate_type="provider_attempt", payload=payload,
            producer_invocation_id=current.invocation_ref.entity_id)
        self._event(
            tx, event_type="llm_call_owner_interrupted/v1",
            aggregate_id=str(provider.call.call_id),
            aggregate_type="llm_call", payload=payload,
            producer_invocation_id=current.invocation_ref.entity_id)
        self._event(
            tx, event_type="llm_invocation_owner_interrupted/v1",
            aggregate_id=str(attempt.attempt_ref.entity_id),
            aggregate_type="llm_invocation_attempt", payload=payload,
            producer_invocation_id=current.invocation_ref.entity_id)
        tx.commit()

    @staticmethod
    def next_attempt_allowed(
            disposition: str, *, attempt_ordinal: int,
            maximum_attempts: int,
    ) -> bool:
        if (disposition not in _LLM_FAILURE_DISPOSITIONS
                or isinstance(attempt_ordinal, bool)
                or not isinstance(attempt_ordinal, int)
                or attempt_ordinal < 0
                or isinstance(maximum_attempts, bool)
                or not isinstance(maximum_attempts, int)
                or not 1 <= maximum_attempts <= 3
                or attempt_ordinal >= maximum_attempts):
            raise AgentLoopMechanicalLifecycleError(
                "failure disposition has invalid attempt bounds")
        return (disposition in _RETRYABLE_LLM_FAILURE_DISPOSITIONS
                and attempt_ordinal + 1 < maximum_attempts)

    def record_invocation_failure(
            self, *, loop: AgentLoopSnapshot, attempt: LLMCallAttempt,
            disposition: str, maximum_attempts: int,
            idempotency_key: str,
            invocation_kinds: Sequence[str],
            allowed_states: Sequence[AgentLoopState],
            failure_code: str | None = None,
            submission_state: str | None = None,
            events: Sequence[
                tuple[str, str, str, Mapping[str, Any]]] = (),
    ) -> bool:
        """Validate and persist one failure, returning its retry disposition."""
        current = self.require_current_revision(
            loop, allowed_states=allowed_states)
        if (not isinstance(attempt, LLMCallAttempt)
                or disposition not in _LLM_FAILURE_DISPOSITIONS
                or not isinstance(idempotency_key, str)
                or not idempotency_key
                or (disposition in _LLM_FAILURES_REQUIRING_TRANSPORT_DETAIL
                    and (failure_code is None or submission_state is None))
                or ((failure_code is None) != (submission_state is None))
                or (failure_code is not None
                    and (not isinstance(failure_code, str)
                         or not failure_code
                         or failure_code != failure_code.strip()))
                or (submission_state is not None
                    and submission_state not in _LLM_SUBMISSION_STATES)
                or (disposition == "submission_unknown"
                    and submission_state != "submission_unknown")):
            raise AgentLoopMechanicalLifecycleError(
                "LLM failure disposition is incomplete")
        invocation = self.exact_object_document(
            attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1")
        attempt_document = self.exact_object_document(
            attempt.attempt_ref,
            expected_type="llm_invocation_attempt/v1")
        if (invocation.get("agent_loop_ref")
                != _ref_payload(self.loop_ref(current))
                or invocation.get("invocation_kind")
                not in tuple(invocation_kinds)
                or invocation.get("model_condition")
                != current.model_condition
                or attempt_document.get("llm_invocation_ref")
                != _ref_payload(attempt.invocation_ref)
                or attempt_document.get("attempt_ordinal")
                != attempt.attempt_ordinal
                or attempt.model_condition != current.model_condition):
            raise AgentLoopMechanicalLifecycleError(
                "failed attempt differs from its current Loop authority")
        allowed = self.next_attempt_allowed(
            disposition, attempt_ordinal=attempt.attempt_ordinal,
            maximum_attempts=maximum_attempts)
        self.commit_llm_failure(
            loop=current, attempt=attempt, disposition=disposition,
            next_attempt_allowed=allowed,
            idempotency_key=idempotency_key,
            failure_code=failure_code, submission_state=submission_state,
            events=events)
        return allowed

    def execution_block(
            self, execution: object, *, block_kind: str,
            operation_or_tool_identity: str, error_code: str,
            error_message: str | None, boundary: str,
            consecutive_count: int, exact_error_ref: VersionRef,
            retry_not_before_utc: str | None,
            validate_error_ref: Callable[[VersionRef], None],
            retain: Callable[[OperationExecutionBlockAuthority], None]
            | None = None,
    ) -> OperationExecutionBlockAuthority:
        """Create and optionally retain the exact process-local block view."""
        validate_error_ref(exact_error_ref)
        authority = OperationExecutionBlockAuthority(
            execution, block_kind, operation_or_tool_identity, error_code,
            error_message, boundary, consecutive_count, exact_error_ref,
            retry_not_before_utc)
        if retain is not None:
            retain(authority)
        return authority

    def history_messages(
            self, loop: AgentLoopSnapshot, *,
            read_response: Callable[[ResourceVersionRef], bytes],
            render_tool_result: Callable[
                [AgentActionRecord, VersionRef], Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], ...]:
        """Build ordered history from immutable turns and settled actions."""
        overlay = self.latest_context_overlay(loop)
        messages: list[Mapping[str, Any]] = (
            list(overlay.model_visible_messages) if overlay is not None else [])
        first_visible_sequence = (
            overlay.last_turn_sequence + 1 if overlay is not None else 0)
        for event in self.turn_events(loop):
            if int(event.payload["sequence"]) < first_visible_sequence:
                continue
            turn = self.hydrate_turn_event(
                loop, event, read_response=read_response)
            response = json.loads(read_response(turn.response_resource_ref))
            assistant: dict[str, Any] = {
                "role": "assistant", "content": response.get("text", "")}
            if turn.tool_calls:
                assistant["tool_calls"] = [{
                    "id": call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": call.raw_arguments,
                    },
                } for call in turn.tool_calls]
            messages.append(assistant)
            turn_ref = self.turn_ref_from_event(event)
            for call in turn.tool_calls:
                from .models import stable_action_id, stable_malformed_action_id
                action_id = (
                    stable_action_id(
                        loop.loop_id, turn.sequence,
                        call.action_identity_key)
                    if call.action_identity_kind == "tool_call_id"
                    else stable_malformed_action_id(
                        loop.loop_id, turn.sequence,
                        call.tool_call_ordinal))
                action_ref, action = self.latest_settled_action(
                    action_id=action_id, turn_ref=turn_ref,
                    ordinal=call.tool_call_ordinal)
                result = render_tool_result(action, action_ref)
                if not isinstance(result, Mapping):
                    raise AgentLoopMechanicalLifecycleError(
                        "tool history policy returned no result mapping")
                messages.append({
                    "role": "tool", "tool_call_id": call.tool_call_id,
                    "content": canonical_json(dict(result)).decode("utf-8"),
                })
        return tuple(messages)


__all__ = ["TurnRecordsMechanicsMixin"]


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


class TurnRecordsExecutionMixin:
    def record_agent_llm_turn_v1(self, loop, attempt, response_bytes, *, idempotency_key):
        self._current(loop)
        if loop.state != AgentLoopState.WAITING_FOR_LLM:
            raise StaleAuthorityHead("optional response requires exact waiting loop")
        invocation = self.kernel._exact_object(attempt.invocation_ref, expected_type="llm_invocation_spec/v1").metadata
        if (invocation["agent_loop_ref"] != _ref_payload(self.mechanical_lifecycle.loop_ref(loop))
                or invocation["turn_sequence"] != loop.next_turn_sequence
                or invocation["model_condition"] != loop.model_condition):
            raise ResourceIntegrityFault("optional response crossed its exact loop/model/sequence")
        record_key = idempotency_key
        replay_after = invocation.get("replay_after_compaction_ref")
        if isinstance(replay_after, Mapping):
            record_key += ":after-compaction:" + str(
                replay_after.get("version_id"))
        payload = canonicalize_llm_response_payload(response_bytes)
        if payload != response_bytes or len(payload) > attempt.max_response_bytes:
            raise ResourceIntegrityFault("optional response is not its canonical bounded envelope")
        provider = self.provider_attempt_for_agent_llm_v1(attempt)
        # Only the input port knows the actual raw transport return. Require its
        # Core raw-return evidence; never invent an HTTP status for subprocess
        # bytes or publish a canonical adapter product as a provider response.
        try:
            provider, raw_event, raw_response_ref, _raw_payload = (
                self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                    attempt.attempt_ref,
                    read_response=lambda ref: self.kernel._read_firing_registered(
                        self._context(loop), ref)))
        except Exception as exc:
            raise OptionalAgentCapabilityUnavailable(
                "normal input port has not committed the exact Core raw return") from exc
        context = self._context(loop)
        ref = ResourceVersionRef(new_id("resource"), new_id("resource_version"))
        observed = observe_registered_llm_response(payload, PublishedLLMResponse(ref, attempt.attempt_ref,
            attempt.invocation_ref, loop.model_condition, len(payload), "llm_response_envelope/v1"))
        length_interrupted = observed.finish_reason == "length"
        turn_ref = VersionRef("agent_turn/v1", new_id("agent_turn"), new_id("agent_turn_version"))
        def stage_response(tx):
            _append_direct_resource_version_publication(tx, ref=ref, payload=payload,
                metadata_factory=lambda size: _direct_resource_metadata(self.core, ref=ref, origin_kind="llm_response",
                    primary=attempt.attempt_ref, secondary=attempt.invocation_ref, task_ref=context.task_ref,
                    round_ref=context.task_round_ref, net_ref=context.net_instance_ref, producer_ref=context.invocation_ref,
                    lifetime_ref=context.operation_execution_lease_ref, operation_binding_ref=context.operation_binding_ref,
                    agent_loop_ref=self.mechanical_lifecycle.loop_ref(loop), payload_size=size, media_type="application/json",
                    content_schema_ref="runtime/llm_response_envelope/v1",
                    content_schema_authority_ref=_ref_payload(_registry_type_catalog_ref(self.core)),
                    summary="Optional canonical LLM envelope", descriptors={"content_role": "llm_response_envelope"},
                    extensions={"registry.llm_response/v1": {"llm_invocation_ref": _ref_payload(attempt.invocation_ref),
                        "llm_invocation_attempt_ref": _ref_payload(attempt.attempt_ref), "model_condition": loop.model_condition}},
                    input_resource_refs=(_resource_from_payload(invocation["request_resource_ref"]),), intended_boundary="not_applicable"),
                media_type="application/json", producer_ref=context.invocation_ref,
                producer_invocation_id=context.invocation_ref.entity_id, relation_key=record_key,
                direct_owners=(attempt.invocation_ref, attempt.attempt_ref),
                input_resources=(_resource_from_payload(invocation["request_resource_ref"]),))

        committed = self.mechanical_lifecycle.record_provider_turn(
            loop=loop, attempt=attempt,
            response_ref=ref, response_size=len(payload),
            turn_ref=turn_ref, tool_call_count=len(observed.tool_calls),
            idempotency_key=record_key,
            stage_response=stage_response,
            length_interrupted=length_interrupted,
            provider_completion={
                "provider_attempt_id": str(provider.attempt_id),
                "response_version_id": str(
                    raw_response_ref.resource_version_id),
                "response_observed_event_id": str(raw_event.event_id),
                "finish_reason": raw_event.payload.get("finish_reason"),
                "external_request_id": raw_event.payload.get(
                    "external_request_id"),
            })
        if length_interrupted:
            interruption = self.mechanical_lifecycle.hydrate_length_interruption(
                committed, sequence=loop.next_turn_sequence,
                read_response=lambda candidate: self.kernel._read_firing_registered(
                    context, candidate))
            if not isinstance(interruption, AgentLengthInterruptionRecord):
                raise ResourceIntegrityFault(
                    "optional length response lacks its interruption record")
            return committed, interruption
        return committed, self.hydrate_current_agent_turn_v1(committed)

    def hydrate_current_agent_turn_v1(self, loop):
        self._current(loop)
        return self.mechanical_lifecycle.hydrate_current_turn(
            loop, read_response=lambda ref: self.kernel._read_firing_registered(
                self._context(loop), ref))

    def record_llm_invocation_failure_v1(self, loop, attempt, disposition, *, idempotency_key, failure_code=None, submission_state=None):
        self._current(loop)
        provider = self.provider_attempt_for_agent_llm_v1(attempt)
        invocation = self.kernel._exact_object(
            attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1").metadata
        invocation_kind = invocation.get("invocation_kind")
        if invocation_kind == "delegated_subtask":
            allowed_states = (AgentLoopState.TURN_STORED,)
        elif invocation_kind == "normal_turn":
            allowed_states = (AgentLoopState.WAITING_FOR_LLM,)
        else:
            raise ResourceIntegrityFault(
                "generic invocation failure crossed an unsupported call kind")
        events = ()
        if (disposition == "protocol_rejected"
                and submission_state == "response_observed"):
            provider, raw_event, raw_response_ref, _raw_payload = (
                self.mechanical_lifecycle.raw_return_for_invocation_attempt(
                    attempt.attempt_ref,
                    read_response=lambda ref: self.kernel._read_firing_registered(
                        self._context(loop), ref)))
            events = ((
                "provider_attempt_completed/v1",
                str(provider.attempt_id), "provider_attempt", {
                    "provider_attempt_id": str(provider.attempt_id),
                    "response_version_id": str(
                        raw_response_ref.resource_version_id),
                    "response_observed_event_id": str(raw_event.event_id),
                    "finish_reason": raw_event.payload.get("finish_reason"),
                    "external_request_id": raw_event.payload.get(
                        "external_request_id")},
            ),)
        return self.mechanical_lifecycle.record_invocation_failure(
            loop=loop, attempt=attempt, disposition=disposition,
            maximum_attempts=1, idempotency_key=idempotency_key,
            invocation_kinds=(invocation_kind,),
            allowed_states=allowed_states,
            failure_code=failure_code, submission_state=submission_state,
            events=events)

    def record_llm_input_owner_interruption_v1(
            self, execution, attempt, submission_state, *, idempotency_key):
        """Close one input-port cancellation as control flow, not failure."""

        if not isinstance(attempt, LLMCallAttempt):
            raise TypeError(
                "LLM owner interruption requires one prepared attempt")
        invocation = self.mechanical_lifecycle.exact_object_document(
            attempt.invocation_ref,
            expected_type="llm_invocation_spec/v1")
        try:
            loop = self.mechanical_lifecycle.hydrate_loop(
                _version_from_payload(invocation["agent_loop_ref"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResourceIntegrityFault(
                "LLM owner interruption lacks its exact AgentLoop") from exc
        self._execution(execution, loop)
        invocation_kind = invocation.get("invocation_kind")
        if invocation_kind == "normal_turn":
            allowed_states = (AgentLoopState.WAITING_FOR_LLM,)
        elif invocation_kind == "context_compaction":
            allowed_states = (AgentLoopState.COMPACTING,)
        elif invocation_kind == "delegated_subtask":
            allowed_states = (AgentLoopState.TURN_STORED,)
        else:
            raise ResourceIntegrityFault(
                "LLM owner interruption crossed an unsupported call kind")
        self.mechanical_lifecycle.record_invocation_owner_interruption(
            loop=loop, attempt=attempt, submission_state=submission_state,
            idempotency_key=idempotency_key,
            invocation_kinds=(invocation_kind,),
            allowed_states=allowed_states)
