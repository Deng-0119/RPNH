"""Owner-only facts and managed receipts for one explicitly admitted tool program.

The isolated runner and all capacity/worker waits belong outside this mixin.
Program records describe the original action; they confer no execution authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import time

from cpn.plugins.api import PluginError, canonical, json_copy
from cpn.plugins.controlled_script import (
    IsolatedProgramBudget, IsolatedProgramResult, ProgramBrokerCall, ProgramBrokerReply,
)
from cpn.plugins.managed_tools import (
    ManagedInvocationObservation, ManagedPluginInvocationConflict,
    ManagedPluginInvocationFailed, ManagedPluginInvocationReconciliationRequired,
    ManagedWorkerCompletion, PreparedManagedInvocation,
)
from cpn.rpnh.registry.errors import ResourceIntegrityFault
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import (
    _ref_payload, _resource_from_payload, _stable_id, _version_from_payload,
)
from cpn.rpnh.registry.resource_service import _resource_payload
from cpn.rpnh.registry.resources import PublishResource, WorkspaceWriteOrigin
from cpn.rpnh.registry.schema_catalog import TypeDefinition
from .managed_output import _fit_page, serialized_managed_json
from .models import AgentLoopState
from .tool_validation import ValidatedAgentToolAction


PROGRAM_TYPE = "agent_tool_program_invocation/v1"
CALL_TYPE = "agent_tool_program_call/v1"
PROGRAM_TOOL = "run_tool_program"
PROGRAM_READER = "read_tool_program_output"
CHILD_READER = "read_program_child_output"


def _bytes(value):
    return serialized_managed_json(json_copy(value)).encode("utf-8")


def program_execution_schema_data():
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    schemas, types = {}, []
    for name in (PROGRAM_TYPE, CALL_TYPE):
        family, version = name.split("/")
        document = json.loads((root / f"{family}.{version}.schema.json").read_text())
        schemas[document["$id"]] = document
        types.append(TypeDefinition(
            name, "object", "optional-agent-loop", document["$id"], None,
            "writer-only", "permanent", "registry-reference-replay",
            "exact-schema-and-reference-validation"))
    return schemas, tuple(types)


def validate_program_policy(policy):
    value = json_copy(policy)
    if (not isinstance(value, dict) or set(value) != {"profile_id", "tools", "budget"}
            or value["profile_id"] != "linux_isolated_python/v1"
            or not isinstance(value["tools"], list) or not value["tools"]
            or any(not isinstance(n, str) or not n for n in value["tools"])
            or value["tools"] != sorted(set(value["tools"]))
            or not isinstance(value["budget"], dict)
            or set(value["budget"]) != set(IsolatedProgramBudget.__dataclass_fields__)):
        raise ValueError("tool program policy differs from its frozen HOST contract")
    return value, IsolatedProgramBudget(**value["budget"])


@dataclass(frozen=True, slots=True)
class PreparedToolProgram:
    program_ref: VersionRef
    parent_identity: Mapping
    source: str
    arguments: object
    allowlist: tuple[str, ...]
    budget: IsolatedProgramBudget


@dataclass(frozen=True, slots=True)
class PreparedProgramChild:
    child_ref: VersionRef
    managed_prepared: PreparedManagedInvocation | ManagedInvocationObservation | None
    closed_reply: ProgramBrokerReply | None = None


@dataclass(frozen=True, slots=True)
class CompletedToolProgram:
    result_refs: tuple[VersionRef, ...]
    metadata: Mapping
    block_authority: object = None


class ProgramExecutionMixin:
    def _program_service(self, loop):
        _binding, _compiled, operation = self._declared(self._context(loop))
        service = self.managed_plugin_services[operation.declaration.config["semantic_node_id"]]
        if (service.owner is not self.owner or service.core is not self.core
                or service.kernel is not self.kernel or service.repository is not self.repository):
            raise ResourceIntegrityFault("program broker crossed its exact managed owner")
        return service

    def _program_fact(self, ref, expected):
        if not isinstance(ref, VersionRef) or ref.entity_type != expected:
            raise ValueError("program fact requires an exact typed reference")
        value = dict(self.kernel._exact_object(ref, expected_type=expected).metadata)
        field = "program_invocation_ref" if expected == PROGRAM_TYPE else "program_call_ref"
        if value.get(field) != _ref_payload(ref):
            raise ResourceIntegrityFault("program fact differs from its immutable reference")
        return value

    def _publish_program_fact(self, ref, document, key, loop):
        existing = self.core.event_store.object_row(ref.version_id)
        if existing is not None:
            if _bytes(self._program_fact(ref, ref.entity_type)) != _bytes(document):
                raise ManagedPluginInvocationConflict("program identity reused with different material")
            return ref
        tx = self.core.begin(idempotency_key=key)
        tx.validate_before_commit(lambda _view: self._current(loop))
        tx.prewrite(object_type=ref.entity_type, logical_id=ref.entity_id,
                    version_id=ref.version_id, payload=_bytes(document), metadata=document,
                    media_type="application/json", schema_ref="registry_v1/" + ref.entity_type,
                    producer_invocation_id=loop.invocation_ref.entity_id)
        tx.commit()
        return ref

    def _program_latest_calls(self, program_ref):
        latest = {}
        rank = {"planned": 0, "started": 1, "returned": 2, "failed": 2, "outcome_unknown": 2, "rejected": 2}
        for row in self.core.event_store.object_rows_by_type(CALL_TYPE):
            document = json.loads(row["metadata_json"])
            if document["program_invocation_ref"] == _ref_payload(program_ref):
                old = latest.get(document["logical_key"])
                if old is None or rank[document["status"]] > rank[old["status"]]:
                    latest[document["logical_key"]] = document
                elif rank[document["status"]] == rank[old["status"]] and document != old:
                    raise ResourceIntegrityFault("program child has conflicting terminal facts")
        return tuple(sorted(latest.values(), key=lambda d: d["accepted_ordinal"]))

    def _program_scope(self, execution, loop, turn, program_ref, *, allow_closed=False):
        self._execution(execution, loop)
        if loop.state != AgentLoopState.TURN_STORED or self.hydrate_current_agent_turn_v1(loop) != turn:
            raise ResourceIntegrityFault("program broker requires its exact current stored turn")
        document = self._program_fact(program_ref, PROGRAM_TYPE)
        if (document["agent_loop_ref"] != _ref_payload(self.mechanical_lifecycle.loop_ref(loop))
                or document["agent_turn_ref"] != _ref_payload(self.mechanical_lifecycle.turn_ref_for(loop.loop_id, turn.sequence))
                or document["execution_ref"] != _ref_payload(execution.operation_execution_lease_ref)
                or document["tool_catalog_ref"] != _resource_payload(loop.tool_catalog_ref)):
            raise ResourceIntegrityFault("program broker crossed its exact loop/turn/execution/catalog")
        final_ref = VersionRef(PROGRAM_TYPE, program_ref.entity_id,
                               _stable_id("invocation_version", program_ref.entity_id, "complete"))
        if not allow_closed and self.core.event_store.object_row(final_ref.version_id) is not None:
            raise ManagedPluginInvocationConflict("closed programs are readable, never reexecuted")
        _policy_ref, _prepared, payload = self._static(self._context(loop), "optional_tool_program_policy")
        if (document["policy_ref"] != _resource_payload(_policy_ref)
                or json.loads(payload) != document["policy"]):
            raise ResourceIntegrityFault("program broker policy differs from its registered HOST resource")
        return document, self._program_service(loop)

    def begin_tool_program_v1(self, execution, loop, turn, prepared_action, policy, settlement_key):
        self._execution(execution, loop)
        if loop.state != AgentLoopState.TURN_STORED or self.hydrate_current_agent_turn_v1(loop) != turn:
            raise ResourceIntegrityFault("program begin requires its exact current stored turn")
        actions = self.prepare_agent_turn_actions_v1(loop, turn)
        if len(actions) != 1 or (prepared_action.tool_call, prepared_action.validation) != (
                actions[0].tool_call, actions[0].validation):
            raise ValueError("a program must be the single original action in its turn")
        validation = prepared_action.validation
        if not isinstance(validation, ValidatedAgentToolAction) or validation.tool_name != PROGRAM_TOOL:
            raise ValueError("program begin requires a validated run_tool_program action")
        catalog = self.current_agent_tool_catalog_v1(execution)
        if not {PROGRAM_TOOL, PROGRAM_READER}.issubset(catalog.tool_names):
            raise ValueError("tool program requires its paired callable result reader")
        value, budget = validate_program_policy(policy)
        policy_ref, _prepared, payload = self._static(self._context(loop), "optional_tool_program_policy")
        if json.loads(payload) != value:
            raise ResourceIntegrityFault("program policy differs from its frozen HOST resource")
        from cpn.plugins.managed_scheduler import ManagedRunCapacity
        if not isinstance(getattr(self, "managed_run_capacity", None), ManagedRunCapacity):
            raise ResourceIntegrityFault("program broker requires the existing shared managed run capacity")
        service = self._program_service(loop)
        bindings = self._managed_tool_bindings(self._context(loop))
        for name in value["tools"]:
            if name not in bindings:
                raise PluginError("program child is outside the selected parent managed catalog")
            declaration = service.catalog.declaration(name)
            if declaration.registration_key != bindings[name]["registration_key"]:
                raise ResourceIntegrityFault("program allowlist differs from exact managed binding")
        arguments = json_copy(validation.arguments)
        if (set(arguments) != {"source", "arguments"} or not isinstance(arguments["source"], str)
                or not arguments["source"].strip()
                or len(arguments["source"].encode("utf-8")) > budget.max_source_bytes
                or not isinstance(settlement_key, str) or not settlement_key):
            raise ValueError("program source/arguments/settlement key are invalid or over budget")
        logical = _stable_id("invocation", PROGRAM_TYPE, validation.action_id, settlement_key)
        ref = VersionRef(PROGRAM_TYPE, logical, _stable_id("invocation_version", logical, "admitted"))
        if self.core.event_store.object_row(_stable_id("invocation_version", logical, "complete")) is not None:
            raise ManagedPluginInvocationConflict("a closed tool program must not be run again")
        parent_ref = self.mechanical_lifecycle.action_ref(validation.action_id, settlement_key)
        identity = {"program_invocation_ref": _ref_payload(ref), "agent_action_ref": _ref_payload(parent_ref)}
        document = {
            **identity, "agent_loop_ref": _ref_payload(self.mechanical_lifecycle.loop_ref(loop)),
            "agent_turn_ref": _ref_payload(self.mechanical_lifecycle.turn_ref_for(loop.loop_id, turn.sequence)),
            "execution_ref": _ref_payload(execution.operation_execution_lease_ref),
            "tool_catalog_ref": _resource_payload(loop.tool_catalog_ref),
            "policy_ref": _resource_payload(policy_ref), "policy": value,
            "source": arguments["source"], "arguments": arguments["arguments"],
            "settlement_key": settlement_key, "status": "admitted", "call_refs": [],
            "output_resource_ref": None, "runtime_identity": None,
        }
        self._publish_program_fact(ref, document, str(logical) + ":admitted", loop)
        return PreparedToolProgram(ref, identity, arguments["source"], arguments["arguments"],
                                   tuple(value["tools"]), budget)

    def _program_call_material(self, execution, loop, turn, program_ref, call):
        program, service = self._program_scope(execution, loop, turn, program_ref)
        if (not isinstance(call, ProgramBrokerCall)
                or json_copy(call.parent_identity) != {
                    "program_invocation_ref": program["program_invocation_ref"],
                    "agent_action_ref": program["agent_action_ref"]}
                or not isinstance(call.key, str) or not call.key.strip() or len(call.key) > 384
                or call.tool not in program["policy"]["tools"]):
            raise PluginError("program child identity or allowlist is invalid")
        declaration = service.catalog.declaration(call.tool)
        service._authorize(execution, declaration)
        arguments = json_copy(call.arguments)
        rejection = None
        try:
            _declaration, _plugin, _operation, arguments = service.validate_arguments(call.tool, arguments)
        except PluginError:
            rejection = "program_arguments_rejected"
        scheduler_policy = self._managed_scheduler_scope(execution, loop)
        if scheduler_policy is None:
            from cpn.plugins.managed_scheduler import ManagedSchedulerPolicy
            scheduler_policy = ManagedSchedulerPolicy(max_in_flight=program["policy"]["budget"]["max_parallel"])
        scheduler_policy.validate_declaration(declaration)
        logical = _stable_id("invocation", CALL_TYPE, program_ref.entity_id, call.key)
        child_id = f"tool-program-child/v1:{program_ref.entity_id}:{call.key}"
        material = {"program_invocation_ref": _ref_payload(program_ref),
                    "agent_action_ref": program["agent_action_ref"], "logical_key": call.key,
                    "managed_call_id": child_id, "tool_name": call.tool,
                    "registration_key": declaration.registration_key,
                    "arguments": arguments, "effect": declaration.effect}
        return program, service, declaration, scheduler_policy, logical, material, rejection

    def prepare_program_child_v1(self, execution, loop, turn, program_ref, call):
        program, service, declaration, scheduler, logical, material, rejection = self._program_call_material(
            execution, loop, turn, program_ref, call)
        calls = self._program_latest_calls(program_ref)
        old = next((c for c in calls if c["logical_key"] == call.key), None)
        if old is not None and any(_bytes(old[k]) != _bytes(v) for k, v in material.items()):
            raise ManagedPluginInvocationConflict("program logical key reused with different tool/arguments")
        if old is not None and old["status"] in {"returned", "failed", "outcome_unknown", "rejected"}:
            return PreparedProgramChild(_version_from_payload(old["program_call_ref"]), None,
                                        self._program_child_reply(program, old))
        if call.cancelled() or time.monotonic() >= call.deadline_monotonic:
            raise PluginError("program child was cancelled or its deadline expired before admission")
        budget = program["policy"]["budget"]
        if old is None and (len(calls) >= budget["max_calls"]
                            or sum(c["status"] in {"planned", "started"} for c in calls) >= budget["max_parallel"]):
            raise PluginError("program child call/concurrency budget exhausted before admission")
        if any(c["status"] == "outcome_unknown" for c in calls):
            raise ManagedPluginInvocationReconciliationRequired("program has an unresolved child outcome")
        planned_ref = VersionRef(CALL_TYPE, logical, _stable_id("invocation_version", logical, "planned"))
        planned = {**material, "program_call_ref": _ref_payload(planned_ref),
                   "accepted_ordinal": old["accepted_ordinal"] if old else len(calls),
                   "status": "planned", "started_receipt_ref": None,
                   "terminal_receipt_ref": None, "output": None, "error": None}
        self._publish_program_fact(planned_ref, planned, str(logical) + ":planned", loop)
        if rejection is not None:
            ref = VersionRef(CALL_TYPE, logical, _stable_id("invocation_version", logical, "rejected"))
            rejected = dict(planned, program_call_ref=_ref_payload(ref), status="rejected",
                            error={"code": rejection})
            self._publish_program_fact(ref, rejected, str(logical) + ":rejected", loop)
            return PreparedProgramChild(ref, None, self._program_child_reply(program, rejected))
        try:
            managed = service.prepare(call.tool, execution=execution, call_id=material["managed_call_id"],
                                      arguments=material["arguments"], registration_key=declaration.registration_key,
                                      allowed_effects=scheduler.admitted_effects, scheduling_policy=scheduler)
        except (ManagedPluginInvocationFailed, ManagedPluginInvocationReconciliationRequired) as exc:
            if exc.evidence is None:
                raise
            terminal = self._program_terminal_fact(loop, planned, exc.evidence)
            return PreparedProgramChild(_version_from_payload(terminal["program_call_ref"]), None,
                                        self._program_child_reply(program, terminal))
        if isinstance(managed, ManagedInvocationObservation):
            # An active duplicate is observed outside owner. Its original
            # finish publishes the typed terminal; never wait here.
            if managed.future.done():
                terminal = self._program_observed_terminal(execution, loop, service, planned, managed)
                return PreparedProgramChild(_version_from_payload(terminal["program_call_ref"]), None,
                                            self._program_child_reply(program, terminal))
            return PreparedProgramChild(planned_ref, managed)
        started_ref = VersionRef(CALL_TYPE, logical, _stable_id("invocation_version", logical, "started"))
        started = dict(planned, program_call_ref=_ref_payload(started_ref), status="started",
                       started_receipt_ref=dict(managed.started_receipt_ref))
        self._publish_program_fact(started_ref, started, str(logical) + ":started", loop)
        return PreparedProgramChild(started_ref, managed)

    def reject_program_child_v1(self, execution, loop, turn, program_ref, call, *, error_code):
        """Record an accepted frame that was not admitted; never invent receipts."""
        if not isinstance(error_code, str) or not error_code or len(error_code) > 512:
            raise ValueError("program non-admission needs a bounded exact error code")
        program, service, _declaration, _scheduler, logical, material, _rejection = (
            self._program_call_material(execution, loop, turn, program_ref, call))
        calls = self._program_latest_calls(program_ref)
        old = next((c for c in calls if c["logical_key"] == call.key), None)
        if old is not None and any(_bytes(old[k]) != _bytes(v) for k, v in material.items()):
            raise ManagedPluginInvocationConflict("program rejected logical key changed its exact material")
        if old is not None and old["status"] in {"returned", "failed", "outcome_unknown", "rejected"}:
            return self._program_child_reply(program, old)
        _key, receipt_refs = service._refs(execution, material["managed_call_id"])
        if (old is not None and old["status"] == "started"
                or self.core.event_store.object_row(receipt_refs["started"].resource_version_id) is not None):
            raise ManagedPluginInvocationReconciliationRequired(
                "an admitted program child must finish or reconcile, never become not_started")
        if old is None and len(calls) >= program["policy"]["budget"]["max_calls"]:
            raise PluginError("accepted program frames exceed the registered call budget")
        ref = VersionRef(CALL_TYPE, logical, _stable_id("invocation_version", logical, "rejected"))
        rejected = {**material, "program_call_ref": _ref_payload(ref),
                    "accepted_ordinal": old["accepted_ordinal"] if old else len(calls),
                    "status": "rejected", "started_receipt_ref": None,
                    "terminal_receipt_ref": None, "output": None, "error": {"code": error_code}}
        self._publish_program_fact(ref, rejected, str(logical) + ":rejected", loop)
        return self._program_child_reply(program, rejected)

    def _program_terminal_fact(self, loop, child, result):
        logical = _version_from_payload(child["program_call_ref"]).entity_id
        phase = result.get("outcome", "returned")
        if phase not in {"returned", "failed", "outcome_unknown"}:
            raise ResourceIntegrityFault("program child lacks a real managed terminal outcome")
        ref = VersionRef(CALL_TYPE, logical, _stable_id("invocation_version", logical, phase))
        terminal = dict(child, program_call_ref=_ref_payload(ref), status=phase,
                        started_receipt_ref=json_copy(result["started_receipt_ref"]),
                        terminal_receipt_ref=json_copy(result["terminal_receipt_ref"]),
                        output=json_copy(result.get("output")), error=json_copy(result.get("error")))
        if terminal["terminal_receipt_ref"] is None:
            raise ResourceIntegrityFault("program child terminal cannot fabricate a missing receipt")
        self._publish_program_fact(ref, terminal, str(logical) + ":" + phase, loop)
        if phase == "outcome_unknown":
            program = self._program_fact(_version_from_payload(child["program_invocation_ref"]), PROGRAM_TYPE)
            self.managed_run_capacity._block(program["execution_ref"]["version_id"])
        return terminal

    def _program_observed_terminal(self, execution, loop, service, child, observation):
        if not observation.future.done():
            raise PluginError("program observation is still active; wait outside the owner")
        key, refs = service._refs(execution, child["managed_call_id"])
        with service._active_lock:
            active = service._active_calls.get((id(self.core), key))
            if active is not None and active.future is not observation.future:
                raise ResourceIntegrityFault("program observation differs from its actual active managed call")
        declaration = service.catalog.declaration(child["tool_name"])
        service._authorize(execution, declaration)
        try:
            result = service._existing(execution, declaration, child["managed_call_id"],
                                       child["arguments"], key, refs)
        except (ManagedPluginInvocationFailed, ManagedPluginInvocationReconciliationRequired) as exc:
            if exc.evidence is None:
                raise
            result = exc.evidence
        if result is None:
            raise ResourceIntegrityFault("completed program observation lacks its real managed receipts")
        return self._program_terminal_fact(loop, child, result)

    def finish_program_child_v1(self, execution, loop, turn, program_ref, call, prepared_child, completion):
        program, service = self._program_scope(execution, loop, turn, program_ref)
        if not isinstance(prepared_child, PreparedProgramChild):
            raise TypeError("program finish requires its prepared child")
        child = self._program_fact(prepared_child.child_ref, CALL_TYPE)
        if (json_copy(call.parent_identity) != {
                "program_invocation_ref": program["program_invocation_ref"],
                "agent_action_ref": program["agent_action_ref"]}
                or child["program_invocation_ref"] != _ref_payload(program_ref)
                or child["logical_key"] != call.key or child["tool_name"] != call.tool
                or child["arguments"] != json_copy(call.arguments)):
            raise ResourceIntegrityFault("program finish changed its admitted child identity")
        if prepared_child.closed_reply is not None:
            return self._program_child_reply(program, child)
        managed = prepared_child.managed_prepared
        if isinstance(managed, ManagedInvocationObservation):
            terminal = self._program_observed_terminal(execution, loop, service, child, managed)
            return self._program_child_reply(program, terminal)
        if not isinstance(managed, PreparedManagedInvocation) or not isinstance(completion, ManagedWorkerCompletion):
            raise TypeError("active program finish requires its exact prepared managed worker completion")
        material = json.loads(managed.receipt_material)
        if (material["call_id"] != child["managed_call_id"]
                or material["registration"]["key"] != child["registration_key"]
                or material["arguments"] != child["arguments"]):
            raise ResourceIntegrityFault("program finish crossed its real managed receipt identity")
        try:
            result = service.finish(managed, completion)
        except (ManagedPluginInvocationFailed, ManagedPluginInvocationReconciliationRequired) as exc:
            if exc.evidence is None:
                raise
            result = exc.evidence
        terminal = self._program_terminal_fact(loop, child, result)
        return self._program_child_reply(program, terminal)

    def _program_child_reply(self, program, child):
        # W6's framed pipe appends a newline to the serialized JSON reply.
        maximum = program["policy"]["budget"]["max_frame_bytes"] - 1
        if child["status"] != "returned":
            frame = {"type": "reply", "key": child["logical_key"], "ok": False,
                     "error_code": child["error"]["code"]}
            if len(canonical(frame)) > maximum:
                raise ValueError("program error reply cannot fit its admitted frame budget")
            return ProgramBrokerReply(error_code=child["error"]["code"],
                                      outcome_unknown=child["status"] == "outcome_unknown")
        frame = {"type": "reply", "key": child["logical_key"], "ok": True, "value": child["output"]}
        if len(canonical(frame)) <= maximum:
            return ProgramBrokerReply(value=child["output"])
        # Include the actual pipe reply envelope in the budget.
        overhead = len(canonical(dict(frame, value=None))) - 4
        try:
            page = self._program_child_page(child, 0, maximum - overhead)
        except ValueError:
            # The real returned child and its complete output are already
            # durable. A display budget cannot make that effect uncertain.
            # The closed parent reader retains access without replay.
            error = "program_result_budget_too_small"
            rejected_frame = {"type": "reply", "key": child["logical_key"],
                              "ok": False, "error_code": error}
            if len(canonical(rejected_frame)) > maximum:
                raise ValueError("program budget cannot fit its accepted-key error reply")
            return ProgramBrokerReply(error_code=error)
        return ProgramBrokerReply(value=page)

    def _program_child_page(self, child, offset, maximum):
        if child["status"] != "returned":
            raise ValueError("program child reader requires a returned child")
        text = serialized_managed_json(child["output"])
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ValueError("program child offset is outside its output")
        return _fit_page({"kind": "agent_tool_program_child_output_page/v1",
                          "program_invocation_ref": child["program_invocation_ref"],
                          "program_call_ref": child["program_call_ref"],
                          "terminal_receipt_ref": child["terminal_receipt_ref"],
                          "reader": CHILD_READER, "content": text[offset:], "offset_chars": offset,
                          "next_offset_chars": None, "total_chars": len(text), "truncated": False}, maximum)

    def read_program_child_output_v1(self, execution, loop, turn, program_ref, child_ref, *, offset_chars=0, max_bytes=10000):
        self._program_scope(execution, loop, turn, program_ref, allow_closed=True)
        child = self._program_fact(child_ref, CALL_TYPE)
        if child["program_invocation_ref"] != _ref_payload(program_ref):
            raise ResourceIntegrityFault("program reader crossed its exact authorized invocation")
        return self._program_child_page(child, offset_chars, max_bytes)

    def complete_tool_program_v1(self, execution, loop, turn, program_ref, result, *, settlement_key):
        program, _service = self._program_scope(execution, loop, turn, program_ref, allow_closed=True)
        if settlement_key != program["settlement_key"] or not isinstance(result, IsolatedProgramResult):
            raise ResourceIntegrityFault("program completion differs from admitted settlement/runtime result")
        if result.runtime_identity.get("profile_id") != program["policy"]["profile_id"]:
            raise ResourceIntegrityFault("program runtime differs from its selected HOST isolation profile")
        final_ref = VersionRef(PROGRAM_TYPE, program_ref.entity_id,
                               _stable_id("invocation_version", program_ref.entity_id, "complete"))
        children = self._program_latest_calls(program_ref)
        if any(c["status"] in {"planned", "started"} for c in children):
            raise ManagedPluginInvocationReconciliationRequired("program completion must collect every in-flight child")
        observed = {o.call.key: o for o in result.calls}
        if (len(observed) != len(result.calls)
                or set(observed) != {c["logical_key"] for c in children}
                or any(observed[c["logical_key"]].call.tool != c["tool_name"]
                       or _bytes(observed[c["logical_key"]].call.arguments) != _bytes(c["arguments"])
                       for c in children)):
            raise ResourceIntegrityFault("program runtime observations differ from actual accepted children")
        status = ("outcome_unknown" if result.status == "reconciliation_required"
                  or any(c["status"] == "outcome_unknown" for c in children)
                  else "returned" if result.status == "returned"
                  else "cancelled" if result.status == "cancelled" else "failed")
        if len(_bytes(result.value)) > program["policy"]["budget"]["max_output_bytes"]:
            raise ValueError("program aggregate exceeds its admitted output-byte budget")
        if (not isinstance(result.stdout, str) or not isinstance(result.stderr, str)
                or len((result.stdout + result.stderr).encode("utf-8"))
                > program["policy"]["budget"]["max_output_bytes"]):
            raise ValueError("program diagnostics exceed their admitted output-byte budget")
        payload = {"kind": "tool_program_output/v1", "program_invocation_ref": _ref_payload(program_ref),
                   "agent_action_ref": program["agent_action_ref"], "status": status,
                   "children": list(children), "aggregate": json_copy(result.value),
                   "runtime_status": result.status, "stdout": result.stdout, "stderr": result.stderr,
                   "runtime_identity": json_copy(result.runtime_identity),
                   "exit_code": result.exit_code, "setup_error": json_copy(result.setup_error)}
        context = self._context(loop)
        template_ref, _template = self._workspace_template(context)
        intent_ref = self._workspace_write_intent(context, template_ref)
        resource = self.kernel.publish_bytes(context, PublishResource(
            origin=WorkspaceWriteOrigin(context.operation_binding_ref, intent_ref), payload=_bytes(payload),
            media_type="application/json", content_schema_ref=None,
            summary="Registered tool program results and complete child provenance",
            lifetime_ref=context.invocation_ref, descriptors={"content_role": "tool_program_result"},
            idempotency_key=str(program_ref.entity_id) + ":result"))
        final = dict(program, program_invocation_ref=_ref_payload(final_ref), status=status,
                     call_refs=[c["program_call_ref"] for c in children],
                     output_resource_ref=_resource_payload(resource), runtime_identity=json_copy(result.runtime_identity))
        self._publish_program_fact(final_ref, final, str(program_ref.entity_id) + ":complete", loop)
        metadata = {"kind": "tool_program_result/v1", "program_invocation_ref": _ref_payload(final_ref),
                    "output_resource_ref": _resource_payload(resource), "status": status,
                    "call_count": len(children), "reader": PROGRAM_READER,
                    "agent_action_ref": program["agent_action_ref"]}
        block = None
        if status == "outcome_unknown":
            block = self.register_operation_execution_block(
                execution, block_kind="submission_reconciliation", operation_or_tool_identity=PROGRAM_TOOL,
                error_code="program_child_outcome_unknown", error_message=None, boundary="tool_program",
                consecutive_count=1, exact_error_ref=final_ref, retry_not_before_utc=None)
        return CompletedToolProgram((resource.as_version_ref(),), metadata, block)

    def _read_tool_program_output(self, execution, loop, turn, arguments, _key):
        self._execution(execution, loop)
        if (not isinstance(arguments, Mapping)
                or not {"agent_action_ref", "output_resource_ref"}.issubset(arguments)
                or set(arguments) - {"agent_action_ref", "output_resource_ref", "offset_chars", "max_bytes"}):
            raise ValueError("program output reader requires exact closed locator arguments")
        ref = _version_from_payload(arguments["agent_action_ref"])
        if ref.entity_type != "agent_action/v2":
            raise ValueError("program output reader requires a v2 parent action")
        source = self.mechanical_lifecycle.hydrate_action(ref)
        metadata = source.result_metadata
        if (source.loop_id != loop.loop_id or source.turn_sequence >= turn.sequence
                or source.state != AgentLoopState.ACTION_APPLIED or source.tool_name != PROGRAM_TOOL
                or source.tool_error_ref is not None or not isinstance(metadata, Mapping)
                or metadata.get("kind") != "tool_program_result/v1"
                or metadata.get("status") not in {"returned", "failed", "cancelled", "outcome_unknown"}
                or metadata.get("agent_action_ref") != _ref_payload(ref)
                or metadata.get("output_resource_ref") != arguments["output_resource_ref"]):
            raise ValueError("program reader requires an earlier same-loop closed applied parent action")
        program = self._program_fact(_version_from_payload(metadata["program_invocation_ref"]), PROGRAM_TYPE)
        if (program["status"] != metadata["status"] or program["agent_action_ref"] != _ref_payload(ref)
                or program["output_resource_ref"] != arguments["output_resource_ref"]):
            raise ResourceIntegrityFault("program reader locator differs from its completed invocation")
        if any(self._program_fact(_version_from_payload(child_ref), CALL_TYPE)["status"]
               in {"planned", "started"} for child_ref in program["call_refs"]):
            raise ResourceIntegrityFault("program reader requires every accepted child to be terminal")
        resource = _resource_from_payload(arguments["output_resource_ref"])
        payload = self.kernel._read_firing_registered(self._context(loop), resource)
        text = serialized_managed_json(json.loads(payload))
        offset = arguments.get("offset_chars", 0)
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ValueError("program output offset is outside its registered result")
        page = _fit_page({"kind": "tool_program_output_page/v1", "agent_action_ref": _ref_payload(ref),
                          "output_resource_ref": _resource_payload(resource), "reader": PROGRAM_READER,
                          "source_status": program["status"],
                          "content": text[offset:], "offset_chars": offset, "next_offset_chars": None,
                          "total_chars": len(text), "truncated": False}, arguments.get("max_bytes", 10000))
        return (ref,), page
