"""Owner settlement of a completely prepared deterministic material firing.

The original Registry reserves at most two metadata attempts for the exact
accepted command and firing, across calls and writer restarts. The original
input, executor return and tool work are never repeated. Unknown write outcomes
retain their existing responsibility instead of receiving another attempt.
"""
from __future__ import annotations

from .registry.event_store import RegistryConflict
from .registry.publication import _version_from_payload
from .registry.strict_contracts import ref_payload
from .registry._event_store.material_settlement import (
    RESULT_PROTOCOL, completion_evidence, material_admission, result_lineage,
    semantic_bindings, terminal_result,
)


class MaterialPublishRejected(RegistryConflict):
    """The exact original firing has durably closed without publication."""

    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__(receipt["error"]["code"] + ": " + receipt["error"]["message"])


def is_material_execution(owner, execution):
    kernel, _repository = owner.operation_repository()
    admission = kernel._exact_object(execution.operation.firing.firing_admission_ref,
        expected_type="firing_admission/v1").metadata
    return admission.get("owner_command_ref") is not None


def stage_saved_result(owner, tx, *, outputs, publication, result_metadata):
    core = owner._core
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, command = material_admission(core.event_store, db, core.task_id,
            ref_payload(outputs.execution.operation.firing.transition_firing_ref))
        completion = completion_evidence(core.event_store, db, core.task_id, admission)
        bindings = semantic_bindings(core.event_store, db, core.task_id, admission, command, completion)
    from .registry._event_store.material_locations import is_location
    from .registry._event_store.material_availability import result_kind
    data = {"protocol": RESULT_PROTOCOL,
        "result_kind": result_kind(command["data"]),
        "command_ref": admission["owner_command_ref"], "firing_ref": admission["transition_firing_ref"],
        "operation_result_ref": result_metadata["operation_result_ref"],
        "completion_ref": ref_payload(publication.completion_ref),
        "output_refs": [ref_payload(item.resource_ref.as_version_ref()) for item in outputs.outputs],
        "binding_refs": bindings, "error": None}
    return owner.control.publish("result", command["command_id"], command["target"], data,
        lineage=tuple(_version_from_payload(ref) for ref in result_lineage(data)),
        transaction=tx, suffix="material-result:" + tx.idempotency_key)


def _known_terminal(owner, outputs):
    core = owner._core
    firing_ref = ref_payload(outputs.execution.operation.firing.transition_firing_ref)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, command = material_admission(core.event_store, db, core.task_id, firing_ref)
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                          (firing_ref["version_id"],)).fetchone()
        if root["state"] not in ("PUBLISHED", "REJECTED"):
            return None
        completion = completion_evidence(core.event_store, db, core.task_id, admission)
        expected_outputs = [{"ordinal": ordinal, "port_id": item.port_id,
            "output_binding_ref": ref_payload(item.output_binding_ref),
            "resource_ref": {"resource_id": str(item.resource_ref.resource_id),
                             "resource_version_id": str(item.resource_ref.resource_version_id)}}
            for ordinal, item in enumerate(outputs.outputs)]
        if (completion.payload["selected_outcome_id"] != outputs.selected_outcome_id
                or completion.payload["ordered_outputs"] != expected_outputs
                or completion.payload["operation_start_event_id"] != str(outputs.execution.start_event_id)
                or completion.payload["operation_execution_lease_ref"] != ref_payload(outputs.execution.operation_execution_lease_ref)
                or completion.payload["invocation_ref"] != ref_payload(outputs.execution.operation.canonical.context.invocation_ref)):
            raise RegistryConflict("material replay changed the original registered executor return")
        result_ref, data = terminal_result(core.event_store, db, core.task_id, admission, command, root)
        if root["state"] == "REJECTED":
            raise MaterialPublishRejected({"status": "REJECTED", "result_ref": result_ref, **data})
    # The exact historical completion remains in the receipt. The Owner API
    # returns its actual current marking, without re-settling the old firing.
    from .registry.module_runtime import hydrate_module_runtime
    return hydrate_module_runtime(core)[2]


def _preflight(owner, outputs):
    from .registry._event_store.address_promotion import validate_address_promotions
    from .registry._event_store.material_control import read_accepted_material_input, validate_active_target
    from .registry.firing_recovery import registered_operation_completion_payload
    core = owner._core
    firing = outputs.execution.operation.firing.transition_firing_ref
    expected_completion = registered_operation_completion_payload(core, outputs)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, command = material_admission(core.event_store, db, core.task_id, ref_payload(firing), current=True)
        completion = completion_evidence(core.event_store, db, core.task_id, admission)
        if dict(completion.payload) != expected_completion:
            raise RegistryConflict("material metadata retry changed the registered executor return")
        params = {key: value for key, value in command["data"].items() if key != "user_action"}
        params["target"] = command["target"]
        validate_active_target(core.event_store, db, core.task_id, params, command["source"])
        from .registry._event_store.material_locations import is_location
        from .registry._event_store.material_availability import is_metadata_operation, is_availability, validate_availability_promotion
        if is_availability(command["data"]):
            validate_availability_promotion(core.event_store, db, core.task_id, admission, command)
        if not is_metadata_operation(command["data"]):
            read_accepted_material_input(core.event_store, db, core.task_id, command["data"]["input_ref"], current=True)
        semantic_bindings(core.event_store, db, core.task_id, admission, command, completion)
        validate_address_promotions(core.event_store, db, task_id=core.task_id,
            transaction_id="preflight-only", publication_commands=({
                "firing_version_id": str(firing.version_id),
                "invocation_version_id": admission["invocation_ref"]["version_id"]},))
        return int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])


def _registered_material_identity(owner, outputs):
    """Validate the caller's complete return before reserving any budget."""
    from .registry.firing_recovery import registered_operation_completion_payload
    core = owner._core
    firing = outputs.execution.operation.firing.transition_firing_ref
    expected = registered_operation_completion_payload(core, outputs)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, _command = material_admission(core.event_store, db, core.task_id,
            ref_payload(firing))
        completion = completion_evidence(core.event_store, db, core.task_id, admission)
        if dict(completion.payload) != expected:
            raise RegistryConflict("material settlement changed the registered executor return")
        return _version_from_payload(admission["owner_command_ref"]), firing


def reject_prepared_material(owner, outputs, *, command_id, reason,
                             expected_registry_ordinal=None):
    """Close only the exact prepared firing; the kernel proves its own seals."""
    from .registry.firing_rejection import stage_material_firing_rejection
    from .registry.module_runtime import hydrate_module_runtime
    core = owner._core
    context = outputs.execution.operation.canonical.context
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        admission, command = material_admission(core.event_store, db, core.task_id,
            ref_payload(context.own_transition_firing_ref))
        if command["data"].get("operation") == "purge_carrier":
            # A physical effect cannot be rolled back by a business rejection.
            # Keep the same published authorization and execution responsibility.
            from .registry.material_publication_attempts import MaterialAttemptRecoveryRequired
            raise MaterialAttemptRecoveryRequired(
                "RECOVERY_REQUIRED: physical purge retains its original authorization and execution responsibility")
        completion = completion_evidence(core.event_store, db, core.task_id, admission)
    _executable, _structure, marking = hydrate_module_runtime(core)
    tx = core.begin(idempotency_key=command_id + ":rejected",
        task_round_id=context.task_round_ref.entity_id, net_instance_id=context.net_instance_ref.entity_id)
    if expected_registry_ordinal is not None:
        tx.expect_registry_ordinal(expected_registry_ordinal)
    rejection_ref = stage_material_firing_rejection(core, tx,
        firing_ref=context.own_transition_firing_ref, invocation_ref=context.invocation_ref,
        owner_command_ref=_version_from_payload(admission["owner_command_ref"]),
        expected_parent_checkpoint_ref=marking.checkpoint_ref,
        registered_completion_event_id=str(completion.event_id), reason=reason)
    data = {"protocol": RESULT_PROTOCOL, "result_kind": "publish_rejected",
        "command_ref": admission["owner_command_ref"], "firing_ref": admission["transition_firing_ref"],
        "rejection_ref": ref_payload(rejection_ref), "registered_completion_event_id": str(completion.event_id),
        "error": reason}
    result = owner.control.publish("result", command["command_id"], command["target"], data,
        lineage=tuple(_version_from_payload(ref) for ref in result_lineage(data)),
        transaction=tx, suffix="material-result:" + tx.idempotency_key)
    tx.commit()
    raise MaterialPublishRejected({"status": "REJECTED", "result_ref": ref_payload(result.as_version_ref()), **data})


def settle_material_operation(owner, outputs, *, command_id, workset_action=None):
    from .registry.event_store import RegistryOrdinalConflict
    from .registry._event_store.address_promotion import ResourceAddressPromotionConflict
    from .registry.firing_success import succeed_module_operation
    from .registry.material_publication_attempts import (
        MaterialAttemptRecoveryRequired,
        check_material_publication_attempt_cut,
        mark_material_publication_ordinal_conflict,
        material_publication_attempt,
        read_material_publication_attempt_state,
        reserve_material_publication_attempt,
    )
    from .workspace_settlement import prepare_firing_workspace_plans
    from cpn.components.agent_loop.models import WorkspaceRevisionConflict
    if workset_action is not None:
        raise RegistryConflict("material settlement cannot substitute a workset outcome")
    terminal = _known_terminal(owner, outputs)
    if terminal is not None:
        return terminal
    core = owner._core
    kernel, repository = owner.operation_repository()
    owner_command_ref, firing_ref = _registered_material_identity(owner, outputs)
    # Caller request keys are not the budget identity. A renamed request or a
    # fresh process reads the same original command/firing reservations.
    settlement_key = "material-settlement:" + str(firing_ref.version_id)
    for _ in range(2):
        state = read_material_publication_attempt_state(core,
            owner_command_ref=owner_command_ref, firing_ref=firing_ref)
        if state["status"] == "TERMINAL":
            terminal = _known_terminal(owner, outputs)
            if terminal is not None:
                return terminal
            raise RegistryConflict("material attempt terminal lacks original closure")
        if state["status"] == "PENDING":
            raise MaterialAttemptRecoveryRequired(
                "RECOVERY_REQUIRED: the original material publication attempt is unresolved")
        if state["status"] == "EXHAUSTED":
            return reject_prepared_material(owner, outputs,
                command_id=settlement_key, reason={"code": "METADATA_CONTENTION",
                    "message": "Registry metadata changed in both permitted publication attempts"})
        if state["status"] not in {"EMPTY", "RETRY_AVAILABLE"}:
            raise RegistryConflict("unknown material publication budget state")
        attempt = reserve_material_publication_attempt(core,
            owner_command_ref=owner_command_ref, firing_ref=firing_ref)
        try:
            with material_publication_attempt(attempt):
                check_material_publication_attempt_cut(core, attempt)
                ordinal = _preflight(owner, outputs)
                if ordinal != attempt.expected_registry_ordinal:
                    # Only the original locked checker can certify a conflict.
                    check_material_publication_attempt_cut(core, attempt)
                    raise RegistryConflict("material preflight changed its original attempt cut")
                plans = prepare_firing_workspace_plans(core, kernel, outputs,
                    idempotency_key=attempt.proposal_key)
                return succeed_module_operation(core, kernel, repository, outputs,
                    idempotency_key=attempt.proposal_key, registration=owner.registration,
                    candidate_publisher=getattr(owner, "revision_candidate_publisher", None),
                    workspace_plans=plans, expected_registry_ordinal=attempt.expected_registry_ordinal,
                    material_result_stager=lambda tx, **kwargs: stage_saved_result(owner, tx, **kwargs))
        except RegistryOrdinalConflict as conflict:
            # Persist only an actual sealed conflict from the original guard.
            # Missing/unknown conflict evidence cannot enable the next slot.
            mark_material_publication_ordinal_conflict(core, attempt, conflict)
            if attempt.slot == 0:
                continue
            reason = {"code": "METADATA_CONTENTION", "message": "Registry metadata changed in both permitted publication attempts"}
        except ResourceAddressPromotionConflict:
            reason = {"code": "BINDING_CONFLICT", "message": "The exact target address binding changed before publication"}
        except WorkspaceRevisionConflict:
            reason = {"code": "WORKSPACE_CONFLICT", "message": "The prepared workspace overlaps a newer committed change"}
        except RegistryConflict as exc:
            code = str(exc).split(":", 1)[0]
            if code not in {"ACTOR_NOT_AUTHORIZED", "EXECUTION_CHANGED", "TARGET_UNAVAILABLE", "INPUT_UNAVAILABLE",
                    "MATERIAL_SCOPE_CHANGED", "MATERIAL_IN_USE", "TARGET_IN_USE"}:
                raise
            reason = {"code": code, "message": str(exc)}
        return reject_prepared_material(owner, outputs, command_id=settlement_key, reason=reason)
    raise AssertionError("material metadata attempts exceeded their bound")
