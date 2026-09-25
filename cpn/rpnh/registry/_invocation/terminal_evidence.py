"""Terminal evidence classification for canonical invocation descendants.

The facade supplies the active store and lifecycle callbacks so this module
shares the caller's transaction context and does not depend back on
invocations.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from ..identities import TypedId
from ..models import EventEnvelope, VersionRef
from ..resources import ResourceVersionRef


def terminal_descendant_event_ids(
        context: Any,
        provider_submission_unknown_ref: VersionRef | None = None,
        *,
        event_store: Any,
        require_ref: Callable[[VersionRef, str, str], Mapping[str, Any]],
        relation_endpoint: Callable[[Mapping[str, Any], str], VersionRef],
        resolve_agent_turn_acceptance: Callable[[Any, VersionRef], Any],
        terminal_call_events: frozenset[str],
        terminal_attempt_events: frozenset[str],
        ref_payload: Callable[[VersionRef], dict[str, str]],
        ref_from_payload: Callable[[Mapping[str, Any]], VersionRef],
        admission_error: type[Exception],
) -> tuple[str, ...]:
    invocation_events = event_store.list_events_by_producer(
        context.invocation_ref.entity_id)
    call_rows_for_invocation = (
        event_store.object_rows_by_producer(
            context.invocation_ref.entity_id,
            object_types=(
                "llm_call_spec/v1", "llm_call_spec/v2",
                "llm_call_spec/v3")))
    attempt_rows_for_invocation = (
        event_store.object_rows_by_producer(
            context.invocation_ref.entity_id,
            object_types=("provider_attempt_spec/v1",)))
    call_refs: set[VersionRef] = set()
    call_entries_by_id: dict[str, list[
        tuple[Mapping[str, Any], Mapping[str, Any], VersionRef]]] = {}
    for row in call_rows_for_invocation:
        try:
            call_ref = VersionRef(
                str(row["object_type"]),
                TypedId.parse(str(row["logical_id"]), expected="llm_call"),
                TypedId.parse(
                    str(row["version_id"]), expected="llm_call_version"))
            metadata = json.loads(str(row["metadata_json"]))
        except (KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise admission_error(
                "producer-owned LLM call identity is malformed") from exc
        if (metadata.get("llm_call_ref") != ref_payload(call_ref)
                or metadata.get("llm_call_id")
                != str(call_ref.entity_id)
                or metadata.get("llm_call_version_id")
                != str(call_ref.version_id)):
            raise admission_error(
                "producer-owned LLM call metadata differs from its "
                "canonical object identity")
        call_refs.add(call_ref)
        call_entries_by_id.setdefault(
            str(call_ref.entity_id), []).append((row, metadata, call_ref))
    if any(len({entry[2].entity_type for entry in entries}) != 1
           for entries in call_entries_by_id.values()):
        raise admission_error(
            "canonical LLM call inventory has conflicting object types")
    v2_call_rows_for_invocation = tuple(
        row for row in call_rows_for_invocation
        if row["object_type"] == "llm_call_spec/v2")
    registered_host_attempts_by_call: dict[
        VersionRef, list[tuple[VersionRef, Mapping[str, Any]]]] = {}
    for row in event_store.object_rows_by_producer(
            context.invocation_ref.entity_id,
            object_types=("registered_host_llm_attempt/v1",)):
        try:
            metadata = json.loads(str(row["metadata_json"]))
            host_attempt_ref = VersionRef(
                "registered_host_llm_attempt/v1",
                TypedId.parse(
                    str(row["logical_id"]),
                    expected="registered_host_llm_attempt"),
                TypedId.parse(
                    str(row["version_id"]),
                    expected="registered_host_llm_attempt_version"))
            call_ref = ref_from_payload(metadata["llm_call_ref"])
            provider_ref = ref_from_payload(metadata["provider_attempt_ref"])
        except (KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise admission_error(
                "registered HOST LLM attempt identity is malformed") from exc
        if (metadata.get("registered_host_llm_attempt_ref")
                != ref_payload(host_attempt_ref)
                or metadata.get("registered_host_llm_attempt_id")
                != str(host_attempt_ref.entity_id)
                or metadata.get("registered_host_llm_attempt_version_id")
                != str(host_attempt_ref.version_id)
                or metadata.get("invocation_kind") != "registered_host"
                or metadata.get("invocation_ref")
                != ref_payload(context.invocation_ref)
                or metadata.get("operation_binding_ref")
                != ref_payload(context.operation_binding_ref)
                or call_ref.entity_type != "llm_call_spec/v3"
                or call_ref not in call_refs
                or provider_ref.entity_type != "provider_attempt_spec/v1"):
            raise admission_error(
                "registered HOST LLM attempt differs from its canonical "
                "firing authority")
        registered_host_attempts_by_call.setdefault(
            call_ref, []).append((host_attempt_ref, metadata))

    attempt_refs: set[VersionRef] = set()
    attempt_ids: list[str] = []
    for row in attempt_rows_for_invocation:
        try:
            metadata = json.loads(str(row["metadata_json"]))
            attempt_ref = VersionRef(
                "provider_attempt_spec/v1",
                TypedId.parse(
                    str(row["logical_id"]), expected="provider_attempt"),
                TypedId.parse(
                    str(row["version_id"]),
                    expected="provider_attempt_version"))
            call_ref = ref_from_payload(metadata["llm_call_ref"])
        except (KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise admission_error(
                "producer-owned provider attempt identity is malformed"
            ) from exc
        if (metadata.get("provider_attempt_ref")
                != ref_payload(attempt_ref)
                or metadata.get("provider_attempt_id")
                != str(attempt_ref.entity_id)
                or metadata.get("provider_attempt_version_id")
                != str(attempt_ref.version_id)):
            raise admission_error(
                "producer-owned provider attempt metadata differs from "
                "its canonical object identity")
        if (metadata.get("llm_call_id") != str(call_ref.entity_id)
                or metadata.get("llm_call_version_id")
                != str(call_ref.version_id)):
            raise admission_error(
                "provider attempt call metadata differs from its "
                "canonical call reference")
        if call_ref not in call_refs:
            raise admission_error(
                "provider attempt references a call outside the exact "
                "canonical invocation inventory")
        attempt_refs.add(attempt_ref)
        attempt_id = str(attempt_ref.entity_id)
        if attempt_id not in attempt_ids:
            attempt_ids.append(attempt_id)
    completed_compaction_rows: list[
        tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    failed_compaction_rows: list[
        tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    compaction_rows: list[
        tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for row in event_store.object_rows_by_producer(
            context.invocation_ref.entity_id,
            object_types=("agent_context_compaction/v2",)):
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise admission_error(
                "completed compaction owner metadata is malformed") from exc
        compaction_rows.append((row, metadata))
        if metadata.get("state") == "completed":
            completed_compaction_rows.append((row, metadata))
        elif metadata.get("state") == "failed":
            failed_compaction_rows.append((row, metadata))
    v3_compaction_rows: list[
        tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for row in event_store.object_rows_by_producer(
            context.invocation_ref.entity_id,
            object_types=("agent_context_compaction/v3",)):
        try:
            metadata = json.loads(str(row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise admission_error(
                "v3 compaction owner metadata is malformed") from exc
        v3_compaction_rows.append((row, metadata))

    def compaction_request_descriptors(
            call: Mapping[str, Any]) -> Mapping[str, Any]:
        raw_ref = call.get("request_resource_ref")
        try:
            if (not isinstance(raw_ref, Mapping)
                    or set(raw_ref)
                    != {"resource_id", "resource_version_id"}):
                raise TypeError("request ref")
            resource_id = TypedId.parse(
                str(raw_ref["resource_id"]), expected="resource")
            resource_version = TypedId.parse(
                str(raw_ref["resource_version_id"]),
                expected="resource_version")
            request_row = event_store.object_row(
                resource_version)
            if (request_row is None
                    or request_row["object_type"]
                    != "resource_version/v1"
                    or request_row["logical_id"] != str(resource_id)):
                raise TypeError("request object")
            request = json.loads(str(request_row["metadata_json"]))
            descriptors = request.get("descriptors")
            if not isinstance(descriptors, Mapping):
                raise TypeError("request descriptors")
            trim = descriptors.get("visible_trim")
            count = descriptors.get("visible_item_count")
            if (descriptors.get("content_role")
                    != "logical_context_compaction_recipe"
                    or not isinstance(
                        descriptors.get("compaction_id"), str)
                    or not descriptors["compaction_id"]
                    or isinstance(trim, bool)
                    or not isinstance(trim, int)
                    or trim < 0
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 1
                    or trim > count):
                raise TypeError("request descriptor values")
            return descriptors
        except (KeyError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            raise admission_error(
                "compaction call request metadata is malformed") from exc

    invocation_event_positions = {
        str(event.event_id): position
        for position, event in enumerate(invocation_events)
    }
    invocation_events_by_id = {
        str(event.event_id): event for event in invocation_events
    }

    def completed_compaction_retry_predecessor(
            call_ref: VersionRef, call: Mapping[str, Any],
            dispositions: tuple[EventEnvelope, ...],
    ) -> EventEnvelope:
        descriptors = compaction_request_descriptors(call)
        compaction_id = descriptors["compaction_id"]
        loop_payload = call.get("agent_loop_ref")
        candidates: list[tuple[
            Mapping[str, Any], Mapping[str, Any], VersionRef,
            Mapping[str, Any], Mapping[str, Any]]] = []
        for compaction_row, compaction in completed_compaction_rows:
            if (str(compaction_row["logical_id"]) != compaction_id
                    or compaction.get("agent_context_compaction_id")
                    != compaction_id):
                continue
            try:
                owner_ref = ref_from_payload(
                    compaction["provider_call_ref"])
                owner_rows = (
                    event_store.object_rows_by_logical(
                        owner_ref.entity_id,
                        object_type="llm_call_spec/v2"))
                if (len(owner_rows) != 1
                        or owner_rows[0]["version_id"]
                        != str(owner_ref.version_id)):
                    raise TypeError("owner call row")
                owner = json.loads(
                    str(owner_rows[0]["metadata_json"]))
                owner_descriptors = compaction_request_descriptors(owner)
            except (KeyError, TypeError, ValueError,
                    json.JSONDecodeError) as exc:
                raise admission_error(
                    "completed compaction retry owner is malformed") from exc
            if (owner.get("llm_call_ref") != ref_payload(owner_ref)
                    or owner.get("agent_loop_ref") != loop_payload
                    or owner_descriptors.get("compaction_id")
                    != compaction_id):
                continue
            candidates.append((
                compaction_row, compaction, owner_ref, owner_rows[0],
                owner_descriptors))
        if len(candidates) != 1:
            raise admission_error(
                "LLM call lacks one completed compaction owner")
        (_compaction_row, _compaction, owner_ref, owner_row,
         owner_descriptors) = candidates[0]
        owner_trim = owner_descriptors["visible_trim"]
        if (owner_descriptors["visible_item_count"]
                != descriptors["visible_item_count"]
                or descriptors["visible_trim"] >= owner_trim):
            raise admission_error(
                "compaction retry call does not precede its final owner")

        chain: dict[int, tuple[
            Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = {}
        for row in v2_call_rows_for_invocation:
            try:
                metadata = json.loads(str(row["metadata_json"]))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise admission_error(
                    "compaction retry call metadata is malformed") from exc
            if metadata.get("agent_loop_ref") != loop_payload:
                continue
            item_descriptors = compaction_request_descriptors(metadata)
            if item_descriptors.get("compaction_id") != compaction_id:
                continue
            trim = item_descriptors["visible_trim"]
            if (item_descriptors["visible_item_count"]
                    != descriptors["visible_item_count"]
                    or trim in chain):
                raise admission_error(
                    "compaction retry sequence is conflicting")
            chain[trim] = (row, metadata, item_descriptors)
        if (set(chain) != set(range(owner_trim + 1))
                or chain[owner_trim][0]["logical_id"]
                != str(owner_ref.entity_id)
                or chain[owner_trim][0]["version_id"]
                != str(owner_ref.version_id)
                or chain[owner_trim][0] is not owner_row
                and dict(chain[owner_trim][0]) != dict(owner_row)):
            raise admission_error(
                "compaction retry sequence is not one contiguous prefix")

        terminal_for_call: dict[str, EventEnvelope] = {}
        for trim in range(owner_trim):
            row, metadata, _item_descriptors = chain[trim]
            item_call_id = str(row["logical_id"])
            item_call_version = str(row["version_id"])
            item_call_ref = VersionRef(
                "llm_call_spec/v2",
                TypedId.parse(item_call_id, expected="llm_call"),
                TypedId.parse(
                    item_call_version, expected="llm_call_version"))
            terminals = tuple(
                event for event in
                event_store.list_events_by_aggregate(
                    item_call_id)
                if event.event_type in terminal_call_events)
            attempts = (
                event_store.provider_attempt_rows_for_call(
                    item_call_ref.entity_id,
                    item_call_ref.version_id))
            if len(attempts) != 1:
                raise admission_error(
                    "compaction retry call lacks one exact failed attempt")
            attempt_row = attempts[0]
            try:
                attempt = json.loads(str(attempt_row["metadata_json"]))
                attempt_ref = ref_from_payload(
                    attempt["provider_attempt_ref"])
            except (KeyError, TypeError, ValueError,
                    json.JSONDecodeError) as exc:
                raise admission_error(
                    "compaction retry attempt metadata is malformed") from exc
            failed_attempts = tuple(
                event for event in
                event_store.list_events_by_aggregate(
                    str(attempt_ref.entity_id))
                if event.event_type == "provider_attempt_failed/v1")
            if (len(terminals) != 1
                    or terminals[0].event_type != "llm_call_failed/v1"
                    or terminals[0].producer_invocation_id
                    != context.invocation_ref.entity_id
                    or terminals[0].payload.get("llm_call_id")
                    != item_call_id
                    or terminals[0].payload.get("llm_call_version_id")
                    != item_call_version
                    or terminals[0].payload.get("reason")
                    != "max_llm_attempts_exhausted"
                    or metadata.get("llm_call_ref")
                    != ref_payload(item_call_ref)
                    or attempt_row["producer_invocation_id"]
                    != str(context.invocation_ref.entity_id)
                    or attempt.get("llm_call_ref")
                    != ref_payload(item_call_ref)
                    or len(failed_attempts) != 1
                    or failed_attempts[0].producer_invocation_id
                    != context.invocation_ref.entity_id
                    or failed_attempts[0].payload.get(
                        "provider_attempt_id")
                    != str(attempt_ref.entity_id)
                    or failed_attempts[0].payload.get("failure_class")
                    != "http_non_success"
                    or failed_attempts[0].payload.get("retry_allowed")
                    is not True
                    or failed_attempts[0].transaction_id
                    != terminals[0].transaction_id):
                raise admission_error(
                    "compaction retry call has mixed terminal facts")

            published_id = str(row["published_event_id"])
            next_published_id = str(chain[trim + 1][0][
                "published_event_id"])
            publication = invocation_events_by_id.get(published_id)
            next_publication = invocation_events_by_id.get(
                next_published_id)
            terminal_position = invocation_event_positions.get(
                str(terminals[0].event_id))
            if (publication is None or next_publication is None
                    or terminal_position is None
                    or publication.event_type
                    != "object_version_published/v1"
                    or publication.payload.get("version_id")
                    != item_call_version
                    or next_publication.event_type
                    != "object_version_published/v1"
                    or next_publication.payload.get("version_id")
                    != str(chain[trim + 1][0]["version_id"])
                    or invocation_event_positions[published_id]
                    >= terminal_position
                    or terminal_position
                    >= invocation_event_positions[next_published_id]):
                raise admission_error(
                    "compaction retry ordering is not exact")
            terminal_for_call[item_call_id] = terminals[0]
        try:
            predecessor = terminal_for_call[str(call_ref.entity_id)]
        except KeyError as exc:
            raise admission_error(
                "compaction call is not an exact retry predecessor") from exc
        if dispositions != (predecessor,):
            raise admission_error(
                "compaction retry call has mixed terminal facts")
        return predecessor

    def failed_compaction_owner(
            call_ref: VersionRef, call: Mapping[str, Any],
            call_loop_ref: VersionRef,
            compacting_loop: Mapping[str, Any],
            dispositions: tuple[EventEnvelope, ...],
            owner: tuple[Mapping[str, Any], Mapping[str, Any]],
    ) -> tuple[EventEnvelope, EventEnvelope]:
        compaction_row, compaction = owner
        if (compaction.get("failure_reason")
                == "task_provider_call_limit_exhausted"):
            try:
                descriptors = compaction_request_descriptors(call)
                compaction_ref = ref_from_payload(
                    compaction["agent_context_compaction_ref"])
                owner_call_ref = ref_from_payload(
                    compaction["provider_call_ref"])
                exhausted_loop_ref = ref_from_payload(
                    compaction["agent_loop_ref"])
                exhausted_loop = require_ref(
                    exhausted_loop_ref, "agent_loop/v1",
                    "agent_loop_version")
            except (KeyError, TypeError, ValueError) as exc:
                raise admission_error(
                    "provider-cap failed compaction refs are malformed"
                ) from exc

            compaction_id = str(compaction_ref.entity_id)
            started_owners = tuple(
                (row, metadata) for row, metadata in compaction_rows
                if (str(row["logical_id"]) == compaction_id
                    and metadata.get("state") == "started"))
            completed_owners = tuple(
                (row, metadata) for row, metadata
                in completed_compaction_rows
                if str(row["logical_id"]) == compaction_id)
            failed_owners = tuple(
                (row, metadata) for row, metadata
                in failed_compaction_rows
                if str(row["logical_id"]) == compaction_id)
            if (len(started_owners) != 1 or completed_owners
                    or len(failed_owners) != 1
                    or failed_owners[0][0] is not compaction_row
                    and dict(failed_owners[0][0])
                    != dict(compaction_row)):
                raise admission_error(
                    "provider-cap failed compaction has mixed lifecycle "
                    "ownership")
            started_row, started = started_owners[0]
            inherited_fields = (
                "first_turn_sequence", "last_turn_sequence",
                "covered_turn_refs")
            if (compaction_ref.entity_type
                    != "agent_context_compaction/v2"
                    or str(compaction_row["logical_id"]) != compaction_id
                    or str(compaction_row["version_id"])
                    != str(compaction_ref.version_id)
                    or compaction.get("agent_context_compaction_id")
                    != compaction_id
                    or compaction.get(
                        "agent_context_compaction_version_id")
                    != str(compaction_ref.version_id)
                    or descriptors.get("compaction_id") != compaction_id
                    or owner_call_ref != call_ref
                    or call.get("llm_call_ref")
                    != ref_payload(call_ref)
                    or call.get("agent_loop_ref")
                    != ref_payload(call_loop_ref)
                    or compaction.get("provider_attempt_ref") is not None
                    or compaction.get("state") != "failed"
                    or compaction.get("replacement_history") is not None
                    or str(started_row["logical_id"]) != compaction_id
                    or str(started_row["version_id"])
                    == str(compaction_ref.version_id)
                    or started.get("agent_context_compaction_id")
                    != compaction_id
                    or started.get("agent_context_compaction_ref") != {
                        "entity_type": "agent_context_compaction/v2",
                        "logical_id": compaction_id,
                        "version_id": str(started_row["version_id"]),
                    }
                    or started.get("state") != "started"
                    or started.get("agent_loop_ref")
                    != ref_payload(call_loop_ref)
                    or started.get("provider_call_ref") is not None
                    or started.get("provider_attempt_ref") is not None
                    or started.get("failure_reason") is not None
                    or started.get("replacement_history") is not None
                    or any(compaction.get(field) != started.get(field)
                           for field in inherited_fields)
                    or call_loop_ref.entity_id
                    != exhausted_loop_ref.entity_id
                    or call_loop_ref.version_id
                    == exhausted_loop_ref.version_id
                    or compacting_loop.get("state") != "COMPACTING"
                    or exhausted_loop.get("state") != "EXHAUSTED"
                    or exhausted_loop.get("agent_loop_id")
                    != str(exhausted_loop_ref.entity_id)
                    or exhausted_loop.get("agent_loop_version_id")
                    != str(exhausted_loop_ref.version_id)
                    or exhausted_loop.get("agent_loop_ref")
                    != ref_payload(exhausted_loop_ref)
                    or not isinstance(
                        compacting_loop.get("revision"), int)
                    or isinstance(
                        compacting_loop.get("revision"), bool)
                    or exhausted_loop.get("revision")
                    != compacting_loop["revision"] + 1):
                raise admission_error(
                    "provider-cap failed compaction breaks exact "
                    "call/loop closure")

            call_payload = ref_payload(call_ref)
            attempt_rows = (
                event_store.provider_attempt_rows_for_call(
                    call_ref.entity_id, call_ref.version_id))
            attempt_events = tuple(
                event for event in invocation_events
                if (event.event_type.startswith("provider_attempt_")
                    and (event.payload.get("llm_call_ref") == call_payload
                         or (event.payload.get("llm_call_id")
                             == str(call_ref.entity_id)
                             and event.payload.get("llm_call_version_id")
                             == str(call_ref.version_id)))))
            materialization_events = (
                event_store
                .provider_materialization_events_for_call(call_ref))
            response_rows: list[Mapping[str, Any]] = []
            for row in event_store.object_rows_by_producer(
                    context.invocation_ref.entity_id,
                    object_types=("resource_version/v1",)):
                try:
                    response = json.loads(str(row["metadata_json"]))
                except (TypeError, ValueError,
                        json.JSONDecodeError) as exc:
                    raise admission_error(
                        "provider-cap response inventory is malformed"
                    ) from exc
                origin = response.get("origin")
                if (isinstance(origin, Mapping)
                        and origin.get("secondary_ref") == call_payload):
                    response_rows.append(row)
            if (attempt_rows or attempt_events or materialization_events
                    or response_rows):
                raise admission_error(
                    "provider-cap compaction call has reserved or "
                    "dispatched provider work")

            call_events = tuple(
                event for event in
                event_store.list_events_by_aggregate(
                    str(call_ref.entity_id))
                if event.event_type.startswith("llm_call_"))
            expected_call_failure = {
                "llm_call_id": str(call_ref.entity_id),
                "llm_call_version_id": str(call_ref.version_id),
                "reason": "task_provider_call_limit_exhausted",
            }
            if (len(call_events) != 1
                    or call_events != dispositions
                    or call_events[0].event_type != "llm_call_failed/v1"
                    or dict(call_events[0].payload)
                    != expected_call_failure
                    or call_events[0].aggregate_id
                    != str(call_ref.entity_id)
                    or call_events[0].aggregate_type != "llm_call"
                    or call_events[0].producer_invocation_id
                    != context.invocation_ref.entity_id):
                raise admission_error(
                    "provider-cap compaction lacks one exact call failure")
            call_failure = call_events[0]

            exhausted_row = event_store.object_row(
                exhausted_loop_ref.version_id)
            exhausted_publications = tuple(
                event for event in invocation_events
                if (event.event_type == "object_version_published/v1"
                    and event.payload.get("version_id")
                    == str(exhausted_loop_ref.version_id)))
            compaction_publications = tuple(
                event for event in invocation_events
                if (event.event_type == "object_version_published/v1"
                    and event.payload.get("version_id")
                    == str(compaction_ref.version_id)))
            loop_terminals = tuple(
                event for event in
                event_store.list_events_by_aggregate(
                    str(exhausted_loop_ref.entity_id),
                    event_types=("agent_loop_terminal/v1",)))
            expected_loop_terminal = {
                "agent_loop_id": str(exhausted_loop_ref.entity_id),
                "state": "EXHAUSTED",
                "revision": exhausted_loop["revision"],
                "adopted_candidate_version_id": None,
                "disposition_version_id": None,
                "llm_turns_used": compacting_loop.get(
                    "llm_turns_used"),
                "terminal_reason": "task_provider_call_limit_exhausted",
            }
            failure_transaction = str(compaction_row["transaction_id"])
            if (exhausted_row is None
                    or len(exhausted_publications) != 1
                    or len(compaction_publications) != 1
                    or len(loop_terminals) != 1
                    or exhausted_row["transaction_id"]
                    != failure_transaction
                    or compaction_row["producer_invocation_id"]
                    != str(context.invocation_ref.entity_id)
                    or started_row["producer_invocation_id"]
                    != str(context.invocation_ref.entity_id)
                    or exhausted_row["producer_invocation_id"]
                    != str(context.invocation_ref.entity_id)
                    or dict(loop_terminals[0].payload)
                    != expected_loop_terminal
                    or loop_terminals[0].aggregate_id
                    != str(exhausted_loop_ref.entity_id)
                    or loop_terminals[0].aggregate_type != "agent_loop"
                    or loop_terminals[0].producer_invocation_id
                    != context.invocation_ref.entity_id
                    or any(str(event.transaction_id)
                           != failure_transaction
                           for event in (call_failure,
                                         loop_terminals[0]))):
                raise admission_error(
                    "provider-cap compaction terminal facts lack one "
                    "closure transaction")
            exhausted_publication = exhausted_publications[0]
            compaction_publication = compaction_publications[0]
            if (str(exhausted_publication.event_id)
                    != str(exhausted_row["published_event_id"])
                    or str(compaction_publication.event_id)
                    != str(compaction_row["published_event_id"])
                    or any(str(event.transaction_id)
                           != failure_transaction
                           for event in (exhausted_publication,
                                         compaction_publication))
                    or exhausted_publication.aggregate_id
                    != str(exhausted_loop_ref.entity_id)
                    or exhausted_publication.aggregate_type
                    != "agent_loop/v1"
                    or exhausted_publication.payload.get("logical_id")
                    != str(exhausted_loop_ref.entity_id)
                    or exhausted_publication.payload.get("object_type")
                    != "agent_loop/v1"
                    or exhausted_publication.payload.get("metadata")
                    != exhausted_loop
                    or compaction_publication.aggregate_id != compaction_id
                    or compaction_publication.aggregate_type
                    != "agent_context_compaction/v2"
                    or compaction_publication.payload.get("logical_id")
                    != compaction_id
                    or compaction_publication.payload.get("object_type")
                    != "agent_context_compaction/v2"
                    or compaction_publication.payload.get("metadata")
                    != compaction):
                raise admission_error(
                    "provider-cap compaction publication closure is "
                    "conflicting")
            return call_failure, compaction_publication

        try:
            descriptors = compaction_request_descriptors(call)
            compaction_ref = ref_from_payload(
                compaction["agent_context_compaction_ref"])
            owner_call_ref = ref_from_payload(
                compaction["provider_call_ref"])
            attempt_ref = ref_from_payload(
                compaction["provider_attempt_ref"])
            failed_loop_ref = ref_from_payload(
                compaction["agent_loop_ref"])
            attempt = require_ref(
                attempt_ref, "provider_attempt_spec/v1",
                "provider_attempt_version")
            failed_loop = require_ref(
                failed_loop_ref, "agent_loop/v1",
                "agent_loop_version")
        except (KeyError, TypeError, ValueError) as exc:
            raise admission_error(
                "failed compaction owner refs are malformed") from exc

        compaction_id = str(compaction_ref.entity_id)
        started_owners = tuple(
            (row, metadata) for row, metadata in compaction_rows
            if (str(row["logical_id"]) == compaction_id
                and metadata.get("state") == "started"))
        completed_owners = tuple(
            (row, metadata) for row, metadata
            in completed_compaction_rows
            if str(row["logical_id"]) == compaction_id)
        if len(started_owners) != 1 or completed_owners:
            raise admission_error(
                "failed compaction has mixed lifecycle ownership")
        started_row, started = started_owners[0]
        inherited_fields = (
            "first_turn_sequence", "last_turn_sequence",
            "covered_turn_refs")
        if (compaction_ref.entity_type
                != "agent_context_compaction/v2"
                or str(compaction_row["logical_id"]) != compaction_id
                or str(compaction_row["version_id"])
                != str(compaction_ref.version_id)
                or compaction.get("agent_context_compaction_id")
                != compaction_id
                or compaction.get(
                    "agent_context_compaction_version_id")
                != str(compaction_ref.version_id)
                or descriptors.get("compaction_id") != compaction_id
                or owner_call_ref != call_ref
                or call.get("llm_call_ref") != ref_payload(call_ref)
                or call.get("agent_loop_ref")
                != ref_payload(call_loop_ref)
                or attempt.get("provider_attempt_ref")
                != ref_payload(attempt_ref)
                or attempt.get("llm_call_ref") != ref_payload(call_ref)
                or compaction.get("state") != "failed"
                or compaction.get("failure_reason")
                != "compaction_summary_exceeds_context_policy"
                or compaction.get("replacement_history") is not None
                or str(started_row["logical_id"]) != compaction_id
                or started.get("agent_context_compaction_id")
                != compaction_id
                or started.get("agent_context_compaction_ref") != {
                    "entity_type": "agent_context_compaction/v2",
                    "logical_id": compaction_id,
                    "version_id": str(started_row["version_id"]),
                }
                or started.get("agent_loop_ref")
                != ref_payload(call_loop_ref)
                or started.get("provider_call_ref") is not None
                or started.get("provider_attempt_ref") is not None
                or started.get("failure_reason") is not None
                or started.get("replacement_history") is not None
                or any(compaction.get(field) != started.get(field)
                       for field in inherited_fields)
                or call_loop_ref.entity_id != failed_loop_ref.entity_id
                or call_loop_ref.version_id == failed_loop_ref.version_id
                or failed_loop.get("state") != "INFRASTRUCTURE_FAILED"
                or not isinstance(
                    compacting_loop.get("revision"), int)
                or isinstance(
                    compacting_loop.get("revision"), bool)
                or failed_loop.get("revision")
                != compacting_loop["revision"] + 1):
            raise admission_error(
                "failed compaction owner breaks exact call/loop closure")

        attempt_row = event_store.object_row(
            attempt_ref.version_id)
        response_version_raw = (
            dispositions[0].payload.get("response_version_id")
            if len(dispositions) == 1 else None)
        try:
            response_version = TypedId.parse(
                str(response_version_raw), expected="resource_version")
        except (TypeError, ValueError) as exc:
            raise admission_error(
                "failed compaction disposition lineage is malformed") from exc
        response_row = event_store.object_row(
            response_version)
        try:
            response = json.loads(str(response_row["metadata_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise admission_error(
                "failed compaction response metadata is malformed") from exc
        expected_origin = {
            "kind": "provider_raw_response",
            "primary_ref": ref_payload(attempt_ref),
            "secondary_ref": ref_payload(call_ref),
        }
        expected_disposition = {
            "llm_call_id": str(call_ref.entity_id),
            "llm_call_version_id": str(call_ref.version_id),
            "provider_attempt_version_id": str(attempt_ref.version_id),
            "response_version_id": str(response_version),
            "reason": "transport_contract_rejected",
        }
        if (len(dispositions) != 1
                or dispositions[0].event_type
                != "llm_call_candidate_not_adopted/v1"
                or dict(dispositions[0].payload) != expected_disposition
                or dispositions[0].producer_invocation_id
                != context.invocation_ref.entity_id
                or attempt_row is None
                or attempt_row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)
                or response_row is None
                or response_row["object_type"] != "resource_version/v1"
                or response_row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)
                or response.get("origin_kind")
                != "provider_raw_response"
                or response.get("origin") != expected_origin):
            raise admission_error(
                "failed compaction has mixed candidate disposition")

        attempt_terminals = tuple(
            event for event in
            event_store.list_events_by_aggregate(
                str(attempt_ref.entity_id))
            if event.event_type in terminal_attempt_events)
        if (len(attempt_terminals) != 1
                or attempt_terminals[0].event_type
                != "provider_attempt_completed/v1"):
            raise admission_error(
                "failed compaction lacks one completed attempt")
        completion = attempt_terminals[0]
        observations = tuple(
            event for event in
            event_store.list_events_by_aggregate(
                str(attempt_ref.entity_id))
            if event.event_type
            == "provider_attempt_submission_observed/v1")
        if len(observations) != 1:
            raise admission_error(
                "failed compaction lacks one response observation")
        observation = observations[0]
        expected_completion = {
            "provider_attempt_id": str(attempt_ref.entity_id),
            "response_version_id": str(response_version),
            "response_observed_event_id": str(observation.event_id),
            "finish_reason": observation.payload.get("finish_reason"),
            "external_request_id": observation.payload.get(
                "external_request_id"),
        }
        if (dict(completion.payload) != expected_completion
                or completion.producer_invocation_id
                != context.invocation_ref.entity_id
                or completion.transaction_id
                != dispositions[0].transaction_id):
            raise admission_error(
                "failed compaction completed attempt is conflicting")

        failed_row = event_store.object_row(
            failed_loop_ref.version_id)
        failed_publications = tuple(
            event for event in invocation_events
            if (event.event_type == "object_version_published/v1"
                and event.payload.get("version_id")
                == str(failed_loop_ref.version_id)))
        compaction_publications = tuple(
            event for event in invocation_events
            if (event.event_type == "object_version_published/v1"
                and event.payload.get("version_id")
                == str(compaction_ref.version_id)))
        failure_transaction = str(compaction_row["transaction_id"])
        if (failed_row is None
                or len(failed_publications) != 1
                or len(compaction_publications) != 1
                or failed_row["transaction_id"] != failure_transaction
                or compaction_row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)
                or failed_row["producer_invocation_id"]
                != str(context.invocation_ref.entity_id)):
            raise admission_error(
                "failed compaction publications lack one transaction")
        failed_publication = failed_publications[0]
        compaction_publication = compaction_publications[0]
        if (str(failed_publication.event_id)
                != str(failed_row["published_event_id"])
                or str(compaction_publication.event_id)
                != str(compaction_row["published_event_id"])
                or any(str(event.transaction_id) != failure_transaction
                       for event in (
                           failed_publication,
                           compaction_publication))
                or failed_publication.aggregate_id
                != str(failed_loop_ref.entity_id)
                or failed_publication.aggregate_type != "agent_loop/v1"
                or failed_publication.payload.get("logical_id")
                != str(failed_loop_ref.entity_id)
                or failed_publication.payload.get("object_type")
                != "agent_loop/v1"
                or failed_publication.payload.get("metadata")
                != failed_loop
                or compaction_publication.aggregate_id != compaction_id
                or compaction_publication.aggregate_type
                != "agent_context_compaction/v2"
                or compaction_publication.payload.get("logical_id")
                != compaction_id
                or compaction_publication.payload.get("object_type")
                != "agent_context_compaction/v2"
                or compaction_publication.payload.get("metadata")
                != compaction):
            raise admission_error(
                "failed compaction publication closure is conflicting")
        ordered_ids = (
            str(completion.event_id),
            str(dispositions[0].event_id),
            str(failed_publication.event_id),
            str(compaction_publication.event_id),
        )
        if (any(event_id not in invocation_event_positions
                for event_id in ordered_ids)
                or tuple(invocation_event_positions[event_id]
                         for event_id in ordered_ids)
                != tuple(sorted(invocation_event_positions[event_id]
                                for event_id in ordered_ids))):
            raise admission_error(
                "failed compaction publication order is conflicting")
        return dispositions[0], compaction_publication
    call_ids = tuple(call_entries_by_id)
    unknown_call_id: str | None = None
    unknown_attempt_id: str | None = None
    unknown_payload: dict[str, str] | None = None
    if provider_submission_unknown_ref is not None:
        unknown = require_ref(
            provider_submission_unknown_ref,
            "provider_submission_unknown/v1",
            "provider_submission_unknown_version")
        unknown_call_ref = ref_from_payload(unknown["llm_call_ref"])
        unknown_attempt_ref = ref_from_payload(
            unknown["provider_attempt_ref"])
        unknown_call_id = str(unknown_call_ref.entity_id)
        unknown_attempt_id = str(unknown_attempt_ref.entity_id)
        unknown_payload = ref_payload(provider_submission_unknown_ref)
        if (unknown_call_ref not in call_refs
                or unknown_attempt_ref not in attempt_refs):
            raise admission_error(
                "submission-unknown witness is outside this invocation")
        unknown_entries = tuple(
            entry for entry in call_entries_by_id[unknown_call_id]
            if entry[2] == unknown_call_ref)
        if len(unknown_entries) != 1:
            raise admission_error(
                "submission-unknown call identity is ambiguous")
    terminal_ids: list[str] = []
    for call_id in call_ids:
        call_events = [
            event for event in
            event_store.list_events_by_aggregate(call_id)
            if event.event_type.startswith("llm_call_")]
        if call_id == unknown_call_id:
            unknown_events = [
                event for event in call_events
                if (event.event_type == "llm_call_submission_unknown/v1"
                    and event.payload.get(
                        "provider_submission_unknown_ref")
                    == unknown_payload)]
            ordinary_terminals = tuple(
                event for event in call_events
                if event.event_type in terminal_call_events)
            if len(unknown_events) != 1 or ordinary_terminals:
                raise admission_error(
                    "LLM call lacks its exact submission-unknown fact")
            terminal_ids.append(str(unknown_events[0].event_id))
            continue
        v3_call_entries = tuple(
            entry for entry in call_entries_by_id[call_id]
            if entry[2].entity_type == "llm_call_spec/v3")
        if len(v3_call_entries) > 1:
            raise admission_error(
                "canonical LLM call inventory has multiple v3 versions")
        if v3_call_entries:
            _row, call_metadata, call_ref = v3_call_entries[0]
            host_attempts = tuple(
                registered_host_attempts_by_call.get(call_ref, ()))
            if len(host_attempts) != 1:
                raise admission_error(
                    "registered HOST LLM call lacks one exact host attempt")
            host_attempt_ref, host_attempt = host_attempts[0]
            provider_ref = ref_from_payload(
                host_attempt["provider_attempt_ref"])
            if (call_metadata.get("invocation_kind") != "registered_host"
                    or call_metadata.get("invocation_ref")
                    != ref_payload(context.invocation_ref)
                    or call_metadata.get("operation_binding_ref")
                    != ref_payload(context.operation_binding_ref)
                    or host_attempt.get("request_resource_ref")
                    != call_metadata.get("request_resource_ref")
                    or provider_ref not in attempt_refs):
                raise admission_error(
                    "registered HOST LLM call crosses its exact firing closure")
            host_payload = ref_payload(host_attempt_ref)
            success_types = {
                "llm_response_registered/v2",
                "llm_invocation_succeeded/v2",
            }
            close_types = {
                "provider_attempt_host_closed/v1",
                "llm_call_registered_host_closed/v1",
                "registered_host_llm_attempt_closed/v1",
            }
            successes = tuple(
                event for event in invocation_events
                if (event.event_type in success_types
                    and event.payload.get(
                        "registered_host_llm_attempt_ref") == host_payload))
            closures = tuple(
                event for event in invocation_events
                if (event.event_type in close_types
                    and event.payload.get(
                        "registered_host_llm_attempt_ref") == host_payload))
            expected_common = {
                "invocation_kind": "registered_host",
                "invocation_ref": ref_payload(context.invocation_ref),
                "operation_binding_ref": ref_payload(
                    context.operation_binding_ref),
                "llm_call_ref": ref_payload(call_ref),
                "registered_host_llm_attempt_ref": host_payload,
                "provider_attempt_ref": ref_payload(provider_ref),
            }
            if successes:
                if (closures
                        or {event.event_type for event in successes}
                        != success_types
                        or len(successes) != len(success_types)
                        or len({str(event.transaction_id)
                                for event in successes}) != 1
                        or any(any(
                            event.payload.get(name) != value
                            for name, value in expected_common.items())
                            for event in successes)):
                    raise admission_error(
                        "registered HOST LLM success closure is conflicting")
                terminal_ids.extend(
                    str(event.event_id) for event in successes)
                continue
            if ({event.event_type for event in closures} != close_types
                    or len(closures) != len(close_types)
                    or len({str(event.transaction_id)
                            for event in closures}) != 1
                    or any(any(
                        event.payload.get(name) != value
                        for name, value in expected_common.items())
                        for event in closures)
                    or any(event.payload.get("next_attempt_allowed") is not False
                           for event in closures)):
                raise admission_error(
                    "registered HOST LLM failure closure is conflicting")
            terminal_ids.extend(
                str(event.event_id) for event in closures
                if event.event_type
                != "provider_attempt_host_closed/v1")
            continue
        v2_call_entries = tuple(
            entry for entry in call_entries_by_id[call_id]
            if entry[2].entity_type == "llm_call_spec/v2")
        if len(v2_call_entries) > 1:
            raise admission_error(
                "canonical LLM call inventory has multiple v2 versions")
        if v2_call_entries:
            call_version = str(v2_call_entries[0][2].version_id)
            call_metadata = v2_call_entries[0][1]
            call_ref = v2_call_entries[0][2]
            firing_ref = context.own_transition_firing_ref
            if firing_ref is None:
                raise admission_error(
                    "v2 call lacks its exact firing authority")
            firing_view = event_store.firing_view(
                firing_version_id=firing_ref.version_id,
                invocation_version_id=context.invocation_ref.version_id)

            def linked_provider_attempt(
                    neutral_ref: VersionRef,
            ) -> tuple[VersionRef, Mapping[str, Any]] | None:
                linkage = tuple(
                    row for row in
                    event_store.relation_rows_for_view(
                        firing_view, version_id=neutral_ref.version_id,
                        relation_type="derived_from", endpoint="source")
                    if relation_endpoint(
                        row, "source_json") == neutral_ref
                    and relation_endpoint(
                        row, "target_json").entity_type
                    == "provider_attempt_spec/v1")
                if len(linkage) > 1:
                    raise admission_error(
                        "neutral invocation attempt links multiple "
                        "provider attempts")
                if not linkage:
                    return None
                provider_ref = relation_endpoint(
                    linkage[0], "target_json")
                return provider_ref, require_ref(
                    provider_ref, "provider_attempt_spec/v1",
                    "provider_attempt_version")

            owner_interruptions = tuple(
                event for event in invocation_events
                if (event.event_type
                    == "llm_invocation_owner_interrupted/v1"
                    and event.payload.get("llm_call_ref")
                    == ref_payload(call_ref)))
            if owner_interruptions:
                if len(owner_interruptions) != 1:
                    raise admission_error(
                        "LLM call has multiple owner interruption facts")
                interruption = owner_interruptions[0]
                call_interruptions = tuple(
                    event for event in call_events
                    if event.event_type
                    == "llm_call_owner_interrupted/v1")
                provider_interruptions = tuple(
                    event for event in invocation_events
                    if (event.event_type
                        == "provider_attempt_owner_interrupted/v1"
                        and event.payload.get("llm_call_ref")
                        == ref_payload(call_ref)))
                try:
                    neutral_ref = ref_from_payload(
                        interruption.payload[
                            "llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                    llm_invocation_ref = ref_from_payload(
                        interruption.payload["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                    provider_ref = ref_from_payload(
                        interruption.payload["provider_attempt_ref"])
                    provider = require_ref(
                        provider_ref, "provider_attempt_spec/v1",
                        "provider_attempt_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "LLM owner interruption refs are malformed") from exc
                if (len(call_interruptions) != 1
                        or len(provider_interruptions) != 1
                        or dict(call_interruptions[0].payload)
                        != dict(interruption.payload)
                        or dict(provider_interruptions[0].payload)
                        != dict(interruption.payload)
                        or any(event.transaction_id
                               != interruption.transaction_id
                               for event in (
                                   call_interruptions[0],
                                   provider_interruptions[0]))
                        or interruption.aggregate_id
                        != str(neutral_ref.entity_id)
                        or interruption.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or neutral.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or provider.get("provider_attempt_ref")
                        != ref_payload(provider_ref)):
                    raise admission_error(
                        "LLM owner interruption lacks one exact closure")
                terminal_ids.extend((
                    str(call_interruptions[0].event_id),
                    str(interruption.event_id),
                ))
                continue

            optional_turns: list[tuple[
                EventEnvelope, VersionRef, Mapping[str, Any],
                VersionRef, Mapping[str, Any]]] = []
            for turn_event in invocation_events:
                if turn_event.event_type != "agent_turn_recorded/v2":
                    continue
                try:
                    neutral_ref = ref_from_payload(
                        turn_event.payload["llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "optional v2 turn has malformed neutral authority") from exc
                linkage = tuple(
                    row for row in
                    event_store.relation_rows_for_view(
                        firing_view, version_id=neutral_ref.version_id,
                        relation_type="derived_from", endpoint="source")
                    if relation_endpoint(
                        row, "source_json") == neutral_ref
                    and relation_endpoint(
                        row, "target_json").entity_type
                    == "provider_attempt_spec/v1")
                if len(linkage) > 1:
                    raise admission_error(
                        "optional v2 turn links multiple provider attempts")
                if not linkage:
                    continue
                provider_ref = relation_endpoint(
                    linkage[0], "target_json")
                provider = require_ref(
                    provider_ref, "provider_attempt_spec/v1",
                    "provider_attempt_version")
                if provider.get("llm_call_ref") == ref_payload(call_ref):
                    optional_turns.append((
                        turn_event, neutral_ref, neutral,
                        provider_ref, provider))
            if optional_turns:
                if len(optional_turns) != 1:
                    raise admission_error(
                        "v2 call has multiple optional terminal turns")
                (turn_event, neutral_ref, neutral,
                 provider_ref, provider) = optional_turns[0]
                try:
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                    response_payload = turn_event.payload[
                        "response_resource_ref"]
                    response_ref = ResourceVersionRef(
                        TypedId.parse(str(response_payload["resource_id"]),
                                      expected="resource"),
                        TypedId.parse(str(response_payload[
                            "resource_version_id"]),
                                      expected="resource_version"))
                    response = require_ref(
                        response_ref.as_version_ref(),
                        "resource_version/v1", "resource_version")
                    turn_ref = VersionRef(
                        "agent_turn/v1",
                        TypedId.parse(str(turn_event.payload[
                            "agent_turn_id"]), expected="agent_turn"),
                        TypedId.parse(str(turn_event.payload[
                            "agent_turn_version_id"]),
                                      expected="agent_turn_version"))
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "optional v2 terminal refs are malformed") from exc
                successes = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(neutral_ref.entity_id), event_types=(
                            "llm_invocation_succeeded/v1",)))
                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(provider_ref.entity_id), event_types=(
                            "provider_attempt_completed/v1",)))
                origin = response.get("origin")
                success = successes[0] if len(successes) == 1 else None
                completion = (
                    completions[0] if len(completions) == 1 else None)
                if (success is None or completion is None
                        or neutral.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or llm_invocation.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or llm_invocation.get("operation_binding_ref")
                        != ref_payload(context.operation_binding_ref)
                        or llm_invocation.get("agent_loop_ref", {}).get(
                            "logical_id")
                        != turn_event.payload.get("agent_loop_id")
                        or llm_invocation.get("turn_sequence")
                        != turn_event.payload.get("sequence")
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or response.get("origin_kind") != "llm_response"
                        or not isinstance(origin, Mapping)
                        or origin.get("primary_ref")
                        != ref_payload(neutral_ref)
                        or origin.get("secondary_ref")
                        != ref_payload(llm_invocation_ref)
                        or response.get("producer_ref")
                        != ref_payload(context.invocation_ref)
                        or success.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or success.transaction_id
                        != turn_event.transaction_id
                        or success.payload.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or success.payload.get(
                            "llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or success.payload.get("response_resource_ref")
                        != {"resource_id": str(response_ref.resource_id),
                            "resource_version_id": str(
                                response_ref.resource_version_id)}
                        or success.payload.get("agent_turn_ref")
                        != ref_payload(turn_ref)
                        or success.payload.get("turn_sequence")
                        != turn_event.payload.get("sequence")
                        or completion.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.transaction_id
                        != turn_event.transaction_id
                        or completion.payload.get("provider_attempt_id")
                        != str(provider_ref.entity_id)
                        or turn_event.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or turn_event.aggregate_id
                        != turn_event.payload.get("agent_loop_id")):
                    raise admission_error(
                        "optional v2 terminal turn lacks one exact closure")
                terminal_ids.append(str(success.event_id))
                continue
            interrupted_turns: list[tuple[
                EventEnvelope, VersionRef, Mapping[str, Any],
                VersionRef, Mapping[str, Any]]] = []
            for interruption in invocation_events:
                if interruption.event_type != "llm_invocation_interrupted/v1":
                    continue
                try:
                    neutral_ref = ref_from_payload(
                        interruption.payload[
                            "llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "optional interrupted turn has malformed neutral "
                        "authority") from exc
                completions = tuple(
                    event for event in invocation_events
                    if (event.event_type
                        == "provider_attempt_completed/v1"
                        and event.transaction_id
                        == interruption.transaction_id))
                if len(completions) > 1:
                    raise admission_error(
                        "optional interrupted turn shares its terminal "
                        "transaction with multiple provider attempts")
                if not completions:
                    continue
                provider_refs = tuple(
                    ref for ref in attempt_refs
                    if str(ref.entity_id)
                    == completions[0].aggregate_id)
                if len(provider_refs) != 1:
                    raise admission_error(
                        "optional interrupted turn lacks one canonical "
                        "provider attempt")
                provider_ref = provider_refs[0]
                provider = require_ref(
                    provider_ref, "provider_attempt_spec/v1",
                    "provider_attempt_version")
                if provider.get("llm_call_ref") == ref_payload(call_ref):
                    interrupted_turns.append((
                        interruption, neutral_ref, neutral,
                        provider_ref, provider))
            if interrupted_turns:
                if len(interrupted_turns) != 1 or call_events:
                    raise admission_error(
                        "optional interrupted call has conflicting terminal "
                        "ownership")
                (interruption, neutral_ref, neutral,
                 provider_ref, provider) = interrupted_turns[0]
                try:
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "optional interrupted call refs are malformed") from exc
                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(provider_ref.entity_id), event_types=(
                            "provider_attempt_completed/v1",)))
                completion = (
                    completions[0] if len(completions) == 1 else None)
                invocation_kind = llm_invocation.get("invocation_kind")
                delegated_interruption = (
                    invocation_kind == "delegated_subtask"
                    and interruption.payload.get("child_session_id")
                    == llm_invocation.get("child_session_id")
                    and interruption.payload.get("local_sequence")
                    == llm_invocation.get("local_sequence")
                    and llm_invocation.get("turn_sequence")
                    == llm_invocation.get("local_sequence")
                    and call_metadata.get("turn_sequence")
                    == llm_invocation.get("local_sequence")
                    and "agent_loop_id" not in interruption.payload
                    and "turn_sequence" not in interruption.payload
                    and "revision" not in interruption.payload)
                if (completion is None
                        or interruption.aggregate_id
                        != str(neutral_ref.entity_id)
                        or interruption.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or interruption.payload.get("finish_reason")
                        != "length"
                        or interruption.payload.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or interruption.payload.get(
                            "llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or neutral.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or llm_invocation.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or invocation_kind not in {
                            "normal_turn", "delegated_subtask"}
                        or (invocation_kind == "delegated_subtask"
                            and not delegated_interruption)
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or llm_invocation.get("operation_binding_ref")
                        != ref_payload(context.operation_binding_ref)
                        or llm_invocation.get("request_resource_ref")
                        != call_metadata.get("request_resource_ref")
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or completion.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.payload.get("provider_attempt_id")
                        != str(provider_ref.entity_id)
                        or completion.transaction_id
                        != interruption.transaction_id):
                    raise admission_error(
                        "optional interrupted call lacks exact closure")
                terminal_ids.append(str(interruption.event_id))
                continue
            v3_owners: list[tuple[
                Mapping[str, Any], Mapping[str, Any], VersionRef,
                Mapping[str, Any], VersionRef, Mapping[str, Any]]] = []
            for compaction_row, compaction in v3_compaction_rows:
                try:
                    neutral_ref = ref_from_payload(
                        compaction["llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "v3 compaction has malformed neutral authority"
                    ) from exc
                successes = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(neutral_ref.entity_id), event_types=(
                            "llm_invocation_succeeded/v1",)))
                if len(successes) > 1:
                    raise admission_error(
                        "v3 compaction has multiple invocation success "
                        "facts")
                if not successes:
                    continue
                completions = tuple(
                    event for event in invocation_events
                    if (event.event_type
                        == "provider_attempt_completed/v1"
                        and event.transaction_id
                        == successes[0].transaction_id))
                if len(completions) > 1:
                    raise admission_error(
                        "v3 compaction shares its terminal transaction "
                        "with multiple provider attempts")
                if not completions:
                    continue
                provider_refs = tuple(
                    ref for ref in attempt_refs
                    if str(ref.entity_id)
                    == completions[0].aggregate_id)
                if len(provider_refs) != 1:
                    raise admission_error(
                        "v3 compaction lacks one canonical provider "
                        "attempt")
                provider_ref = provider_refs[0]
                provider = require_ref(
                    provider_ref, "provider_attempt_spec/v1",
                    "provider_attempt_version")
                if provider.get("llm_call_ref") == ref_payload(call_ref):
                    v3_owners.append((
                        compaction_row, compaction, neutral_ref, neutral,
                        provider_ref, provider))
            if v3_owners:
                if len(v3_owners) != 1 or call_events:
                    raise admission_error(
                        "v3 compaction call has conflicting terminal ownership")
                (compaction_row, compaction, neutral_ref, neutral,
                 provider_ref, provider) = v3_owners[0]
                try:
                    compaction_ref = ref_from_payload(
                        compaction["agent_context_compaction_ref"])
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                    waiting_loop_ref = ref_from_payload(
                        compaction["agent_loop_ref"])
                    waiting_loop = require_ref(
                        waiting_loop_ref, "agent_loop/v1",
                        "agent_loop_version")
                    compacting_loop_ref = ref_from_payload(
                        call_metadata["agent_loop_ref"])
                    compacting_loop = require_ref(
                        compacting_loop_ref, "agent_loop/v1",
                        "agent_loop_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "v3 compaction terminal refs are malformed") from exc
                successes = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(neutral_ref.entity_id), event_types=(
                            "llm_invocation_succeeded/v1",)))
                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(provider_ref.entity_id), event_types=(
                            "provider_attempt_completed/v1",)))
                publications = tuple(
                    event for event in invocation_events
                    if event.event_type == "object_version_published/v1"
                    and event.payload.get("version_id") in {
                        str(compaction_ref.version_id),
                        str(waiting_loop_ref.version_id),
                    })
                publication_by_version = {
                    str(event.payload.get("version_id")): event
                    for event in publications
                }
                success = successes[0] if len(successes) == 1 else None
                completion = (
                    completions[0] if len(completions) == 1 else None)
                compaction_publication = publication_by_version.get(
                    str(compaction_ref.version_id))
                waiting_publication = publication_by_version.get(
                    str(waiting_loop_ref.version_id))
                transaction_id = str(compaction_row["transaction_id"])
                if (success is None or completion is None
                        or len(publications) != 2
                        or compaction_publication is None
                        or waiting_publication is None
                        or compaction_ref.entity_type
                        != "agent_context_compaction/v3"
                        or str(compaction_row["logical_id"])
                        != str(compaction_ref.entity_id)
                        or str(compaction_row["version_id"])
                        != str(compaction_ref.version_id)
                        or compaction.get("agent_context_compaction_id")
                        != str(compaction_ref.entity_id)
                        or compaction.get(
                            "agent_context_compaction_version_id")
                        != str(compaction_ref.version_id)
                        or compaction.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or compaction.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or neutral.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or llm_invocation.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("invocation_kind")
                        != "context_compaction"
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or llm_invocation.get("operation_binding_ref")
                        != ref_payload(context.operation_binding_ref)
                        or llm_invocation.get("request_resource_ref")
                        != call_metadata.get("request_resource_ref")
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or compacting_loop.get("state") != "COMPACTING"
                        or waiting_loop.get("state") != "WAITING_FOR_LLM"
                        or compacting_loop_ref.entity_id
                        != waiting_loop_ref.entity_id
                        or not isinstance(
                            compacting_loop.get("revision"), int)
                        or isinstance(
                            compacting_loop.get("revision"), bool)
                        or waiting_loop.get("revision")
                        != compacting_loop["revision"] + 1
                        or success.payload.get(
                            "agent_context_compaction_ref")
                        != ref_payload(compaction_ref)
                        or success.payload.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or success.payload.get(
                            "llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or success.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.payload.get("provider_attempt_id")
                        != str(provider_ref.entity_id)
                        or any(str(event.transaction_id) != transaction_id
                               for event in (
                                   success, completion,
                                   compaction_publication,
                                   waiting_publication))
                        or compaction_publication.payload.get(
                            "object_type")
                        != "agent_context_compaction/v3"
                        or compaction_publication.payload.get("metadata")
                        != compaction
                        or waiting_publication.payload.get("object_type")
                        != "agent_loop/v1"
                        or waiting_publication.payload.get("metadata")
                        != waiting_loop):
                    raise admission_error(
                        "v3 compaction lacks one exact terminal closure")
                terminal_ids.append(str(success.event_id))
                continue
            delegated_owners: list[tuple[
                EventEnvelope, VersionRef, Mapping[str, Any],
                VersionRef, Mapping[str, Any]]] = []
            for success in invocation_events:
                if success.event_type != "llm_invocation_succeeded/v1":
                    continue
                try:
                    neutral_ref = ref_from_payload(
                        success.payload["llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "delegated call has malformed neutral authority"
                    ) from exc
                if llm_invocation.get(
                        "invocation_kind") != "delegated_subtask":
                    continue
                linked = linked_provider_attempt(neutral_ref)
                if linked is None:
                    raise admission_error(
                        "delegated call lacks its provider-attempt link")
                provider_ref, provider = linked
                if provider.get("llm_call_ref") == ref_payload(call_ref):
                    delegated_owners.append((
                        success, neutral_ref, neutral,
                        provider_ref, provider))
            if delegated_owners:
                if len(delegated_owners) != 1 or call_events:
                    raise admission_error(
                        "delegated call has conflicting terminal ownership")
                (success, neutral_ref, neutral,
                 provider_ref, provider) = delegated_owners[0]
                try:
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                    response_payload = success.payload[
                        "response_resource_ref"]
                    response_ref = ResourceVersionRef(
                        TypedId.parse(str(response_payload["resource_id"]),
                                      expected="resource"),
                        TypedId.parse(str(response_payload[
                            "resource_version_id"]),
                            expected="resource_version"))
                    response = require_ref(
                        response_ref.as_version_ref(),
                        "resource_version/v1", "resource_version")
                    call_loop_ref = ref_from_payload(
                        call_metadata["agent_loop_ref"])
                    call_loop = require_ref(
                        call_loop_ref, "agent_loop/v1",
                        "agent_loop_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "delegated terminal refs are malformed") from exc
                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(provider_ref.entity_id), event_types=(
                            "provider_attempt_completed/v1",)))
                completion = (
                    completions[0] if len(completions) == 1 else None)
                origin = response.get("origin")
                local_sequence = llm_invocation.get("local_sequence")
                child_session_id = llm_invocation.get(
                    "child_session_id")
                if (completion is None
                        or neutral.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or neutral.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("invocation_kind")
                        != "delegated_subtask"
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or llm_invocation.get("operation_binding_ref")
                        != ref_payload(context.operation_binding_ref)
                        or llm_invocation.get("agent_loop_ref")
                        != ref_payload(call_loop_ref)
                        or call_loop.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or context.agent_ref is None
                        or llm_invocation.get("subject_agent_ref")
                        != ref_payload(context.agent_ref)
                        or llm_invocation.get("execution_agent_ref")
                        != ref_payload(context.agent_ref)
                        or not isinstance(child_session_id, str)
                        or not child_session_id
                        or isinstance(local_sequence, bool)
                        or not isinstance(local_sequence, int)
                        or local_sequence < 0
                        or llm_invocation.get("turn_sequence")
                        != local_sequence
                        or call_metadata.get("turn_sequence")
                        != local_sequence
                        or llm_invocation.get("request_resource_ref")
                        != call_metadata.get("request_resource_ref")
                        or llm_invocation.get(
                            "semantic_prompt_resource_ref")
                        != call_metadata.get(
                            "semantic_prompt_resource_ref")
                        or llm_invocation.get("tool_catalog_ref")
                        != call_metadata.get("tool_catalog_ref")
                        or llm_invocation.get("prior_turn_refs")
                        != call_metadata.get("prior_turn_refs")
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or response.get("origin_kind") != "llm_response"
                        or not isinstance(origin, Mapping)
                        or origin.get("primary_ref")
                        != ref_payload(neutral_ref)
                        or origin.get("secondary_ref")
                        != ref_payload(llm_invocation_ref)
                        or response.get("producer_ref")
                        != ref_payload(context.invocation_ref)
                        or success.aggregate_id
                        != str(neutral_ref.entity_id)
                        or success.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or success.payload.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or success.payload.get(
                            "llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or success.payload.get("response_resource_ref")
                        != {
                            "resource_id": str(response_ref.resource_id),
                            "resource_version_id": str(
                                response_ref.resource_version_id)}
                        or success.payload.get("turn_sequence")
                        != local_sequence
                        or completion.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.payload.get("provider_attempt_id")
                        != str(provider_ref.entity_id)
                        or completion.transaction_id
                        != success.transaction_id):
                    raise admission_error(
                        "delegated call lacks one exact terminal closure")
                terminal_ids.append(str(success.event_id))
                continue
            call_loop_ref: VersionRef | None = None
            compacting_loop: Mapping[str, Any] | None = None
            try:
                candidate_loop_ref = ref_from_payload(
                    call_metadata.get("agent_loop_ref"))
                candidate_loop_row = event_store.object_row(
                    candidate_loop_ref.version_id)
                if (candidate_loop_ref.entity_type == "agent_loop/v1"
                        and candidate_loop_ref.version_id.kind
                        == "agent_loop_version"
                        and candidate_loop_row is not None
                        and candidate_loop_row["logical_id"]
                        == str(candidate_loop_ref.entity_id)
                        and candidate_loop_row["object_type"]
                        == "agent_loop/v1"):
                    candidate_loop = json.loads(
                        str(candidate_loop_row["metadata_json"]))
                    if candidate_loop.get("state") == "COMPACTING":
                        call_loop_ref = candidate_loop_ref
                        compacting_loop = candidate_loop
            except (KeyError, TypeError, ValueError,
                    json.JSONDecodeError):
                pass
            turn_events = tuple(
                event for event in invocation_events
                if event.event_type == "agent_turn_recorded/v1"
                and event.payload.get("llm_call_version_id")
                == call_version)
            if turn_events and compacting_loop is None:
                response_versions = {
                    str(event.payload.get("response_version_id", ""))
                    for event in turn_events}
                if len(response_versions) != 1 or "" in response_versions:
                    raise admission_error(
                        "agent turn facts name conflicting responses")
                try:
                    response_version = TypedId.parse(
                        next(iter(response_versions)),
                        expected="resource_version")
                except (TypeError, ValueError) as exc:
                    raise admission_error(
                        "agent turn response version is invalid") from exc
                response_row = event_store.object_row(
                    response_version)
                if (response_row is None
                        or response_row["object_type"]
                        != "resource_version/v1"):
                    raise admission_error(
                        "agent turn response object is missing")
                candidate_ref = VersionRef(
                    "resource_version/v1",
                    TypedId.parse(
                        str(response_row["logical_id"]),
                        expected="resource"),
                    response_version)
                closure = resolve_agent_turn_acceptance(
                    context, candidate_ref)
                terminal_ids.append(str(closure.turn_event.event_id))
                if closure.legacy_adoption_event is not None:
                    terminal_ids.append(str(
                        closure.legacy_adoption_event.event_id))
                continue
            failed_compaction_attempts: list[tuple[
                EventEnvelope, VersionRef, Mapping[str, Any],
                VersionRef, Mapping[str, Any]]] = []
            for failure in invocation_events:
                if failure.event_type != "llm_invocation_failed/v1":
                    continue
                try:
                    neutral_ref = ref_from_payload(
                        failure.payload["llm_invocation_attempt_ref"])
                    neutral = require_ref(
                        neutral_ref, "llm_invocation_attempt/v1",
                        "llm_invocation_attempt_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "failed compaction attempt has malformed neutral "
                        "authority") from exc
                linked = linked_provider_attempt(neutral_ref)
                if linked is None:
                    continue
                provider_ref, provider = linked
                if provider.get("llm_call_ref") == ref_payload(call_ref):
                    failed_compaction_attempts.append((
                        failure, neutral_ref, neutral,
                        provider_ref, provider))
            if failed_compaction_attempts:
                if (len(failed_compaction_attempts) != 1
                        or call_events):
                    raise admission_error(
                        "failed compaction call has conflicting terminal "
                        "ownership")
                (failure, neutral_ref, neutral,
                 provider_ref, provider) = failed_compaction_attempts[0]
                try:
                    llm_invocation_ref = ref_from_payload(
                        neutral["llm_invocation_ref"])
                    llm_invocation = require_ref(
                        llm_invocation_ref, "llm_invocation_spec/v1",
                        "llm_invocation_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "failed compaction call refs are malformed") from exc
                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(provider_ref.entity_id), event_types=(
                            "provider_attempt_completed/v1",)))
                completion = (
                    completions[0] if len(completions) == 1 else None)
                if (completion is None
                        or failure.aggregate_id
                        != str(neutral_ref.entity_id)
                        or failure.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or failure.payload.get("disposition")
                        != "protocol_rejected"
                        or failure.payload.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or failure.payload.get(
                            "llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or failure.payload.get("attempt_ordinal")
                        != neutral.get("attempt_ordinal")
                        or neutral.get("llm_invocation_attempt_ref")
                        != ref_payload(neutral_ref)
                        or llm_invocation.get("llm_invocation_ref")
                        != ref_payload(llm_invocation_ref)
                        or llm_invocation.get("invocation_kind")
                        != "context_compaction"
                        or llm_invocation.get("invocation_ref")
                        != ref_payload(context.invocation_ref)
                        or llm_invocation.get("operation_binding_ref")
                        != ref_payload(context.operation_binding_ref)
                        or provider.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or completion.producer_invocation_id
                        != context.invocation_ref.entity_id
                        or completion.payload.get("provider_attempt_id")
                        != str(provider_ref.entity_id)):
                    raise admission_error(
                        "failed compaction call lacks exact closure")
                terminal_ids.append(str(failure.event_id))
                continue
            if compacting_loop is not None:
                dispositions = tuple(
                    event for event in call_events
                    if event.event_type in terminal_call_events)
                compaction_owners = tuple(
                    (row, compaction)
                    for row, compaction in completed_compaction_rows
                    if isinstance(
                        compaction.get("provider_call_ref"), Mapping)
                    and compaction["provider_call_ref"].get("logical_id")
                    == call_id)
                failed_owners = tuple(
                    (row, compaction)
                    for row, compaction in failed_compaction_rows
                    if isinstance(
                        compaction.get("provider_call_ref"), Mapping)
                    and compaction["provider_call_ref"].get("logical_id")
                    == call_id)
                if failed_owners:
                    if len(failed_owners) != 1 or compaction_owners:
                        raise admission_error(
                            "compaction call has mixed completed and failed ownership")
                    failed_disposition, failed_publication = (
                        failed_compaction_owner(
                            VersionRef(
                                "llm_call_spec/v2",
                                TypedId.parse(
                                    call_id, expected="llm_call"),
                                TypedId.parse(
                                    call_version,
                                    expected="llm_call_version")),
                            call_metadata, call_loop_ref,
                            compacting_loop, dispositions,
                            failed_owners[0]))
                    terminal_ids.extend((
                        str(failed_disposition.event_id),
                        str(failed_publication.event_id)))
                    continue
                if len(compaction_owners) != 1:
                    if (len(dispositions) != 1
                            or dispositions[0].event_type
                            != "llm_call_failed/v1"):
                        raise admission_error(
                            "LLM call lacks one completed compaction owner")
                    predecessor = completed_compaction_retry_predecessor(
                        VersionRef(
                            "llm_call_spec/v2",
                            TypedId.parse(call_id, expected="llm_call"),
                            TypedId.parse(
                                call_version,
                                expected="llm_call_version")),
                        call_metadata, dispositions)
                    terminal_ids.append(str(predecessor.event_id))
                    continue
                compaction_row, compaction = compaction_owners[0]
                try:
                    call_ref = VersionRef(
                        "llm_call_spec/v2",
                        TypedId.parse(call_id, expected="llm_call"),
                        TypedId.parse(
                            call_version, expected="llm_call_version"))
                    owner_call_ref = ref_from_payload(
                        compaction["provider_call_ref"])
                    attempt_ref = ref_from_payload(
                        compaction["provider_attempt_ref"])
                    waiting_loop_ref = ref_from_payload(
                        compaction["agent_loop_ref"])
                    compaction_ref = ref_from_payload(
                        compaction["agent_context_compaction_ref"])
                    attempt = require_ref(
                        attempt_ref, "provider_attempt_spec/v1",
                        "provider_attempt_version")
                    compacting_loop = require_ref(
                        call_loop_ref, "agent_loop/v1",
                        "agent_loop_version")
                    waiting_loop = require_ref(
                        waiting_loop_ref, "agent_loop/v1",
                        "agent_loop_version")
                except (KeyError, TypeError, ValueError) as exc:
                    raise admission_error(
                        "completed compaction owner refs are malformed") from exc
                if (owner_call_ref != call_ref
                        or compaction_ref.entity_type
                        != "agent_context_compaction/v2"
                        or str(compaction_ref.entity_id)
                        != str(compaction_row["logical_id"])
                        or str(compaction_ref.version_id)
                        != str(compaction_row["version_id"])
                        or compaction.get(
                            "agent_context_compaction_id")
                        != str(compaction_ref.entity_id)
                        or compaction.get(
                            "agent_context_compaction_version_id")
                        != str(compaction_ref.version_id)
                        or str(attempt_ref.entity_id) not in attempt_ids
                        or attempt.get("provider_attempt_ref")
                        != ref_payload(attempt_ref)
                        or attempt.get("llm_call_ref")
                        != ref_payload(call_ref)
                        or call_loop_ref.entity_id
                        != waiting_loop_ref.entity_id
                        or call_loop_ref.version_id
                        == waiting_loop_ref.version_id
                        or waiting_loop.get("state")
                        != "WAITING_FOR_LLM"
                        or not isinstance(
                            compacting_loop.get("revision"), int)
                        or isinstance(
                            compacting_loop.get("revision"), bool)
                        or waiting_loop.get("revision")
                        != compacting_loop["revision"] + 1
                        or dispositions):
                    raise admission_error(
                        "completed compaction owner breaks exact call/loop closure")

                completions = tuple(
                    event for event in
                    event_store.list_events_by_aggregate(
                        str(attempt_ref.entity_id),
                        event_types=("provider_attempt_completed/v1",)))
                if len(completions) != 1:
                    raise admission_error(
                        "completed compaction lacks one completed attempt")
                completion = completions[0]

                compaction_publications = tuple(
                    event for event in invocation_events
                    if event.event_type == "object_version_published/v1"
                    and event.payload.get("version_id")
                    == str(compaction_ref.version_id))
                waiting_publications = tuple(
                    event for event in invocation_events
                    if event.event_type == "object_version_published/v1"
                    and event.payload.get("version_id")
                    == str(waiting_loop_ref.version_id))
                expected_transaction = str(
                    compaction_row["transaction_id"])
                waiting_row = event_store.object_row(
                    waiting_loop_ref.version_id)
                if (len(compaction_publications) != 1
                        or len(waiting_publications) != 1
                        or waiting_row is None
                        or waiting_row["transaction_id"]
                        != expected_transaction
                        or str(completion.transaction_id)
                        != expected_transaction):
                    raise admission_error(
                        "completed compaction facts do not share one transaction")
                compaction_publication = compaction_publications[0]
                waiting_publication = waiting_publications[0]
                if (str(compaction_publication.event_id)
                        != str(compaction_row["published_event_id"])
                        or str(waiting_publication.event_id)
                        != str(waiting_row["published_event_id"])
                        or any(
                            str(event.transaction_id)
                            != expected_transaction
                            for event in (
                                compaction_publication,
                                waiting_publication))
                        or compaction_publication.aggregate_id
                        != str(compaction_ref.entity_id)
                        or compaction_publication.aggregate_type
                        != "agent_context_compaction/v2"
                        or compaction_publication.payload.get("logical_id")
                        != str(compaction_ref.entity_id)
                        or compaction_publication.payload.get("object_type")
                        != "agent_context_compaction/v2"
                        or compaction_publication.payload.get("metadata")
                        != compaction
                        or waiting_publication.aggregate_id
                        != str(waiting_loop_ref.entity_id)
                        or waiting_publication.aggregate_type
                        != "agent_loop/v1"
                        or waiting_publication.payload.get("logical_id")
                        != str(waiting_loop_ref.entity_id)
                        or waiting_publication.payload.get("object_type")
                        != "agent_loop/v1"
                        or waiting_publication.payload.get("metadata")
                        != waiting_loop):
                    raise admission_error(
                        "completed compaction publication closure is conflicting")
                terminal_ids.append(str(
                    compaction_publication.event_id))
                continue
        terminal = [event for event in call_events
                    if event.event_type in terminal_call_events]
        if len(terminal) != 1:
            raise admission_error(
                "LLM call terminal cardinality differs from one: "
                f"terminal_count={len(terminal)}; "
                "call_event_types="
                f"{tuple(event.event_type for event in call_events)}")
        terminal_ids.append(str(terminal[0].event_id))
    for attempt_id in attempt_ids:
        attempt_events = [
            event for event in
            event_store.list_events_by_aggregate(attempt_id)
            if event.event_type.startswith("provider_attempt_")]
        if attempt_id == unknown_attempt_id:
            unknown_events = [
                event for event in attempt_events
                if (event.event_type
                    == "provider_attempt_submission_unknown/v1"
                    and event.payload.get(
                        "provider_submission_unknown_ref")
                    == unknown_payload)]
            ordinary_terminals = tuple(
                event for event in attempt_events
                if event.event_type in terminal_attempt_events)
            if len(unknown_events) != 1 or ordinary_terminals:
                raise admission_error(
                    "provider attempt lacks its exact submission-unknown fact")
            terminal_ids.append(str(unknown_events[0].event_id))
            continue
        attempt_terminals = tuple(
            event for event in attempt_events
            if event.event_type in terminal_attempt_events)
        if (len(attempt_terminals) != 1
                or attempt_events[-1] != attempt_terminals[0]):
            raise admission_error(
                "provider attempt terminal closure is not exactly one "
                "final event: "
                f"terminal_count={len(attempt_terminals)}; "
                "attempt_event_types="
                f"{tuple(event.event_type for event in attempt_events)}")
        terminal_ids.append(str(attempt_terminals[0].event_id))
    return tuple(sorted(terminal_ids))
