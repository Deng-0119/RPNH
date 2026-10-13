"""Bounded operator commands over an already assembled trusted local HOST.

A ProjectEndpoint is bound by the HOST to one original session. Neither a
request actor nor a path supplied by a client can select a Core or identity.
This is a same-OS controlled integration, not remote account authentication.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import uuid

from ..module import ModuleDeclaration
from ..registry.event_store import RegistryConflict
from ..registry.publication import _version_from_payload
from ..registry.schema_catalog import canonical_json
from ..registry.strict_contracts import ref_payload
from ..registry.user_collaboration import PROJECT, PROPOSAL, DECISION, RECEIPT, SESSION
from ..registry._event_store.branch_publication import branch_command_key
from ..registry._event_store.user_collaboration import require_owner, require_propose, exact
from .branches import read_branch_version, current_branch
from .materials import ClosedModuleAuthor, validate_closed_revision, _elements
from .plain_merge import PlainModuleMergeAnalyzer
from .plain_merge_result import PlainModuleMergeAuthor
from .references import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from .user_collaboration import UserCollaborationHost
from .project_framing import encode_response

MAX_REQUEST_BYTES = 300000

# Exact request surfaces. Unknown actor/session/origin/permission fields fail
# before any Registry write, rather than being ignored or interpreted.
COMMANDS = {
    "status": ((), ()),
    "fact": (("reference",), ()),
    "module": (("revision",), ()),
    "history": (("revision",), ()),
    "invite": (("subject_session", "role", "command_id"), ()),
    "join": (("invitation", "command_id"), ()),
    "revoke": (("subject_session", "command_id"), ()),
    "propose": (("base", "branch", "head", "command_id", "reason"), ("previous_proposal", "document", "element_ids")),
    "restore-content": (("restore_from", "base", "branch", "head", "command_id", "reason"), ()),
    "review": (("proposal", "disposition", "reason", "command_id"), ()),
    "analyze": (("base", "local", "incoming", "command_id"), ()),
    "merge": (("analysis", "choices", "command_id"), ()),
    "accept": (("revision", "branch", "head", "proposals", "reviews", "command_id"), ()),
    "adopt": (("receipt", "branch", "head", "runtime", "marking_mapping", "retire_token_refs", "owner_inputs", "command_id"), ()),
    "viewer": ((), ()),
    "runtime-status": ((), ()),
    "artifacts-plan": ((), ()),
    "artifact-read": (("checkpoint", "workspace", "path"), ()),
    "read-plan": (("revision", "include_runtime"), ()),
    "download": (("revision", "destination"), ()),
}


def parse_request(raw):
    """Strict, bounded JSON transport; all command fields remain untrusted."""
    if type(raw) is not bytes or len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds 300000 bytes")
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate request key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite request value")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite request number")
        return parsed
    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)
        canonical_json(result)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("invalid bounded request") from exc
    return result


class ProjectEndpoint:
    """One fixed trusted route. Construct only inside the owner application.

    completed_origin is an original owned Core whose successful native TEXT
    output will be delivered as the next proposal. It is set by trusted HOST
    assembly, never decoded from request JSON. If the text came from a human,
    the source executor must identify itself as a human-input import tool.
    reader and viewer are already-authorized original read surfaces. This
    adapter never issues read grants or opens Registry writers.
    """
    def __init__(self, host, session, project_ref, *, owner, origin_core,
                 principal_ref, completed_origin=None, execution_ref=None, human_validator=None, reader=None, viewer=None):
        if type(host) is not UserCollaborationHost or host._core is not owner._core:
            raise TypeError("original HOST and its actual owner are required")
        # Re-derive identity from the actual owned origin before accepting a
        # route. Keep any existing observer; resume itself mints no grant.
        resumed = host.resume_session(session.ref, origin_core=origin_core,
                                      principal_ref=principal_ref)
        if resumed.actor != session.actor:
            raise RegistryConflict("route identity changed")
        host._session(session)
        if project_ref.entity_type != PROJECT:
            raise ValueError("exact original project required")
        self.host, self.session, self.project_ref = host, session, project_ref
        self.owner, self.origin_core = owner, origin_core
        self.completed_origin, self.reader, self.viewer = completed_origin, reader, viewer
        self.execution_ref, self.human_validator = execution_ref, human_validator
        self.core, self.gateway = host._core, owner.schema_gateway
        self.registration = owner.registration

    def _version(self, value, kind=None):
        if type(value) is not dict or set(value) != {"entity_type", "logical_id", "version_id"}:
            raise ValueError("exact local version reference required")
        ref = _version_from_payload(value)
        if kind is not None and ref.entity_type != kind:
            raise ValueError("wrong reference type")
        return ref

    def _qualified(self, value, kind="collaboration_net_revision/v1"):
        ref = SourceQualifiedVersionRef.from_dict(value, catalog=self.core.catalog)
        if ref.source_id != self.host._binding["source_id"] or ref.ref.entity_type != kind:
            raise ValueError("exact local supported reference required")
        return ref

    def _project(self):
        return self.host.read_fact(self.session, self.project_ref)

    def _authorize(self, *, owner=False):
        project = self._project()
        with self.core.event_store.connect() as db:
            db.execute("BEGIN")
            session = exact(self.core.event_store, db, self.core.task_id,
                            ref_payload(self.session.ref), SESSION)
            if owner:
                require_owner(self.core.event_store, db, self.core.task_id, project, session)
            else:
                require_propose(self.core.event_store, db, self.core.task_id, project, session["self_ref"])
        return project

    def _command(self, request):
        value = request["command_id"]
        if type(value) is not str or not value or len(value) > 160 or value.strip() != value or any(ord(c) < 32 for c in value):
            raise ValueError("command_id must be a stable printable string of 1–160 characters")
        # Stable session qualification prevents independent clients colliding.
        return "project-entry:" + self.session.actor + ":" + request["op"] + ":" + value

    def _author(self):
        producer = SourceQualifiedVersionRef(self.host._binding["source_id"],
                                             self.host.session_principal(self.session))
        return ClosedModuleAuthor(self.gateway, self.registration, producer)

    def _read_revision(self, ref):
        if self.reader is None:
            self._authorize(owner=True)
            value = validate_closed_revision(self.core, ref, self.registration)
            return {"module": value.module.to_dict(), "element_ids": {r["locator"]: r["element_id"] for r in value.element_map["elements"]}}
        cut = self.reader.capture_cut(ref.source_id)
        record = self.reader.read_exact(entry_ref=ref, at_cut=cut)["record"]
        material = SourceQualifiedResourceRef.from_dict(record["definition_ref"], catalog=self.core.catalog)
        body = self.reader.read_material(resource_ref=material, at_cut=cut)["body"]
        mapping = SourceQualifiedResourceRef.from_dict(record["element_mapping_ref"], catalog=self.core.catalog)
        elements = json.loads(self.reader.read_material(resource_ref=mapping, at_cut=cut)["body"])
        result = {"module": json.loads(body), "element_ids": {r["locator"]: r["element_id"] for r in elements["elements"]}}
        self.reader.final_recheck((ref.source_id,))
        self._project()
        return result

    def _history(self, reference):
        from .authoring import read_net_revision
        pending, records = [reference], {}
        cut = None if self.reader is None else self.reader.capture_cut(reference.source_id)
        while pending:
            ref = pending.pop()
            if ref in records:
                continue
            if len(records) >= 128:
                raise ValueError("history exceeds the bounded 128 revision view")
            if self.reader is None:
                self._authorize(owner=True)
                row = read_net_revision(self.core, ref, local_source_id=self.host._binding["source_id"]).to_dict()
            else:
                row = self.reader.read_exact(entry_ref=ref, at_cut=cut)["record"]
                self.reader.final_recheck((ref.source_id,))
            records[ref] = row
            pending.extend(self._qualified(parent) for parent in row["parent_revision_refs"])
        self._project()
        return list(records.values())

    def dispatch(self, request):
        if type(request) is not dict or request.get("op") not in COMMANDS:
            raise ValueError("unknown project command")
        required, optional = COMMANDS[request["op"]]
        if not set(required) <= set(request) or set(request) - {"op", *required, *optional}:
            raise ValueError("unexpected or missing command fields; identity is HOST-bound")
        json.dumps(request, allow_nan=False)
        if len(canonical_json(request)) > MAX_REQUEST_BYTES:
            raise ValueError("request exceeds 300000 bytes")
        op = request["op"]
        project = self._project()  # Current original read fence on every call.
        if op == "status":
            facts = self.host.read_view(self.session)
            # Facts are source-wide according to the original read contract;
            # present this exact project and associated proposal history only.
            result = {"project": project, "actor": self.session.actor,
                      "session": ref_payload(self.session.ref),
                      "facts": {kind: [v for v in rows if v["project_ref"] == project["self_ref"]]
                                for kind, rows in facts.items()}}
            if project["data"]["owner_session_ref"] == ref_payload(self.session.ref):
                branch = current_branch(self.core, self._version(project["data"]["branch_ref"]).entity_id)
                result["branch"] = branch.to_dict()
            return result
        if op == "fact":
            fact = self.host.read_fact(self.session, self._version(request["reference"]))
            if fact["project_ref"] not in (None, project["self_ref"]) and fact["self_ref"] != project["self_ref"]:
                raise RegistryConflict("fact belongs to another project")
            return fact
        if op == "history":
            return {"revisions": self._history(self._qualified(request["revision"]))}
        if op == "module":
            return self._read_revision(self._qualified(request["revision"]))
        if op == "read-plan":
            if type(request["include_runtime"]) is not bool:
                raise ValueError("include_runtime must be an explicit boolean")
            from .project_read_plan import suggest_read_scope
            return suggest_read_scope(self, self._qualified(request["revision"]), include_runtime=request["include_runtime"])
        if op == "artifacts-plan":
            from .project_artifacts import artifact_plan
            return artifact_plan(self)
        if op == "artifact-read":
            from .project_artifacts import read_artifact
            return read_artifact(self, request)
        if op == "runtime-status":
            self._authorize(owner=True)
            return self.owner.snapshot()
        if op == "download":
            if self.reader is None:
                raise ValueError("download requires an existing scoped Registry reader")
            from .subnet_exchange import plan_subnet_export, export_subnet
            import base64
            revision = self._qualified(request["revision"])
            cut = self.reader.capture_cut(revision.source_id)
            plan = plan_subnet_export(self.reader, root_revision_ref=revision,
                cuts={revision.source_id: cut})
            exported = export_subnet(self.reader, plan=plan, destination=request["destination"])
            return {"result": exported.to_dict(),
                "archive_base64": base64.b64encode(exported.package.archive_bytes).decode(),
                "dependency_archives_base64": [base64.b64encode(raw).decode() for raw in exported.dependency_archives]}
        if op == "viewer":
            self._authorize(owner=True)
            if self.viewer is None:
                raise ValueError("this HOST route has no existing authorized Viewer")
            result = self.viewer.dashboard()
            self._project()
            return result
        command = self._command(request)
        if op == "invite":
            self._authorize(owner=True)
            return ref_payload(self.host.invite(self.session, self.project_ref,
                subject_session_ref=self._version(request["subject_session"], SESSION),
                role=request["role"], command_id=command))
        if op == "join":
            invitation = self.host.read_fact(self.session, self._version(request["invitation"], "user_collaboration_participant/v1"))
            if invitation["project_ref"] != project["self_ref"]:
                raise RegistryConflict("invitation belongs to another project")
            return ref_payload(self.host.join(self.session,
                self._version(request["invitation"], "user_collaboration_participant/v1"), command_id=command))
        if op == "revoke":
            self._authorize(owner=True)
            return ref_payload(self.host.revoke(self.session, self.project_ref,
                subject_session_ref=self._version(request["subject_session"], SESSION), command_id=command))
        if op == "review":
            proposal = self.host.read_fact(self.session, self._version(request["proposal"], PROPOSAL))
            if proposal["project_ref"] != project["self_ref"]:
                raise RegistryConflict("proposal belongs to another project")
            return ref_payload(self.host.review(self.session, self._version(request["proposal"], PROPOSAL),
                disposition=request["disposition"], reason=request["reason"], command_id=command))
        if op in {"propose", "restore-content"}:
            self._authorize()
            from ..registry.user_collaboration_source import completed_text, strict_proposal_json
            base = self._qualified(request["base"])
            branch_ref = self._qualified(request["branch"], "collaboration_branch/v1")
            head = self._qualified(request["head"])
            branch = read_branch_version(self.core, branch_ref)
            if (base != head or branch.head_revision_ref != head
                    or branch_ref.ref.entity_id != self._version(project["data"]["branch_ref"]).entity_id):
                raise RegistryConflict("proposal must pin this project's exact branch and head")
            # Immutable retry can retain the historical expected branch. New
            # commands must start from the actual current head.
            prior = self.host._prior("proposal", command)
            if prior is None and current_branch(self.core, branch_ref.ref.entity_id).branch_ref != branch_ref:
                raise RegistryConflict("proposal branch is stale")
            parent = validate_closed_revision(self.core, base, self.registration)
            restore_from = None
            if op == "restore-content":
                restore_from = self._qualified(request["restore_from"])
                if restore_from.to_dict() not in [row["revision_ref"] for row in self._history(base)]:
                    raise RegistryConflict("restore target is not in the exact current head history")
                # Read authorization is separate from contribution eligibility.
                selected = self._read_revision(restore_from)
                document = selected["module"]
                current_ids = {r["element_id"]: r["kind"] for r in parent.element_map["elements"]}
                kinds = _elements(ModuleDeclaration.from_dict(document))
                # Identity equality, never matching names, proves retention.
                ids = {path: identity if current_ids.get(identity) == kinds[path] else
                       "element:" + uuid.uuid5(uuid.NAMESPACE_URL, command + ":" + path).hex
                       for path, identity in selected["element_ids"].items()}
            elif "document" in request:
                document = strict_proposal_json(canonical_json(request["document"]))
                if "element_ids" not in request:
                    raise ValueError("human proposal needs explicit element_ids from module inspection; no implicit rename matching")
                ids = request["element_ids"]
            else:
                document, ids = None, request.get("element_ids")
            is_human_input = document is not None
            origin, execution = self.completed_origin, self.execution_ref
            if document is not None:
                if self.human_validator is None:
                    raise ValueError("this HOST has no native human-input validation route")
                origin, execution = self.human_validator(self.session, document, command,
                    None if restore_from is None else restore_from.to_dict())
            if origin is None:
                raise ValueError("no successful source TEXT is attached by this trusted route")
            _, raw, _ = completed_text(origin)
            delivered_document = strict_proposal_json(raw)
            if document is not None and canonical_json(document) != canonical_json(delivered_document):
                raise RegistryConflict("validated TEXT changed the submitted human content")
            document = delivered_document
            if ids is None:
                raise ValueError("proposal requires explicit element identities")
            module = ModuleDeclaration.from_dict(document)
            # Delivery validates original actor/execution before candidate publication.
            delivery = self.host.ingest_completed_text(self.session, origin_core=origin,
                execution_ref=execution, command_id=command + ":delivery")
            candidate = self._author().publish(module=module, element_ids=ids,
                parent_ref=base, command_id=command + ":candidate")
            reason = request["reason"]
            if restore_from is not None:
                reason = reason + "\nContent restoration from exact revision: " + canonical_json(restore_from.to_dict()).decode()
            proposal = self.host.submit_proposal(self.session, self.project_ref, payload=document,
                base_revision_ref=base, expected_branch_ref=branch_ref, expected_head_ref=head,
                candidate_revision_ref=candidate.revision.revision_ref,
                previous_proposal_ref=None if "previous_proposal" not in request else
                    self._version(request["previous_proposal"], PROPOSAL),
                reason=reason, command_id=command,
                **self.host.delivered_proposal_inputs(self.session, delivery))
            return {"proposal": ref_payload(proposal), "revision": candidate.revision.revision_ref.to_dict(),
                    "delivery": ref_payload(delivery), "input_kind": "human_submitted_module" if is_human_input else "original_completed_TEXT"}
        self._authorize(owner=True)
        if op == "analyze":
            author = self._author()
            analysis = PlainModuleMergeAnalyzer(self.gateway, self.registration, author.producer).analyze(
                base_ref=self._qualified(request["base"]), local_ref=self._qualified(request["local"]),
                incoming_ref=self._qualified(request["incoming"]), command_id=command)
            return {"analysis": analysis.analysis_ref.to_dict(), "document": analysis.document}
        if op == "merge":
            author = self._author()
            analysis = SourceQualifiedResourceRef.from_dict(request["analysis"], catalog=self.core.catalog)
            merged = PlainModuleMergeAuthor(self.gateway, self.registration, author.producer).publish(
                analysis_ref=analysis, choices=request["choices"], command_id=command)
            return {"revision": merged.revision.to_dict(), "module": merged.module.to_dict()}
        branch_ref = self._qualified(request["branch"], "collaboration_branch/v1")
        head = self._qualified(request["head"])
        branch = read_branch_version(self.core, branch_ref)
        if branch.head_revision_ref != head:
            raise RegistryConflict("expected branch and head differ")
        if op == "accept":
            revision = self._qualified(request["revision"])
            decision = self.host.decide(self.session, self.project_ref, operation="advance",
                proposal_refs=tuple(self._version(r, PROPOSAL) for r in request["proposals"]),
                review_refs=tuple(self._version(r, "user_collaboration_review/v1") for r in request["reviews"]),
                result_revision_ref=revision, expected_branch_ref=branch_ref,
                expected_head_ref=head, command_id=command + ":decision")
            with self.host.command(self.host.action(self.session, decision), branch_command_key(command)):
                accepted = self.gateway.advance_author_branch(expected_branch_version_ref=branch_ref,
                    expected_head_revision_ref=head, expected_stream_head=branch.sequence,
                    next_revision_ref=revision, command_id=command)
            return {"branch": accepted.to_dict(), "decision": ref_payload(decision),
                    "receipt": ref_payload(self.host.receipt_ref(branch_command_key(command)))}
        if op == "adopt":
            from ..net_operations import prepare_replacement
            receipt_ref = self._version(request["receipt"], RECEIPT)
            receipt = self.host.read_fact(self.session, receipt_ref)
            if receipt["project_ref"] != project["self_ref"] or receipt["data"]["operation"] != "advance":
                raise RegistryConflict("adoption requires this project's exact acceptance receipt")
            revision = SourceQualifiedVersionRef(self.host._binding["source_id"],
                self._version(receipt["data"]["result_revision_ref"], "collaboration_net_revision/v1"))
            value = validate_closed_revision(self.core, revision, self.registration)
            accepted_decision = self.host.read_fact(self.session, self._version(receipt["data"]["decision_ref"], DECISION))
            prior = self.host._prior("decision", command + ":decision")
            if prior is not None:
                expected = prior["data"]
                if (expected["accepted_receipt_ref"] != ref_payload(receipt_ref)
                        or expected["expected_branch_ref"] != ref_payload(branch_ref.ref)
                        or expected["expected_head_ref"] != ref_payload(head.ref)):
                    raise RegistryConflict("adoption retry changed its exact request")
                existing_receipt = self.host._prior("receipt", command)
                if existing_receipt is not None:
                    submissions = [body for _, body in self.owner.control.records("candidate") if body["command_id"] == command]
                    if len(submissions) != 1:
                        raise RegistryConflict("adoption retry has no unique original owner submission")
                    original = submissions[0]["data"]
                    if any(canonical_json(original[key]) != canonical_json(request[key]) for key in
                           ("marking_mapping", "retire_token_refs", "owner_inputs")) or original["base_net_ref"] != request["runtime"]:
                        raise RegistryConflict("adoption retry changed its original runtime transfer plan")
                    return {"status": "ADOPTED", "replayed": True, "receipt": existing_receipt}
                raise RegistryConflict("adoption decision exists without completed receipt; inspect original owner pending edit before retry")
            plan = prepare_replacement(self.owner, value.module,
                marking_mapping=request["marking_mapping"],
                retire_token_refs=tuple(self._version(r, "petri_token/v1") for r in request["retire_token_refs"]),
                owner_inputs={k: tuple(v) for k, v in request["owner_inputs"].items()})
            if plan.base_net_ref != self._version(request["runtime"], "net_instance/v1"):
                raise RegistryConflict("adoption runtime expectation is stale")
            decision = self.host.decide(self.session, self.project_ref, operation="adopt",
                proposal_refs=tuple(self._version(r, PROPOSAL) for r in accepted_decision["data"]["proposal_refs"]),
                review_refs=tuple(self._version(r, "user_collaboration_review/v1") for r in accepted_decision["data"]["review_refs"]), result_revision_ref=revision,
                expected_branch_ref=branch_ref, expected_head_ref=head,
                expected_runtime_ref=plan.base_net_ref, accepted_receipt_ref=receipt_ref,
                command_id=command + ":decision")
            result = self.owner.control.edits.submit(plan.owner_command(), command,
                collaboration_action=self.host.action(self.session, decision))
            response = {"result": result, "decision": ref_payload(decision)}
            if self.host._prior("receipt", command) is not None:
                response["receipt"] = ref_payload(self.host.receipt_ref(command))
            return response
        raise ValueError("unsupported project command")


def run_console(endpoint, input_stream, output_stream):
    """JSON-lines console on an inherited HOST-bound stream, never a login."""
    while True:
        raw = input_stream.readline(MAX_REQUEST_BYTES + 2)
        if not raw:
            return
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            output_stream.write(encode_response({"ok": False, "error": "invalid_or_oversize_frame",
                "message": "Request must be a complete bounded JSON line; no command was dispatched."}))
            output_stream.flush()
            return  # Do not interpret a trailing chunk as a second command.
        try:
            result = endpoint.dispatch(parse_request(raw))
            response = {"ok": True, "result": result}
        except (ValueError, TypeError, KeyError, RegistryConflict, PermissionError) as exc:
            response = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        output_stream.write(encode_response(response))
        output_stream.flush()
