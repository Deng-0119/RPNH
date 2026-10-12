"""Trusted demo frontdoor. Persistent facts live exclusively in original Registries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

from cpn.components.execution_services import ExecutionServices
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.collaboration import UserCollaborationHost, user_collaboration_schema_data
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.run_inputs import WAIT, reference, command_key
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run, resume_run, _load_resume_material
from cpn.rpnh.run_inputs import RunInputHost, InputWaitSpec
from .contracts import MODEL, POLICY, ORDER, REQUEST_WAIT, DECISION_WAIT, module, schema_id
from .host import registration


def catalog():
    schemas, types, paths = user_collaboration_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


def safe_destination(path, workspace):
    """Validate the caller's explicit write boundary, including existing descendants."""
    root = Path(workspace).resolve(strict=True)
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(root):
        raise ValueError("destination escapes the declared workspace")
    for item in (path, *path.parents):
        if item == root.parent:
            break
        if item.is_symlink():
            raise ValueError("runtime path must not traverse a symlink")
    if path.exists():
        for item in (path, *path.rglob("*")):
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError("runtime tree contains a symlink")
            if item.is_file() and item.stat().st_nlink != 1:
                raise ValueError("runtime tree contains a hard link")
    return path


def personal(path, label, *, reopen, read_only=False):
    core = _RegistryCore(path, create=not reopen, read_only=read_only, catalog=catalog())
    if reopen:
        rows = core.event_store.object_rows_by_type("principal/v1")
        if len(rows) != 1:
            raise ValueError("personal Registry must contain exactly its original principal")
        row = rows[0]
        return core, _version_from_payload({"entity_type": "principal/v1",
                                           "logical_id": row["logical_id"], "version_id": row["version_id"]})
    identity = _bootstrap_identity(core, NativeBootstrapManifest(("rpnh/module_declaration/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="approval-personal-" + label, command_id="personal-source")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    data = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id),
            "display_name": "Controlled " + label}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
        version_id=principal.version_id, payload=canonical_json(data), metadata=data,
        media_type="application/json", schema_ref="registry_v1/principal/v1", idempotency_key="personal-principal")
    return core, principal


class ApprovalApp:
    def __init__(self, directory, *, workspace, reopen=False, decision_seconds=900):
        self.directory = safe_destination(directory, workspace)
        self.lock = threading.RLock()  # Serialization only; no pending/completed state.
        if not reopen and self.directory.exists():
            raise ValueError("fresh runtime directory must be absent")
        if not reopen:
            self.directory.mkdir(parents=True)
        self.read_only = False
        run_dir = self.directory / "run"
        if reopen:
            preflight = _RegistryCore(run_dir, create=False, read_only=True, catalog=catalog())
            registration().bind_schema_catalog(preflight.catalog)
            self.read_only = bool(preflight.event_store.object_rows_by_type("run_terminal_evidence/v1"))
            if self.read_only:
                # Terminal reopen is pure observation: never clone checkpoint,
                # rerun terminal publication, or advance any writer epoch.
                reg = registration()
                material = _load_resume_material(preflight, reg)
                self.owner = None
                self.core = preflight
                owner_principal = material.principal_ref
            else:
                # Original input checkpoints and exact completions use native
                # resume, without checkpoint reentry or a new input generation.
                self.owner = resume_run(registration(), run_dir=run_dir, model_condition=MODEL, catalog=catalog())
        else:
            if type(decision_seconds) is not int or not 1 <= decision_seconds <= 86400:
                raise ValueError("decision_seconds must be an integer from 1 to 86400")
            declaration = module()
            policy = OwnerInput(schema_id("policy"), canonical_json({"case_id": "case-001",
                "policy": POLICY, "order": ORDER, "decision_seconds": decision_seconds}), "Synthetic refund policy and order")
            self.owner = start_run(declaration, registration(), run_dir=run_dir,
                task_input=policy, entry_inputs={"policy": policy}, catalog=catalog(),
                budgets=ModuleBudgetDeclaration(tuple(declaration.to_dict()["budget_buckets"]),
                    ("rpnh/module_declaration/v1",), 2, 0, 2, 0), model_condition=MODEL,
                owner_statement="Controlled local mock refund; requester and approver are separate native actors",
                command_id="approval:fresh", owner_name="Separate demo owner")
            self.owner.schema_gateway.bind_source_identity(source_id="approval-owner", command_id="owner-source")
        if not self.read_only:
            self.core = self.owner._core
            owner_principal = self.owner.principal_ref
        self.people = {label: personal(self.directory / label, label, reopen=reopen, read_only=self.read_only)
                       for label in ("requester", "approver", "unrelated")}
        self.host = None if self.read_only else UserCollaborationHost(self.core, self.owner.schema_gateway)
        self.sessions = {}
        origins = {"owner": (self.core, owner_principal), **self.people}
        for label, (core, principal) in origins.items():
            if reopen:
                from cpn.rpnh.registry.user_collaboration import fact_ref, mint_session
                session_ref = fact_ref(self.core.task_id, "session", "approval-session:" + label)
                if self.read_only:
                    # Restore a read handle from the original session and owned
                    # origin, never client JSON; no writer session is resumed.
                    from cpn.rpnh.registry._event_store.branch_publication import _local_binding
                    from cpn.rpnh.registry._event_store.user_collaboration import exact
                    with core.event_store.connect() as db:
                        origin = _local_binding(db, core.catalog, core.task_id, core.object_store)
                        exact(core.event_store, db, core.task_id, ref_payload(principal), "principal/v1")
                    with self.core.event_store.connect() as db:
                        data = exact(self.core.event_store, db, self.core.task_id, ref_payload(session_ref))["data"]
                    if (data["origin_principal_ref"] != ref_payload(principal)
                            or data["origin_task_ref"] != origin["task_ref"]
                            or data["origin_source"] != origin["source_id"]):
                        raise RegistryConflict("terminal read session differs from original owned Core")
                    session = mint_session(self.core.event_store, session_ref, data["actor"])
                else:
                    session = self.host.resume_session(session_ref, origin_core=core, principal_ref=principal)
            else:
                session = self.host.create_session(origin_core=core, principal_ref=principal,
                                                   command_id="approval-session:" + label)
            self.sessions[label] = session
        self.inputs = None if self.read_only else RunInputHost(self.owner)
        if not reopen:
            _, _, marking = hydrate_module_runtime(self.owner._core)
            anchor = next(t for t in marking.tokens if t.state.consumed_by is None)
            self.inputs.open_wait(self.sessions["owner"], spec=InputWaitSpec(
                transition_id="candidate.run", input_port="candidate.request",
                anchor_token_ref=anchor.token_ref, binding_ref=anchor.state.resource_ref,
                allowed_principal_refs=(self.host.session_principal(self.sessions["requester"]),)),
                command_id=REQUEST_WAIT)
        if not self.read_only:
            self._progress()

    def _require(self, session, labels):
        from cpn.rpnh.registry.user_collaboration import check_session
        check_session(self.core.event_store, session)
        for label in labels:
            expected = self.sessions[label]
            if session.ref == expected.ref and session.actor == expected.actor:
                return label
        raise PermissionError("client is not granted this material or action")

    def _wait(self, command):
        return self.inputs.inspect(reference(self.core.task_id, WAIT, command_key(command)))

    def _wait_document(self, wait):
        return dict(self.core.get_version(wait["wait_ref"].version_id).metadata)

    def _read(self, ref):
        version_id = ref.resource_version_id if hasattr(ref, "resource_version_id") else ref.version_id
        return json.loads(self.core.object_store.read_registered(self.core.get_version(version_id)))

    def _products(self, stage):
        rows = self.core.event_store.object_rows_by_type("resource_version/v1")
        found = []
        for row in rows:
            version = self.core.get_version(row["version_id"])
            if version.metadata.get("descriptors", {}).get("approval_stage") == stage:
                ref = VersionRef("resource_version/v1", version.logical_id, version.version_id)
                found.append((ref, self._read(ref)))
        if len(found) > 1:
            raise RegistryConflict("more than one immutable output for the one-case demo")
        return found[0] if found else None

    def _run(self):
        loop = OwnerEventLoop(self.owner, self.directory / "run" / "owner.sock")
        try:
            services = ExecutionServices(owner=self.owner, event_loop=loop)
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = Orchestrator(owner=self.owner, event_loop=loop,
                    prepare_dispatcher=services.prepare_dispatcher, submit_operation=pool.submit).run()
            if result.completion_error is not None:
                raise result.completion_error
            return result
        finally:
            loop.close()

    def _progress(self):
        # Invoke the existing Harness; only its typed marking chooses operations.
        request = self._wait(REQUEST_WAIT)
        if "receipt" not in request:
            return
        if not self._products("refund"):
            self._expire_inputs()
        self._run()
        if self._products("refund"):
            return
        candidate = self._products("candidate")
        if candidate is None:
            return  # unresolved execution stays unknown; never manufacture a candidate
        decision_ref = reference(self.owner._core.task_id, WAIT, command_key(DECISION_WAIT))
        if any(w["wait_ref"] == decision_ref for w in self.inputs.waits()):
            return
        _, _, marking = hydrate_module_runtime(self.owner._core)
        anchor = next(t for t in marking.tokens if t.state.consumed_by is None
                      and t.state.resource_ref and t.state.resource_ref.as_version_ref() == candidate[0])
        policy = self._read(self.owner.original_input_ref)
        deadline = datetime.now(timezone.utc) + timedelta(seconds=policy["decision_seconds"])
        self.inputs.open_wait(self.sessions["owner"], spec=InputWaitSpec(
            transition_id="refund.run", input_port="refund.decision", anchor_token_ref=anchor.token_ref,
            binding_ref=anchor.state.resource_ref,
            allowed_principal_refs=(self.host.session_principal(self.sessions["approver"]),),
            expires_at_utc=deadline.isoformat(),
            expiry_input=OwnerInput(schema_id("decision"), canonical_json({"decision": "expired",
                "candidate_ref": ref_payload(candidate[0]), "candidate": candidate[1]}), "Expired; no refund")),
            command_id=DECISION_WAIT)

    def request(self, session, body):
        with self.lock:
            self._require(session, ("requester",))
            if self.read_only:
                raise RegistryConflict("terminal runtime is read-only; inspect original result")
            if (type(body) is not dict or set(body) != {"command_id", "case_id", "amount_minor", "currency", "reason"}
                    or type(body["amount_minor"]) is not int or not 1 <= body["amount_minor"] <= POLICY["max_amount_minor"]
                    or body["case_id"] != "case-001" or body["currency"] != POLICY["currency"]
                    or not isinstance(body["reason"], str) or not 1 <= len(body["reason"].strip()) <= 2000):
                raise ValueError("request must match synthetic case and integer-minor-unit policy")
            wait = self._wait(REQUEST_WAIT)
            doc = self._wait_document(wait)
            value = {k: v for k, v in body.items() if k != "command_id"}
            value.update(target={"run_ref": doc["run_ref"], "task_ref": doc["task_ref"],
                "net_ref": doc["net_ref"], "transition_id": "refund.run"},
                requester_session_ref=ref_payload(session.ref),
                wait_ref=ref_payload(reference(self.owner._core.task_id, WAIT, command_key(DECISION_WAIT))))
            receipt = self.inputs.submit(session, wait_ref=wait["wait_ref"],
                expected_checkpoint_ref=_version_from_payload(doc["checkpoint_ref"]),
                binding_ref=wait["spec"].binding_ref,
                declaration=OwnerInput(schema_id("request"), canonical_json(value), "Synthetic refund request"),
                command_id="request:" + self._command(body))
            self._progress()
            return {"receipt_ref": ref_payload(receipt.receipt_ref), "replayed": receipt.replayed}

    @staticmethod
    def _command(body):
        key = body.get("command_id")
        if not isinstance(key, str) or not 1 <= len(key) <= 200:
            raise ValueError("command_id must be a nonempty bounded string")
        return key

    def decide(self, session, body):
        with self.lock:
            self._require(session, ("approver",))
            if self.read_only:
                raise RegistryConflict("terminal runtime is read-only; inspect original result")
            if type(body) is not dict or set(body) != {"command_id", "decision", "candidate_ref", "wait_ref", "target", "candidate"}:
                raise ValueError("decision requires the complete unchanged candidate and exact target")
            candidate = self._products("candidate")
            if candidate is None:
                raise RegistryConflict("no registered candidate")
            ref, value = candidate
            if (body["candidate_ref"] != ref_payload(ref) or canonical_json(body["candidate"]) != canonical_json(value)
                    or body["wait_ref"] != value["wait_ref"] or body["target"] != value["target"]
                    or body["decision"] not in {"approve", "deny"}):
                raise RegistryConflict("decision differs from exact candidate, wait or target")
            wait = self._wait(DECISION_WAIT)
            doc = self._wait_document(wait)
            receipt = self.inputs.submit(session, wait_ref=wait["wait_ref"],
                expected_checkpoint_ref=_version_from_payload(doc["checkpoint_ref"]),
                binding_ref=wait["spec"].binding_ref,
                declaration=OwnerInput(schema_id("decision"), canonical_json({"decision": body["decision"],
                    "candidate_ref": body["candidate_ref"], "candidate": value}), "Exact refund decision"),
                command_id="decision:" + self._command(body))
            self._progress()
            return {"receipt_ref": ref_payload(receipt.receipt_ref), "replayed": receipt.replayed}

    def expire_due(self):
        """Owner maintenance at an HTTP observation boundary; native expiry owns admission."""
        with self.lock:
            if self.read_only:
                return
            if self._expire_inputs():
                self._progress()

    def _expire_inputs(self):
        from cpn.rpnh.registry.module_execution import active_module_firings
        executable, _, marking = hydrate_module_runtime(self.owner._core)
        if self._products("refund") or active_module_firings(self.owner._core, executable.net_ref):
            return False  # Recovery owns dispatched/unknown work, never expiry or replay.
        changed = False
        for wait in self.inputs.waits():
            deadline = wait["spec"].expires_at_utc
            if deadline and wait["status"] in {"waiting", "accepted"} and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
                self.inputs.expire(self.sessions["owner"], wait_ref=wait["wait_ref"],
                    expected_checkpoint_ref=marking.checkpoint_ref,
                    command_id="approval:expire:" + str(wait["wait_ref"].version_id))
                changed = True
        return changed

    def state(self, session):
        with self.lock:
            label = self._require(session, ("requester", "approver"))
            candidate = self._products("candidate")
            final = self._products("refund")
            terminals = self.core.event_store.object_rows_by_type("run_terminal_evidence/v1")
            # Published outputs without terminal settlement remain unknown, never green.
            status = "not_requested" if candidate is None else "waiting"
            if final:
                status = final[1]["status"] if terminals else "unknown"
            elif candidate:
                waits = [w for w in self.inputs.waits() if ref_payload(w["wait_ref"]) == candidate[1]["wait_ref"]]
                if len(waits) != 1 or "receipt" in waits[0]:
                    status = "unknown"
            elif "receipt" in self._wait(REQUEST_WAIT):
                status = "unknown"
            actions = ["request"] if label == "requester" and status == "not_requested" else []
            if label == "approver" and status == "waiting":
                actions = ["decide"]
            shown = None
            if candidate and label == "approver":
                shown = {**candidate[1], "candidate_ref": ref_payload(candidate[0]),
                         "immutable_candidate": candidate[1]}
            _, _, marking = hydrate_module_runtime(self.core)
            return {"client": {"display_name": "Controlled " + label, "actor": session.actor,
                               "session_ref": ref_payload(session.ref), "allowed_actions": actions},
                "case": {"id": "case-001", "status": status,
                         "request": None if candidate is None else {k: candidate[1][k]
                            for k in ("order", "amount_minor", "currency", "reason")}, "candidate": shown},
                "result": None if final is None else {"resource_ref": ref_payload(final[0]), **final[1]},
                "checkpoint_ref": ref_payload(marking.checkpoint_ref),
                "viewer_url": "/viewer", "navigation": {"home": "/", "requester": "/requester", "approver": "/approver"},
                "notices": ["Separate controlled browser clients and native actors; synthetic data only.",
                            "独立受控客户端与原生主体；仅合成数据。"]}
