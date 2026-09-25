"""Terminal implementation for the InvocationLifecycle facade."""

from __future__ import annotations

import json
from typing import Any, Callable, Literal, Mapping, Sequence

from ...budgets import BudgetContractError, validate_budget_binding, validate_budget_scopes
from ..event_store import RegistryConflict, RegistryCorruptError, StaleWriterError, validate_registered_net_closure, verified_adoption_head, verified_checkpoint_head
from ..identities import TypedId
from ..models import EventEnvelope, PendingEvent, TypedRelation, VersionRef
from ..resources import ResourceVersionRef
from ..schema_catalog import canonical_json
from ..admission_publication import PreparedFiringAdmissionPublications
from ..invocations import (
    FiringAdmission,
    FiringClaim,
    InvocationAdmissionError,
    InvocationClosedError,
    InvocationContext,
    TerminalResultPackage,
    ToolExecutionContext,
    _AgentTurnAcceptanceClosure,
    _HISTORICAL_MECHANICAL_TERMINAL_READY,
    _HistoricalMechanicalTerminalReadyRequest,
    _ref_from_payload,
    _ref_payload,
    _scope_firing_idempotency_key,
    _stable_id,
    terminal_descendant_event_ids,
)

def _terminal_descendant_event_ids(
        lifecycle, context: InvocationContext,
        provider_submission_unknown_ref: VersionRef | None = None,
) -> tuple[str, ...]:
    # Resolve this through the compatibility module at call time.  Existing
    # consumers replace this seam when exercising the evidence classifier.
    from .. import invocations as invocation_facade

    return invocation_facade.terminal_descendant_event_ids(
        context,
        provider_submission_unknown_ref,
        event_store=lifecycle.service.event_store,
        require_ref=lifecycle._require_ref,
        relation_endpoint=lifecycle._relation_endpoint,
        resolve_agent_turn_acceptance=lifecycle._resolve_agent_turn_acceptance,
        terminal_call_events=lifecycle._TERMINAL_CALL_EVENTS,
        terminal_attempt_events=lifecycle._TERMINAL_ATTEMPT_EVENTS,
        ref_payload=_ref_payload,
        ref_from_payload=_ref_from_payload,
        admission_error=InvocationAdmissionError,
    )

def _relation_endpoint(row: Mapping[str, Any], field: str) -> VersionRef:
    value = json.loads(str(row[field]))
    return VersionRef(
        str(value["entity_type"]),
        TypedId.parse(str(value["entity_id"])),
        TypedId.parse(str(value["version_id"])))

def _exact_relation_rows(
        lifecycle, relation_type: str, *, source: VersionRef | None = None,
        target: VersionRef | None = None) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    if source is not None:
        candidates = lifecycle.service.event_store.relation_rows_for_version(
            source.version_id, relation_type=relation_type,
            endpoint="source")
    elif target is not None:
        candidates = lifecycle.service.event_store.relation_rows_for_version(
            target.version_id, relation_type=relation_type,
            endpoint="target")
    else:
        candidates = tuple(
            row for row in lifecycle.service.event_store.relation_rows()
            if row["relation_type"] == relation_type)
    for row in candidates:
        if (source is not None
                and lifecycle._relation_endpoint(row, "source_json") != source):
            continue
        if (target is not None
                and lifecycle._relation_endpoint(row, "target_json") != target):
            continue
        rows.append(row)
    return tuple(rows)

def _resolve_agent_turn_acceptance(
        lifecycle, context: InvocationContext,
        candidate_ref: VersionRef) -> _AgentTurnAcceptanceClosure:
    """Resolve one exact persisted v2 AgentTurn acceptance closure."""
    candidate = lifecycle._require_ref(
        candidate_ref, "resource_version/v1", "resource_version")
    origin = candidate.get("origin")
    try:
        attempt_ref = _ref_from_payload(origin["primary_ref"])
        call_ref = _ref_from_payload(origin["secondary_ref"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvocationAdmissionError(
            "agent turn response origin lacks exact attempt/call refs") from exc
    expected_invocation = _ref_payload(context.invocation_ref)
    expected_binding = _ref_payload(context.operation_binding_ref)
    expected_response = {
        "resource_id": str(candidate_ref.entity_id),
        "resource_version_id": str(candidate_ref.version_id),
    }
    expected_origin = {
        "kind": "provider_raw_response",
        "primary_ref": _ref_payload(attempt_ref),
        "secondary_ref": _ref_payload(call_ref),
    }
    if (call_ref.entity_type != "llm_call_spec/v2"
            or attempt_ref.entity_type != "provider_attempt_spec/v1"
            or candidate.get("origin_kind") != "provider_raw_response"
            or origin != expected_origin):
        raise InvocationAdmissionError(
            "agent turn response origin is not the exact v2 call/attempt")
    attempt = lifecycle._require_ref(
        attempt_ref, "provider_attempt_spec/v1",
        "provider_attempt_version")
    call = lifecycle._require_ref(
        call_ref, "llm_call_spec/v2", "llm_call_version")
    candidate_row = lifecycle.service.event_store.object_row(
        candidate_ref.version_id)
    lifecycle.service.event_store.require_canonical_producer(
        candidate_ref, context.invocation_ref)
    if (candidate_row is None
            or candidate_row["producer_invocation_id"]
            != str(context.invocation_ref.entity_id)
            or candidate.get("producer_ref") != expected_invocation
            or isinstance(candidate.get("size"), bool)
            or not isinstance(candidate.get("size"), int)
            or call.get("llm_call_id") != str(call_ref.entity_id)
            or call.get("llm_call_version_id")
            != str(call_ref.version_id)
            or call.get("llm_call_ref") != _ref_payload(call_ref)
            or call.get("invocation_ref") != expected_invocation
            or call.get("operation_binding_ref") != expected_binding
            or attempt.get("provider_attempt_id")
            != str(attempt_ref.entity_id)
            or attempt.get("provider_attempt_version_id")
            != str(attempt_ref.version_id)
            or attempt.get("provider_attempt_ref")
            != _ref_payload(attempt_ref)
            or attempt.get("llm_call_ref") != _ref_payload(call_ref)
            or attempt.get("llm_call_id") != str(call_ref.entity_id)
            or attempt.get("llm_call_version_id")
            != str(call_ref.version_id)
            or attempt.get("invocation_ref") != expected_invocation
            or attempt.get("operation_binding_ref") != expected_binding
            ):
        raise InvocationAdmissionError(
            "agent turn response is outside the current invocation closure")

    observations = lifecycle.service.event_store.list_events_by_aggregate(
        str(attempt_ref.entity_id),
        event_types=("provider_attempt_submission_observed/v1",))
    if len(observations) != 1:
        raise InvocationAdmissionError(
            "agent turn response lacks one exact provider observation")
    observation = observations[0]
    if (observation.producer_invocation_id
            != context.invocation_ref.entity_id
            or observation.payload.get("provider_attempt_id")
            != str(attempt_ref.entity_id)
            or observation.payload.get("provider_attempt_version_id")
            != str(attempt_ref.version_id)
            or observation.payload.get("provider_attempt_ref")
            != _ref_payload(attempt_ref)
            or observation.payload.get("llm_call_ref")
            != _ref_payload(call_ref)
            or observation.payload.get("invocation_ref")
            != expected_invocation
            or observation.payload.get("response_resource_ref")
            != expected_response
            or observation.payload.get("response_size")
            != candidate.get("size")):
        raise InvocationAdmissionError(
            "agent turn provider observation breaks exact response lineage")

    completions = lifecycle.service.event_store.list_events_by_aggregate(
        str(attempt_ref.entity_id),
        event_types=("provider_attempt_completed/v1",))
    if len(completions) != 1:
        raise InvocationAdmissionError(
            "agent turn response lacks one exact provider completion")
    completion = completions[0]
    if (completion.producer_invocation_id
            != context.invocation_ref.entity_id
            or completion.payload.get("provider_attempt_id")
            != str(attempt_ref.entity_id)
            or completion.payload.get("response_version_id")
            != str(candidate_ref.version_id)
            or completion.payload.get("response_observed_event_id")
            != str(observation.event_id)):
        raise InvocationAdmissionError(
            "agent turn provider completion does not consume its observation")

    related_turns = lifecycle.service.event_store.agent_turn_events_for_closure(
        call_version_id=call_ref.version_id,
        attempt_version_id=attempt_ref.version_id,
        response_version_id=candidate_ref.version_id,
        loop_id=call.get("agent_loop_ref", {}).get("logical_id", ""),
        sequence=call.get("turn_sequence"),
    )
    if len(related_turns) != 1:
        raise InvocationAdmissionError(
            "agent turn response lacks one unique recorded turn")
    turn_event = related_turns[0]
    sequence = call.get("turn_sequence")
    if (turn_event.producer_invocation_id
            != context.invocation_ref.entity_id
            or turn_event.payload.get("llm_call_version_id")
            != str(call_ref.version_id)
            or turn_event.payload.get("provider_attempt_version_id")
            != str(attempt_ref.version_id)
            or turn_event.payload.get("response_version_id")
            != str(candidate_ref.version_id)
            or turn_event.payload.get("response_size")
            != candidate.get("size")
            or turn_event.payload.get("sequence") != sequence):
        raise InvocationAdmissionError(
            "recorded agent turn differs from the exact provider response")
    try:
        turn_ref = VersionRef(
            "agent_turn/v1",
            TypedId.parse(
                str(turn_event.payload["agent_turn_id"]),
                expected="agent_turn"),
            TypedId.parse(
                str(turn_event.payload["agent_turn_version_id"]),
                expected="agent_turn_version"))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvocationAdmissionError(
            "recorded agent turn has an invalid immutable ref") from exc
    try:
        loop_id = TypedId.parse(
            str(turn_event.payload["agent_loop_id"]),
            expected="agent_loop")
    except (KeyError, TypeError, ValueError) as exc:
        raise InvocationAdmissionError(
            "recorded agent turn lacks an exact loop id") from exc
    turn_rows_by_id = list(
        lifecycle.service.event_store.object_rows_by_logical(
            turn_ref.entity_id))
    turn_version_row = lifecycle.service.event_store.object_row(
        turn_ref.version_id)
    if (turn_version_row is not None
            and all(row["version_id"] != turn_version_row["version_id"]
                    for row in turn_rows_by_id)):
        turn_rows_by_id.append(turn_version_row)
    turn_rows = tuple(turn_rows_by_id)
    if turn_rows and (len(turn_rows) != 1
                      or turn_rows[0]["object_type"] != "agent_turn/v1"
                      or turn_rows[0]["logical_id"]
                      != str(turn_ref.entity_id)
                      or turn_rows[0]["version_id"]
                      != str(turn_ref.version_id)):
        raise InvocationAdmissionError(
            "agent turn has a mixed or partial historical object")
    turn_row = turn_rows[0] if turn_rows else None
    turn = (lifecycle._require_ref(
        turn_ref, "agent_turn/v1", "agent_turn_version")
        if turn_row is not None else None)
    loop_rows = lifecycle.service.event_store.object_rows_by_logical(
        loop_id, object_type="agent_loop/v1")
    current_loop_rows = tuple(
        row for row in loop_rows
        if json.loads(str(row["metadata_json"])).get("revision")
        == turn_event.payload.get("revision"))
    if len(current_loop_rows) != 1:
        raise InvocationAdmissionError(
            "recorded agent turn lacks one exact loop revision")
    loop_row = current_loop_rows[0]
    loop_ref = VersionRef(
        "agent_loop/v1", loop_id,
        TypedId.parse(
            str(loop_row["version_id"]), expected="agent_loop_version"))
    loop = lifecycle._require_ref(
        loop_ref, "agent_loop/v1", "agent_loop_version")
    loop_positions = tuple(
        index for index, row in enumerate(loop_rows)
        if row["version_id"] == str(loop_ref.version_id))
    prior_loop = None
    prior_loop_ref = None
    if len(loop_positions) == 1 and loop_positions[0] > 0:
        prior_row = loop_rows[loop_positions[0] - 1]
        prior_loop = json.loads(prior_row["metadata_json"])
        prior_loop_ref = {
            "entity_type": "agent_loop/v1",
            "logical_id": str(loop_ref.entity_id),
            "version_id": str(prior_row["version_id"]),
        }
    if (prior_loop is None
            or prior_loop_ref != call.get("agent_loop_ref")
            or prior_loop.get("revision") != loop.get("revision") - 1
            or prior_loop.get("next_turn_sequence") != sequence
            or prior_loop.get("llm_turns_used") != sequence
            or loop_row["transaction_id"]
            != str(turn_event.transaction_id)
            or loop_row["transaction_id"]
            != str(completion.transaction_id)
            or loop_row["producer_invocation_id"]
            != str(context.invocation_ref.entity_id)
            or turn_event.payload.get("agent_loop_id")
            != str(loop_ref.entity_id)
            or turn_event.aggregate_id != str(loop_ref.entity_id)
            or loop.get("agent_loop_ref") != _ref_payload(loop_ref)
            or loop.get("invocation_ref") != expected_invocation
            or loop.get("operation_binding_ref") != expected_binding
            or loop.get("state") != "TURN_STORED"
            or loop.get("revision")
            != turn_event.payload.get("revision")
            or loop.get("next_turn_sequence") != sequence + 1
            or loop.get("llm_turns_used") != sequence + 1
            or call.get("agent_loop_ref", {}).get("logical_id")
            != str(loop_ref.entity_id)):
        raise InvocationAdmissionError(
            "agent turn event/loop are not one atomic successor")
    if turn is not None and (turn_row is None
            or turn_row["transaction_id"] != loop_row["transaction_id"]
            or turn_row["producer_invocation_id"]
            != str(context.invocation_ref.entity_id)
            or turn.get("agent_turn_ref") != _ref_payload(turn_ref)
            or turn.get("agent_turn_id") != str(turn_ref.entity_id)
            or turn.get("agent_turn_version_id")
            != str(turn_ref.version_id)
            or turn.get("agent_loop_ref") != _ref_payload(loop_ref)
            or turn.get("llm_call_ref") != _ref_payload(call_ref)
            or turn.get("provider_attempt_ref")
            != _ref_payload(attempt_ref)
            or turn.get("response_resource_ref") != expected_response
            or turn.get("response_size") != candidate.get("size")
            or turn.get("sequence") != sequence
            or turn.get("recorded_revision")
            != turn_event.payload.get("revision")):
        raise InvocationAdmissionError(
            "historical agent turn object differs from event closure")

    dispositions = tuple(lifecycle.service.event_store.list_events_by_aggregate(
        str(call_ref.entity_id),
        event_types=tuple(lifecycle._TERMINAL_CALL_EVENTS)))
    if turn is None:
        if dispositions:
            raise InvocationAdmissionError(
                "event-only agent turn cannot carry call disposition/adoption")
        legacy_adoption = None
    else:
        if (len(dispositions) != 1
                or dispositions[0].event_type
                != "llm_call_result_adopted/v1"
                or dispositions[0].payload.get("llm_call_id")
                != str(call_ref.entity_id)
                or dispositions[0].payload.get("llm_call_version_id")
                != str(call_ref.version_id)
                or dispositions[0].payload.get(
                    "provider_attempt_version_id")
                != str(attempt_ref.version_id)
                or dispositions[0].payload.get("response_version_id")
                != str(candidate_ref.version_id)
                or dispositions[0].producer_invocation_id
                != context.invocation_ref.entity_id):
            raise InvocationAdmissionError(
                "historical agent turn lacks one exact adoption disposition")
        legacy_adoption = dispositions[0]
    return _AgentTurnAcceptanceClosure(
        attempt_ref, call_ref, candidate_ref, observation, completion,
        turn_event, legacy_adoption)

def _validate_provider_candidate(
        lifecycle, context: InvocationContext,
        candidate_ref: VersionRef) -> tuple[VersionRef, VersionRef]:
    candidate = lifecycle._require_ref(
        candidate_ref, "resource_version/v1", "resource_version")
    origin = candidate.get("origin")
    if not isinstance(origin, Mapping):
        raise InvocationAdmissionError(
            "candidate lineage target lacks a typed provider origin")
    try:
        attempt_ref = _ref_from_payload(origin["primary_ref"])
        call_ref = _ref_from_payload(origin["secondary_ref"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvocationAdmissionError(
            "provider candidate origin lacks exact attempt/call refs") from exc
    expected_origin = lifecycle._LLM_CALL_RESPONSE_ORIGIN.get(call_ref.entity_type)
    if (expected_origin is None
            or candidate.get("origin_kind") != expected_origin
            or origin.get("kind") != expected_origin):
        raise InvocationAdmissionError(
            "provider candidate origin does not match its exact call type")
    attempt = lifecycle._require_ref(
        attempt_ref, "provider_attempt_spec/v1", "provider_attempt_version")
    call = lifecycle._require_ref(
        call_ref, call_ref.entity_type, "llm_call_version")
    candidate_row = lifecycle.service.event_store.object_row(candidate_ref.version_id)
    lifecycle.service.event_store.require_canonical_producer(
        candidate_ref, context.invocation_ref)
    if call_ref.entity_type == "llm_call_spec/v1":
        lifecycle.service.event_store.require_canonical_attempt_response(
            attempt_ref, candidate_ref)
    else:
        closure = lifecycle._resolve_agent_turn_acceptance(
            context, candidate_ref)
        if (closure.attempt_ref != attempt_ref
                or closure.call_ref != call_ref):
            raise InvocationAdmissionError(
                "agent turn closure differs from candidate origin")
    exact_context = {
        "invocation_id": str(context.invocation_ref.entity_id),
        "invocation_version_id": str(context.invocation_ref.version_id),
    }
    exact_refs = {
        "invocation_ref": _ref_payload(context.invocation_ref),
        "operation_binding_ref": _ref_payload(
            context.operation_binding_ref),
    }
    exact_call_context = (
        exact_context if call_ref.entity_type == "llm_call_spec/v1"
        else exact_refs)
    if (any(attempt.get(name) != value
            for name, value in exact_context.items())
            or any(attempt.get(name) != value
                   for name, value in exact_refs.items())
            or any(call.get(name) != value
                   for name, value in exact_call_context.items())
            or attempt.get("provider_attempt_id")
            != str(attempt_ref.entity_id)
            or attempt.get("provider_attempt_version_id")
            != str(attempt_ref.version_id)
            or attempt.get("provider_attempt_ref")
            != _ref_payload(attempt_ref)
            or attempt.get("llm_call_id") != str(call_ref.entity_id)
            or attempt.get("llm_call_version_id") != str(call_ref.version_id)
            or attempt.get("llm_call_ref") != _ref_payload(call_ref)
            or call.get("llm_call_id") != str(call_ref.entity_id)
            or call.get("llm_call_version_id") != str(call_ref.version_id)
            or call.get("llm_call_ref") != _ref_payload(call_ref)
            or candidate.get("producer_ref")
            != _ref_payload(context.invocation_ref)
            or candidate_row is None
            ):
        raise InvocationAdmissionError(
            "provider candidate lineage is not the exact invocation/attempt/call chain")
    return attempt_ref, call_ref

def _adopted_provider_candidates(
    lifecycle, context: InvocationContext) -> frozenset[VersionRef]:
    adopted: set[VersionRef] = set()
    for event in lifecycle.service.event_store.adopted_call_events_for_invocation(
            context.invocation_ref.entity_id):
        try:
            call_version = TypedId.parse(
                str(event.payload["llm_call_version_id"]),
                expected="llm_call_version")
            response_version = TypedId.parse(
                str(event.payload["response_version_id"]),
                expected="resource_version")
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "adopted provider candidate fact has invalid exact refs") from exc
        call_row = lifecycle.service.event_store.object_row(call_version)
        if (call_row is None
                or call_row["object_type"]
                not in lifecycle._LLM_CALL_RESPONSE_ORIGIN):
            raise InvocationAdmissionError(
                "adopted provider candidate has no exact call version")
        try:
            call_ref = VersionRef(
                str(call_row["object_type"]),
                TypedId.parse(
                    str(call_row["logical_id"]), expected="llm_call"),
                call_version)
            call = lifecycle._require_ref(
                call_ref, call_ref.entity_type, "llm_call_version")
        except (TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "adopted provider candidate has an invalid call object") from exc
        exact_invocation = _ref_payload(context.invocation_ref)
        call_belongs_to_invocation = (
            (call.get("invocation_id")
             == str(context.invocation_ref.entity_id)
             and call.get("invocation_version_id")
             == str(context.invocation_ref.version_id))
            if call_ref.entity_type == "llm_call_spec/v1"
            else call.get("invocation_ref") == exact_invocation)
        if not call_belongs_to_invocation:
            continue
        response_row = lifecycle.service.event_store.object_row(response_version)
        if (response_row is None
                or response_row["object_type"] != "resource_version/v1"):
            raise InvocationAdmissionError(
                "adopted provider candidate has no exact response resource")
        candidate_ref = VersionRef(
            "resource_version/v1",
            TypedId.parse(str(response_row["logical_id"]), expected="resource"),
            response_version)
        attempt_ref, origin_call_ref = lifecycle._validate_provider_candidate(
            context, candidate_ref)
        try:
            event_call_id = TypedId.parse(
                str(event.payload["llm_call_id"]), expected="llm_call")
            event_attempt_version = TypedId.parse(
                str(event.payload["provider_attempt_version_id"]),
                expected="provider_attempt_version")
            selection_ref = _ref_from_payload(
                event.payload["selection_authority_ref"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                "adopted provider candidate fact has invalid lineage refs") from exc
        selection = lifecycle._require_ref(
            selection_ref, "output_binding/v1", "output_binding_version")
        binding = lifecycle._require_ref(
            context.operation_binding_ref, "operation_binding/v1",
            "operation_binding_version")
        if (event_call_id != call_ref.entity_id
                or event.aggregate_id != str(call_ref.entity_id)
                or event.producer_invocation_id
                != context.invocation_ref.entity_id
                or event_attempt_version != attempt_ref.version_id
                or origin_call_ref != call_ref
                or call.get("llm_call_ref") != _ref_payload(call_ref)
                or call.get("operation_binding_ref")
                != _ref_payload(context.operation_binding_ref)
                or _ref_payload(selection_ref)
                not in binding.get("output_binding_refs", [])
                or selection.get("node_ref")
                != (_ref_payload(context.own_node_ref)
                    if context.own_node_ref else None)
                or selection.get("net_ref")
                != _ref_payload(context.net_instance_ref)):
            raise InvocationAdmissionError(
                "adopted provider candidate fact breaks exact call/attempt/selection lineage")
        adopted.add(candidate_ref)
    return frozenset(adopted)

def _validate_terminal_output_refs(
        lifecycle, context: InvocationContext,
        values: Sequence[VersionRef], *,
        enforce_normal_cardinality: bool,
        recovering_committed_agent_actions: bool = False,
) -> tuple[VersionRef, ...]:
    refs = tuple(values)
    if any(not isinstance(ref, VersionRef) for ref in refs):
        raise TypeError(
            "terminal outputs must be exact registered VersionRef values")
    if len({str(ref.version_id) for ref in refs}) != len(refs):
        raise InvocationAdmissionError(
            "terminal result repeats an exact output resource ref")
    if not refs and not enforce_normal_cardinality:
        return refs
    adopted_candidates = (
        frozenset() if recovering_committed_agent_actions
        else lifecycle._adopted_provider_candidates(context))
    binding = lifecycle._require_ref(
        context.operation_binding_ref, "operation_binding/v1",
        "operation_binding_version")
    declared_outputs = {
        _ref_from_payload(value)
        for value in binding.get("output_binding_refs", [])
        if isinstance(value, Mapping)
    }
    output_counts = {ref: 0 for ref in declared_outputs}
    for ref in refs:
        resource = lifecycle._require_ref(
            ref, "resource_version/v1", "resource_version")
        row = lifecycle.service.event_store.object_row(ref.version_id)
        if (row is None
                or row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)
                or resource.get("producer_ref")
                != _ref_payload(context.invocation_ref)
                or resource.get("task_ref") != _ref_payload(context.task_ref)
                or resource.get("round_ref")
                != _ref_payload(context.task_round_ref)
                or resource.get("net_ref")
                != _ref_payload(context.net_instance_ref)):
            raise InvocationAdmissionError(
                "terminal output is not the invocation's exact registered resource")
        lifecycle.service.event_store.require_canonical_producer(
            ref, context.invocation_ref)
        origin = resource.get("origin")
        if not isinstance(origin, Mapping):
            raise InvocationAdmissionError(
                "terminal output lacks a typed publication origin")
        if (resource.get("origin_kind") != "petri_output"
                or origin.get("kind") != "petri_output"):
            raise InvocationAdmissionError(
                "on-net terminal output must have PetriOutputOrigin")
        output_binding_ref = _ref_from_payload(origin.get("primary_ref", {}))
        activation_ref = _ref_from_payload(origin.get("secondary_ref", {}))
        if (output_binding_ref not in declared_outputs
                or activation_ref != context.activation_ref):
            raise InvocationAdmissionError(
                "terminal output does not use this invocation's exact output binding")
        output_counts[output_binding_ref] += 1
        output_binding = lifecycle._require_ref(
            output_binding_ref, "output_binding/v1",
            "output_binding_version")
        expected_binding = {
            "task_round_ref": _ref_payload(context.task_round_ref),
            "net_ref": _ref_payload(context.net_instance_ref),
            "node_ref": (_ref_payload(context.own_node_ref)
                         if context.own_node_ref else None),
        }
        if any(output_binding.get(name) != value
               for name, value in expected_binding.items()):
            raise InvocationAdmissionError(
                "terminal output binding belongs to another round/net/node")
        selected_schema_authority = output_binding.get(
            "content_schema_ref")
        selected_schema_id = output_binding.get(
            "content_schema_id")
        if (not isinstance(selected_schema_authority, Mapping)
                or not isinstance(selected_schema_id, str)
                or resource.get("content_schema_ref")
                != selected_schema_id
                or resource.get("content_schema_authority_ref")
                != selected_schema_authority):
            raise InvocationAdmissionError(
                "terminal output content schema differs from adopted output binding")
        place_ref = lifecycle._metadata_ref(output_binding, "place_ref")
        lifecycle._require_ref(
            place_ref, place_ref.entity_type, place_ref.version_id.kind)

        if recovering_committed_agent_actions:
            continue

        derived_rows = lifecycle._exact_relation_rows(
            "derived_from", source=ref)
        derived_candidates: set[VersionRef] = set()
        for relation in derived_rows:
            target = lifecycle._relation_endpoint(relation, "target_json")
            target_metadata = lifecycle._require_ref(
                target, target.entity_type, target.version_id.kind)
            if target_metadata.get("origin_kind") not in {
                    "provider_response", "provider_raw_response"}:
                continue
            if relation["transaction_id"] != row["transaction_id"]:
                raise InvocationAdmissionError(
                    "provider candidate derivation was not published atomically")
            lifecycle._validate_provider_candidate(context, target)
            derived_candidates.add(target)
        unadopted_candidates = derived_candidates - adopted_candidates
        unaccepted_candidates = {
            candidate for candidate in unadopted_candidates
            if _ref_from_payload(lifecycle._require_ref(
                candidate, "resource_version/v1", "resource_version")
                ["origin"]["secondary_ref"]).entity_type
            != "llm_call_spec/v2"
        }
        if unaccepted_candidates:
            raise InvocationAdmissionError(
                "terminal output derives from a provider candidate not adopted by this invocation")
        if adopted_candidates and not (derived_candidates & adopted_candidates):
            raise InvocationAdmissionError(
                "terminal output is missing exact derived_from(adopted candidate)")
    if context.origin == "petri_operation" and enforce_normal_cardinality:
        if not refs:
            raise InvocationAdmissionError(
                "completed registered operation requires at least one document output")
        for output_binding_ref, count in output_counts.items():
            output_binding = lifecycle._require_ref(
                output_binding_ref, "output_binding/v1",
                "output_binding_version")
            cardinality = output_binding.get("normal_output_cardinality")
            if (not isinstance(cardinality, Mapping)
                    or (count and (
                        count < int(cardinality.get("minimum", -1))
                        or count > int(cardinality.get("maximum", -1))))):
                raise InvocationAdmissionError(
                    "terminal output count differs from adopted binding cardinality")
    return refs

def mark_operation_terminal_ready(
        lifecycle, context: InvocationContext, package: TerminalResultPackage, *,
        idempotency_key: str,
        recovering_committed_agent_actions: bool = False,
) -> VersionRef:
    return lifecycle._mark_operation_terminal_ready(
        context, package,
        idempotency_key=idempotency_key,
        recovering_committed_agent_actions=(
            recovering_committed_agent_actions),
        historical_request=None,
    )

def _mark_historical_mechanical_receipt_terminal_ready(
        lifecycle, context: InvocationContext, package: TerminalResultPackage, *,
        idempotency_key: str,
) -> VersionRef:
    return lifecycle._mark_operation_terminal_ready(
        context, package,
        idempotency_key=idempotency_key,
        recovering_committed_agent_actions=False,
        historical_request=_HISTORICAL_MECHANICAL_TERMINAL_READY,
    )

def _mark_operation_terminal_ready(
        lifecycle, context: InvocationContext, package: TerminalResultPackage, *,
        idempotency_key: str,
        recovering_committed_agent_actions: bool,
        historical_request: _HistoricalMechanicalTerminalReadyRequest | None,
) -> VersionRef:
    recovering_historical_mechanical_receipt = (
        historical_request is _HISTORICAL_MECHANICAL_TERMINAL_READY)
    if (historical_request is not None
            and not recovering_historical_mechanical_receipt):
        raise TypeError("unknown terminal-ready recovery authority")
    if (recovering_committed_agent_actions
            and recovering_historical_mechanical_receipt):
        raise TypeError(
            "terminal-ready recovery modes are mutually exclusive")
    if package.business_outcome != "completed":
        raise InvocationAdmissionError(
            "current operations settle only completed invocation results")
    existing = lifecycle._existing_command_events(idempotency_key)
    if existing:
        ready = [event for event in existing
                 if event.event_type == "operation_terminal_ready/v1"]
        if len(ready) != 1:
            raise InvocationAdmissionError(
                "idempotency key belongs to another command")
        if (_ref_from_payload(ready[0].payload["invocation_ref"])
                != context.invocation_ref):
            raise InvocationAdmissionError(
                "terminal-ready replay context differs from committed command")
        return _ref_from_payload(ready[0].payload["operation_result_ref"])
    historical_lease_writer_epoch: int | None = None
    if recovering_committed_agent_actions:
        lifecycle._revalidate_committed_agent_terminal(context, package)
    elif recovering_historical_mechanical_receipt:
        historical_lease_writer_epoch = (
            lifecycle._revalidate_historical_mechanical_receipt_terminal(
            context, package)
        )
    else:
        lifecycle.revalidate_io(context, boundary="terminal-ready")
    lease_starts = list(lifecycle.service.event_store.list_events_by_aggregate(
        str(context.operation_execution_lease_ref.entity_id),
        event_types=("operation_execution_started/v1",)))
    if (len(lease_starts) != 1
            or lease_starts[0].event_type
            != "operation_execution_started/v1"):
        raise InvocationAdmissionError(
            "terminal-ready requires one current operation execution start")
    if context.own_node_ref is not None:
        lifecycle._require_registered_operation_closure(
            net_ref=context.net_instance_ref, plan_ref=context.plan_ref,
            team_design_root_ref=context.team_design_root_ref,
            node_ref=context.own_node_ref,
            operation_binding_ref=context.operation_binding_ref)
    output_resource_refs = lifecycle._validate_terminal_output_refs(
        context, package.output_resource_refs,
        enforce_normal_cardinality=(package.business_outcome == "completed"),
        recovering_committed_agent_actions=(
            recovering_committed_agent_actions))
    terminal_facts = lifecycle._terminal_descendant_event_ids(context, None)
    for label, ref in (
            ("observed read set", package.observed_read_set_ref),
            ("provenance set", package.provenance_set_ref),
            *(("provider attempt evidence", ref)
              for ref in package.provider_attempt_evidence_refs)):
        if ref is None:
            continue
        try:
            lifecycle._require_ref(ref, ref.entity_type, ref.version_id.kind)
        except (InvocationAdmissionError, TypeError, ValueError) as exc:
            raise InvocationAdmissionError(
                f"terminal {label} strong reference is not registered") from exc
    result_id = _stable_id("operation_result", idempotency_key)
    result_version = _stable_id("operation_result_version", idempotency_key)
    result_ref = VersionRef("operation_result/v1", result_id, result_version)
    metadata = {
        "operation_result_ref": _ref_payload(result_ref),
        "invocation_ref": _ref_payload(context.invocation_ref),
        "transition_firing_ref": _ref_payload(
            context.own_transition_firing_ref),
        "business_outcome": package.business_outcome,
        "output_resource_refs": [
            _ref_payload(ref) for ref in output_resource_refs],
        "provider_attempt_evidence_refs": [
            _ref_payload(ref)
            for ref in package.provider_attempt_evidence_refs],
        "workspace_access_set_ref": (
            _ref_payload(package.observed_read_set_ref)
            if package.observed_read_set_ref else None),
    }
    lifecycle.service.catalog.validate_instance(
        "operation_result/v1", category="object", instance=metadata)
    tx = lifecycle.service.begin(
        idempotency_key=idempotency_key,
        task_round_id=context.task_round_ref.entity_id,
        net_instance_id=context.net_instance_ref.entity_id)
    tx.prewrite(
        object_type="operation_result/v1", logical_id=result_id,
        version_id=result_version, payload=canonical_json(metadata), metadata=metadata,
        media_type="application/json", schema_ref="registry_v1/operation_result/v1",
        producer_invocation_id=context.invocation_ref.entity_id)
    tx.relate(TypedRelation(
        _stable_id("relation", f"{idempotency_key}:terminal-result"),
        "terminal_result_of_invocation", result_ref,
        context.invocation_ref),
        producer_invocation_id=context.invocation_ref.entity_id)
    for index, (label, ref) in enumerate((
            ("observed_read_set", package.observed_read_set_ref),
            ("provenance_set", package.provenance_set_ref),
            *(("provider_attempt_evidence", ref)
              for ref in package.provider_attempt_evidence_refs))):
        if ref is not None:
            tx.relate(TypedRelation(
                _stable_id(
                    "relation", f"{idempotency_key}:terminal-strong:{index}"),
                "derived_from", result_ref, ref, metadata={"role": label}),
                producer_invocation_id=context.invocation_ref.entity_id)
    ready_event: PendingEvent = PendingEvent(
        event_type="operation_terminal_ready/v1", criticality="authoritative",
        stream_id=f"invocation:{context.invocation_ref.entity_id}",
        aggregate_id=str(context.invocation_ref.entity_id),
        aggregate_type="invocation", idempotency_key=idempotency_key,
        command_id=idempotency_key,
        payload={
            "invocation_ref": _ref_payload(context.invocation_ref),
            "operation_execution_lease_ref": _ref_payload(
                context.operation_execution_lease_ref),
            "operation_result_ref": _ref_payload(result_ref),
            "business_outcome": package.business_outcome,
            "output_resource_refs": [
                _ref_payload(ref) for ref in output_resource_refs],
            "provider_attempt_evidence_refs": [
                _ref_payload(ref)
                for ref in package.provider_attempt_evidence_refs],
            "workspace_access_set_ref": (
                _ref_payload(package.observed_read_set_ref)
                if package.observed_read_set_ref else None),
            "sealed_terminal_event_ids": list(terminal_facts),
        }, payload_schema_ref="registry_v1/operation_terminal_ready/v1",
        task_control=True, producer_principal=str(context.principal_ref.entity_id),
        producer_invocation_id=context.invocation_ref.entity_id)
    if recovering_historical_mechanical_receipt:
        if historical_lease_writer_epoch is None:
            raise RuntimeError(
                "historical terminal-ready revalidation produced no lease epoch")
        assert context.own_transition_firing_ref is not None
        ready_event = (
            lifecycle.service.event_store
            ._authorize_historical_mechanical_terminal_ready_event(
                ready_event,
                task_id=tx.task_id,
                branch_id=tx.branch_id,
                task_round_id=context.task_round_ref.entity_id,
                net_instance_id=context.net_instance_ref.entity_id,
                transaction_id=tx.transaction_id,
                writer_epoch=tx.writer_epoch,
                lease_writer_epoch=historical_lease_writer_epoch,
                task_ref=context.task_ref,
                task_round_ref=context.task_round_ref,
                net_instance_ref=context.net_instance_ref,
                invocation_ref=context.invocation_ref,
                firing_ref=context.own_transition_firing_ref,
                lease_ref=context.operation_execution_lease_ref,
                operation_result_ref=result_ref,
            ))
    tx.append(ready_event)
    tx.commit()
    return result_ref

def settle_firing(lifecycle, context: InvocationContext, operation_result_ref: VersionRef,
                  *, idempotency_key: str) -> EventEnvelope:
    canonical = lifecycle.hydrate_context(context.invocation_ref)
    if canonical != context:
        raise InvocationAdmissionError("settlement context is not canonical")
    existing = lifecycle._existing_command_events(idempotency_key)
    if existing:
        settled = [event for event in existing
                   if event.event_type == "transition_firing_settled/v1"]
        if (len(settled) != 1
                or _ref_from_payload(
                    settled[0].payload["invocation_ref"])
                != context.invocation_ref
                or _ref_from_payload(
                    settled[0].payload["operation_result_ref"])
                != operation_result_ref):
            raise InvocationAdmissionError(
                "settlement replay differs from committed command")
        return settled[0]
    raise InvocationAdmissionError(
        "direct firing settlement cannot author Petri output/control tokens; "
        "use RegistryFacade.settle_registered_firing_batch with a "
        "Petri/Executor-produced TypedMarkingSnapshot")
