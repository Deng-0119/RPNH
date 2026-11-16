"""Explicit Viewer management binding to an already running original Owner.

The ordinary Viewer provider remains read-only. An embedding HOST may wrap it
with this adapter only after supplying its own OwnerEventLoop, original HOST
session and a finite set of permitted task addresses. No session is created by
HTTP, no client actor is accepted, and no command/result cache is authoritative.
All material actions use the existing Owner ports on the owner thread.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from urllib.parse import parse_qs

SCHEMA = "rpnh/viewer_material_management/v1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
_READS = {"catalog", "editor", "read_input", "read_receipt", "read_catalog", "read_progress",
          "location_catalog", "disable_catalog", "read_disable_plan", "read_disable_receipt",
          "carrier_catalog", "read_material_carriers", "purge_catalog", "read_purge_receipt"}
_WRITES = {"prepare_input", "submit", "abandon_input", "location_submit", "disable_plan", "disable_submit",
           "purge_authorize", "purge_execute"}
_CLOSED = {"create": False, "revise": False, "publish": False}


class MaterialManagementError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _text(value, maximum=512):
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise MaterialManagementError("invalid_query")
        value[key] = child
    return value


def _json(value):
    try:
        return json.loads(value, object_pairs_hook=_unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MaterialManagementError("invalid_query") from exc


def parse_request(method, query, body=b""):
    """A closed HTTP envelope; its identities are selectors, never authority."""
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=8)
    except ValueError as exc:
        raise MaterialManagementError("invalid_query") from exc
    if any(len(value) != 1 for value in values.values()):
        raise MaterialManagementError("invalid_query")
    params = {key: value[0] for key, value in values.items()}
    action = params.get("action")
    method = method.upper()
    if action in {"carrier_catalog", "read_material_carriers", "purge_catalog", "read_purge_receipt"} and method != "GET":
        raise MaterialManagementError("method_not_allowed", 405)
    if action in _WRITES:
        if method != "POST":
            raise MaterialManagementError("method_not_allowed", 405)
        if set(params) != {"action"} or not isinstance(body, bytes):
            raise MaterialManagementError("invalid_query")
        if len(body) > MAX_REQUEST_BYTES:
            raise MaterialManagementError("scope_limit", 413)
        request = _json(body)
        if (not isinstance(request, dict)
                or set(request) != {"source_id", "owner_command", "command_id", "params"}
                or not _text(request["command_id"]) or not isinstance(request["params"], dict)
                or request["owner_command"] != {"prepare_input": "prepare_material_input",
                    "submit": "material_submit", "abandon_input": "abandon_material_input",
                    "location_submit": "material_submit", "disable_plan": "prepare_material_disable_plan",
                    "disable_submit": "material_submit", "purge_authorize": "material_submit",
                    "purge_execute": "material_submit"}[action]):
            raise MaterialManagementError("invalid_query")
        if action in {"purge_authorize", "purge_execute"}:
            from cpn.rpnh.registry._event_store.material_purge import validate_purge_params
            from cpn.rpnh.registry.event_store import RegistryConflict
            expected = "authorize_purge" if action == "purge_authorize" else "purge_carrier"
            try:
                validate_purge_params(request["params"])
            except (RegistryConflict, TypeError, ValueError) as exc:
                raise MaterialManagementError("invalid_query") from exc
            if request["params"]["operation"] != expected:
                raise MaterialManagementError("invalid_query")
        params = {"action": action, **request}
    else:
        if method not in {"GET", "HEAD"}:
            raise MaterialManagementError("method_not_allowed", 405)
        if body or action not in _READS:
            raise MaterialManagementError("invalid_query")
        required = {"action", "source_id"}
        optional = set()
        if action in {"catalog", "editor", "location_catalog", "disable_catalog", "carrier_catalog", "purge_catalog"}:
            optional = {"resource_ref"}
        elif action == "read_material_carriers":
            required.update(("resource_ref", "observe_payloads"))
        elif action == "read_input":
            required.add("input_command_id")
        elif action == "read_disable_plan":
            required.add("plan_command_id")
        elif action in {"read_disable_receipt", "read_purge_receipt"}:
            required.add("material_command_id")
        elif action in {"read_receipt", "read_progress"}:
            optional = {"material_command_id", "material_command_ref"}
            if len(set(params) & optional) != 1:
                raise MaterialManagementError("invalid_query")
        if not required <= set(params) or set(params) - required - optional:
            raise MaterialManagementError("invalid_query")
        for key in ("resource_ref", "material_command_ref"):
            if key in params:
                params[key] = _json(params[key])
        if action in {"carrier_catalog", "read_material_carriers", "purge_catalog"} and "resource_ref" in params:
            from cpn.rpnh.registry._event_store.material_control import reference
            from cpn.rpnh.registry.event_store import RegistryConflict
            try:
                params["resource_ref"] = reference(params["resource_ref"], "resource_version/v1")
            except (RegistryConflict, TypeError, ValueError) as exc:
                raise MaterialManagementError("invalid_query") from exc
        if action == "read_material_carriers":
            if params["observe_payloads"] not in {"false", "true"}:
                raise MaterialManagementError("invalid_query")
            params["observe_payloads"] = params["observe_payloads"] == "true"
        for key in ("input_command_id", "material_command_id", "plan_command_id"):
            if key in params and not _text(params[key]):
                raise MaterialManagementError("invalid_query")
    if not _text(params.get("source_id")):
        raise MaterialManagementError("invalid_query")
    return params


def closed_catalog(source_id):
    return {"schema_version": SCHEMA, "action": "catalog", "source_id": source_id,
        "capabilities": dict(_CLOSED), "prepare_allowed": False, "targets": [],
        "document_types": [], "reason": "management_not_configured",
        "input_abandonment_allowed": False,
        "input_retention": "retained_no_implicit_abandonment"}


def closed_location_catalog(source_id):
    return {"schema_version": "rpnh/viewer_material_locations/v1",
        "action": "location_catalog", "source_id": source_id,
        "supported": False, "reason": "management_not_configured",
        "resource_ref": None, "as_of_ordinal": None, "positions": [], "targets": [],
        "bind_targets": [],
        "body_mode": "metadata_only"}


def handle_material_request(provider, method, query, body=b""):
    params = parse_request(method, query, body)
    action = getattr(provider, "material_management", None)
    if not callable(action):
        if params["action"] == "catalog":
            return closed_catalog(params["source_id"])
        if params["action"] == "location_catalog":
            return closed_location_catalog(params["source_id"])
        if params["action"] == "disable_catalog":
            from .material_disable import closed_disable_catalog
            return closed_disable_catalog(params["source_id"])
        if params["action"] == "carrier_catalog":
            from .material_carriers import closed_carrier_catalog
            return closed_carrier_catalog(params["source_id"])
        if params["action"] == "purge_catalog":
            from .material_purge import closed_purge_catalog
            return closed_purge_catalog(params["source_id"])
        raise MaterialManagementError("management_not_configured", 501)
    return action(params)


def handle_material_delivery_request(provider, query, body):
    """Explicit POST envelope used only by the native HTTP adapter."""
    if query or type(body) is not bytes:
        raise MaterialManagementError("invalid_query")
    if len(body) > MAX_REQUEST_BYTES:
        raise MaterialManagementError("scope_limit", 413)
    value = _json(body)
    if (type(value) is not dict
            or set(value) != {"source_id", "target_id", "command_id", "resource_ref"}
            or not all(_text(value[key]) for key in ("source_id", "target_id", "command_id"))):
        raise MaterialManagementError("invalid_query")
    from cpn.rpnh.registry._event_store.material_control import reference
    from cpn.rpnh.registry.event_store import RegistryConflict
    try:
        reference(value["resource_ref"], "resource_version/v1")
    except (RegistryConflict, TypeError, ValueError) as exc:
        raise MaterialManagementError("invalid_query") from exc
    action = getattr(provider, "material_delivery", None)
    if not callable(action):
        raise MaterialManagementError("management_not_configured", 501)
    return action(value)


@dataclass(frozen=True, slots=True)
class MaterialTarget:
    """HOST-selected real operation and semantic address, never a file path."""
    id: str
    label: str
    transition_id: str
    opaque_name: str

    def __post_init__(self):
        if not all(_text(value) for value in
                (self.id, self.label, self.transition_id, self.opaque_name)):
            raise ValueError("material targets require finite named HOST selections")


@dataclass(frozen=True, slots=True)
class MaterialReadTarget:
    """A HOST-selected original human read operation, never a client grant."""
    id: str
    label: str
    transition_id: str

    def __post_init__(self):
        if not all(_text(value) for value in (self.id, self.label, self.transition_id)):
            raise ValueError("human read targets require finite named HOST selections")


@dataclass(frozen=True, slots=True)
class MaterialLocationTarget:
    """One HOST-selected position; adding an entry needs its own opt-in."""
    id: str
    label: str
    transition_id: str
    opaque_name: str
    allow_bind: bool = False

    def __post_init__(self):
        if not all(_text(value) for value in
                (self.id, self.label, self.transition_id, self.opaque_name)):
            raise ValueError("location targets require finite named HOST selections")
        if type(self.allow_bind) is not bool:
            raise ValueError("location binding requires an explicit boolean HOST gate")


@dataclass(frozen=True, slots=True)
class MaterialDisableTarget:
    """One separately enabled original finite-version availability operation."""
    id: str
    label: str
    transition_id: str

    def __post_init__(self):
        if not all(_text(value) for value in (self.id, self.label, self.transition_id)):
            raise ValueError("disable targets require finite named HOST selections")


@dataclass(frozen=True, slots=True)
class MaterialPurgeTarget:
    """One HOST-selected original operation in a separately enabled purge phase."""
    id: str
    label: str
    transition_id: str

    def __post_init__(self):
        if not all(_text(value) for value in (self.id, self.label, self.transition_id)):
            raise ValueError("purge targets require finite named HOST selections")


class MaterialManagementViewer:
    """Opt-in decorator. Supplying a provider alone does not enable a write."""
    def __init__(self, provider, *, event_loop, host, session, targets=(),
                 capabilities=None, reply_timeout=5.0, read_targets=(), abandon_inputs=False,
                 terminal_materials=False, location_targets=(), disable_targets=(),
                 carrier_inspection=False, purge_authorization_targets=(), purge_execution_targets=()):
        from cpn.rpnh.control_server import OwnerEventLoop
        from cpn.rpnh.collaboration.user_collaboration import UserCollaborationHost
        from cpn.rpnh.registry._event_store.material_control import require_session
        if (not callable(provider) or type(event_loop) is not OwnerEventLoop
                or type(host) is not UserCollaborationHost
                or host._core is not event_loop.owner._core):
            raise TypeError("Viewer requires the existing Owner loop and its trusted HOST")
        if (not isinstance(reply_timeout, (int, float)) or isinstance(reply_timeout, bool)
                or not 0 < reply_timeout <= 60):
            raise ValueError("Viewer owner reply timeout must be within 0..60 seconds")
        self._provider, self._loop, self._host, self._session = provider, event_loop, host, session
        self._owner = event_loop.owner
        self._timeout = reply_timeout
        self._targets = tuple(targets)
        self._read_targets = tuple(read_targets)
        self._location_targets = tuple(location_targets)
        self._disable_targets = tuple(disable_targets)
        self._purge_authorization_targets = tuple(purge_authorization_targets)
        self._purge_execution_targets = tuple(purge_execution_targets)
        for phase in (self._purge_authorization_targets, self._purge_execution_targets):
            if (any(type(item) is not MaterialPurgeTarget for item in phase)
                    or len({item.id for item in phase}) != len(phase)
                    or len({item.transition_id for item in phase}) != len(phase)):
                raise ValueError("each purge phase requires distinct explicit original targets")
        if type(carrier_inspection) is not bool:
            raise ValueError("carrier inspection requires an explicit boolean HOST gate")
        self._carrier_inspection = carrier_inspection
        if (any(type(item) is not MaterialDisableTarget for item in self._disable_targets)
                or len({item.id for item in self._disable_targets}) != len(self._disable_targets)
                or len({item.transition_id for item in self._disable_targets}) != len(self._disable_targets)):
            raise ValueError("disable targets require distinct explicit original operations")
        if (any(type(item) is not MaterialLocationTarget for item in self._location_targets)
                or len({item.id for item in self._location_targets}) != len(self._location_targets)
                or len({item.opaque_name for item in self._location_targets}) != len(self._location_targets)):
            raise ValueError("location targets require distinct explicit semantic positions")
        if type(abandon_inputs) is not bool:
            raise ValueError("input abandonment requires an explicit boolean HOST gate")
        self._abandon_inputs = abandon_inputs
        if type(terminal_materials) is not bool:
            raise ValueError("terminal materials require an explicit boolean HOST gate")
        self._terminal_materials = terminal_materials
        if (any(type(item) is not MaterialReadTarget for item in self._read_targets)
                or len({item.id for item in self._read_targets}) != len(self._read_targets)
                or len({item.transition_id for item in self._read_targets}) != len(self._read_targets)):
            raise ValueError("human read targets require distinct explicit original operations")
        if (any(type(item) is not MaterialTarget for item in self._targets)
                or len({item.id for item in self._targets}) != len(self._targets)
                or len({(item.transition_id, item.opaque_name) for item in self._targets}) != len(self._targets)):
            raise ValueError("Viewer requires distinct explicit material targets")
        if capabilities is not None and (not isinstance(capabilities, dict)
                or set(capabilities) - set(_CLOSED)
                or any(type(value) is not bool for value in capabilities.values())):
            raise ValueError("material capabilities require explicit boolean HOST gates")
        self._capabilities = {key: (capabilities or {}).get(key) is True for key in _CLOSED}
        core = self._owner._core
        with core.event_store.connect() as db:
            require_session(core.event_store, db, core.task_id, session)
        # This binding is the original published source, also used by the local
        # browser. A request cannot substitute a different source or Registry.
        self.source_id = host._binding["source_id"]

    def __call__(self):
        return self._provider()

    def __getattr__(self, name):
        return getattr(self._provider, name)

    def material_management(self, params):
        if params["source_id"] != self.source_id:
            if params["action"] == "catalog":
                return closed_catalog(params["source_id"])
            if params["action"] == "location_catalog":
                return closed_location_catalog(params["source_id"])
            if params["action"] == "disable_catalog":
                from .material_disable import closed_disable_catalog
                return closed_disable_catalog(params["source_id"])
            raise MaterialManagementError("source_not_allowed", 403)
        # JSON is detached before crossing threads. Timeout never cancels the
        # original Owner task; the client must query the same stable identity.
        request = deepcopy(params)
        try:
            if request["action"] in {"carrier_catalog", "read_material_carriers"}:
                # Pure observation has no Owner command or event-loop work.
                # Each request opens its own original read cut and rechecks
                # the current trusted session before original proof I/O.
                from .material_carriers import CarrierRequests
                return CarrierRequests(self).dispatch(request)
            if request["action"] in {"purge_catalog", "read_purge_receipt"}:
                from .material_purge import PurgeRequests
                return PurgeRequests(self).dispatch(request)
            future = self._loop.submit_host(lambda: self._dispatch(request))
            return future.result(timeout=self._timeout)
        except TimeoutError as exc:
            raise MaterialManagementError("result_unknown", 503) from exc
        except MaterialManagementError:
            raise
        except Exception as exc:
            from cpn.rpnh.registry.event_store import RegistryConflict
            if isinstance(exc, RegistryConflict):
                text = str(exc)
                for prefix, code, status in (
                    ("ACTOR_NOT_AUTHORIZED", "access_changed", 403),
                    ("SCOPE_NOT_ALLOWED", "target_not_allowed", 403),
                    ("COMMAND_CONTENT_MISMATCH", "command_content_mismatch", 409),
                    ("INVALID_COMMAND", "invalid_query", 400),
                    ("PROTECTED_RESOURCE", "target_not_allowed", 403),
                    ("MATERIAL_SCOPE_CHANGED", "material_scope_changed", 409),
                    ("MATERIAL_DISABLED", "material_disabled", 409),
                    ("MATERIAL_UNKNOWN", "material_availability_unknown", 503),
                    ("MATERIAL_UNAVAILABLE", "material_unavailable", 409),
                    ("INPUT_IN_USE", "input_in_use", 409),
                    ("INPUT_ABANDONED", "input_abandoned", 409),
                    ("EXECUTION_CHANGED", "execution_changed", 409),
                    ("TARGET_UNAVAILABLE", "target_unavailable", 409)):
                    if text.startswith(prefix):
                        raise MaterialManagementError(code, status) from exc
            raise MaterialManagementError("result_unknown", 503) from exc

    def material_delivery(self, params):
        if params["source_id"] != self.source_id:
            raise MaterialManagementError("source_not_allowed", 403)
        request = deepcopy(params)
        future = self._loop.submit_host(lambda: self._dispatch_read(request))
        try:
            return future.result(timeout=self._timeout)
        except MaterialManagementError:
            raise
        except Exception as exc:
            from cpn.rpnh.registry.event_store import RegistryConflict
            if isinstance(exc, RegistryConflict):
                for prefix, code, status in (
                        ("ACTOR_NOT_AUTHORIZED", "access_changed", 403),
                        ("COMMAND_CONTENT_MISMATCH", "command_content_mismatch", 409),
                        ("PROTECTED_RESOURCE", "target_not_allowed", 403),
                        ("MATERIAL_UNAVAILABLE", "material_unavailable", 409),
                        ("TARGET_UNAVAILABLE", "target_unavailable", 409),
                        ("EXECUTION_CHANGED", "execution_changed", 409)):
                    if str(exc).startswith(prefix):
                        raise MaterialManagementError(code, status) from exc
            # Timeout does not cancel an Owner action or replace its witness.
            raise MaterialManagementError("result_unknown", 503) from exc

    def _read_target(self, db, item, execution):
        from cpn.rpnh.registry._event_store.material_control import validate_active_target
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
        from cpn.rpnh.registry.strict_contracts import ref_payload
        from cpn.rpnh.human_viewer import READ_EXECUTOR, READ_IDENTITY, READ_CONTRACTS
        core = self._owner._core
        transition = validate_active_target(core.event_store, db, core.task_id,
            {"operation": "read_material", "target": item.transition_id, "execution": execution},
            ref_payload(self._owner.principal_ref))
        binding = exact_descriptor(db, core.object_store, core.task_id, transition["operation_binding_ref"])
        spec = exact_descriptor(db, core.object_store, core.task_id, binding["operation_spec_ref"])
        registered = self._owner.registration.declaration("executor", READ_EXECUTOR)
        if (spec["executor_key"] != READ_EXECUTOR or spec["implementation_identity"] != READ_IDENTITY
                or spec["implementation_contracts"] != READ_CONTRACTS
                or registered["identity"] != READ_IDENTITY or registered["contracts"] != READ_CONTRACTS):
            raise MaterialManagementError("target_unavailable", 409)

    def _dispatch_read(self, params):
        from cpn.rpnh.registry._event_store.material_control import require_session
        from cpn.rpnh.registry._event_store.human_viewer import validate_read_baseline
        from cpn.rpnh.registry.publication import _version_from_payload
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_prepared
        from cpn.rpnh.human_viewer import (
            read_human_viewer_progress, prepare_human_viewer_operation,
            record_human_viewer_transport,
            record_human_viewer_unknown, run_human_viewer_operation,
        )
        from .human_viewer_transport import human_viewer_http_body
        selected = [item for item in self._read_targets if item.id == params["target_id"]]
        if len(selected) != 1:
            raise MaterialManagementError("target_not_allowed", 403)
        item, = selected
        core = self._owner._core
        command_id = params["command_id"]
        progress = read_human_viewer_progress(self._owner, session=self._session,
            material_command_id=command_id)
        if progress["state"] == "not_found_at_read_cut":
            with core.event_store.connect() as db:
                db.execute("BEGIN")
                require_session(core.event_store, db, core.task_id, self._session)
                execution = self._execution(db)
                self._read_target(db, item, execution)
                request = {"protocol": "rpnh/material_command/v1", "operation": "read_material",
                    "target": item.transition_id, "execution": execution,
                    "target_resource_ref": params["resource_ref"]}
                validate_read_baseline(core.event_store, db, core.task_id, request, current=True)
                target = exact_prepared(db, core.object_store, core.task_id, params["resource_ref"])
                if target.size > MAX_REQUEST_BYTES:
                    raise MaterialManagementError("scope_limit", 413)
            action = self._host.material_action(self._session, owner_command="material_submit",
                command_id=command_id, params=request)
            accepted = self._owner.command("material_submit", request, command_id=command_id,
                collaboration_action=action)
            progress = read_human_viewer_progress(self._owner, session=self._session,
                material_command_ref=accepted["command_ref"])
        else:
            # The original progress reader checks the exact current session
            # against the command's original actor and session. Replaying a
            # completed/unknown delivery must not depend on today's available
            # body or a new execution, and must never submit a new command.
            if (progress["target"] != item.transition_id
                    or progress["resource_ref"] != params["resource_ref"]):
                raise MaterialManagementError("command_content_mismatch", 409)
            accepted = {"command_ref": progress["command_ref"]}
        if progress.get("can_issue_new_delivery") is not True:
            return progress
        # This is one explicit HOST request on the original Owner thread.
        # The original PN, grants and claims decide whether admission occurs.
        admitted = self._owner.admit(item.transition_id, logical_tau=0,
            command_id=command_id + ":human-http:admit",
            owner_command_ref=_version_from_payload(accepted["command_ref"]))
        if admitted is None:
            return read_human_viewer_progress(self._owner, session=self._session,
                material_command_ref=accepted["command_ref"])
        active = self._owner.start(admitted, command_id=command_id + ":human-http:start")
        delivery = prepare_human_viewer_operation(self._owner, active)

        def settle(proof, reason):
            if proof is None:
                return record_human_viewer_unknown(delivery, reason or "HTTP_HANDLER_FAILED")
            record_human_viewer_transport(delivery, proof)
            outputs = run_human_viewer_operation(self._owner, active)
            return self._owner.succeed(outputs, command_id=command_id + ":human-http:success")

        def closed(proof, reason):
            future = self._loop.submit_host(lambda: settle(proof, reason))
            # A late outcome is queried through the original command; never
            # cancel this future or use an unknown result to resend bytes.
            return future.result(timeout=self._timeout)

        try:
            return human_viewer_http_body(delivery, closed)
        except Exception:
            record_human_viewer_unknown(delivery, "HTTP_BODY_UNAVAILABLE")
            raise

    def _execution(self, db, *, allow_terminal=False):
        from cpn.rpnh.registry._event_store.material_control import canonical_rows, row_ref
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
        core = self._owner._core
        rows = canonical_rows(core.event_store, db, core.task_id,
                              object_type="run_execution_authority/v1")
        if not rows or len({row["logical_id"] for row in rows}) != 1:
            raise MaterialManagementError("execution_changed", 409)
        run = exact_descriptor(db, core.object_store, core.task_id, row_ref(rows[-1]))
        running = run["status"] == "running" and run.get("terminal_evidence_ref") is None
        terminal = allow_terminal and run["status"] == "terminal" and run.get("terminal_evidence_ref") is not None
        if not (running or terminal):
            raise MaterialManagementError("target_unavailable", 409)
        checkpoint = exact_descriptor(db, core.object_store, core.task_id, run["latest_checkpoint_ref"])
        net = exact_descriptor(db, core.object_store, core.task_id, checkpoint["net_instance_ref"])
        return {"task_ref": run["task_ref"], "task_round_ref": net["task_round_ref"],
                "net_instance_ref": checkpoint["net_instance_ref"]}

    def _location_target(self, db, request):
        from cpn.rpnh.registry._event_store.material_control import validate_active_target
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
        from cpn.rpnh.registry._event_store.material_locations import validate_location_spec
        from cpn.rpnh.registry.strict_contracts import ref_payload
        from cpn.rpnh.material_locations import LOCATION_EXECUTOR, LOCATION_CONTRACTS
        core = self._owner._core
        transition = validate_active_target(core.event_store, db, core.task_id, request,
                                             ref_payload(self._owner.principal_ref))
        binding = exact_descriptor(db, core.object_store, core.task_id, transition["operation_binding_ref"])
        spec = exact_descriptor(db, core.object_store, core.task_id, binding["operation_spec_ref"])
        validate_location_spec(spec, binding)
        registered = self._owner.registration.declaration("executor", spec["executor_key"])
        if (spec["executor_key"] != LOCATION_EXECUTOR
                or registered["identity"] != spec["implementation_identity"]
                or registered["contracts"] != spec["implementation_contracts"]
                or registered["contracts"] != LOCATION_CONTRACTS):
            raise MaterialManagementError("target_unavailable", 409)

    def _location_catalog_at_cut(self, db, selected=None):
        from cpn.rpnh.registry._event_store.material_control import reference, TEXT_SCHEMA
        from cpn.rpnh.registry._event_store.material_locations import validate_location_baseline
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_prepared
        from cpn.rpnh.material_body_policy import resolve_body_availability
        from cpn.rpnh.registry.resources import ResourceAddress
        from cpn.rpnh.registry.strict_contracts import ref_payload
        if not self._location_targets:
            return closed_location_catalog(self.source_id)
        core = self._owner._core
        execution = self._execution(db)
        bindable = False
        if selected is not None:
            selected = reference(selected, "resource_version/v1")
            original = exact_prepared(db, core.object_store, core.task_id, selected)
            if original.metadata.get("task_ref") != execution["task_ref"]:
                raise MaterialManagementError("source_not_allowed", 403)
            # A selected historical position remains inspectable/unbindable.
            # Only an available canonical text product can gain another entry.
            # This is metadata selection; the original location baseline below
            # remains the authority for every disclosed command.
            if (any(item.allow_bind for item in self._location_targets)
                    and original.metadata.get("content_schema_ref") == TEXT_SCHEMA
                    and original.media_type == "application/json"
                    and original.metadata.get("origin_kind") == "petri_output"
                    and original.producer_invocation_id is not None
                    and original.metadata.get("descriptors", {}).get("owner_input_role") is None):
                bindable = resolve_body_availability(core.event_store, db, core.task_id,
                    selected)["availability"] == "available"
        upper = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        kernel, _repository = self._owner.operation_repository()
        positions, targets, bind_targets = [], [], []
        for item in self._location_targets:
            address = {"scope_ref": execution["task_ref"], "opaque_name": item.opaque_name}
            current = kernel._current_binding(
                ResourceAddress(self._owner.identity.task_ref, item.opaque_name),
                through_ordinal=upper, include_tombstone=True)
            binding = None if current is None else ref_payload(current.as_version_ref())
            resource = None
            tombstoned = current is not None and current.tombstone
            if current is not None and not tombstoned:
                metadata = exact_prepared(db, core.object_store, core.task_id, binding).metadata
                resource = {"entity_type": "resource_version/v1",
                    "logical_id": metadata["resource_ref"]["resource_id"],
                    "version_id": metadata["resource_ref"]["resource_version_id"]}
            if bindable and item.allow_bind and resource is None:
                request = {"protocol": "rpnh/material_command/v1", "operation": "bind",
                    "target": item.transition_id, "execution": execution, "address": address,
                    "expected_binding_ref": binding, "target_resource_ref": selected}
                self._location_target(db, request)
                validate_location_baseline(core.event_store, db, core.task_id, request)
                bind_targets.append({"id": item.id, "label": item.label, "address": address,
                    "binding_ref": binding, "tombstoned": bool(tombstoned),
                    "resource_ref": selected, "params": request})
            if selected is not None and resource != selected:
                continue
            position = {"id": item.id, "label": item.label, "address": address,
                "binding_ref": binding, "resource_ref": resource,
                "tombstoned": bool(tombstoned), "unbind_target_id": None}
            if resource is not None:
                request = {"protocol": "rpnh/material_command/v1", "operation": "unbind",
                    "target": item.transition_id, "execution": execution, "address": address,
                    "expected_binding_ref": binding}
                self._location_target(db, request)
                validate_location_baseline(core.event_store, db, core.task_id, request)
                position["unbind_target_id"] = item.id
                targets.append({"id": item.id, "label": item.label,
                    "resource_ref": resource, "params": request})
            positions.append(position)
        return {"schema_version": "rpnh/viewer_material_locations/v1",
            "action": "location_catalog", "source_id": self.source_id,
            "supported": True, "reason": None, "resource_ref": selected,
            "as_of_ordinal": upper, "positions": positions, "targets": targets,
            "bind_targets": bind_targets,
            "body_mode": "metadata_only"}

    def _targets_at_cut(self, db):
        from cpn.rpnh.registry._event_store.material_control import (
            validate_material_baseline)
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_prepared
        from cpn.rpnh.registry.resources import ResourceAddress
        from cpn.rpnh.registry.strict_contracts import ref_payload
        core = self._owner._core
        # This only permits reading the original execution locator. Each
        # disclosed operation still proves its exact terminal contract below.
        # Body-reading callers keep the running-only default.
        execution = self._execution(db, allow_terminal=self._terminal_materials)
        kernel, _repository = self._owner.operation_repository()
        upper = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        targets = []
        for item in self._targets:
            address = ResourceAddress(self._owner.identity.task_ref, item.opaque_name)
            binding = kernel._current_binding(address, through_ordinal=upper, include_tombstone=True)
            expected = None if binding is None else ref_payload(binding.as_version_ref())
            base = None
            if binding is not None and not binding.tombstone:
                metadata = exact_prepared(db, core.object_store, core.task_id, expected).metadata
                base = {"entity_type": "resource_version/v1",
                    "logical_id": metadata["resource_ref"]["resource_id"],
                    "version_id": metadata["resource_ref"]["resource_version_id"]}
            operation = "create_text" if base is None else "replace_text"
            if not self._capabilities["create" if base is None else "revise"]:
                continue
            target = {"id": item.id, "label": item.label, "target": item.transition_id,
                "operation": operation, "execution": execution,
                "address": {"scope_ref": execution["task_ref"], "opaque_name": item.opaque_name},
                "expected_binding_ref": expected, "base_resource_ref": base}
            self._literal_target(db, target)
            validate_material_baseline(core.event_store, db, core.task_id, target)
            targets.append(target)
        return targets

    def _literal_target(self, db, request):
        """Disclose only this installed text adapter's original operation ABI."""
        from cpn.rpnh.registry._event_store.material_control import validate_active_target
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_descriptor
        from cpn.rpnh.registry.strict_contracts import ref_payload
        from cpn.rpnh.material_execution import EXECUTOR_CONTRACTS, MATERIAL_TOOL, TOOL_IDENTITY, TOOL_CONTRACTS
        core = self._owner._core
        transition = validate_active_target(core.event_store, db, core.task_id, request,
                                           ref_payload(self._owner.principal_ref))
        binding = exact_descriptor(db, core.object_store, core.task_id, transition["operation_binding_ref"])
        spec = exact_descriptor(db, core.object_store, core.task_id, binding["operation_spec_ref"])
        registered = self._owner.registration.declaration("executor", spec["executor_key"])
        tool = self._owner.registration.declaration("tool", MATERIAL_TOOL)
        if (registered["identity"] != spec["implementation_identity"]
                or registered["contracts"] != spec["implementation_contracts"]
                or any(registered["contracts"].get(key) != value for key, value in EXECUTOR_CONTRACTS.items())
                or MATERIAL_TOOL not in spec["allowed_tool_ids"] or binding.get("workspace_binding_ref") is None
                or tool["identity"] != TOOL_IDENTITY or tool["contracts"] != TOOL_CONTRACTS):
            raise MaterialManagementError("target_unavailable", 409)

    def _dispatch(self, params):
        if params["action"] in {"purge_authorize", "purge_execute"}:
            from .material_purge import PurgeRequests
            return PurgeRequests(self).dispatch(params)
        if params["action"] in {"disable_catalog", "disable_plan", "read_disable_plan",
                                "disable_submit", "read_disable_receipt"}:
            from .material_disable import DisableRequests
            return DisableRequests(self).dispatch(params)
        from cpn.rpnh.registry._event_store.material_control import (
            require_session, validate_submit_params,
            validate_material_baseline, reference, TEXT_SCHEMA)
        from cpn.rpnh.registry._event_store.collaboration_descriptors import exact_prepared
        from cpn.rpnh.registry.strict_contracts import ref_payload
        from cpn.rpnh.material_types import decode_agent_text
        action, core = params["action"], self._owner._core
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            require_session(core.event_store, db, core.task_id, self._session)
            if action == "location_catalog":
                return self._location_catalog_at_cut(db, params.get("resource_ref"))
            if action == "read_catalog":
                result = {"schema_version": "rpnh/viewer_human_read/v1", "action": action,
                    "source_id": self.source_id, "read_allowed": False, "targets": [],
                    "reason": "management_not_configured", "body_mode": "explicit_original_delivery"}
                if not self._read_targets:
                    return result
                execution = self._execution(db)
                for item in self._read_targets:
                    self._read_target(db, item, execution)
                result.update(read_allowed=True, reason=None,
                    targets=[{"id": item.id, "label": item.label,
                        "transition_id": item.transition_id} for item in self._read_targets])
                return result
            if action in {"catalog", "editor"}:
                if not any(self._capabilities.values()):
                    if action == "catalog":
                        result = closed_catalog(self.source_id)
                        result["input_abandonment_allowed"] = self._abandon_inputs
                        return result
                    raise MaterialManagementError("action_unavailable", 403)
                selected_observation = None
                if action == "catalog" and "resource_ref" in params:
                    # A received editing copy can refer to a historical V1
                    # after its address points at V2. Check that exact V1 at
                    # this metadata cut; current target heads cannot speak for
                    # its current availability. No body or witness is read.
                    selected = reference(params["resource_ref"], "resource_version/v1")
                    prepared = exact_prepared(db, core.object_store, core.task_id, selected)
                    if prepared.metadata.get("task_ref") != ref_payload(self._owner.identity.task_ref):
                        raise MaterialManagementError("source_not_allowed", 403)
                    if (prepared.metadata.get("content_schema_ref") != TEXT_SCHEMA
                            or prepared.media_type != "application/json"):
                        raise MaterialManagementError("unsupported_type", 415)
                    from cpn.rpnh.material_body_policy import resolve_body_availability
                    availability = resolve_body_availability(core.event_store, db, core.task_id, selected)
                    if availability["availability"] == "disabled":
                        raise MaterialManagementError("material_disabled", 409)
                    if availability["availability"] != "available":
                        raise MaterialManagementError("material_availability_unknown", 503)
                    selected_observation = {"resource_ref": selected, "document_type": TEXT_SCHEMA,
                        "availability": "available", "availability_as_of": availability["availability_as_of"]}
                targets = self._targets_at_cut(db)
                result = {"schema_version": SCHEMA, "action": action,
                    "source_id": self.source_id, "capabilities": dict(self._capabilities),
                    "prepare_allowed": bool(targets), "targets": targets,
                    "document_types": [TEXT_SCHEMA], "reason": None,
                    "input_abandonment_allowed": self._abandon_inputs,
                    "input_retention": "retained_no_implicit_abandonment"}
                if selected_observation is not None:
                    result.update(selected_observation)
                if action == "editor":
                    selected = params.get("resource_ref")
                    if selected is None:
                        result.update(value="", document_type=TEXT_SCHEMA, complete=True)
                    else:
                        selected = reference(selected, "resource_version/v1")
                        prepared = exact_prepared(db, core.object_store, core.task_id, selected)
                        if prepared.metadata.get("task_ref") != ref_payload(self._owner.identity.task_ref):
                            raise MaterialManagementError("source_not_allowed", 403)
                        if (prepared.metadata.get("content_schema_ref") != TEXT_SCHEMA
                                or prepared.media_type != "application/json"):
                            raise MaterialManagementError("unsupported_type", 415)
                        if prepared.size > MAX_REQUEST_BYTES:
                            raise MaterialManagementError("scope_limit", 413)
                        from cpn.rpnh.collaboration.material_read_policy import (
                            require_untracked_material_read, MaterialReadPolicyError)
                        try:
                            require_untracked_material_read(core, selected, purpose="material_editor")
                        except MaterialReadPolicyError as exc:
                            raise MaterialManagementError(exc.code.lower(),
                                503 if exc.code == "MATERIAL_AVAILABILITY_UNKNOWN" else 409) from exc
                        value = decode_agent_text(core.object_store.read_registered(prepared, max_bytes=MAX_REQUEST_BYTES),
                            content_schema_ref=TEXT_SCHEMA, media_type=prepared.media_type)
                        result.update(value=value, document_type=TEXT_SCHEMA, complete=True,
                                      resource_ref=selected)
                    result["targets"] = [item for item in targets
                        if item["operation"] == "create_text" or item["base_resource_ref"] == selected]
                    result["prepare_allowed"] = bool(result["targets"])
                return result
            if action == "location_submit":
                from cpn.rpnh.registry._event_store.material_locations import (
                    validate_location_params, validate_location_baseline)
                from cpn.rpnh.registry.resources import ResourceAddress
                request = params["params"]
                operation = request.get("operation")
                if (operation not in {"unbind", "bind"}
                        or (operation == "bind" and not any(
                            item.allow_bind for item in self._location_targets))):
                    raise MaterialManagementError("unsupported_operation", 400)
                validate_location_params(request)
                if not self._location_targets:
                    raise MaterialManagementError("action_unavailable", 403)
                if (request["address"]["scope_ref"] != ref_payload(self._owner.identity.task_ref)
                        or not any(item.transition_id == request["target"]
                            and item.opaque_name == request["address"]["opaque_name"]
                            and (operation == "unbind" or item.allow_bind)
                            for item in self._location_targets)):
                    raise MaterialManagementError("target_not_allowed", 403)
                previous = self._owner.control.materials.read_receipt(session=self._session,
                    material_command_id=params["command_id"])
                if previous["status"] == "NOT_FOUND_AT_READ_CUT":
                    # Check the frozen selection at one read cut. The Owner's
                    # original lock/CAS still decides a later execution race.
                    if request["execution"] != self._execution(db):
                        raise MaterialManagementError("execution_changed", 409)
                    self._location_target(db, request)
                    kernel, _repository = self._owner.operation_repository()
                    upper = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
                    current = kernel._current_binding(
                        ResourceAddress(self._owner.identity.task_ref, request["address"]["opaque_name"]),
                        through_ordinal=upper, include_tombstone=True)
                    expected = None if current is None else ref_payload(current.as_version_ref())
                    if (expected != request["expected_binding_ref"]
                            or (operation == "unbind" and (current is None or current.tombstone))
                            or (operation == "bind" and current is not None and not current.tombstone)):
                        raise MaterialManagementError("address_changed", 409)
                # Replays validate the same immutable predecessor, even after
                # this command's Success or another command moves the address.
                validate_location_baseline(core.event_store, db, core.task_id, request)
                if operation == "bind" and previous["status"] == "NOT_FOUND_AT_READ_CUT":
                    from cpn.rpnh.material_body_policy import resolve_body_availability
                    availability = resolve_body_availability(core.event_store, db, core.task_id,
                        request["target_resource_ref"])["availability"]
                    if availability == "disabled":
                        raise MaterialManagementError("material_disabled", 409)
                    if availability != "available":
                        raise MaterialManagementError("material_availability_unknown", 503)
            elif action == "submit":
                request = params["params"]
                if request.get("operation") not in {"create_text", "replace_text"}:
                    raise MaterialManagementError("unsupported_operation", 400)
                validate_submit_params(request)
                enabled = self._capabilities["create" if request["operation"] == "create_text" else "revise"]
                if not enabled or not self._capabilities["publish"]:
                    raise MaterialManagementError("action_unavailable", 403)
                if (request["address"]["scope_ref"] != ref_payload(self._owner.identity.task_ref)
                        or not any(item.transition_id == request["target"]
                            and item.opaque_name == request["address"]["opaque_name"] for item in self._targets)):
                    raise MaterialManagementError("target_not_allowed", 403)
                # Check the supplied frozen baseline. Never refresh it from the
                # current address head when another producer has moved it.
                previous = self._owner.control.materials.read_receipt(session=self._session,
                    material_command_id=params["command_id"])
                if previous["status"] == "NOT_FOUND_AT_READ_CUT":
                    # A cached catalog or pre-existing input cannot bypass
                    # this HOST's terminal gate for a new command. Exact
                    # original-command replay below retains its old identity.
                    self._execution(db, allow_terminal=self._terminal_materials)
                    self._literal_target(db, request)
                validate_material_baseline(core.event_store, db, core.task_id, request)
            elif action == "prepare_input":
                if not (self._capabilities["create"] or self._capabilities["revise"]):
                    raise MaterialManagementError("action_unavailable", 403)
                previous = self._owner.control.materials.read_input(params["command_id"], session=self._session)
                if previous["status"] != "INPUT_ACCEPTED" and not self._targets_at_cut(db):
                    raise MaterialManagementError("target_unavailable", 409)
            elif action == "abandon_input":
                if not self._abandon_inputs:
                    raise MaterialManagementError("action_unavailable", 403)
                from cpn.rpnh.registry._event_store.material_input_lifecycle import (
                    validate_params, transaction_key)
                from cpn.rpnh.registry.event_store import RegistryConflict
                try:
                    validate_params(params["params"])
                    expected_key = transaction_key(params["params"]["input_ref"])
                except RegistryConflict as exc:
                    raise MaterialManagementError("invalid_query") from exc
                if params["command_id"] != expected_key:
                    raise MaterialManagementError("invalid_query")
        if action in _WRITES:
            name = params["owner_command"]
            sealed = self._host.material_action(self._session, owner_command=name,
                command_id=params["command_id"], params=params["params"])
            return self._owner.command(name, params["params"], command_id=params["command_id"],
                                       collaboration_action=sealed)
        if action == "read_input":
            return self._owner.command("read_material_input",
                {"input_command_id": params["input_command_id"]},
                command_id=params["input_command_id"], session=self._session)
        if action == "read_receipt":
            arguments = {key: params[key] for key in ("material_command_id", "material_command_ref")
                         if key in params}
            return self._owner.command("read_material_receipt", arguments,
                command_id=params.get("material_command_id", "viewer:read-receipt"), session=self._session)
        if action == "read_progress":
            from cpn.rpnh.human_viewer import read_human_viewer_progress
            arguments = {key: params[key] for key in ("material_command_id", "material_command_ref")
                         if key in params}
            return read_human_viewer_progress(self._owner, session=self._session, **arguments)
        raise MaterialManagementError("invalid_query")


__all__ = ("MaterialManagementViewer", "MaterialTarget", "MaterialReadTarget", "MaterialLocationTarget", "MaterialDisableTarget", "MaterialPurgeTarget", "MaterialManagementError",
           "handle_material_request", "handle_material_delivery_request", "MAX_REQUEST_BYTES", "SCHEMA")
