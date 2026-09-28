"""Transactional runtime for the isolated same-Registry execution net."""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any, Mapping

from ._registry import _RegistryCore
from .event_store import RegistryConflict
from .execution_net import (
    ExecutionCheckpoint,
    ExecutionInputArc,
    ExecutionNetDefinition,
    ExecutionNetError,
    ExecutionOutputArc,
    ExecutionParentAuthority,
    ExecutionRecovery,
    ExecutionState,
    ExecutionToken,
    ExecutionTransition,
    execution_child_stream_id,
)
from .identities import TypedId, stable_execution_id
from .models import PendingEvent, TypedRelation, VersionRef
from .schema_catalog import canonical_json


_OBJECT_TYPES = {
    "execution_net_definition/v1",
    "execution_instance/v1",
    "execution_token/v1",
    "execution_transition_firing/v1",
    "execution_checkpoint/v1",
}


def _ref(ref: VersionRef | None) -> dict[str, str] | None:
    if ref is None:
        return None
    return {
        "entity_type": ref.entity_type,
        "logical_id": str(ref.entity_id),
        "version_id": str(ref.version_id),
    }


def _from_ref(value: object, *, expected_type: str | None = None) -> VersionRef:
    if not isinstance(value, Mapping):
        raise ExecutionNetError("execution authority requires an exact ref")
    try:
        ref = VersionRef(
            str(value["entity_type"]),
            TypedId.parse(str(value["logical_id"])),
            TypedId.parse(str(value["version_id"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionNetError("execution authority has a malformed ref") from exc
    if expected_type is not None and ref.entity_type != expected_type:
        raise ExecutionNetError(
            f"expected {expected_type}, received {ref.entity_type}")
    return ref


def _relation_id(command_key: str, label: str) -> TypedId:
    return TypedId(
        "relation",
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"d1-c:execution-relation:{command_key}:{label}",
        ).hex,
    )


class ExecutionRuntime:
    """Persist and hydrate an execution net beneath one business invocation."""

    def __init__(self, core: _RegistryCore) -> None:
        if not isinstance(core, _RegistryCore):
            raise TypeError("execution runtime requires one Registry core")
        self.core = core

    def _load(self, ref: VersionRef, expected_type: str,
              *, parent_invocation_id: TypedId | None = None,
              ) -> dict[str, Any]:
        if not isinstance(ref, VersionRef) or ref.entity_type != expected_type:
            raise ExecutionNetError(
                f"execution object requires one exact {expected_type} ref")
        prepared = self.core.get_version(ref.version_id)
        if (prepared.object_type != expected_type
                or prepared.logical_id != ref.entity_id
                or (parent_invocation_id is not None
                    and prepared.producer_invocation_id
                    != parent_invocation_id)):
            raise ExecutionNetError(
                f"execution exact reference differs: {ref.version_id}")
        payload = self.core.object_store.read_registered(prepared)
        if payload != canonical_json(prepared.metadata):
            raise ExecutionNetError(
                f"execution object bytes differ: {ref.version_id}")
        return dict(prepared.metadata)

    def _validate_parent(
            self, parent: ExecutionParentAuthority, *, require_open: bool,
            ) -> dict[str, Any]:
        if not isinstance(parent, ExecutionParentAuthority):
            raise TypeError("execution runtime requires parent authority")
        invocation = self._load(parent.invocation_ref, "invocation/v1")
        firing = self._load(parent.business_firing_ref, "transition_firing/v1")
        net = self._load(parent.business_net_ref, "net_instance/v1")
        checkpoint = self._load(
            parent.business_checkpoint_ref, "marking_checkpoint/v1")
        if (invocation.get("invocation_ref") != _ref(parent.invocation_ref)
                or invocation.get("own_transition_firing_ref")
                != _ref(parent.business_firing_ref)
                or invocation.get("net_instance_ref")
                != _ref(parent.business_net_ref)
                or invocation.get("admission_marking_checkpoint_ref")
                != _ref(parent.business_checkpoint_ref)
                or firing.get("transition_firing_ref")
                != _ref(parent.business_firing_ref)
                or firing.get("net_instance_ref")
                != _ref(parent.business_net_ref)
                or firing.get("admission_marking_checkpoint_ref")
                != _ref(parent.business_checkpoint_ref)
                or net.get("net_instance_ref") != _ref(parent.business_net_ref)
                or checkpoint.get("marking_checkpoint_ref")
                != _ref(parent.business_checkpoint_ref)
                or checkpoint.get("net_instance_ref")
                != _ref(parent.business_net_ref)):
            raise ExecutionNetError(
                "execution parent refs cross business invocation authority")
        if require_open:
            publication = self.core.event_store.firing_publication_row(
                parent.business_firing_ref.version_id)
            if (publication is None
                    or publication["state"] != "PROVISIONAL"
                    or publication["invocation_logical_id"]
                    != str(parent.invocation_ref.entity_id)
                    or publication["invocation_version_id"]
                    != str(parent.invocation_ref.version_id)
                    or publication["net_version_id"]
                    != str(parent.business_net_ref.version_id)
                    or publication["admission_checkpoint_version_id"]
                    != str(parent.business_checkpoint_ref.version_id)):
                raise ExecutionNetError(
                    "execution parent business firing is not exact and open")
        return invocation

    @staticmethod
    def _definition_metadata(
            definition_ref: VersionRef,
            definition: ExecutionNetDefinition) -> dict[str, Any]:
        return {
            "execution_net_definition_ref": _ref(definition_ref),
            "definition_key": definition.definition_key,
            "places": list(definition.places),
            "transitions": [
                {"transition_id": item.transition_id,
                 "recovery_mode": item.recovery_mode}
                for item in definition.transitions
            ],
            "input_arcs": [
                {"place": item.place, "transition_id": item.transition_id,
                 "weight": item.weight}
                for item in definition.input_arcs
            ],
            "output_arcs": [
                {"transition_id": item.transition_id, "place": item.place,
                 "weight": item.weight}
                for item in definition.output_arcs
            ],
            "initial_place": definition.initial_place,
            "initial_tokens": definition.initial_tokens,
            "terminal_places": list(definition.terminal_places),
        }

    @staticmethod
    def _definition_from_metadata(
            metadata: Mapping[str, Any]) -> ExecutionNetDefinition:
        try:
            return ExecutionNetDefinition(
                definition_key=str(metadata["definition_key"]),
                places=tuple(str(value) for value in metadata["places"]),
                transitions=tuple(ExecutionTransition(
                    str(value["transition_id"]),
                    str(value["recovery_mode"]),  # type: ignore[arg-type]
                ) for value in metadata["transitions"]),
                input_arcs=tuple(ExecutionInputArc(
                    str(value["place"]), str(value["transition_id"]),
                    value["weight"],
                ) for value in metadata["input_arcs"]),
                output_arcs=tuple(ExecutionOutputArc(
                    str(value["transition_id"]), str(value["place"]),
                    value["weight"],
                ) for value in metadata["output_arcs"]),
                initial_place=str(metadata["initial_place"]),
                initial_tokens=metadata["initial_tokens"],
                terminal_places=tuple(
                    str(value) for value in metadata["terminal_places"]),
            )
        except (KeyError, TypeError) as exc:
            raise ExecutionNetError(
                "execution definition metadata is malformed") from exc

    @staticmethod
    def _command_key(
            instance_ref: VersionRef, action: str, caller_key: str) -> str:
        if action not in {"instantiate", "start", "settle"}:
            raise ExecutionNetError("execution command action is unsupported")
        if not isinstance(caller_key, str) or not caller_key:
            raise ExecutionNetError("execution command key is required")
        return f"execution:{instance_ref.version_id}:{action}:{caller_key}"

    def _prewrite(self, tx: Any, object_type: str, ref: VersionRef,
                  metadata: Mapping[str, Any], parent: ExecutionParentAuthority,
                  ) -> None:
        if object_type not in _OBJECT_TYPES:
            raise ExecutionNetError("unregistered execution object type")
        self.core.catalog.validate_instance(
            object_type, category="object", instance=metadata)
        tx.prewrite(
            object_type=object_type,
            logical_id=ref.entity_id,
            version_id=ref.version_id,
            payload=canonical_json(metadata),
            metadata=metadata,
            media_type="application/json",
            schema_ref=f"registry_v1/{object_type}",
            producer_invocation_id=parent.invocation_ref.entity_id,
        )

    def instantiate(
            self, *, parent: ExecutionParentAuthority,
            definition: ExecutionNetDefinition, idempotency_key: str,
            ) -> ExecutionState:
        if self.core.read_only:
            raise ExecutionNetError("read-only Registry cannot instantiate execution")
        if not isinstance(definition, ExecutionNetDefinition):
            raise TypeError("execution instantiate requires a strict definition")
        invocation = self._validate_parent(parent, require_open=False)
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ExecutionNetError("execution instantiate key is required")
        instance_id = stable_execution_id(
            "execution_instance", parent.invocation_ref.version_id,
            idempotency_key)
        instance_ref = VersionRef(
            "execution_instance/v1", instance_id,
            stable_execution_id(
                "execution_instance_version", instance_id, idempotency_key),
        )
        command_key = self._command_key(
            instance_ref, "instantiate", idempotency_key)
        if not self.core.event_store.list_events_by_idempotency_key(command_key):
            invocation = self._validate_parent(parent, require_open=True)
        definition_ref = VersionRef(
            "execution_net_definition/v1",
            stable_execution_id(
                "execution_net_definition", instance_ref.version_id,
                definition.definition_key),
            stable_execution_id(
                "execution_net_definition_version", instance_ref.version_id,
                definition.definition_key),
        )
        checkpoint_logical_id = stable_execution_id(
            "execution_checkpoint", instance_ref.version_id)
        checkpoint_ref = VersionRef(
            "execution_checkpoint/v1", checkpoint_logical_id,
            stable_execution_id(
                "execution_checkpoint_version", instance_ref.version_id,
                command_key),
        )
        token_refs = tuple(VersionRef(
            "execution_token/v1",
            stable_execution_id(
                "execution_token", instance_ref.version_id, str(ordinal)),
            stable_execution_id(
                "execution_token_version", instance_ref.version_id,
                str(ordinal)),
        ) for ordinal in range(definition.initial_tokens))
        definition_metadata = self._definition_metadata(
            definition_ref, definition)
        instance_metadata = {
            "execution_instance_ref": _ref(instance_ref),
            "execution_net_definition_ref": _ref(definition_ref),
            "parent_invocation_ref": _ref(parent.invocation_ref),
            "parent_business_firing_ref": _ref(parent.business_firing_ref),
            "parent_business_net_ref": _ref(parent.business_net_ref),
            "parent_business_checkpoint_ref": _ref(
                parent.business_checkpoint_ref),
            "initial_checkpoint_ref": _ref(checkpoint_ref),
        }
        token_metadata = tuple({
            "execution_token_ref": _ref(ref),
            "execution_instance_ref": _ref(instance_ref),
            "place": definition.initial_place,
            "ordinal": ordinal,
            "produced_by_firing_ref": None,
        } for ordinal, ref in enumerate(token_refs))
        checkpoint_metadata = {
            "execution_checkpoint_ref": _ref(checkpoint_ref),
            "execution_instance_ref": _ref(instance_ref),
            "execution_net_definition_ref": _ref(definition_ref),
            "sequence": 0,
            "previous_checkpoint_ref": None,
            "transition_firing_ref": None,
            "token_refs": [_ref(ref) for ref in token_refs],
            "active_firing_refs": [],
            "evidence_refs": [],
            "next_token_ordinal": definition.initial_tokens,
            "status": "running",
        }
        task_round_ref = _from_ref(
            invocation.get("task_round_ref"), expected_type="task_round/v1")
        tx = self.core.begin(
            idempotency_key=command_key,
            task_round_id=task_round_ref.entity_id,
            net_instance_id=parent.business_net_ref.entity_id,
        )
        self._prewrite(tx, "execution_net_definition/v1", definition_ref,
                       definition_metadata, parent)
        self._prewrite(tx, "execution_instance/v1", instance_ref,
                       instance_metadata, parent)
        for ref, metadata in zip(token_refs, token_metadata, strict=True):
            self._prewrite(tx, "execution_token/v1", ref, metadata, parent)
        self._prewrite(tx, "execution_checkpoint/v1", checkpoint_ref,
                       checkpoint_metadata, parent)
        tx.append(PendingEvent(
            event_type="execution_instance_attached/v1",
            criticality="authoritative",
            stream_id=execution_child_stream_id(parent),
            aggregate_id=str(parent.business_firing_ref.entity_id),
            aggregate_type="execution_child_set",
            idempotency_key=command_key,
            command_id=command_key,
            payload={
                "parent_invocation_ref": _ref(parent.invocation_ref),
                "parent_business_firing_ref": _ref(
                    parent.business_firing_ref),
                "execution_instance_ref": _ref(instance_ref),
                "initial_checkpoint_ref": _ref(checkpoint_ref),
            },
            payload_schema_ref=(
                "registry_v1/execution_instance_attached/v1"),
            producer_invocation_id=parent.invocation_ref.entity_id,
        ))
        for label, target in (
                ("definition", definition_ref),
                ("parent-invocation", parent.invocation_ref),
                ("parent-firing", parent.business_firing_ref),
                ("parent-net", parent.business_net_ref),
                ("parent-checkpoint", parent.business_checkpoint_ref)):
            tx.relate(TypedRelation(
                relation_id=_relation_id(command_key, label),
                relation_type="derived_from",
                source=instance_ref,
                target=target,
                metadata={"execution_link": label},
                producer_invocation_id=parent.invocation_ref.entity_id,
            ))
        tx.commit()
        return self.hydrate(instance_ref, parent=parent)

    def _instance(
            self, instance_ref: VersionRef,
            parent: ExecutionParentAuthority | None,
            ) -> tuple[dict[str, Any], ExecutionParentAuthority,
                       VersionRef, ExecutionNetDefinition]:
        metadata = self._load(instance_ref, "execution_instance/v1")
        stored_parent = ExecutionParentAuthority(
            invocation_ref=_from_ref(
                metadata.get("parent_invocation_ref"),
                expected_type="invocation/v1"),
            business_firing_ref=_from_ref(
                metadata.get("parent_business_firing_ref"),
                expected_type="transition_firing/v1"),
            business_net_ref=_from_ref(
                metadata.get("parent_business_net_ref"),
                expected_type="net_instance/v1"),
            business_checkpoint_ref=_from_ref(
                metadata.get("parent_business_checkpoint_ref"),
                expected_type="marking_checkpoint/v1"),
        )
        if parent is not None and parent != stored_parent:
            raise ExecutionNetError(
                "execution instance belongs to another parent invocation")
        metadata = self._load(
            instance_ref, "execution_instance/v1",
            parent_invocation_id=stored_parent.invocation_ref.entity_id)
        if metadata.get("execution_instance_ref") != _ref(instance_ref):
            raise ExecutionNetError("execution instance self ref differs")
        definition_ref = _from_ref(
            metadata.get("execution_net_definition_ref"),
            expected_type="execution_net_definition/v1")
        definition_metadata = self._load(
            definition_ref, "execution_net_definition/v1",
            parent_invocation_id=stored_parent.invocation_ref.entity_id)
        if definition_metadata.get(
                "execution_net_definition_ref") != _ref(definition_ref):
            raise ExecutionNetError("execution definition self ref differs")
        definition = self._definition_from_metadata(definition_metadata)
        return metadata, stored_parent, definition_ref, definition

    def _checkpoint(
            self, *, instance_ref: VersionRef, definition_ref: VersionRef,
            definition: ExecutionNetDefinition,
            parent: ExecutionParentAuthority, checkpoint_ref: VersionRef,
            ) -> ExecutionCheckpoint:
        metadata = self._load(
            checkpoint_ref, "execution_checkpoint/v1",
            parent_invocation_id=parent.invocation_ref.entity_id)
        if (metadata.get("execution_checkpoint_ref") != _ref(checkpoint_ref)
                or metadata.get("execution_instance_ref") != _ref(instance_ref)
                or metadata.get("execution_net_definition_ref")
                != _ref(definition_ref)):
            raise ExecutionNetError("execution checkpoint lineage differs")
        token_refs = tuple(
            _from_ref(value, expected_type="execution_token/v1")
            for value in metadata.get("token_refs", ()))
        active_refs = tuple(
            _from_ref(
                value, expected_type="execution_transition_firing/v1")
            for value in metadata.get("active_firing_refs", ()))
        evidence_refs = tuple(
            _from_ref(value) for value in metadata.get("evidence_refs", ()))
        if (len(set(token_refs)) != len(token_refs)
                or len(set(active_refs)) != len(active_refs)
                or len(set(evidence_refs)) != len(evidence_refs)):
            raise ExecutionNetError(
                "execution checkpoint repeats tokens or active firings")
        tokens: list[ExecutionToken] = []
        for token_ref in token_refs:
            token = self._load(
                token_ref, "execution_token/v1",
                parent_invocation_id=parent.invocation_ref.entity_id)
            produced = (
                _from_ref(token["produced_by_firing_ref"],
                          expected_type="execution_transition_firing/v1")
                if token.get("produced_by_firing_ref") is not None else None)
            if (token.get("execution_token_ref") != _ref(token_ref)
                    or token.get("execution_instance_ref")
                    != _ref(instance_ref)
                    or token.get("place") not in definition.places):
                raise ExecutionNetError("execution token lineage differs")
            ordinal = token.get("ordinal")
            if isinstance(ordinal, bool) or not isinstance(ordinal, int):
                raise ExecutionNetError("execution token ordinal is malformed")
            tokens.append(ExecutionToken(
                token_ref, str(token["place"]), ordinal, produced))
        tokens.sort(key=lambda item: item.ordinal)
        if len({item.ordinal for item in tokens}) != len(tokens):
            raise ExecutionNetError("execution token ordinals are not unique")
        sequence = metadata.get("sequence")
        next_ordinal = metadata.get("next_token_ordinal")
        status = metadata.get("status")
        if (isinstance(sequence, bool) or not isinstance(sequence, int)
                or isinstance(next_ordinal, bool)
                or not isinstance(next_ordinal, int)
                or any(item.ordinal >= next_ordinal for item in tokens)
                or status not in {"running", "map_ready"}):
            raise ExecutionNetError("execution checkpoint counters are malformed")
        expected_map_ready = (
            not active_refs and bool(tokens)
            and all(item.place in definition.terminal_places for item in tokens))
        if (status == "map_ready") != expected_map_ready:
            raise ExecutionNetError(
                "execution checkpoint terminal classification differs")
        previous = (
            _from_ref(metadata["previous_checkpoint_ref"],
                      expected_type="execution_checkpoint/v1")
            if metadata.get("previous_checkpoint_ref") is not None else None)
        firing = (
            _from_ref(metadata["transition_firing_ref"],
                      expected_type="execution_transition_firing/v1")
            if metadata.get("transition_firing_ref") is not None else None)
        return ExecutionCheckpoint(
            checkpoint_ref=checkpoint_ref,
            sequence=sequence,
            previous_checkpoint_ref=previous,
            transition_firing_ref=firing,
            tokens=tuple(tokens),
            active_firing_refs=active_refs,
            evidence_refs=evidence_refs,
            next_token_ordinal=next_ordinal,
            status=status,
        )

    def hydrate(
            self, instance_ref: VersionRef, *,
            parent: ExecutionParentAuthority | None = None,
            ) -> ExecutionState:
        instance, stored_parent, definition_ref, definition = self._instance(
            instance_ref, parent)
        self._validate_parent(stored_parent, require_open=False)
        initial_checkpoint_ref = _from_ref(
            instance.get("initial_checkpoint_ref"),
            expected_type="execution_checkpoint/v1")
        checkpoint_ref = self._checkpoint_head(
            initial_checkpoint_ref.entity_id)
        checkpoint = self._checkpoint(
            instance_ref=instance_ref, definition_ref=definition_ref,
            definition=definition, parent=stored_parent,
            checkpoint_ref=checkpoint_ref)
        return ExecutionState(
            instance_ref=instance_ref,
            definition_ref=definition_ref,
            parent=stored_parent,
            definition=definition,
            checkpoint=checkpoint,
        )

    def _checkpoint_head(
            self, checkpoint_id: TypedId, *,
            through_ordinal: int | None = None) -> VersionRef:
        events = self.core.event_store.list_events_by_aggregate(
            str(checkpoint_id), event_types=("object_version_published/v1",))
        matching = tuple(
            event for event in events
            if (through_ordinal is None
                or (event.ordinal is not None
                    and event.ordinal <= through_ordinal))
            and event.payload.get("logical_id") == str(checkpoint_id)
            and event.payload.get("object_type")
            == "execution_checkpoint/v1")
        if not matching:
            raise ExecutionNetError("execution instance has no checkpoint head")
        return VersionRef(
            "execution_checkpoint/v1", checkpoint_id,
            TypedId.parse(
                str(matching[-1].payload["version_id"]),
                expected="execution_checkpoint_version"),
        )

    def _assert_checkpoint_predecessor(
            self, tx: Any, checkpoint_ref: VersionRef,
            command_key: str) -> None:
        if self.core.event_store.list_events_by_idempotency_key(command_key):
            return
        current = self._checkpoint_head(
            checkpoint_ref.entity_id,
            through_ordinal=tx._initial_ordinal)
        if current != checkpoint_ref:
            raise RegistryConflict("execution checkpoint predecessor is stale")

    def enabled_transitions(self, state: ExecutionState) -> tuple[str, ...]:
        if not isinstance(state, ExecutionState):
            raise TypeError("enabled transition query requires execution state")
        if state.checkpoint.map_ready:
            return ()
        marking = Counter(token.place for token in state.checkpoint.tokens)
        return tuple(
            transition.transition_id
            for transition in state.definition.transitions
            if all(marking[arc.place] >= arc.weight
                   for arc in state.definition.inputs_for(
                       transition.transition_id))
        )

    def start(
            self, *, instance_ref: VersionRef,
            parent: ExecutionParentAuthority,
            checkpoint_ref: VersionRef, transition_id: str,
            idempotency_key: str,
            materialization_key: str | None = None,
            ) -> ExecutionState:
        if self.core.read_only:
            raise ExecutionNetError("read-only Registry cannot start execution")
        invocation = self._validate_parent(parent, require_open=False)
        _, stored_parent, definition_ref, definition = self._instance(
            instance_ref, parent)
        if stored_parent != parent:
            raise ExecutionNetError("execution parent changed")
        checkpoint = self._checkpoint(
            instance_ref=instance_ref, definition_ref=definition_ref,
            definition=definition, parent=parent,
            checkpoint_ref=checkpoint_ref)
        if checkpoint.map_ready:
            raise ExecutionNetError(
                "execution transition requires a running checkpoint")
        transition = definition.transition(transition_id)
        if transition.recovery_mode == "pure":
            if materialization_key is not None:
                raise ExecutionNetError(
                    "pure execution cannot carry a materialization key")
        elif not isinstance(materialization_key, str) or not materialization_key:
            raise ExecutionNetError(
                "idempotent materialization requires its stable key")
        command_key = self._command_key(instance_ref, "start", idempotency_key)
        if not self.core.event_store.list_events_by_idempotency_key(command_key):
            invocation = self._validate_parent(parent, require_open=True)
        firing_logical_id = stable_execution_id(
            "execution_transition_firing", instance_ref.version_id,
            command_key)
        active_firing_ref = VersionRef(
            "execution_transition_firing/v1", firing_logical_id,
            stable_execution_id(
                "execution_transition_firing_version", firing_logical_id,
                "active", command_key),
        )
        checkpoint_successor_ref = VersionRef(
            "execution_checkpoint/v1", checkpoint_ref.entity_id,
            stable_execution_id(
                "execution_checkpoint_version", instance_ref.version_id,
                "start", active_firing_ref.version_id),
        )
        available: dict[str, list[ExecutionToken]] = {}
        for token in checkpoint.tokens:
            available.setdefault(token.place, []).append(token)
        consumed: list[ExecutionToken] = []
        for arc in definition.inputs_for(transition_id):
            candidates = available.get(arc.place, ())
            if len(candidates) < arc.weight:
                raise ExecutionNetError(
                    f"execution transition {transition_id!r} is not enabled")
            consumed.extend(candidates[:arc.weight])
        consumed_refs = {item.token_ref for item in consumed}
        remaining = tuple(
            token.token_ref for token in checkpoint.tokens
            if token.token_ref not in consumed_refs)
        firing_metadata = {
            "execution_transition_firing_ref": _ref(active_firing_ref),
            "execution_instance_ref": _ref(instance_ref),
            "execution_net_definition_ref": _ref(definition_ref),
            "predecessor_checkpoint_ref": _ref(checkpoint_ref),
            "transition_id": transition_id,
            "recovery_mode": transition.recovery_mode,
            "materialization_key": materialization_key,
            "input_token_refs": [_ref(item.token_ref) for item in consumed],
            "status": "active",
            "active_firing_ref": None,
            "settled_checkpoint_ref": None,
            "output_token_refs": [],
        }
        checkpoint_metadata = {
            "execution_checkpoint_ref": _ref(checkpoint_successor_ref),
            "execution_instance_ref": _ref(instance_ref),
            "execution_net_definition_ref": _ref(definition_ref),
            "sequence": checkpoint.sequence + 1,
            "previous_checkpoint_ref": _ref(checkpoint_ref),
            "transition_firing_ref": _ref(active_firing_ref),
            "token_refs": [_ref(ref) for ref in remaining],
            "active_firing_refs": [
                *[_ref(ref) for ref in checkpoint.active_firing_refs],
                _ref(active_firing_ref),
            ],
            "evidence_refs": [
                _ref(ref) for ref in checkpoint.evidence_refs],
            "next_token_ordinal": checkpoint.next_token_ordinal,
            "status": "running",
        }
        task_round_ref = _from_ref(
            invocation.get("task_round_ref"), expected_type="task_round/v1")
        tx = self.core.begin(
            idempotency_key=command_key,
            task_round_id=task_round_ref.entity_id,
            net_instance_id=parent.business_net_ref.entity_id,
        )
        self._prewrite(tx, "execution_transition_firing/v1",
                       active_firing_ref, firing_metadata, parent)
        self._prewrite(tx, "execution_checkpoint/v1",
                       checkpoint_successor_ref, checkpoint_metadata, parent)
        self._assert_checkpoint_predecessor(
            tx, checkpoint_ref, command_key)
        tx.commit()
        return self.hydrate(instance_ref, parent=parent)

    def _active_firing(
            self, *, instance_ref: VersionRef, definition_ref: VersionRef,
            parent: ExecutionParentAuthority, firing_ref: VersionRef,
            ) -> dict[str, Any]:
        firing = self._load(
            firing_ref, "execution_transition_firing/v1",
            parent_invocation_id=parent.invocation_ref.entity_id)
        if (firing.get("execution_transition_firing_ref") != _ref(firing_ref)
                or firing.get("execution_instance_ref") != _ref(instance_ref)
                or firing.get("execution_net_definition_ref")
                != _ref(definition_ref)
                or firing.get("status") != "active"):
            raise ExecutionNetError("execution active firing lineage differs")
        return firing

    def classify_active_firings(
            self, state: ExecutionState) -> tuple[ExecutionRecovery, ...]:
        if not isinstance(state, ExecutionState):
            raise TypeError("recovery classification requires execution state")
        result: list[ExecutionRecovery] = []
        for firing_ref in state.checkpoint.active_firing_refs:
            firing = self._active_firing(
                instance_ref=state.instance_ref,
                definition_ref=state.definition_ref,
                parent=state.parent, firing_ref=firing_ref)
            mode = firing["recovery_mode"]
            key = firing.get("materialization_key")
            result.append(ExecutionRecovery(
                firing_ref=firing_ref,
                transition_id=str(firing["transition_id"]),
                recovery_mode=mode,
                action=("replay_pure" if mode == "pure"
                        else "retry_same_materialization"),
                materialization_key=key,
            ))
        return tuple(result)

    def settle(
            self, *, instance_ref: VersionRef,
            parent: ExecutionParentAuthority,
            checkpoint_ref: VersionRef, firing_ref: VersionRef,
            idempotency_key: str,
            evidence_refs: tuple[VersionRef, ...] = (),
            ) -> ExecutionState:
        if self.core.read_only:
            raise ExecutionNetError("read-only Registry cannot settle execution")
        invocation = self._validate_parent(parent, require_open=False)
        _, _, definition_ref, definition = self._instance(
            instance_ref, parent)
        checkpoint = self._checkpoint(
            instance_ref=instance_ref, definition_ref=definition_ref,
            definition=definition, parent=parent,
            checkpoint_ref=checkpoint_ref)
        if firing_ref not in checkpoint.active_firing_refs:
            raise ExecutionNetError(
                "execution settlement requires the checkpoint's active firing")
        active = self._active_firing(
            instance_ref=instance_ref, definition_ref=definition_ref,
            parent=parent, firing_ref=firing_ref)
        if (not isinstance(evidence_refs, tuple)
                or any(not isinstance(ref, VersionRef)
                       for ref in evidence_refs)
                or len(set(evidence_refs)) != len(evidence_refs)):
            raise ExecutionNetError(
                "execution evidence must be unique exact Registry refs")
        for evidence_ref in evidence_refs:
            prepared = self.core.get_version(evidence_ref.version_id)
            if (prepared.object_type != evidence_ref.entity_type
                    or prepared.logical_id != evidence_ref.entity_id
                    or prepared.producer_invocation_id
                    != parent.invocation_ref.entity_id):
                raise ExecutionNetError(
                    "execution evidence crosses its parent authority")
            self.core.object_store.read_registered(prepared)
        transition_id = str(active["transition_id"])
        outputs = definition.outputs_for(transition_id)
        command_key = self._command_key(instance_ref, "settle", idempotency_key)
        if not self.core.event_store.list_events_by_idempotency_key(command_key):
            invocation = self._validate_parent(parent, require_open=True)
        settled_firing_ref = VersionRef(
            "execution_transition_firing/v1", firing_ref.entity_id,
            stable_execution_id(
                "execution_transition_firing_version", firing_ref.entity_id,
                "settled", firing_ref.version_id,
                checkpoint_ref.version_id, command_key),
        )
        produced: list[tuple[VersionRef, dict[str, Any]]] = []
        ordinal = checkpoint.next_token_ordinal
        for arc in outputs:
            for output_ordinal in range(arc.weight):
                token_ref = VersionRef(
                    "execution_token/v1",
                    stable_execution_id(
                        "execution_token", firing_ref.version_id,
                        arc.place, str(output_ordinal)),
                    stable_execution_id(
                        "execution_token_version", firing_ref.version_id,
                        arc.place, str(output_ordinal),
                        checkpoint_ref.version_id, command_key),
                )
                produced.append((token_ref, {
                    "execution_token_ref": _ref(token_ref),
                    "execution_instance_ref": _ref(instance_ref),
                    "place": arc.place,
                    "ordinal": ordinal,
                    "produced_by_firing_ref": _ref(settled_firing_ref),
                }))
                ordinal += 1
        successor_tokens = tuple(
            token.token_ref for token in checkpoint.tokens) + tuple(
                ref for ref, _ in produced)
        remaining_active_refs = tuple(
            ref for ref in checkpoint.active_firing_refs
            if ref != firing_ref)
        successor_evidence_refs = tuple(dict.fromkeys(
            checkpoint.evidence_refs + evidence_refs))
        places = [token.place for token in checkpoint.tokens] + [
            str(metadata["place"]) for _, metadata in produced]
        map_ready = (not remaining_active_refs and bool(places)
                     and all(place in definition.terminal_places
                             for place in places))
        status = "map_ready" if map_ready else "running"
        successor_ref = VersionRef(
            "execution_checkpoint/v1", checkpoint_ref.entity_id,
            stable_execution_id(
                "execution_checkpoint_version", instance_ref.version_id,
                "settle", firing_ref.version_id,
                checkpoint_ref.version_id, command_key),
        )
        settled_metadata = {
            **active,
            "execution_transition_firing_ref": _ref(settled_firing_ref),
            "status": "settled",
            "active_firing_ref": _ref(firing_ref),
            "settled_checkpoint_ref": _ref(successor_ref),
            "output_token_refs": [_ref(ref) for ref, _ in produced],
        }
        checkpoint_metadata = {
            "execution_checkpoint_ref": _ref(successor_ref),
            "execution_instance_ref": _ref(instance_ref),
            "execution_net_definition_ref": _ref(definition_ref),
            "sequence": checkpoint.sequence + 1,
            "previous_checkpoint_ref": _ref(checkpoint_ref),
            "transition_firing_ref": _ref(settled_firing_ref),
            "token_refs": [_ref(ref) for ref in successor_tokens],
            "active_firing_refs": [
                _ref(ref) for ref in remaining_active_refs],
            "evidence_refs": [
                _ref(ref) for ref in successor_evidence_refs],
            "next_token_ordinal": ordinal,
            "status": status,
        }
        task_round_ref = _from_ref(
            invocation.get("task_round_ref"), expected_type="task_round/v1")
        tx = self.core.begin(
            idempotency_key=command_key,
            task_round_id=task_round_ref.entity_id,
            net_instance_id=parent.business_net_ref.entity_id,
        )
        for ref, metadata in produced:
            self._prewrite(tx, "execution_token/v1", ref, metadata, parent)
        self._prewrite(tx, "execution_transition_firing/v1",
                       settled_firing_ref, settled_metadata, parent)
        self._prewrite(tx, "execution_checkpoint/v1", successor_ref,
                       checkpoint_metadata, parent)
        self._assert_checkpoint_predecessor(
            tx, checkpoint_ref, command_key)
        tx.commit()
        return self.hydrate(instance_ref, parent=parent)


__all__ = ("ExecutionRuntime",)
