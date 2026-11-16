"""Viewer projection of the original finite disable plan and Owner commands.

There is no retained plan cache, independent result, or lifecycle authority.
Every lookup reads original Registry facts and rechecks this HOST session.
Only an explicit plan POST or command POST invokes an original Owner write.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_prepared, exact_descriptor
from cpn.rpnh.registry._event_store.material_control import (
    canonical_rows, row_ref, require_session, reference, validate_active_target,
    read_accepted_material_command,
)
from cpn.rpnh.registry._event_store.material_availability import (
    PLAN_SCHEMA, PLAN_ROLE, PLAN_PROTOCOL, EXECUTOR, IDENTITY, CONTRACTS,
    _targets, _target, read_plan, validate_plan_params, validate_availability_params,
)
from .material_management import MaterialManagementError, MAX_REQUEST_BYTES

SCHEMA = "rpnh/viewer_material_disable/v1"


def closed_disable_catalog(source_id):
    return {"schema_version": SCHEMA, "action": "disable_catalog", "source_id": source_id,
        "supported": False, "prepare_allowed": False, "reason": "management_not_configured",
        "resource_ref": None, "availability": None, "as_of_ordinal": None,
        "targets": [], "plan_params": None, "body_mode": "metadata_only"}


class DisableRequests:
    def __init__(self, viewer):
        self.viewer = viewer
        self.owner = viewer._owner
        self.core = self.owner._core
        self.store = self.core.event_store

    @contextmanager
    def _cut(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            try:
                require_session(self.store, db, self.core.task_id, self.viewer._session)
            except RegistryConflict as exc:
                if str(exc) in {"collaboration requires the target owner A",
                                "collaboration owner authority is revoked"}:
                    raise MaterialManagementError("access_changed", 403) from exc
                raise
            yield db

    def _enabled(self):
        if not self.viewer._disable_targets:
            raise MaterialManagementError("action_unavailable", 403)

    def _actor(self):
        return {"actor": self.viewer._session.actor,
                "session_ref": ref_payload(self.viewer._session.ref)}

    @staticmethod
    def _upper(db):
        return int(db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0])

    def _target(self, db, target, execution):
        request = {"operation": "disable_versions", "target": target, "execution": execution}
        transition = validate_active_target(self.store, db, self.core.task_id,
            request, ref_payload(self.owner.principal_ref))
        binding = exact_descriptor(db, self.core.object_store, self.core.task_id,
            transition["operation_binding_ref"])
        spec = exact_descriptor(db, self.core.object_store, self.core.task_id,
            binding["operation_spec_ref"])
        registered = self.owner.registration.declaration("executor", spec["executor_key"])
        if (spec["executor_key"] != EXECUTOR or registered["identity"] != IDENTITY
                or registered["contracts"] != CONTRACTS
                or spec["implementation_identity"] != registered["identity"]
                or spec["implementation_contracts"] != registered["contracts"]):
            raise MaterialManagementError("target_unavailable", 409)

    def _available(self, db, refs):
        from cpn.rpnh.material_body_policy import resolve_body_availability
        for ref in refs:
            _target(self.store, db, self.core.task_id, ref)
            status = resolve_body_availability(self.store, db, self.core.task_id, ref)["availability"]
            if status == "disabled":
                raise MaterialManagementError("material_disabled", 409)
            if status != "available":
                raise MaterialManagementError("material_availability_unknown", 503)

    def _targets(self, db, plan_ref=None):
        execution = self.viewer._execution(db)
        values = []
        for item in self.viewer._disable_targets:
            self._target(db, item.transition_id, execution)
            if plan_ref is None:
                values.append({"id": item.id, "label": item.label,
                    "target": item.transition_id, "execution": execution})
            else:
                values.append({"id": item.id, "label": item.label, "params": {
                    "protocol": "rpnh/material_command/v1", "operation": "disable_versions",
                    "target": item.transition_id, "execution": execution,
                    "confirmed_plan_ref": plan_ref}})
        return values

    def _plan_ref(self, db, ref):
        ref = reference(ref, "resource_version/v1")
        item = exact_prepared(db, self.core.object_store, self.core.task_id, ref)
        desc = item.metadata.get("descriptors", {})
        if (item.metadata.get("content_schema_ref") != PLAN_SCHEMA
                or desc.get("owner_input_role") != PLAN_ROLE):
            raise MaterialManagementError("command_content_mismatch", 409)
        if (desc.get("owner_input_actor") != self.viewer._session.actor
                or desc.get("owner_input_session_version_id") != str(self.viewer._session.ref.version_id)
                or item.metadata.get("task_ref") != ref_payload(self.owner.identity.task_ref)):
            raise MaterialManagementError("access_changed", 403)
        if item.size > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        plan = read_plan(self.store, db, self.core.task_id, ref, action=self._actor())
        return desc["owner_input_command_id"], plan

    def _plan_key(self, db, key):
        rows = canonical_rows(self.store, db, self.core.task_id, object_type="resource_version/v1",
            conditions="AND json_extract(o.metadata_json,'$.content_schema_ref')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_input_role')=? "
            "AND json_extract(o.metadata_json,'$.descriptors.owner_input_command_id')=?",
            parameters=(PLAN_SCHEMA, PLAN_ROLE, key))
        if not rows:
            # An observed original identity of another kind is not absence.
            existing = db.execute("SELECT 1 FROM transactions WHERE task_id=? AND idempotency_key=?",
                (str(self.core.task_id), key)).fetchone()
            if existing is not None:
                raise MaterialManagementError("command_content_mismatch", 409)
            return None
        if len(rows) != 1:
            raise MaterialManagementError("result_unknown", 503)
        ref = row_ref(rows[0])
        original_key, plan = self._plan_ref(db, ref)
        if original_key != key:
            raise MaterialManagementError("command_content_mismatch", 409)
        return ref, plan

    def _delivery(self, db, refs):
        from cpn.rpnh.material_body_policy import expanded_material_carrier_refs
        from cpn.rpnh.registry._event_store.material_delivery_drain import assert_material_deliveries_drained
        result = {"status": "unknown", "proof_refs": [], "reason": None,
                  "authorizes_publication": False}
        try:
            proofs = assert_material_deliveries_drained(self.store, db, self.core.task_id,
                expanded_material_carrier_refs(self.store, db, self.core.task_id, refs))
        except RegistryConflict as exc:
            code = str(exc).split(":", 1)[0]
            result.update(status="in_use" if code == "TARGET_IN_USE" else "unknown", reason=code)
        else:
            result.update(status="drained", proof_refs=list(proofs))
        return result

    def _plan_view(self, db, key):
        found = self._plan_key(db, key)
        result = {"schema_version": SCHEMA, "action": "read_disable_plan",
            "source_id": self.viewer.source_id, "plan_command_id": key,
            "status": "NOT_FOUND_AT_READ_CUT", "plan_ref": None, "plan": None,
            "submit_targets": [], "reason": "not_found_at_read_cut", "current_scope": "unknown",
            "as_of_ordinal": self._upper(db), "delivery_observation": None}
        if found is None:
            return result
        ref, plan = found
        result.update(status="PLAN_PREPARED", plan_ref=ref, plan=plan, reason=None)
        result["delivery_observation"] = self._delivery(db, plan["target_resource_refs"])
        try:
            read_plan(self.store, db, self.core.task_id, ref, action=self._actor(), current=True)
            result["current_scope"] = "current"
            self._available(db, plan["target_resource_refs"])
            result["submit_targets"] = self._targets(db, ref)
        except MaterialManagementError as exc:
            result["reason"] = exc.code
        except RegistryConflict as exc:
            code = str(exc).split(":", 1)[0]
            if code == "MATERIAL_SCOPE_CHANGED":
                result.update(current_scope="changed", reason="material_scope_changed")
            elif code in {"TARGET_UNAVAILABLE", "EXECUTION_CHANGED"}:
                result["reason"] = code.lower()
            else:
                # Authority/corruption errors are never downgraded to a
                # harmless stale-plan observation.
                raise
        return result

    def _catalog(self, db, selected):
        result = closed_disable_catalog(self.viewer.source_id)
        if not self.viewer._disable_targets:
            return result
        result.update(supported=True, reason=None, as_of_ordinal=self._upper(db))
        targets = self._targets(db)
        if selected is None:
            return result
        selected = reference(selected, "resource_version/v1")
        _target(self.store, db, self.core.task_id, selected)
        from cpn.rpnh.material_body_policy import resolve_body_availability
        availability = resolve_body_availability(self.store, db, self.core.task_id, selected)["availability"]
        result.update(resource_ref=selected, availability=availability)
        if availability != "available":
            result["reason"] = "material_disabled" if availability == "disabled" else "material_availability_unknown"
            return result
        result.update(prepare_allowed=bool(targets), targets=targets,
            plan_params={"protocol": PLAN_PROTOCOL, "target_resource_refs": [selected]})
        return result

    def _command(self, db, command_ref):
        body = read_accepted_material_command(self.store, db, self.core.task_id, command_ref, current=False)
        data = body["data"]
        if data.get("operation") != "disable_versions":
            raise MaterialManagementError("command_content_mismatch", 409)
        if not any(item.transition_id == body["target"] for item in self.viewer._disable_targets):
            raise MaterialManagementError("target_not_allowed", 403)
        original = data.get("user_action", {})
        actor = self._actor()
        if (original.get("actor") != actor["actor"] or original.get("session_ref") != actor["session_ref"]
                or data["execution"]["task_ref"] != ref_payload(self.owner.identity.task_ref)):
            raise MaterialManagementError("access_changed", 403)
        key, plan = self._plan_ref(db, data["confirmed_plan_ref"])
        params = {**{name: value for name, value in data.items() if name != "user_action"},
                  "target": body["target"]}
        return params, key, plan

    def _receipt(self, key):
        original = self.owner.control.materials.read_receipt(session=self.viewer._session,
            material_command_id=key)
        with self._cut() as db:
            result = {"schema_version": SCHEMA, "action": "read_disable_receipt",
                "source_id": self.viewer.source_id, "command_id": key, "status": original["status"],
                "params": None, "plan_ref": None, "plan_command_id": None, "plan": None,
                "receipt": original, "as_of_ordinal": self._upper(db)}
            if original["status"] != "NOT_FOUND_AT_READ_CUT":
                params, plan_key, plan = self._command(db, original["command_ref"])
                result.update(params=params, plan_ref=params["confirmed_plan_ref"],
                    plan_command_id=plan_key, plan=plan)
            return result

    def dispatch(self, params):
        action = params["action"]
        with self._cut() as db:
            if action == "disable_catalog":
                return self._bounded(self._catalog(db, params.get("resource_ref")))
            self._enabled()
            if action == "read_disable_plan":
                return self._bounded(self._plan_view(db, params["plan_command_id"]))
            if action == "disable_plan":
                request = params["params"]
                validate_plan_params(request)
                refs = _targets(request["target_resource_refs"])
                previous = self._plan_key(db, params["command_id"])
                if previous is None:
                    self._targets(db)
                    self._available(db, refs)
                elif previous[1]["target_resource_refs"] != refs:
                    raise MaterialManagementError("command_content_mismatch", 409)
            elif action == "disable_submit":
                request = params["params"]
                validate_availability_params(request)
                if not any(item.transition_id == request["target"] for item in self.viewer._disable_targets):
                    raise MaterialManagementError("target_not_allowed", 403)
                _key, plan = self._plan_ref(db, request["confirmed_plan_ref"])
                previous = self.owner.control.materials.read_receipt(session=self.viewer._session,
                    material_command_id=params["command_id"])
                if previous["status"] == "NOT_FOUND_AT_READ_CUT":
                    if request["execution"] != self.viewer._execution(db):
                        raise MaterialManagementError("execution_changed", 409)
                    self._target(db, request["target"], request["execution"])
                    read_plan(self.store, db, self.core.task_id, request["confirmed_plan_ref"],
                        action=self._actor(), current=True)
                    self._available(db, plan["target_resource_refs"])
                else:
                    original, _key, _plan = self._command(db, previous["command_ref"])
                    if original != request:
                        raise MaterialManagementError("command_content_mismatch", 409)
            elif action != "read_disable_receipt":
                raise MaterialManagementError("invalid_query")
        if action == "read_disable_receipt":
            return self._bounded(self._receipt(params["material_command_id"]))
        sealed = self.viewer._host.material_action(self.viewer._session,
            owner_command=params["owner_command"], command_id=params["command_id"], params=params["params"])
        result = self.owner.command(params["owner_command"], params["params"],
            command_id=params["command_id"], collaboration_action=sealed)
        if action == "disable_plan":
            with self._cut() as db:
                result = self._plan_view(db, params["command_id"])
        return self._bounded(result)

    @staticmethod
    def _bounded(value):
        if len(canonical_json(value)) > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        return deepcopy(value)
