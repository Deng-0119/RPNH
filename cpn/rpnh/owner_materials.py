"""Original Owner ports for complete material input and exact command receipts.

This service holds no queue or command state. UI operations and scheduler
selection are projections of the original Registry facts at a fixed read cut.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from .registry.event_store import RegistryConflict
from .registry.publication import _version_from_payload
from .registry.resources import ResourceVersionRef
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload
from .registry.user_collaboration import check_handle
from .registry._event_store.collaboration_descriptors import (
    exact_prepared)
from .registry._event_store.material_control import (
    OWNER_SCHEMA, TEXT_SCHEMA, INPUT_ROLE, COMMAND_PROTOCOL,
    canonical_rows, row_ref, control_body, require_session,
    read_accepted_material_input, read_accepted_material_command,
    validate_input_params, validate_submit_params, validate_active_target,
    read_committed_material_admission, validate_material_baseline,
)


class OwnerMaterials:
    def __init__(self, owner):
        self.owner = owner

    @contextmanager
    def _cut(self, session=None):
        core = self.owner._core
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            if session is not None:
                require_session(core.event_store, db, core.task_id, session)
            yield db

    def _action(self, action, name, command_id, params):
        core = self.owner._core
        check_handle(core.event_store, action)
        expected = {"owner_command": name, "command_id": command_id, "params": params}
        if (not isinstance(command_id, str) or not command_id
                or action.kind != name or action.creation != canonical_json(expected)):
            raise RegistryConflict("COMMAND_CONTENT_MISMATCH: HOST request differs")
        return action

    def _inputs(self, db, input_command_id):
        core = self.owner._core
        return canonical_rows(core.event_store, db, core.task_id, conditions=
            "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_input_role')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_input_command_id')=?",
            parameters=(TEXT_SCHEMA, INPUT_ROLE, input_command_id))

    def _controls(self, db, command_id, kind):
        core = self.owner._core
        rows = canonical_rows(core.event_store, db, core.task_id, conditions=
            "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.command_id')=?",
            parameters=(OWNER_SCHEMA, kind, command_id))
        result = []
        for row in rows:
            prepared = exact_prepared(db, core.object_store, core.task_id, row_ref(row))
            body = control_body(core.event_store, prepared)
            if body["source"] != ref_payload(self.owner.principal_ref):
                raise RegistryConflict("material control belongs to another owner")
            result.append((row_ref(row), body))
        return result

    def prepare_input(self, params, command_id, *, collaboration_action):
        validate_input_params(params)
        action = self._action(collaboration_action, "prepare_material_input", command_id, params)
        core = self.owner._core
        with self._cut(action.session) as db:
            rows = self._inputs(db, command_id)
            if rows:
                if len(rows) != 1:
                    raise RegistryConflict("material preparation has ambiguous original inputs")
                prepared = read_accepted_material_input(core.event_store, db, core.task_id,
                    row_ref(rows[0]), current=False)
                desc = prepared.metadata["descriptors"]
                if (desc["owner_input_actor"] != action.session.actor
                        or desc["owner_input_session_version_id"] != str(action.session.ref.version_id)
                        or core.object_store.read_registered(prepared) != canonical_json(params["value"])):
                    raise RegistryConflict("COMMAND_CONTENT_MISMATCH: input preparation changed")
                from .registry._event_store.material_input_lifecycle import decorate_input_receipt
                return decorate_input_receipt(core.event_store, db, core.task_id,
                    self._input_receipt(command_id, row_ref(rows[0])))
        tx = core.begin(idempotency_key=command_id)
        tx.bind_collaboration_action(action)
        input_ref = self.owner.control.publish_input(
            {"schema_id": TEXT_SCHEMA, "value": params["value"],
             "summary": "Complete Viewer material input; retained for one command"},
            command_id=command_id, transaction=tx,
            descriptors={"owner_input_role": INPUT_ROLE,
                "owner_input_actor": action.session.actor,
                "owner_input_session_version_id": str(action.session.ref.version_id)})
        result = self._input_receipt(command_id, ref_payload(input_ref.as_version_ref()))
        self.owner.control.publish("receipt", command_id, None, result,
            lineage=(input_ref.as_version_ref(),), transaction=tx, suffix="material-input")
        tx.commit()
        return result

    @staticmethod
    def _input_receipt(command_id, input_ref):
        return {"protocol": "rpnh/material_input_receipt/v1",
            "input_command_id": command_id, "input_ref": input_ref,
            "status": "INPUT_ACCEPTED"}

    def read_input(self, input_command_id, *, session):
        if not isinstance(input_command_id, str) or not input_command_id:
            raise ValueError("one stable input preparation identity is required")
        core = self.owner._core
        with self._cut() as db:
            require_session(core.event_store, db, core.task_id, session)
            rows = self._inputs(db, input_command_id)
            if not rows:
                return {"protocol": "rpnh/material_input_receipt/v1",
                    "input_command_id": input_command_id, "status": "NOT_FOUND_AT_READ_CUT"}
            if len(rows) != 1:
                raise RegistryConflict("material preparation has ambiguous original inputs")
            read_accepted_material_input(core.event_store, db, core.task_id, row_ref(rows[0]))
            from .registry._event_store.material_input_lifecycle import decorate_input_receipt
            return decorate_input_receipt(core.event_store, db, core.task_id,
                self._input_receipt(input_command_id, row_ref(rows[0])))

    def submit(self, params, command_id, *, collaboration_action):
        from .registry._event_store.material_locations import (
            is_location, command_lineage, command_descriptors,
        )
        validate_submit_params(params)
        action = self._action(collaboration_action, "material_submit", command_id, params)
        core = self.owner._core
        data = {key: value for key, value in params.items() if key != "target"}
        data["user_action"] = action.material()
        with self._cut(action.session) as db:
            previous = self._controls(db, command_id, "command")
            if previous:
                if len(previous) != 1:
                    raise RegistryConflict("material command identity is not unique")
                command_ref, body = previous[0]
                read_accepted_material_command(core.event_store, db, core.task_id, command_ref)
                if body["target"] != params["target"] or body["data"] != data:
                    raise RegistryConflict("COMMAND_CONTENT_MISMATCH: immutable command changed")
                return self._submit_receipt(command_id, command_ref)
            validate_active_target(core.event_store, db, core.task_id, params,
                                   ref_payload(self.owner.principal_ref))
            validate_material_baseline(core.event_store, db, core.task_id, params)
            from .registry._event_store.material_availability import is_metadata_operation
            if not is_metadata_operation(params):
                read_accepted_material_input(core.event_store, db, core.task_id, params["input_ref"])
        tx = core.begin(idempotency_key=command_id)
        tx.bind_collaboration_action(action)
        command = self.owner.control.publish("command", command_id, params["target"], data,
            lineage=tuple(_version_from_payload(ref) for ref in command_lineage(params)), transaction=tx,
            descriptors={name: value for name, value in command_descriptors(command_id, params).items()
                         if name not in {"owner_control_kind", "command_id"}})
        tx.commit()
        return self._submit_receipt(command_id, ref_payload(command.as_version_ref()))

    @staticmethod
    def _submit_receipt(command_id, command_ref):
        return {"protocol": "rpnh/material_submit_receipt/v1",
                "command_id": command_id, "command_ref": command_ref,
                "status": "REGISTERED"}

    def close_not_admitted(self, *, material_command_ref):
        """Close only proved pre-admission invalidity through the original Owner."""
        from .material_not_admitted import close_not_admitted
        return close_not_admitted(self.owner, material_command_ref=material_command_ref)

    def stage_admission(self, *, transaction, owner_command_ref,
                        admission_ref, firing_ref, invocation_ref):
        core = self.owner._core
        with self._cut() as db:
            body = read_accepted_material_command(core.event_store, db, core.task_id,
                                                  ref_payload(owner_command_ref))
        self.owner.control.publish("receipt", body["command_id"], body["target"],
            {"protocol": "rpnh/material_admission/v1",
             "command_ref": ref_payload(owner_command_ref),
             "firing_ref": ref_payload(firing_ref), "invocation_ref": ref_payload(invocation_ref),
             "admission_ref": ref_payload(admission_ref)},
            lineage=(owner_command_ref, admission_ref, firing_ref, invocation_ref),
            transaction=transaction, suffix="material-admission")

    def _admissions(self, db, command_ref):
        core = self.owner._core
        rows = db.execute(
            "SELECT o.* FROM objects o JOIN events e ON e.event_id=o.published_event_id "
            "JOIN transactions t ON t.transaction_id=o.transaction_id "
            "WHERE o.object_type='firing_admission/v1' AND e.task_id=? "
            "AND t.status='committed' "
            "AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=? "
            "ORDER BY e.ordinal", (str(core.task_id), command_ref["version_id"])).fetchall()
        for row in rows:
            self._checked_admission(db, row, command_ref)
        return rows

    def _checked_admission(self, db, row, command_ref=None):
        """Read original committed control authority without promoting a candidate."""
        core = self.owner._core
        return read_committed_material_admission(
            core.event_store, db, core.task_id, row, command_ref)

    def _prepared_result(self, db, admission):
        """Use the original durable executor return, never mere staged bytes."""
        core = self.owner._core
        firing = admission["transition_firing_ref"]
        rows = db.execute(
            "SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
            "JOIN firing_temporary_members m ON m.member_kind='event' AND m.member_identity=e.event_id "
            "WHERE t.status='committed' AND e.task_id=? AND m.firing_version_id=? "
            "AND e.event_type='registered_operation_completion_recorded/v1'",
            (str(core.task_id), firing["version_id"])).fetchall()
        if not rows:
            return False
        if len(rows) != 1:
            raise RegistryConflict("material firing has ambiguous original executor returns")
        from .registry._event_store.material_locations import is_location
        command = read_accepted_material_command(core.event_store, db, core.task_id,
            admission["owner_command_ref"], current=False)
        from .registry._event_store.material_availability import is_metadata_operation
        if is_metadata_operation(command["data"]):
            from .registry._event_store.material_settlement import completion_evidence
            completion_evidence(core.event_store, db, core.task_id, admission)
            return True
        event = core.event_store._row_to_envelope(rows[0])
        body = event.payload
        if (body["transition_firing_ref"] != firing
                or body["invocation_ref"] != admission["invocation_ref"]
                or body["firing_admission_ref"] != admission["firing_admission_ref"]):
            raise RegistryConflict("material executor return differs from its original admission")
        candidate = body["workspace_revision_candidate_ref"]
        if candidate is None or not body["ordered_outputs"]:
            return False
        row = db.execute(
            "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "JOIN firing_temporary_members m ON m.member_kind='object' AND m.member_identity=o.version_id "
            "WHERE t.status='committed' AND m.firing_version_id=? "
            "AND o.object_type='workspace_revision_candidate/v1' AND o.logical_id=? AND o.version_id=?",
            (firing["version_id"], candidate["logical_id"], candidate["version_id"])).fetchone()
        if row is None:
            raise RegistryConflict("material executor return lacks its exact provisional workspace candidate")
        meta = json.loads(row["metadata_json"])
        core.catalog.validate_instance("workspace_revision_candidate/v1", category="object", instance=meta)
        outputs = [item["resource_ref"] for item in body["ordered_outputs"]]
        if (meta["workspace_revision_candidate_ref"] != candidate
                or meta["invocation_ref"] != admission["invocation_ref"]
                or meta["transition_firing_ref"] != firing
                or meta["semantic_output_refs"] != outputs):
            raise RegistryConflict("material workspace candidate differs from its registered executor outputs")
        return True

    def read_receipt(self, *, session, material_command_id=None, material_command_ref=None):
        if (material_command_id is None) == (material_command_ref is None):
            raise ValueError("one exact material command reference or identity is required")
        core = self.owner._core
        with self._cut() as db:
            require_session(core.event_store, db, core.task_id, session)
            if material_command_ref is None:
                found = self._controls(db, material_command_id, "command")
                if not found:
                    return {"protocol": "rpnh/material_receipt_view/v1",
                        "command_id": material_command_id, "status": "NOT_FOUND_AT_READ_CUT"}
                if len(found) != 1:
                    raise RegistryConflict("material command identity is not unique")
                command_ref, _body = found[0]
            else:
                command_ref = material_command_ref
            body = read_accepted_material_command(core.event_store, db, core.task_id,
                                                  command_ref, current=False)
            from .registry._event_store.material_not_admitted import read_not_admitted, receipt_view
            terminal = read_not_admitted(core.event_store, db, core.task_id, command_ref)
            if terminal is not None:
                return receipt_view(body, command_ref, terminal)
            result = {"protocol": "rpnh/material_receipt_view/v1",
                "command_id": body["command_id"], "command_ref": command_ref,
                "status": "REGISTERED"}
            admissions = self._admissions(db, command_ref)
            if len(admissions) > 1:
                raise RegistryConflict("material command has multiple original firings")
            if not admissions:
                return result
            admission = self._checked_admission(db, admissions[0], command_ref)
            result.update(status="ADMITTED", admission_ref=admission["firing_admission_ref"],
                firing_ref=admission["transition_firing_ref"], invocation_ref=admission["invocation_ref"])
            publication = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                                    (result["firing_ref"]["version_id"],)).fetchone()
            if publication is None:
                raise RegistryConflict("material admission lacks its original publication root")
            if publication["state"] in ("PUBLISHED", "REJECTED"):
                from .registry._event_store.material_settlement import terminal_result
                result_ref, data = terminal_result(core.event_store, db, core.task_id,
                                                   admission, body, publication)
                result.update(status=publication["state"], result_ref=result_ref,
                              error=data["error"])
                if publication["state"] == "PUBLISHED":
                    result.update(result_kind=data["result_kind"], completion_ref=data["completion_ref"],
                        operation_result_ref=data["operation_result_ref"],
                        output_refs=data["output_refs"], binding_refs=data["binding_refs"])
                else:
                    result.update(rejection_ref=data["rejection_ref"],
                        registered_completion_event_id=data["registered_completion_event_id"])
            elif publication["state"] != "PROVISIONAL":
                result["status"] = "RECOVERY_REQUIRED"
            elif self._prepared_result(db, admission):
                result["status"] = "PREPARED"
            return result

    def is_management_transition(self, structure, transition_id):
        transition = next((item for item in structure.compiled.symbolic.transitions
                           if item.name == transition_id), None)
        if transition is None:
            return False
        operation = next(item for item in structure.compiled.operations
                         if item.declaration.name == transition.operation)
        return operation.executor_declaration["contracts"].get("owner_material_commands") == COMMAND_PROTOCOL

    def next_command(self, transition_id):
        core = self.owner._core
        with self._cut() as db:
            rows = canonical_rows(core.event_store, db, core.task_id, conditions=
                "AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
                "AND json_extract(o.metadata_json,'$.descriptors.owner_material_protocol')=? "
                "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='command'",
                parameters=(OWNER_SCHEMA, COMMAND_PROTOCOL))
            for row in rows:
                command_ref = row_ref(row)
                if self._admissions(db, command_ref):
                    continue
                body = read_accepted_material_command(core.event_store, db, core.task_id,
                                                       command_ref, current=False)
                if body["target"] != transition_id:
                    continue
                from .registry._event_store.material_not_admitted import read_not_admitted
                if read_not_admitted(core.event_store, db, core.task_id, command_ref) is not None:
                    continue
                try:
                    read_accepted_material_command(core.event_store, db, core.task_id, command_ref)
                    params = {key: value for key, value in body["data"].items() if key != "user_action"}
                    params["target"] = body["target"]
                    validate_active_target(core.event_store, db, core.task_id, params, body["source"])
                except RegistryConflict as exc:
                    if str(exc).startswith(("ACTOR_NOT_AUTHORIZED:", "EXECUTION_CHANGED:", "TARGET_UNAVAILABLE:")):
                        continue
                    raise
                return _version_from_payload(command_ref)
        return None

    def eligible_transitions(self, structure, enabled):
        from .registry._event_store.human_viewer import READ_EXECUTOR, READ_IDENTITY, READ_CONTRACTS
        # A human read needs the requesting HTTP transport before Start. Its
        # accepted command remains an original Registry fact; an explicit
        # Owner admission still proves the same command, PN and input claim.
        # Automatic scheduling must not start a read with no attached sender.
        operations = {item.declaration.name: item for item in structure.compiled.operations}
        human_reads = {
            transition.name for transition in structure.compiled.symbolic.transitions
            if (operations[transition.operation].declaration.executor == READ_EXECUTOR
                and operations[transition.operation].executor_declaration.get("identity") == READ_IDENTITY
                and operations[transition.operation].executor_declaration.get("contracts") == READ_CONTRACTS)
        }
        return tuple(name for name in enabled
                     if name not in human_reads
                     and (not self.is_management_transition(structure, name)
                          or self.next_command(name) is not None))

    def _replay_acknowledged_material_input(self, db, kernel, execution, resource, key):
        """Rehydrate one already consumed input; issue no new I/O authority.

        Only a complete original delivery/ack/read closure is reusable. A
        partial grant, release or boundary remains recovery work and cannot
        fall through to a second grant or a new one-use release.
        """
        from .registry.resources import AcknowledgeResourceDelivery
        core = self.owner._core
        context = execution.operation.canonical.context
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
            (str(context.own_transition_firing_ref.version_id),)).fetchone()
        if (root is None or root["state"] != "PROVISIONAL"
                or root["invocation_version_id"] != str(context.invocation_ref.version_id)):
            raise RegistryConflict("INPUT_UNAVAILABLE: completed material input cannot become a new body use")
        resource_data = {"resource_id": str(resource.resource_id),
                         "resource_version_id": str(resource.resource_version_id)}
        rows = db.execute(
            "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
            "JOIN firing_temporary_members m ON m.member_kind='object' AND m.member_identity=o.version_id "
            "WHERE t.status='committed' AND m.firing_version_id=? "
            "AND o.object_type='resource_delivery/v1' AND t.idempotency_key=? "
            "AND json_extract(o.metadata_json,'$.state')='prepared'",
            (str(context.own_transition_firing_ref.version_id), key + ":prepare")).fetchall()
        if not rows:
            prior = db.execute(
                "SELECT 1 FROM transactions WHERE task_id=? AND idempotency_key IN (?,?,?,?,?,?,?) LIMIT 1",
                (str(core.task_id), *(key + suffix for suffix in
                    (":grant", ":activate", ":allow", ":prepare", ":release", ":boundary", ":acknowledge")))).fetchone()
            if prior is not None:
                raise RegistryConflict("RECOVERY_REQUIRED: material input has an incomplete original delivery")
            return None
        if len(rows) != 1:
            raise RegistryConflict("material input has ambiguous original preparations")
        prepared_ref = _version_from_payload(row_ref(rows[0]))
        prepared = kernel._exact_object(prepared_ref, expected_type="resource_delivery/v1").metadata
        terminals = db.execute(
            "SELECT e.* FROM events e JOIN transactions t ON t.transaction_id=e.transaction_id "
            "JOIN firing_temporary_members m ON m.member_kind='event' AND m.member_identity=e.event_id "
            "WHERE t.status='committed' AND e.task_id=? AND m.firing_version_id=? "
            "AND e.aggregate_id=? AND e.event_type IN "
            "('resource_delivery_acknowledged/v1','resource_delivery_unknown/v1','resource_delivery_failed/v1')",
            (str(core.task_id), str(context.own_transition_firing_ref.version_id),
             str(prepared_ref.entity_id))).fetchall()
        if len(terminals) != 1 or terminals[0]["event_type"] != "resource_delivery_acknowledged/v1":
            raise RegistryConflict("RECOVERY_REQUIRED: material input lacks one acknowledged original delivery")
        terminal_event = terminals[0]
        fact = json.loads(terminal_event["payload_json"])
        authorized_ref = _version_from_payload(fact["delivery_ref"])
        terminal_ref = _version_from_payload(fact["terminal_delivery_ref"])
        boundary_ref = _version_from_payload(fact["boundary_receipt_ref"])
        authorized = kernel._exact_object(authorized_ref, expected_type="resource_delivery/v1").metadata
        terminal = kernel._exact_object(terminal_ref, expected_type="resource_delivery/v1").metadata
        expected = {"context_ref": ref_payload(context.invocation_ref), "resource_ref": resource_data,
                    "boundary": "parent_receipt", "purpose": "owner request input"}
        for item in (prepared, authorized, terminal):
            if (any(item.get(name) != value for name, value in expected.items())
                    or item.get("delivery_id") != str(prepared_ref.entity_id)
                    or item.get("authorization_ref") != prepared.get("authorization_ref")
                    or item.get("resource_byte_count") != prepared.get("resource_byte_count")):
                raise RegistryConflict("material input delivery differs from its exact admitted input")
        if (authorized.get("state") != "release_authorized"
                or authorized.get("previous_delivery_ref") != ref_payload(prepared_ref)
                or terminal.get("state") != "acknowledged"
                or terminal.get("previous_delivery_ref") != ref_payload(authorized_ref)
                or terminal.get("boundary_receipt_ref") != ref_payload(boundary_ref)
                or terminal_event["producer_invocation_id"] != str(context.invocation_ref.entity_id)):
            raise RegistryConflict("material input lacks its original exact delivery lineage")
        grant_ref = _version_from_payload(prepared["authorization_ref"])
        grant = kernel._exact_object(grant_ref, expected_type="capability_grant/v1").metadata
        if (grant.get("target_invocation_id") != str(context.invocation_ref.entity_id)
                or grant.get("operation_binding_ref") != ref_payload(context.operation_binding_ref)
                or grant.get("resource_version_ids") != [str(resource.resource_version_id)]):
            raise RegistryConflict("material input receipt names another original grant")
        reads = db.execute(
            "SELECT * FROM events WHERE transaction_id=? AND event_type='observed_read/v1'",
            (terminal_event["transaction_id"],)).fetchall()
        if len(reads) != 1:
            raise RegistryConflict("material input acknowledgement lacks one same-transaction read")
        read = reads[0]
        observed = json.loads(read["payload_json"])
        if (read["aggregate_id"] != str(prepared_ref.entity_id)
                or read["producer_invocation_id"] != str(context.invocation_ref.entity_id)
                or observed.get("actor_invocation_ref") != ref_payload(context.invocation_ref)
                or observed.get("firing_ref") != ref_payload(context.own_transition_firing_ref)
                or observed.get("resource_ref") != resource_data
                or observed.get("authorization_ref") != ref_payload(grant_ref)
                or observed.get("boundary") != "parent_receipt"
                or observed.get("boundary_receipt_ref") != ref_payload(boundary_ref)
                or observed.get("witness_ref") != fact.get("witness_ref")
                or observed.get("transaction_id") != terminal_event["transaction_id"]):
            raise RegistryConflict("material input observed read crosses its exact delivery")
        # The original terminal path revalidates its immutable boundary and
        # witness, then returns the existing receipt without appending facts.
        receipt = kernel.acknowledge_delivery(context, AcknowledgeResourceDelivery(
            authorized_ref, boundary_ref, "acknowledged", key + ":acknowledge"))
        if (str(receipt.terminal_event_id) != terminal_event["event_id"]
                or str(receipt.observed_read_event_id) != read["event_id"]
                or receipt.delivery_ref != terminal_ref):
            raise RegistryConflict("material input replay changed the original receipt")
        payload = kernel._read_firing_registered(context, resource)
        if (len(payload) != prepared["resource_byte_count"]
                or observed.get("resource_byte_count") != len(payload)):
            raise RegistryConflict("material input bytes differ from the acknowledged byte count")
        return {"resource_ref": ref_payload(resource.as_version_ref()),
                "grant_ref": ref_payload(grant_ref),
                "observed_read_event_id": str(receipt.observed_read_event_id)}, payload

    def deliver_input(self, execution):
        """Consume once, or rehydrate this invocation's exact acknowledged D1."""
        core = self.owner._core
        from .registry.operation_execution import verify_operation_execution
        kernel, repository = self.owner.operation_repository()
        execution = verify_operation_execution(core, kernel, repository, execution)
        context = execution.operation.canonical.context
        with self._cut() as db:
            rows = db.execute(
                "SELECT o.* FROM objects o JOIN transactions t ON t.transaction_id=o.transaction_id "
                "JOIN events e ON e.event_id=o.published_event_id "
                "WHERE o.object_type='firing_admission/v1' AND t.status='committed' AND e.task_id=? "
                "AND json_extract(o.metadata_json,'$.invocation_ref.version_id')=?",
                (str(core.task_id), str(context.invocation_ref.version_id))).fetchall()
            if len(rows) != 1:
                raise RegistryConflict("material input requires one exact original admission")
            admission = self._checked_admission(db, rows[0])
            command_ref = admission.get("owner_command_ref")
            if command_ref is None:
                raise RegistryConflict("material input requires its actual command admission")
            command = read_accepted_material_command(core.event_store, db, core.task_id,
                                                       command_ref, current=False)
            if "input_ref" not in command["data"]:
                raise RegistryConflict("INPUT_UNAVAILABLE: location action has no material body input")
            input_ref = _version_from_payload(command["data"]["input_ref"])
            resource = ResourceVersionRef(input_ref.entity_id, input_ref.version_id)
            key = "material-input:" + str(context.invocation_ref.version_id)
            replay = self._replay_acknowledged_material_input(db, kernel, execution, resource, key)
            if replay is not None:
                receipt, payload = replay
                return command, receipt, payload
            read_accepted_material_command(core.event_store, db, core.task_id, command_ref)
            read_accepted_material_input(core.event_store, db, core.task_id,
                                          command["data"]["input_ref"], current=True)
        receipt, payload = self.owner.control.deliver(context, kernel,
            resource, key)
        return command, receipt, payload

