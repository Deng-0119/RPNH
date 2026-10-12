"""Trusted fixture adapter; all durable waits/results belong to the original Registry."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace

from cpn.components.execution_services import ExecutionServices
from cpn.orchestrator.runner import Orchestrator
from cpn.rpnh.collaboration import UserCollaborationHost
from cpn.rpnh.control_server import OwnerEventLoop
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.event_store import RegistryConflict
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.module_budgets import ModuleBudgetDeclaration
from cpn.rpnh.registry.module_execution import active_module_firings
from cpn.rpnh.registry.module_runtime import hydrate_module_runtime
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.run_inputs import WAIT, reference, command_key
from cpn.rpnh.registry.schema_catalog import canonical_json
from cpn.rpnh.registry.strict_contracts import ref_payload
from cpn.rpnh.run import OwnerInput, start_run, resume_run, _load_resume_material
from cpn.rpnh.run_inputs import RunInputHost, InputWaitSpec
from .contracts import MODEL, STAGE, STAGE2, FIXTURES, ASSOCIATION, module, schema_id, evidence
from .host import registration
from .native import catalog, safe_destination, personal


class A2AQuoteApp:
    def __init__(self, directory, *, workspace, reopen=False, proof_seconds=900,
                 context_id="synthetic-context-1", request_id="synthetic-task-1"):
        self.directory = safe_destination(directory, workspace)
        self.lock = threading.RLock()  # Serialization, never a business state map.
        if not reopen and self.directory.exists():
            raise ValueError("fresh runtime directory must be absent")
        if not reopen:
            self.directory.mkdir(parents=True)
        self.read_only = False
        run_dir = self.directory / "run"
        reg = registration()
        if reopen:
            core = _RegistryCore(run_dir, create=False, read_only=True, catalog=catalog())
            reg.bind_schema_catalog(core.catalog)
            self.read_only = bool(core.event_store.object_rows_by_type("run_terminal_evidence/v1"))
            if self.read_only:
                material = _load_resume_material(core, reg)
                # A terminal observer has no writer gateway, RunOwner or input host.
                self.owner = SimpleNamespace(_core=core, original_input_ref=material.original_input_ref,
                                             principal_ref=material.principal_ref)
            else:
                self.owner = resume_run(reg, run_dir=run_dir, model_condition=MODEL, catalog=catalog())
        else:
            if type(proof_seconds) is not int or not 1 <= proof_seconds <= 86400:
                raise ValueError("proof_seconds must be an integer from 1 to 86400")
            if any(not isinstance(x, str) or not 1 <= len(x) <= 150 for x in (context_id, request_id)):
                raise ValueError("synthetic task/context labels must be bounded nonempty strings")
            declaration = module()
            value = {"case_id": "quote-001", "hotel": "Synthetic Harbor Hotel",
                "base_amount_minor": 20000, "currency": "USD",
                "assistant_task_id": "assistant:" + request_id, "assistant_context_id": context_id,
                "merchant_task_id": "merchant:" + request_id, "merchant_context_id": context_id,
                "proof_seconds": proof_seconds, "evidence_level": STAGE}
            request = OwnerInput(schema_id("request"), canonical_json(value), "Synthetic hotel quote request")
            self.owner = start_run(declaration, reg, run_dir=run_dir,
                task_input=request, entry_inputs={"request": request}, catalog=catalog(),
                budgets=ModuleBudgetDeclaration(tuple(declaration.to_dict()["budget_buckets"]),
                    ("rpnh/module_declaration/v1",), 1, 0, 1, 0), model_condition=MODEL,
                owner_statement="Local fixture association only; no OID4VP verification or external effects",
                command_id="a2a:fresh", owner_name="Separate quote demo owner")
            self.owner.schema_gateway.bind_source_identity(source_id="a2a-owner", command_id="owner-source")
        self.people = {label: personal(self.directory / label, label, reopen=reopen, read_only=self.read_only)
                       for label in ("holder", "verifier", "unrelated")}
        self.host = None if self.read_only else UserCollaborationHost(self.owner._core, self.owner.schema_gateway)
        self.sessions = {}
        for label, (core, principal) in {"owner": (self.owner._core, self.owner.principal_ref), **self.people}.items():
            if reopen:
                from cpn.rpnh.registry.user_collaboration import fact_ref, mint_session
                session_ref = fact_ref(self.owner._core.task_id, "session", "a2a-session:" + label)
                if self.read_only:
                    from cpn.rpnh.registry._event_store.branch_publication import _local_binding
                    with core.event_store.connect() as db:
                        origin = _local_binding(db, core.catalog, core.task_id, core.object_store)
                    data = self.owner._core.get_version(session_ref.version_id).metadata["data"]
                    if (data["origin_principal_ref"] != ref_payload(principal)
                            or data["origin_task_ref"] != origin["task_ref"]
                            or data["origin_source"] != origin["source_id"]):
                        raise RegistryConflict("terminal session differs from original owned Core")
                    session = mint_session(self.owner._core.event_store, session_ref, data["actor"])
                else:
                    session = self.host.resume_session(session_ref, origin_core=core, principal_ref=principal)
            else:
                session = self.host.create_session(origin_core=core, principal_ref=principal,
                                                    command_id="a2a-session:" + label)
            self.sessions[label] = session
        self.inputs = None if self.read_only else RunInputHost(self.owner)
        if not reopen:
            self._open_wait(1)
        if not self.read_only:
            # Reopen only from persisted association. Never guess a missing mapping.
            self._association(self._current_wait())
            self._progress()

    def _require(self, session, label):
        from cpn.rpnh.registry.user_collaboration import check_session
        check_session(self.owner._core.event_store, session)
        expected = self.sessions[label]
        if session.ref != expected.ref or session.actor != expected.actor:
            raise PermissionError("client is not bound to this action")

    def _read(self, ref):
        version_id = ref.resource_version_id if hasattr(ref, "resource_version_id") else ref.version_id
        return json.loads(self.owner._core.object_store.read_registered(self.owner._core.get_version(version_id)))

    def _result(self):
        found = []
        for row in self.owner._core.event_store.object_rows_by_type("resource_version/v1"):
            version = self.owner._core.get_version(row["version_id"])
            if version.metadata.get("descriptors", {}).get("a2a_stage") == "quote":
                ref = VersionRef("resource_version/v1", version.logical_id, version.version_id)
                found.append((ref, self._read(ref)))
        if len(found) > 1:
            raise RegistryConflict("more than one quote output for the original request")
        return found[0] if found else None

    def _current_wait(self):
        waits = [w for w in self.inputs.waits() if w["status"] != "superseded"]
        if len(waits) != 1:
            raise RegistryConflict("missing exact persisted association; unresolved, require a new request")
        return waits[0]

    def _document(self, wait):
        return dict(self.owner._core.get_version(wait["wait_ref"].version_id).metadata)

    def _association(self, wait):
        """The immutable native wait's expiry input carries the full nonsecret binding."""
        from jsonschema import Draft7Validator
        spec, doc = wait["spec"], self._document(wait)
        if spec.expiry_input is None:
            raise RegistryConflict("missing persisted association; require a new request")
        try:
            payload = json.loads(spec.expiry_input.payload)
            association = payload["association"]
            Draft7Validator(ASSOCIATION).validate(association)
        except Exception as exc:
            raise RegistryConflict("missing or invalid persisted association; require a new request") from exc
        request = self._read(spec.binding_ref)
        expected = {"run_ref": doc["run_ref"], "task_ref": doc["task_ref"], "net_ref": doc["net_ref"],
            "wait_ref": ref_payload(wait["wait_ref"]), "request_resource_ref": ref_payload(spec.binding_ref.as_version_ref()),
            "transition_id": "quote.run", "expires_at_utc": spec.expires_at_utc,
            "verifier_identity_ref": ref_payload(self.host.session_principal(self.sessions["verifier"])),
            "verifier_host_session_ref": ref_payload(self.sessions["verifier"].ref),
            **{k: request[k] for k in ("assistant_task_id", "assistant_context_id", "merchant_task_id", "merchant_context_id")}}
        if (any(association[k] != v for k, v in expected.items())
                or payload != evidence(association, "timeout")):
            raise RegistryConflict("persisted association differs from native authority")
        return association

    def _open_wait(self, generation, predecessor=None):
        executable, _, marking = hydrate_module_runtime(self.owner._core)
        anchor = next(t for t in marking.tokens if t.state.consumed_by is None)
        request = self._read(anchor.state.resource_ref)
        command = "a2a:proof-wait:" + str(generation)
        wait_ref = reference(self.owner._core.task_id, WAIT, command_key(command))
        # These are nonsecret references to synthetic requests, never nonce bytes or URIs.
        suffix = str(wait_ref.version_id)
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=request["proof_seconds"])).isoformat()
        association = {"run_ref": ref_payload(self.owner.identity.run_ref),
            "task_ref": ref_payload(self.owner.identity.task_ref), "net_ref": ref_payload(executable.net_ref),
            "wait_ref": ref_payload(wait_ref), "request_resource_ref": ref_payload(anchor.state.resource_ref.as_version_ref()),
            "transition_id": "quote.run", "generation": generation,
            **{k: request[k] for k in ("assistant_task_id", "assistant_context_id", "merchant_task_id", "merchant_context_id")},
            "verifier_identity_ref": ref_payload(self.host.session_principal(self.sessions["verifier"])),
            "verifier_host_session_ref": ref_payload(self.sessions["verifier"].ref),
            "verification_session_ref": "synthetic-session:" + suffix,
            "authorization_request_ref": "synthetic-request:" + suffix,
            "challenge_ref": "synthetic-challenge:" + suffix, "expires_at_utc": deadline}
        self.inputs.open_wait(self.sessions["owner"], spec=InputWaitSpec(
            transition_id="quote.run", input_port="quote.evidence", anchor_token_ref=anchor.token_ref,
            binding_ref=anchor.state.resource_ref,
            allowed_principal_refs=(self.host.session_principal(self.sessions["verifier"]),),
            expires_at_utc=deadline,
            expiry_input=OwnerInput(schema_id("evidence"), canonical_json(evidence(association, "timeout")),
                                    "Request expired; no discount; Stage 1 fixture only"),
            supersedes_wait_ref=predecessor), command_id=command)

    def _run(self):
        loop = OwnerEventLoop(self.owner, self.directory / "run" / "owner.sock")
        try:
            services = ExecutionServices(owner=self.owner, event_loop=loop)
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = Orchestrator(owner=self.owner, event_loop=loop,
                    prepare_dispatcher=services.prepare_dispatcher, submit_operation=pool.submit).run()
            if result.completion_error is not None:
                raise result.completion_error
        finally:
            loop.close()

    def _progress(self):
        if self.owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1"):
            return
        self._expire_input()
        wait = self._current_wait()
        self._association(wait)
        if "receipt" in wait:
            self._run()

    def _expire_input(self):
        executable, _, marking = hydrate_module_runtime(self.owner._core)
        if self._result() or active_module_firings(self.owner._core, executable.net_ref):
            return False
        wait = self._current_wait()
        association = self._association(wait)
        if (wait["status"] in {"waiting", "accepted"}
                and datetime.now(timezone.utc) >= datetime.fromisoformat(association["expires_at_utc"])):
            self.inputs.expire(self.sessions["owner"], wait_ref=wait["wait_ref"],
                expected_checkpoint_ref=marking.checkpoint_ref,
                command_id="a2a:expire:" + str(wait["wait_ref"].version_id))
            return True
        return False

    def expire_due(self):
        with self.lock:
            if not self.read_only and self._expire_input():
                self._progress()

    @staticmethod
    def _command(body):
        command = body.get("command_id")
        if not isinstance(command, str) or not 1 <= len(command) <= 200:
            raise ValueError("command_id must be a bounded nonempty string")
        return command

    def select(self, session, body):
        """Only fixture selection crosses HTTP; fixture output is generated by the HOST."""
        with self.lock:
            self._require(session, "holder")
            if type(body) is not dict or set(body) != {"command_id", "association", "fixture"}:
                raise ValueError("select requires exact association and a synthetic fixture; no authority fields")
            if not isinstance(body["fixture"], str) or body["fixture"] not in FIXTURES:
                raise ValueError("unknown synthetic fixture")
            return self.submit_evidence(self.sessions["verifier"],
                evidence(body["association"], body["fixture"]), command_id=self._command(body))

    def submit_evidence(self, session, value, *, command_id):
        """Trusted local adapter API, deliberately not exposed as an HTTP callback."""
        with self.lock:
            self._require(session, "verifier")
            if self.read_only:
                raise RegistryConflict("terminal runtime is read-only")
            command_id = self._command({"command_id": command_id})
            wait = self._current_wait()
            association = self._association(wait)
            if (type(value) is not dict or canonical_json(value.get("association")) != canonical_json(association)
                    or value.get("fixture") not in FIXTURES
                    or canonical_json(value) != canonical_json(evidence(association, value["fixture"]))):
                raise RegistryConflict("callback differs from exact request, challenge, generation or fixture")
            doc = self._document(wait)
            receipt = self.inputs.submit(session, wait_ref=wait["wait_ref"],
                expected_checkpoint_ref=_version_from_payload(doc["checkpoint_ref"]),
                binding_ref=wait["spec"].binding_ref,
                declaration=OwnerInput(schema_id("evidence"), canonical_json(value), "Stage 1 synthetic fixture evidence"),
                command_id="a2a:evidence:" + command_id)
            self._progress()
            return {"receipt_ref": ref_payload(receipt.receipt_ref), "replayed": receipt.replayed}

    def renew(self, session, body):
        with self.lock:
            self._require(session, "holder")
            if self.read_only:
                raise RegistryConflict("terminal runtime is read-only")
            if type(body) is not dict or set(body) != {"association"}:
                raise ValueError("renew requires the complete original association")
            wait = self._current_wait()
            association = self._association(wait)
            if (canonical_json(body["association"]) != canonical_json(association) or wait["status"] != "waiting"
                    or datetime.now(timezone.utc) >= datetime.fromisoformat(association["expires_at_utc"])):
                raise RegistryConflict("only the current unexpired waiting request can be renewed")
            self._open_wait(association["generation"] + 1, wait["wait_ref"])
            return {"status": "waiting", "generation": association["generation"] + 1}

    def state(self, session):
        with self.lock:
            self._require(session, "holder")
            result = self._result()
            terminals = self.owner._core.event_store.object_rows_by_type("run_terminal_evidence/v1")
            status, association = "unresolved", None
            if result:
                status = "completed" if terminals else "unknown"
                association = result[1]["association"]
            elif not self.read_only:
                try:
                    wait = self._current_wait()
                    association = self._association(wait)
                    status = "waiting" if wait["status"] == "waiting" else "unknown"
                except RegistryConflict:
                    pass  # No guessed mapping or regenerated callback on missing facts.
            _, _, marking = hydrate_module_runtime(self.owner._core)
            return {"status": status, "association": association,
                "request": self._read(self.owner.original_input_ref),
                "result": None if result is None else {"resource_ref": ref_payload(result[0]), **result[1]},
                "client": {"display_name": "Controlled synthetic holder", "actor": session.actor,
                    "session_ref": ref_payload(session.ref), "allowed_actions": ["select", "renew"] if status == "waiting" else []},
                "checkpoint_ref": ref_payload(marking.checkpoint_ref), "viewer_url": "/viewer",
                "evidence_level": STAGE, "stage2": STAGE2,
                "stage2_missing": ["@a2a-js/sdk", "@credo-ts/*", "@openwallet-foundation/askar-*",
                                   "existing signed test credentials / wallet test materials"],
                "counts": {"quote_firings": len(self.owner._core.event_store.object_rows_by_type("transition_firing/v1")),
                    "input_receipts": len(self.owner._core.event_store.object_rows_by_type("run_input_receipt/v1")),
                    "model_calls": list(self.owner._core.event_store.actual_model_call_counts())}}
