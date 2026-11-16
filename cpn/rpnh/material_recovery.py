"""Explicit recovery of one original prepared material firing.

The scope is a sealed, process-local read proof. The Registry remains the only
authority: preflight, the fresh writer, and the original publication lock each
check the original command, completion and retained business responsibilities.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path

from .registration import Registration
from .registry._registry import _RegistryCore
from .registry.errors import ResourceIntegrityFault
from .registry.event_store import RegistryConflict, RegistryOrdinalConflict, StaleWriterError
from .registry.models import VersionRef
from .registry.resource_service import _ResourceServiceKernel
from .registry.run_authority import RunReadCut, current_run_execution_authority
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload
from .registry._event_store.material_recovery import (
    active_claim_records, current_marking, material_budget_event_inventory, recovery_facts,
)
from .registry._event_store.material_settlement import material_admission, terminal_result


_SCOPE_SEAL = object()


@dataclass(frozen=True, slots=True)
class MaterialRecoveryScope:
    """A minted exact read boundary, never a persisted permission or lease."""

    owner_command_ref: VersionRef
    firing_ref: VersionRef
    run_dir: Path
    task_id: object
    writer_epoch: int
    registry_ordinal: int
    facts_json: bytes = field(repr=False)
    _seal: object = field(repr=False, compare=False)
    base_registry_ordinal: int | None = None

    def __post_init__(self):
        if self._seal is not _SCOPE_SEAL:
            raise TypeError("material recovery scope must be minted from original facts")


@dataclass(frozen=True, slots=True)
class MaterialFiringResumeResult:
    """Only RESUMED supplies an owner; terminal replays are read-only."""

    recovery_status: str
    receipt: dict
    owner: object | None = None
    reason: str | None = None


def _require_scope(core, scope):
    if (type(scope) is not MaterialRecoveryScope or scope._seal is not _SCOPE_SEAL
            or Path(core.run_dir).resolve() != scope.run_dir
            or core.task_id != scope.task_id):
        raise ResourceIntegrityFault("material recovery requires its exact minted scope")


def _mint_scope(core, command_ref, firing_ref):
    from .registry.material_publication_attempts import MaterialAttemptRecoveryRequired

    cut = RunReadCut.capture(core)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        facts = recovery_facts(core.event_store, db, task_id=core.task_id,
            owner_command_ref=ref_payload(command_ref), firing_ref=ref_payload(firing_ref))
        state, _inventory = material_budget_event_inventory(core.event_store, db,
            task_id=core.task_id, owner_command_ref=ref_payload(command_ref),
            firing_ref=ref_payload(firing_ref))
        if state["status"] == "PENDING":
            raise MaterialAttemptRecoveryRequired(
                "RECOVERY_REQUIRED: original material publication attempt is unresolved")
    cut.assert_unchanged(core)
    return MaterialRecoveryScope(command_ref, firing_ref, Path(core.run_dir).resolve(),
        core.task_id, cut.writer_epoch, cut.physical_head, canonical_json(facts), _SCOPE_SEAL,
        base_registry_ordinal=cut.physical_head)


def validate_material_recovery_scope(core, scope, *, current_run=None):
    """Do not refresh a changed bystander set into a new recovery plan."""
    _require_scope(core, scope)
    expected_epoch = scope.writer_epoch if core.read_only else scope.writer_epoch + 1
    cut = RunReadCut.capture(core)
    if (cut.writer_epoch != expected_epoch or core.writer_epoch != expected_epoch
            or cut.physical_head != scope.registry_ordinal):
        raise ResourceIntegrityFault("material recovery original read boundary changed")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        facts = recovery_facts(core.event_store, db, task_id=core.task_id,
            owner_command_ref=ref_payload(scope.owner_command_ref), firing_ref=ref_payload(scope.firing_ref))
        base = scope.registry_ordinal if scope.base_registry_ordinal is None else scope.base_registry_ordinal
        if type(base) is not int or base > scope.registry_ordinal:
            raise ResourceIntegrityFault("material recovery has an invalid original budget cut")
        if base != scope.registry_ordinal:
            _state, inventory = material_budget_event_inventory(core.event_store, db,
                task_id=core.task_id, owner_command_ref=ref_payload(scope.owner_command_ref),
                firing_ref=ref_payload(scope.firing_ref))
            delta = db.execute("SELECT event_id,transaction_id FROM events WHERE ordinal>? "
                "AND ordinal<=?", (base, scope.registry_ordinal)).fetchall()
            if not delta or any(inventory.get(row["event_id"]) != row["transaction_id"] for row in delta):
                raise ResourceIntegrityFault("material recovery cut contains non-budget facts")
    if (canonical_json(facts) != scope.facts_json
            or (current_run is not None and facts["run"] != dict(current_run))):
        raise ResourceIntegrityFault("material recovery original responsibilities changed")
    cut.assert_unchanged(core)
    return facts


def _advance_scope_for_attempt(core, scope, attempt):
    """Advance only over this fresh reservation, retaining all frozen B facts."""
    from .registry.material_publication_attempts import _token

    _require_scope(core, scope)
    _token(attempt, core=core)
    if (attempt.owner_command_ref != scope.owner_command_ref or attempt.firing_ref != scope.firing_ref
            or attempt.writer_epoch != scope.writer_epoch + 1):
        raise ResourceIntegrityFault("material recovery reservation differs from its exact selection")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        state, _inventory = material_budget_event_inventory(core.event_store, db,
            task_id=core.task_id, owner_command_ref=ref_payload(scope.owner_command_ref),
            firing_ref=ref_payload(scope.firing_ref))
        latest = state["attempts"][-1]
        if (state["status"] != "PENDING"
                or latest["body"]["material_publication_attempt_ref"] != ref_payload(attempt.attempt_ref)
                or latest["reservation_transaction_id"] != attempt.reservation_transaction_id
                or latest["reservation_base_ordinal"] != scope.registry_ordinal
                or latest["reservation_end_ordinal"] != attempt.expected_registry_ordinal):
            raise ResourceIntegrityFault("material recovery reservation changed its original base cut")
    advanced = replace(scope, registry_ordinal=attempt.expected_registry_ordinal,
        base_registry_ordinal=(scope.registry_ordinal if scope.base_registry_ordinal is None
                               else scope.base_registry_ordinal))
    validate_material_recovery_scope(core, advanced)
    return advanced


def material_recovery_receipt(core, command_ref, firing_ref):
    """Original Owner metadata only; this never grants resource delivery."""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        found = material_admission(core.event_store, db, core.task_id, ref_payload(firing_ref))
        if found is None or found[0]["owner_command_ref"] != ref_payload(command_ref):
            raise RegistryConflict("material recovery exact command/firing pair differs")
        admission, command = found
        root = db.execute("SELECT * FROM firing_publications WHERE firing_version_id=?",
                          (str(firing_ref.version_id),)).fetchone()
        receipt = {"protocol": "rpnh/material_receipt_view/v1", "command_id": command["command_id"],
            "command_ref": ref_payload(command_ref), "status": "ADMITTED",
            "admission_ref": admission["firing_admission_ref"], "firing_ref": ref_payload(firing_ref),
            "invocation_ref": admission["invocation_ref"]}
        if root["state"] in {"PUBLISHED", "REJECTED"}:
            result_ref, data = terminal_result(core.event_store, db, core.task_id, admission, command, root)
            receipt.update(status=root["state"], result_ref=result_ref, error=data["error"])
            keys = (("completion_ref", "operation_result_ref", "output_refs", "binding_refs")
                if root["state"] == "PUBLISHED" else ("rejection_ref", "registered_completion_event_id"))
            receipt.update({key: data[key] for key in keys})
        elif root["state"] != "PROVISIONAL":
            receipt["status"] = "RECOVERY_REQUIRED"
        else:
            rows = db.execute("SELECT e.event_id FROM events e JOIN transactions t "
                "ON t.transaction_id=e.transaction_id JOIN firing_temporary_members m "
                "ON m.member_kind='event' AND m.member_identity=e.event_id "
                "WHERE t.status='committed' AND m.firing_version_id=? "
                "AND e.event_type='registered_operation_completion_recorded/v1'",
                (str(firing_ref.version_id),)).fetchall()
            if rows:
                from .registry._event_store.material_settlement import completion_evidence
                completion_evidence(core.event_store, db, core.task_id, admission)
                receipt["status"] = "PREPARED"
        return receipt


def validate_completed_material_recovery(core, scope):
    """Prove the real selected closure and the untouched remaining roots."""
    _require_scope(core, scope)
    cut = RunReadCut.capture(core)
    if core.read_only or core.writer_epoch != scope.writer_epoch + 1 or cut.writer_epoch != core.writer_epoch:
        raise ResourceIntegrityFault("material recovery entry requires its sole fresh writer")
    before = json.loads(scope.facts_json)
    receipt = material_recovery_receipt(core, scope.owner_command_ref, scope.firing_ref)
    if receipt["status"] not in {"PUBLISHED", "REJECTED"}:
        raise ResourceIntegrityFault("material recovery entry lacks its actual original terminal")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        run, checkpoint, tokens = current_marking(core.event_store, db, core.task_id)
        if run["status"] == "terminal":
            from .registry._event_store.terminal_management import _original_terminal
            terminal = _original_terminal(core.event_store, db, core.task_id)
            if terminal["authority"] != run or before["run"]["status"] != "terminal":
                raise ResourceIntegrityFault("terminal recovery changed its original business closure class")
        retained = active_claim_records(core.event_store, db, core.task_id, checkpoint)
        expected = [record for record in before["active"]
                    if record["firing"]["transition_firing_ref"] != ref_payload(scope.firing_ref)]
        if canonical_json(retained) != canonical_json(expected):
            raise ResourceIntegrityFault("material recovery changed another original firing")
        root = db.execute("SELECT p.*,t.writer_epoch FROM firing_publications p "
            "JOIN transactions t ON t.transaction_id=p.published_transaction_id "
            "WHERE p.firing_version_id=? AND t.status='committed'",
            (str(scope.firing_ref.version_id),)).fetchone()
        if root is None or root["writer_epoch"] != core.writer_epoch:
            raise ResourceIntegrityFault("material recovery terminal belongs to another writer")
        if (run["run_ref"] != before["run"]["run_ref"]
                or run["declaration_ref"] != before["run"]["declaration_ref"]
                or checkpoint["net_instance_ref"] != before["checkpoint"]["net_instance_ref"]):
            raise ResourceIntegrityFault("material recovery changed the adopted execution")
        if receipt["status"] == "REJECTED":
            if checkpoint != before["checkpoint"] or tokens != before["tokens"]:
                raise ResourceIntegrityFault("material rejection changed the original marking")
        elif (root["marking_checkpoint_version_id"] != checkpoint["marking_checkpoint_ref"]["version_id"]
                or checkpoint["previous_checkpoint_ref"] != before["checkpoint"]["marking_checkpoint_ref"]
                or checkpoint["transition_firing_refs"] != [ref_payload(scope.firing_ref)]):
            raise ResourceIntegrityFault("material recovery did not create its exact one successor")
        present = {canonical_json(ref) for ref in checkpoint["token_refs"]}
        if any(canonical_json(ref) not in present for record in retained
               for ref in record["firing"]["claimed_input_refs"]):
            raise ResourceIntegrityFault("material recovery lost an original business input")
    cut.assert_unchanged(core)
    return cut


def recover_prepared_material(owner, recovery, *, command_id):
    """Settle the original completed work once, without re-running HOST."""
    from .material_settlement import _preflight, reject_prepared_material, stage_saved_result
    from .registry._event_store.address_promotion import ResourceAddressPromotionConflict
    from .registry.firing_recovery import (
        RegisteredOperationFiringRecovery, recover_registered_operation_completion,
        registered_operation_completion_payload,
    )
    from .registry.material_publication_attempts import (
        MaterialAttemptRecoveryRequired, check_material_publication_attempt_cut,
        mark_material_publication_ordinal_conflict, material_publication_attempt,
        read_material_publication_attempt_state, reserve_material_publication_attempt,
    )
    from .workspace_settlement import prepare_firing_workspace_plans
    from cpn.components.agent_loop.models import WorkspaceRevisionConflict

    core, scope = owner._core, recovery.material_recovery_scope
    facts = validate_material_recovery_scope(core, scope)
    selected = next(record for record in facts["active"]
                    if record["firing"]["transition_firing_ref"] == ref_payload(scope.firing_ref))
    if (type(recovery) is not RegisteredOperationFiringRecovery or recovery.checkpoint_reentry
            or recovery.outputs.execution.operation.firing.transition_firing_ref != scope.firing_ref
            or str(recovery.completion_event_id) != facts["completion_event_id"]
            or recovery.stale_writer_fencing_epoch != selected["admission"]["writer_fencing_epoch"]
            or dict(recovery.completion_payload) != facts["completion_payload"]
            or registered_operation_completion_payload(core, recovery.outputs) != facts["completion_payload"]):
        raise ResourceIntegrityFault("material recovery changed its exact selected executor return")
    kernel, repository = owner.operation_repository()
    _ref, current = current_run_execution_authority(core, kernel)
    state = read_material_publication_attempt_state(core,
        owner_command_ref=scope.owner_command_ref, firing_ref=scope.firing_ref)
    if state["status"] == "EXHAUSTED":
        # Both original comparisons already failed. This closes the prepared
        # responsibility without a third plan, new Start, or repeated HOST.
        return reject_prepared_material(owner, recovery.outputs, command_id=command_id,
            reason={"code": "METADATA_CONTENTION",
                "message": "Registry metadata changed in both permitted publication attempts"},
            expected_registry_ordinal=scope.registry_ordinal)
    if state["status"] not in {"EMPTY", "RETRY_AVAILABLE"}:
        raise MaterialAttemptRecoveryRequired(
            "RECOVERY_REQUIRED: original material publication attempt is unresolved")
    attempt = reserve_material_publication_attempt(core,
        owner_command_ref=scope.owner_command_ref, firing_ref=scope.firing_ref)
    scope = _advance_scope_for_attempt(core, scope, attempt)
    recovery = replace(recovery, material_recovery_scope=scope)
    try:
        with material_publication_attempt(attempt):
            check_material_publication_attempt_cut(core, attempt)
            if _preflight(owner, recovery.outputs) != scope.registry_ordinal:
                check_material_publication_attempt_cut(core, attempt)
                raise ResourceIntegrityFault("material recovery changed before publication planning")
            plans = prepare_firing_workspace_plans(core, kernel, recovery.outputs,
                idempotency_key=attempt.proposal_key)
            check_material_publication_attempt_cut(core, attempt)
            return recover_registered_operation_completion(core, kernel, repository, recovery,
                current_run=current, idempotency_key=attempt.proposal_key, registration=owner.registration,
                candidate_publisher=getattr(owner, "revision_candidate_publisher", None), workspace_plans=plans,
                material_result_stager=lambda tx, **kwargs: stage_saved_result(owner, tx, **kwargs))
    except RegistryOrdinalConflict as conflict:
        mark_material_publication_ordinal_conflict(core, attempt, conflict)
        # The conflict is durable, but its intervening facts are not permission
        # to replace the frozen B cut and plan again in this recovery.
        raise MaterialAttemptRecoveryRequired(
            "RECOVERY_REQUIRED: material comparison changed the frozen recovery cut") from conflict
    except ResourceAddressPromotionConflict:
        reason = {"code": "BINDING_CONFLICT", "message": "The exact target address binding changed before recovery publication"}
    except WorkspaceRevisionConflict:
        reason = {"code": "WORKSPACE_CONFLICT", "message": "The prepared workspace overlaps a newer committed change"}
    except RegistryConflict as exc:
        code = str(exc).split(":", 1)[0]
        if code not in {"ACTOR_NOT_AUTHORIZED", "EXECUTION_CHANGED", "TARGET_UNAVAILABLE", "INPUT_UNAVAILABLE"}:
            raise
        reason = {"code": code, "message": str(exc)}
    # Never recapture a changed business firing after a failed publication.
    validate_material_recovery_scope(core, scope)
    return reject_prepared_material(owner, recovery.outputs, command_id=command_id,
        reason=reason, expected_registry_ordinal=scope.registry_ordinal)


def resume_material_firing(registration, *, run_dir, model_condition,
                           owner_command_ref, firing_ref, catalog=None,
                           host_execution_bindings=None):
    """Explicitly recover one command's original firing while retaining others."""
    from cpn.update_context import reject_candidate_runtime
    reject_candidate_runtime()
    if (not isinstance(registration, Registration) or type(model_condition) is not str or not model_condition
            or not isinstance(owner_command_ref, VersionRef) or owner_command_ref.entity_type != "resource_version/v1"
            or not isinstance(firing_ref, VersionRef) or firing_ref.entity_type != "transition_firing/v1"):
        raise TypeError("material recovery requires registration, exact command/firing refs and model")
    from .run import _load_resume_material, resume_run
    preflight = _RegistryCore(Path(run_dir), create=False, read_only=True, catalog=catalog)
    current_run_execution_authority(preflight, _ResourceServiceKernel(preflight),
                                    expected_model_condition=model_condition)
    registration.bind_schema_catalog(preflight.catalog)
    _load_resume_material(preflight, registration)
    receipt = material_recovery_receipt(preflight, owner_command_ref, firing_ref)
    if receipt["status"] in {"PUBLISHED", "REJECTED"}:
        return MaterialFiringResumeResult("ALREADY_SETTLED", receipt)
    try:
        scope = _mint_scope(preflight, owner_command_ref, firing_ref)
        owner = resume_run(registration, run_dir=run_dir, model_condition=model_condition,
            catalog=catalog, host_execution_bindings=host_execution_bindings,
            _material_recovery_scope=scope)
    except (RegistryConflict, ResourceIntegrityFault, StaleWriterError) as exc:
        # The original command/root remains responsible. A reserved fence alone
        # is not a successful resume or an externally visible terminal fact.
        # A write may already have committed before the caller lost its result.
        # Query that exact original root before reporting unresolved work.
        current_receipt = material_recovery_receipt(preflight, owner_command_ref, firing_ref)
        if current_receipt["status"] in {"PUBLISHED", "REJECTED"}:
            return MaterialFiringResumeResult("ALREADY_SETTLED", current_receipt)
        return MaterialFiringResumeResult("RECOVERY_REQUIRED", current_receipt, reason=str(exc))
    receipt = material_recovery_receipt(owner._core, owner_command_ref, firing_ref)
    return MaterialFiringResumeResult("RESUMED", receipt, owner=owner)


__all__ = ("MaterialFiringResumeResult", "resume_material_firing")
