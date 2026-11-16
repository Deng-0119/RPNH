"""Viewer wiring for the original two-phase, one-blob purge contract.

Only the original HOST/Owner submit port writes. Reads validate original
command, authorization and result evidence; no frontend plan, lifecycle cache,
physical tool, recovery authority or deletion path is introduced here.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json

from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor, exact_prepared
from cpn.rpnh.registry._event_store.material_control import (
    OWNER_SCHEMA, COMMAND_PROTOCOL, canonical_rows, control_body, row_ref,
    reference, read_accepted_material_command, validate_active_target,
)
from cpn.rpnh.registry._event_store.material_purge import (
    AUTH_SCHEMA, RESULT_SCHEMA, AUTH_EXECUTOR, AUTH_IDENTITY, AUTH_CONTRACTS,
    PURGE_EXECUTOR, PURGE_IDENTITY, PURGE_CONTRACTS, PURGE_TOOL, TOOL_IDENTITY, TOOL_CONTRACTS,
    _selection, read_purge_authorization, validate_purge_baseline, validate_purge_params,
)
from .material_carriers import CarrierRequests, _PROOF_ERRORS
from .material_management import MaterialManagementError, MAX_REQUEST_BYTES

SCHEMA = "rpnh/viewer_material_purge/v1"
A, B = "authorize_purge", "purge_carrier"
SCOPE = "one_exact_immutable_blob"


def closed_purge_catalog(source_id, *, reason="management_not_configured"):
    return {"schema_version": SCHEMA, "action": "purge_catalog", "source_id": source_id,
        "supported": False, "capabilities": {A: False, B: False}, "reason": reason,
        "resource_ref": None, "as_of_ordinal": None, "logical_availability": None,
        "inspection": None, "choices": [], "scope": SCOPE, "all_carriers_cleared": False,
        "response_content": "metadata_and_proof_refs_only"}


class PurgeRequests:
    def __init__(self, viewer):
        self.viewer, self.owner = viewer, viewer._owner
        self.core, self.store = self.owner._core, self.owner._core.event_store
        self.task_ref = ref_payload(self.owner.identity.task_ref)
        self.carriers = CarrierRequests(viewer)

    @contextmanager
    def _cut(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            self.carriers._session(db)
            yield db

    def _configured(self, phase):
        return (self.viewer._purge_authorization_targets if phase == A
                else self.viewer._purge_execution_targets)

    def _actor(self):
        return {"actor": self.viewer._session.actor,
                "session_ref": ref_payload(self.viewer._session.ref)}

    def _gate(self, phase, target=None):
        targets = self._configured(phase)
        if not targets:
            raise MaterialManagementError("action_unavailable", 403)
        if target is not None and not any(item.transition_id == target for item in targets):
            raise MaterialManagementError("target_not_allowed", 403)

    def _target(self, db, phase, target, execution):
        transition = validate_active_target(self.store, db, self.core.task_id,
            {"operation": phase, "target": target, "execution": execution},
            ref_payload(self.owner.principal_ref))
        binding = exact_descriptor(db, self.core.object_store, self.core.task_id,
            transition["operation_binding_ref"])
        spec = exact_descriptor(db, self.core.object_store, self.core.task_id, binding["operation_spec_ref"])
        expected = ((AUTH_EXECUTOR, AUTH_IDENTITY, AUTH_CONTRACTS) if phase == A
                    else (PURGE_EXECUTOR, PURGE_IDENTITY, PURGE_CONTRACTS))
        registered = self.owner.registration.declaration("executor", expected[0])
        if (spec["executor_key"] != expected[0] or spec["implementation_identity"] != expected[1]
                or spec["implementation_contracts"] != expected[2]
                or registered["identity"] != expected[1] or registered["contracts"] != expected[2]):
            raise MaterialManagementError("target_unavailable", 409)
        if phase == B:
            tool = self.owner.registration.declaration("tool", PURGE_TOOL)
            if tool["identity"] != TOOL_IDENTITY or tool["contracts"] != TOOL_CONTRACTS:
                raise MaterialManagementError("target_unavailable", 409)

    def _targets(self, db, phase, **scope):
        if not self._configured(phase):
            return [], "action_unavailable"
        try:
            execution = self.viewer._execution(db)
        except MaterialManagementError as exc:
            if exc.code not in {"execution_changed", "target_unavailable"}:
                raise
            return [], exc.code
        targets, reason = [], "target_unavailable"
        for item in self._configured(phase):
            try:
                self._target(db, phase, item.transition_id, execution)
            except MaterialManagementError as exc:
                if exc.code not in {"execution_changed", "target_unavailable"}:
                    raise
                reason = exc.code
                continue
            except RegistryConflict as exc:
                if not str(exc).startswith(("EXECUTION_CHANGED", "TARGET_UNAVAILABLE")):
                    raise
                reason = "execution_changed" if str(exc).startswith("EXECUTION_CHANGED") else "target_unavailable"
                continue
            targets.append({"id": item.id, "label": item.label, "params": {
                "protocol": COMMAND_PROTOCOL, "operation": phase, "target": item.transition_id,
                "execution": execution, **scope}})
        return targets, None if targets else reason

    def _catalog(self, db, selected):
        result = closed_purge_catalog(self.viewer.source_id)
        result.update(supported=True, capabilities={A: bool(self._configured(A)), B: bool(self._configured(B))},
            as_of_ordinal=self.carriers._upper(db), reason="selection_required")
        if not self._configured(A):
            result["reason"] = "authorization_unavailable"
            return result
        if not self.viewer._carrier_inspection:
            result["reason"] = "carrier_inspection_unavailable"
            return result
        if selected is None:
            return result
        observed = self.carriers._inspect(db, selected, False)
        inspection, availability = observed["inspection"], observed["logical_availability"]
        result.update(resource_ref=selected, inspection=inspection, logical_availability=availability)
        if availability["availability"] != "disabled" or not availability["effect_refs"]:
            result["reason"] = ("purge_not_disabled" if availability["availability"] == "available"
                                else "material_availability_unknown")
            return result
        targets, reason = self._targets(db, A)
        result["reason"] = reason
        if not targets:
            return result
        for entry in inspection["carriers"]:
            if entry["role"] not in {"semantic_output", "workspace_file"}:
                continue
            baseline, _proof = _selection(self.store, db, self.core.task_id,
                inspection["command_ref"], entry["carrier_ref"])
            if baseline["target_resource_ref"] != selected or baseline["carrier_role"] != entry["role"]:
                raise RegistryConflict("PURGE_UNAUTHORIZED: selected carrier differs from original proof")
            result["choices"].append({"carrier_ref": entry["carrier_ref"], "carrier_role": entry["role"],
                "authorization_targets": [{**target, "params": {**target["params"],
                    "material_command_ref": inspection["command_ref"], "carrier_ref": entry["carrier_ref"]}}
                    for target in targets]})
        return result

    def _raw_ref(self, db, command_ref):
        item = exact_prepared(db, self.core.object_store, self.core.task_id,
            reference(command_ref, "resource_version/v1"))
        if item.metadata.get("content_schema_ref") != OWNER_SCHEMA:
            raise MaterialManagementError("command_content_mismatch", 409)
        if item.size > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        body = control_body(self.store, item)
        data = body.get("data", {})
        if (body.get("kind") != "command" or data.get("protocol") != COMMAND_PROTOCOL
                or data.get("operation") not in {A, B}):
            raise MaterialManagementError("command_content_mismatch", 409)
        action = data.get("user_action", {})
        if (body.get("source") != ref_payload(self.owner.principal_ref)
                or item.metadata.get("task_ref") != self.task_ref
                or data.get("execution", {}).get("task_ref") != self.task_ref
                or any(action.get(key) != value for key, value in self._actor().items())):
            raise MaterialManagementError("access_changed", 403)
        return body

    def _raw_key(self, db, key):
        rows = canonical_rows(self.store, db, self.core.task_id,
            conditions="AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_control_kind')='command' "
            "AND json_extract(o.metadata_json,'$.descriptors.command_id')=?", parameters=(OWNER_SCHEMA, key))
        if not rows:
            if db.execute("SELECT 1 FROM transactions WHERE task_id=? AND idempotency_key=?",
                    (str(self.core.task_id), key)).fetchone() is not None:
                raise MaterialManagementError("command_content_mismatch", 409)
            return None
        if len(rows) != 1:
            raise RegistryConflict("PURGE_UNKNOWN: original command identity is ambiguous")
        ref = row_ref(rows[0])
        body = self._raw_ref(db, ref)
        if body["command_id"] != key:
            raise MaterialManagementError("command_content_mismatch", 409)
        return ref, body

    @staticmethod
    def _params(body):
        return {**{key: value for key, value in body["data"].items() if key != "user_action"},
                "target": body["target"]}

    @staticmethod
    def _selection_view(value):
        return {key: value[key] for key in
            ("material_command_ref", "target_resource_ref", "carrier_ref", "carrier_role")}

    def _published_a_hint(self, db, command_ref):
        rows = canonical_rows(self.store, db, self.core.task_id, object_type="firing_admission/v1",
            conditions="AND json_extract(o.metadata_json,'$.owner_command_ref.version_id')=?",
            parameters=(command_ref["version_id"],))
        if len(rows) != 1:
            return False
        firing = json.loads(rows[0]["metadata_json"])["transition_firing_ref"]
        root = db.execute("SELECT state FROM firing_publications WHERE firing_version_id=?",
            (firing["version_id"],)).fetchone()
        # Only an early refusal. Full original receipt/AUTH proof is still
        # mandatory before this branch can disclose anything.
        return root is not None and root["state"] == "PUBLISHED"

    def _lookup_gate(self, db, command_ref, body):
        phase = body["data"]["operation"]
        if any(item.transition_id == body["target"] for item in self._configured(phase)):
            return False
        if phase != A or not self._configured(B):
            self._gate(phase, body["target"])
        if not self._published_a_hint(db, command_ref):
            raise MaterialManagementError("action_unavailable", 403)
        targets, reason = self._targets(db, B)
        if not targets:
            raise MaterialManagementError(reason, 409)
        return True

    def _resource_document(self, db, ref, schema):
        try:
            item = exact_prepared(db, self.core.object_store, self.core.task_id,
                reference(ref, "resource_version/v1"))
        except RegistryConflict as exc:
            if schema == AUTH_SCHEMA:
                raise RegistryConflict("PURGE_UNAUTHORIZED: authorization is not an exact canonical output") from exc
            raise
        if (item.metadata.get("content_schema_ref") != schema
                or item.metadata.get("origin_kind") != "petri_output"
                or item.metadata.get("task_ref") != self.task_ref):
            raise RegistryConflict("PURGE_UNAUTHORIZED: output differs from its exact original schema or task")
        if item.size > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        body = json.loads(self.core.object_store.read_registered(item))
        self.core.catalog.validate_schema_ref(schema, body)
        return item, body

    def _authorization(self, db, authorization_ref):
        _item, initial = self._resource_document(db, authorization_ref, AUTH_SCHEMA)
        raw = self._raw_ref(db, initial["authorization_command_ref"])
        if raw["data"]["operation"] != A:
            raise RegistryConflict("PURGE_UNAUTHORIZED: authorization producer is not original Phase A")
        # Current reader/actor is checked above before the full archive-backed
        # normal-Success authorization proof. A's current HOST gate is not
        # required when an independently allowed B uses this historical AUTH.
        body, original = read_purge_authorization(self.store, db, self.core.task_id, authorization_ref)
        if body != initial or original != raw or body["source_task_ref"] != self.task_ref:
            raise RegistryConflict("PURGE_UNAUTHORIZED: authorization changed its exact command identity")
        return body

    def _command_proof(self, db, ref, raw):
        if raw["data"]["operation"] == B:
            self._authorization(db, raw["data"]["authorization_ref"])
        original = read_accepted_material_command(self.store, db, self.core.task_id, ref, current=False)
        if original != raw:
            raise RegistryConflict("PURGE_UNKNOWN: original command proof differs from its exact control")
        return original

    def _receipt(self, key):
        with self._cut() as db:
            found = self._raw_key(db, key)
            if found is not None:
                self._lookup_gate(db, *found)
        receipt = self.owner.control.materials.read_receipt(session=self.viewer._session,
            material_command_id=key)
        with self._cut() as db:
            result = {"schema_version": SCHEMA, "action": "read_purge_receipt",
                "source_id": self.viewer.source_id, "command_id": key, "status": receipt["status"],
                "operation": None, "params": None, "selection": None, "authorization_ref": None,
                "authorization": None, "purge_result_ref": None, "purge_result": None,
                "execute_targets": [], "execute_reason": "not_found_at_read_cut",
                "receipt": receipt, "as_of_ordinal": self.carriers._upper(db),
                "scope": SCOPE, "all_carriers_cleared": False}
            found = self._raw_key(db, key)
            if receipt["status"] == "NOT_FOUND_AT_READ_CUT":
                if found is not None:
                    # A command appeared after the original absence cut.
                    # Do not attach it to an earlier absent receipt.
                    raise MaterialManagementError("result_unknown", 503)
                return result
            if found is None or found[0] != receipt["command_ref"] or receipt["command_id"] != key:
                raise RegistryConflict("PURGE_UNKNOWN: receipt differs from original command identity")
            ref, raw = found
            b_only = self._lookup_gate(db, ref, raw)
            phase = raw["data"]["operation"]
            self._command_proof(db, ref, raw)
            result.update(operation=phase, params=self._params(raw))
            published = receipt["status"] == "PUBLISHED"
            if published and (receipt.get("result_kind") != ("purge_authorized" if phase == A else "carrier_purged")
                    or len(receipt.get("output_refs", ())) != 1 or receipt.get("binding_refs") != []
                    or receipt.get("error") is not None):
                raise RegistryConflict("PURGE_UNKNOWN: published receipt lacks its exact phase outcome")
            if phase == A:
                if not published:
                    if b_only:
                        raise MaterialManagementError("action_unavailable", 403)
                    selection, _inspection = _selection(self.store, db, self.core.task_id,
                        raw["data"]["material_command_ref"], raw["data"]["carrier_ref"])
                    result.update(selection=self._selection_view(selection), execute_reason="authorization_not_published")
                    return result
                auth_ref = receipt["output_refs"][0]
                auth = self._authorization(db, auth_ref)
                if auth["authorization_command_ref"] != ref:
                    raise RegistryConflict("PURGE_UNAUTHORIZED: receipt names another authorization command")
                targets, reason = self._targets(db, B, authorization_ref=auth_ref)
                if b_only and not targets:
                    raise MaterialManagementError(reason, 409)
                result.update(authorization_ref=auth_ref, authorization=auth,
                    selection=self._selection_view(auth), execute_targets=targets, execute_reason=reason)
            else:
                auth_ref = raw["data"]["authorization_ref"]
                auth = self._authorization(db, auth_ref)
                result.update(authorization_ref=auth_ref, authorization=auth,
                    selection=self._selection_view(auth), execute_reason="phase_not_authorization")
                if published:
                    output = receipt["output_refs"][0]
                    item, physical = self._resource_document(db, output, RESULT_SCHEMA)
                    if (item.metadata.get("producer_ref") != receipt["invocation_ref"]
                            or physical["source_task_ref"] != self.task_ref
                            or physical["authorization_ref"] != auth_ref
                            or physical["carrier_ref"] != auth["carrier_ref"]):
                        raise RegistryConflict("PURGE_UNAUTHORIZED: physical result differs from original authorization")
                    result.update(purge_result_ref=output, purge_result=physical)
            return result

    def _submit(self, params):
        request = params["params"]
        phase = A if params["action"] == "purge_authorize" else B
        validate_purge_params(request)
        if request["operation"] != phase:
            raise MaterialManagementError("invalid_query")
        self._gate(phase, request["target"])
        with self._cut() as db:
            previous = self._raw_key(db, params["command_id"])
            if previous is not None:
                ref, raw = previous
                if self._params(raw) != request:
                    raise MaterialManagementError("command_content_mismatch", 409)
                self._command_proof(db, ref, raw)
            else:
                if request["execution"] != self.viewer._execution(db):
                    raise MaterialManagementError("execution_changed", 409)
                self._target(db, phase, request["target"], request["execution"])
                if phase == B:
                    self._authorization(db, request["authorization_ref"])
                validate_purge_baseline(self.store, db, self.core.task_id,
                    {**request, "user_action": self._actor()})
        sealed = self.viewer._host.material_action(self.viewer._session,
            owner_command="material_submit", command_id=params["command_id"], params=request)
        return self.owner.command("material_submit", request, command_id=params["command_id"],
            collaboration_action=sealed)

    def dispatch(self, params):
        if params["source_id"] != self.viewer.source_id:
            raise MaterialManagementError("source_not_allowed", 403)
        if not self._configured(A) and not self._configured(B):
            if params["action"] == "purge_catalog":
                return closed_purge_catalog(self.viewer.source_id, reason="action_unavailable")
            raise MaterialManagementError("action_unavailable", 403)
        try:
            if params["action"] == "purge_catalog":
                with self._cut() as db:
                    result = self._catalog(db, params.get("resource_ref"))
            elif params["action"] == "read_purge_receipt":
                result = self._receipt(params["material_command_id"])
            elif params["action"] in {"purge_authorize", "purge_execute"}:
                result = self._submit(params)
            else:
                raise MaterialManagementError("invalid_query")
        except MaterialManagementError:
            raise
        except RegistryConflict as exc:
            for prefix, code, status in (
                ("ACTOR_NOT_AUTHORIZED", "access_changed", 403),
                ("COMMAND_CONTENT_MISMATCH", "command_content_mismatch", 409),
                ("INVALID_COMMAND", "invalid_query", 400),
                ("PURGE_NOT_DISABLED", "purge_not_disabled", 409),
                ("PURGE_UNAUTHORIZED", "purge_unauthorized", 403),
                ("PURGE_UNSUPPORTED", "purge_unsupported", 409),
                ("PURGE_IN_USE", "purge_in_use", 409),
                ("EXECUTION_CHANGED", "execution_changed", 409),
                ("TARGET_UNAVAILABLE", "target_unavailable", 409)):
                if str(exc).startswith(prefix):
                    raise MaterialManagementError(code, status) from exc
            raise MaterialManagementError("purge_unknown", 503) from exc
        except _PROOF_ERRORS as exc:
            raise MaterialManagementError("purge_unknown", 503) from exc
        if len(canonical_json(result)) > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        return deepcopy(result)
