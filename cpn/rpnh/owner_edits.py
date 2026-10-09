"""Whole-declaration owner edits: register, pause admissions, drain, adopt.

The queue belongs to the sole execution owner. Immutable candidate, submission
and result resources are the command evidence; the cached publication is only
an address inventory. Submission checkpoint is provenance, never adoption CAS.
"""
from dataclasses import dataclass, replace

from .compiler import compile_module
from .module import ModuleDeclaration
from .registry.event_store import RegistryConflict
from .registry.identities import new_id
from .registry.models import VersionRef
from .registry.module_nets import publish_module_net, select_preserved_slot_refs
from .registry.module_operations import publish_module_operations
from .registry.module_runtime import hydrate_module_runtime
from .registry.owner_adoption import (
    OwnerAdoptionRequest, owner_command_document, owner_result_document,
    stage_owner_adoption,
)
from .registry.publication import _version_from_payload, _resource_from_payload
from .registry.resources import PrivateSystemOrigin, PublishResource
from .registry.schema_catalog import canonical_json
from .registry.strict_contracts import ref_payload


@dataclass
class PendingEdit:
    command_id: str
    candidate_record: object
    publication: object
    request: OwnerAdoptionRequest
    old_compiled: object
    compiled: object
    marking_mapping: dict
    retire_token_refs: tuple
    owner_inputs: dict
    status: str = "REGISTERED"


class OwnerEdits:
    def __init__(self, owner):
        self.owner = owner
        self.queue = []

    def retract(self, arguments, command_id):
        if (set(arguments) != {"edit_command_id"}
                or not isinstance(arguments["edit_command_id"], str) or not arguments["edit_command_id"]):
            raise ValueError("retract_edit requires one explicit edit_command_id")
        target = arguments["edit_command_id"]
        prior = [(ref, body) for ref, body in self.owner.control.records("command")
                 if body["command_id"] == command_id]
        data = {"command": "retract_edit", "edit_command_id": target}
        if prior and (len(prior) != 1 or prior[0][1]["data"] != data):
            raise ValueError("retraction command id was reused for different data")
        result = [(ref, body["data"]) for ref, body in self.owner.control.records("result")
                  if body["command_id"] == command_id]
        if result:
            ref, body = result[-1]
            return {**body, "result_ref": ref_payload(ref.as_version_ref())}
        edit = next((edit for edit in self.queue if edit.command_id == target), None)
        command = self.owner.control.publish("command", command_id, target, data,
            lineage=() if edit is None else (edit.request.owner_command_ref.as_version_ref(),))
        if edit is None:
            status = "NOT_PENDING"
        else:
            self.queue.remove(edit)
            self.result(edit, "RETRACTED", retraction_command_ref=ref_payload(command.as_version_ref()))
            status = "RETRACTED"
        self.owner.admission_paused = any(edit.status != "NEEDS_MARKING_DECISION" for edit in self.queue)
        body = {"status": status, "edit_command_id": target}
        ref = self.owner.control.publish("result", command_id, target, body,
            lineage=(command.as_version_ref(),), suffix="retraction")
        return {**body, "result_ref": ref_payload(ref.as_version_ref())}

    def correct(self, arguments, command_id):
        """A corrected full candidate is a new command, never an auto merge."""
        if set(arguments) != {"edit_command_id", "candidate", "base_net_ref", "marking_mapping",
                              "retire_token_refs", "owner_inputs"}:
            raise ValueError("correct_edit requires the retained candidate command id")
        target = arguments["edit_command_id"]
        if not isinstance(target, str) or not target:
            raise ValueError("correct_edit requires one explicit edit_command_id")
        candidate_arguments = {key: value for key, value in arguments.items() if key != "edit_command_id"}
        # The same JSON shapes accepted by submission must be checked while the
        # previous NEEDS candidate still exists, not after its retirement.
        if (not isinstance(candidate_arguments["marking_mapping"], dict)
                or not isinstance(candidate_arguments["retire_token_refs"], list)
                or not isinstance(candidate_arguments["owner_inputs"], dict)):
            raise TypeError("owner edit marking choices must be explicit JSON containers")
        for declarations in candidate_arguments["owner_inputs"].values():
            if not isinstance(declarations, list):
                raise ValueError("owner_inputs maps symbolic places to explicit input declaration arrays")
            for declaration in declarations:
                if (not isinstance(declaration, dict)
                        or set(declaration) != {"schema_id", "value", "summary"}
                        or not isinstance(declaration["schema_id"], str)
                        or not isinstance(declaration["summary"], str)):
                    raise ValueError("owner input requires schema_id, JSON value and summary")
        for ref in candidate_arguments["retire_token_refs"]:
            _version_from_payload(ref)
        prior_candidates = [body for _ref, body in self.owner.control.records("candidate")
                            if body["command_id"] == command_id]
        prior_commands = [body for _ref, body in self.owner.control.records("command")
                          if body["command_id"] == command_id]
        retractions = [body for _ref, body in self.owner.control.records("command")
                       if body["command_id"] == f"{command_id}:retract"]
        if prior_candidates:
            if (len(prior_candidates) != 1 or prior_candidates[0]["data"] != candidate_arguments
                    or len(retractions) != 1
                    or retractions[0]["data"] != {"command": "retract_edit", "edit_command_id": target}):
                raise ValueError("correction command id was reused for different immutable candidate data")
        elif prior_commands:
            if (len(prior_commands) != 1
                    or prior_commands[0]["data"] != {"command": "correct_edit", **arguments}):
                raise ValueError("correction command id was reused for different data")
        if prior_candidates or prior_commands:
            results = [(ref, body["data"]) for ref, body in self.owner.control.records("result")
                       if body["command_id"] == command_id]
            if results:
                ref, body = results[-1]
                if body["status"] == "ADOPTED":
                    return {"status": "ADOPTED", "net_ref": body["candidate_net_ref"],
                            "checkpoint_ref": body["checkpoint_ref"],
                            "result_ref": ref_payload(ref.as_version_ref()),
                            "transaction_id": self.owner._core.event_store.object_row(
                                ref.resource_version_id)["transaction_id"]}
                return {**body, "result_ref": ref_payload(ref.as_version_ref())}
        edit = next((edit for edit in self.queue if edit.command_id == target), None)
        if edit is None or edit.status != "NEEDS_MARKING_DECISION":
            raise ValueError("only a retained NEEDS_MARKING_DECISION candidate can be corrected")
        # Parse/lower and validate base before retracting the previous command.
        compile_module(ModuleDeclaration.from_dict(candidate_arguments["candidate"]), self.owner.registration)
        if candidate_arguments["base_net_ref"] != self.owner.snapshot()["net_ref"]:
            command = self.owner.control.publish("command", command_id, target,
                {"command": "correct_edit", **arguments},
                lineage=(edit.request.owner_command_ref.as_version_ref(),))
            body = {"status": "CONFLICT", "reason": "base NET changed; explicitly resubmit"}
            result = self.owner.control.publish("result", command_id, target, body,
                lineage=(command.as_version_ref(),), suffix="conflict")
            return {**body, "result_ref": ref_payload(result.as_version_ref())}
        self.retract({"edit_command_id": target}, f"{command_id}:retract")
        return self.submit(candidate_arguments, command_id)

    def result(self, edit, status, **details):
        edit.status = status
        body = {"status": status, "owner_command_ref": ref_payload(edit.request.owner_command_ref.as_version_ref()),
                "candidate_ref": ref_payload(edit.publication.declaration_resource_ref.as_version_ref()), **details}
        # Each observed status has its own immutable resource; corrections are
        # subsequent owner commands, not mutation of this candidate or result.
        resource = self.owner.control.publish("result", edit.command_id,
            str(edit.publication.net_ref.entity_id), body,
            lineage=(edit.request.owner_command_ref.as_version_ref(),
                     edit.publication.declaration_resource_ref.as_version_ref()),
            suffix=f"status:{status}:{len(self.owner.control.records('result'))}")
        return {**body, "result_ref": ref_payload(resource.as_version_ref())}

    def submit(self, arguments, command_id):
        required = {"candidate", "base_net_ref", "marking_mapping", "retire_token_refs", "owner_inputs"}
        if set(arguments) != required:
            raise ValueError("whole edit requires full candidate, base NET, mapping, retirement and owner inputs")
        if (not isinstance(arguments["marking_mapping"], dict)
                or not isinstance(arguments["retire_token_refs"], list)
                or not isinstance(arguments["owner_inputs"], dict)):
            raise TypeError("owner edit marking choices must be explicit JSON containers")
        owner = self.owner
        previous = [body for _ref, body in owner.control.records("candidate") if body["command_id"] == command_id]
        if previous:
            if len(previous) != 1 or previous[0]["data"] != arguments:
                raise ValueError("edit command id was reused for different immutable candidate data")
            results = [body["data"] for _ref, body in owner.control.records("result")
                       if body["command_id"] == command_id]
            return results[-1] if results else {"status": "REGISTERED"}
        module = ModuleDeclaration.from_dict(arguments["candidate"])
        compiled = compile_module(module, owner.registration)
        from .registry.schema_catalog import PROTECTED_SCHEMA_REFS
        for schema_id in compiled.source.required_schemas:
            if schema_id in PROTECTED_SCHEMA_REFS and schema_id not in owner.schema_gateway.schema_refs:
                owner.schema_gateway.bind_builtin_schema(schema_id)
        executable, structure, marking = hydrate_module_runtime(owner._core)
        base = _version_from_payload(arguments["base_net_ref"])
        if base.entity_type != "net_instance/v1":
            raise ValueError("edit base must be an exact registered net ref")
        candidate_record = owner.control.publish("candidate", command_id, None, arguments,
            lineage=(owner.original_input_ref.as_version_ref(),))
        if base != executable.net_ref:
            result = owner.control.publish("result", command_id, None,
                {"status": "CONFLICT", "reason": "base NET changed; explicitly resubmit",
                 "active_net_ref": ref_payload(executable.net_ref)},
                lineage=(candidate_record.as_version_ref(),), suffix="conflict")
            return {"status": "CONFLICT", "result_ref": ref_payload(result.as_version_ref())}
        inputs = {}
        for place, declarations in arguments["owner_inputs"].items():
            if not isinstance(declarations, list):
                raise ValueError("owner_inputs maps symbolic places to explicit input declaration arrays")
            inputs[place] = tuple(owner.control.publish_input(declaration,
                command_id=f"{command_id}:owner-input:{place}:{index}",
                derived_from=(owner.control.current_input(),))
                for index, declaration in enumerate(declarations))
        binding = owner._core.get_version(executable.transitions[0].operation_binding_ref.version_id).metadata
        authority = _version_from_payload(binding["authority_decision_ref"])
        operations = publish_module_operations(owner._core, compiled, owner.registration,
            owner.schema_gateway.schema_refs, idempotency_key=f"{command_id}:operations")
        old_resources = owner._core.get_version(executable.net_ref.version_id).metadata[
            "module_resource_bindings"]["owner_resource_inputs"]
        resources = {}
        for lease in compiled.symbolic.lease_identities:
            if lease.kind != "resource":
                continue
            if lease.name in old_resources:
                resources[lease.name] = _resource_from_payload(old_resources[lease.name])
            elif lease.name in inputs and len(inputs[lease.name]) == 1:
                resources[lease.name] = inputs[lease.name][0]
            else:
                raise ValueError("new resource lease requires one explicitly registered owner input by its symbolic key")
        place_inputs = {place: refs for place, refs in inputs.items()
                        if place in {item.name for item in compiled.symbolic.places}}
        if set(inputs) - set(place_inputs) - set(resources):
            raise ValueError("owner input key is neither a declared place nor resource lease")
        host_factory = {}
        if owner.host_execution_bindings is not None:
            if "rpnh/module_declaration/v1" not in owner.schema_gateway.schema_refs:
                owner.schema_gateway.bind_builtin_schema("rpnh/module_declaration/v1")
            authored = owner.control.publish_input({"schema_id": "rpnh/module_declaration/v1",
                "value": module.to_dict(), "summary": f"Authored candidate Module {module.name}"},
                command_id=f"{command_id}:authored-module", derived_from=(candidate_record,))
            def host_factory(plan):
                return owner.host_execution_bindings(core=owner._core, plan=plan,
                    source_ref=authored, bootstrap_ref=owner.bootstrap_ref, compiled=compiled)
        publication = publish_module_net(owner._core, compiled, owner.registration, identity=owner.identity,
            bootstrap_ref=owner.bootstrap_ref, principal_ref=owner.principal_ref,
            task_round_ref=owner.task_round_ref, authority_decision_ref=authority, entry_inputs={},
            schema_refs=owner.schema_gateway.schema_refs, operation_refs=operations,
            owner_resource_inputs=resources, owner_input_resources=tuple(r for bundle in inputs.values() for r in bundle),
            preserved_slot_refs=select_preserved_slot_refs(owner._core, structure.compiled, compiled),
            host_execution_bindings=host_factory,
            idempotency_key=f"{command_id}:candidate-net")
        checkpoint = VersionRef("marking_checkpoint/v1", new_id("marking_checkpoint"), new_id("marking_checkpoint_version"))
        checkpoint_body = owner._core.get_version(marking.checkpoint_ref.version_id).metadata
        workspaces = tuple(_version_from_payload(ref) for ref in checkpoint_body.get("workspace_revision_refs", []))
        request = OwnerAdoptionRequest(command_id, publication.declaration_resource_ref,
            publication.declaration_resource_ref, owner.principal_ref, authority, base, marking.checkpoint_ref,
            publication.net_ref, marking.checkpoint_ref, checkpoint, marking.epoch, marking.next_token_id,
            (), (), workspaces, (), ())
        document = owner_command_document(request)
        command = owner.control.publish("command", command_id, document["target"], document["data"],
            lineage=(publication.declaration_resource_ref.as_version_ref(),))
        request = replace(request, owner_command_ref=command)
        edit = PendingEdit(command_id, candidate_record, publication, request, structure.compiled, compiled,
            arguments["marking_mapping"], tuple(_version_from_payload(ref) for ref in arguments["retire_token_refs"]), place_inputs)
        self.queue.append(edit)
        owner.admission_paused = True
        self.result(edit, "REGISTERED")
        return self.advance()

    def advance(self):
        """Called on submission and after each ordinary operation completion."""
        if not self.queue:
            self.owner.admission_paused = False
            return None
        owner, edit = self.owner, self.queue[0]
        snapshot = owner.snapshot()
        if snapshot["net_ref"] != ref_payload(edit.request.base_net_ref):
            self.queue.pop(0)
            result = self.result(edit, "CONFLICT", reason="base NET changed; explicitly resubmit")
            owner.admission_paused = bool(self.queue)
            return result
        if snapshot["active_firings"]:
            return self.result(edit, "DRAINING", reason="active firings must settle normally; no interruption",
                active_firings=snapshot["active_firings"])
        _executable, _structure, marking = hydrate_module_runtime(owner._core)
        from .registry.owner_mapping import plan_owner_mapping, preview_owner_mapping, allocate_owner_mapping
        decision = plan_owner_mapping(owner._core, edit.old_compiled, edit.compiled, marking,
            edit.marking_mapping, edit.retire_token_refs, edit.owner_inputs)
        if decision.status != "READY":
            # The active graph is unchanged, candidate retained; admission may
            # continue while the owner supplies an explicit corrected command.
            owner.admission_paused = False
            return self.result(edit, "NEEDS_MARKING_DECISION", needs=decision.needs)
        preview = preview_owner_mapping(owner._core, edit.publication, marking, decision)
        from .pn_validation.runtime_gate import analyze_owner_preview
        pn_evidence = analyze_owner_preview(owner, preview)
        tx = owner._core.begin(idempotency_key=edit.command_id)
        allocated = allocate_owner_mapping(owner._core, edit.publication, marking, decision,
            command_id=edit.command_id, preview=preview, transaction=tx)
        checkpoint_body = owner._core.get_version(marking.checkpoint_ref.version_id).metadata
        workspaces = tuple(_version_from_payload(ref) for ref in checkpoint_body.get("workspace_revision_refs", []))
        request = replace(edit.request, predecessor_checkpoint_ref=marking.checkpoint_ref,
            epoch=marking.epoch, next_token_id=allocated.next_token_id, attempts=allocated.attempts,
            token_refs=allocated.token_refs, workspace_revision_refs=workspaces,
            token_mappings=allocated.token_mappings, ordinary_retirements=allocated.ordinary_retirements)
        if allocated.owner_input_mappings:
            request = replace(request, owner_input_mappings=allocated.owner_input_mappings)
        if pn_evidence is not None:
            request = replace(request, pn_validation_json=canonical_json(pn_evidence).decode())
        document = owner_result_document(request)
        result = PublishResource(origin=PrivateSystemOrigin(owner.bootstrap_ref),
            payload=canonical_json(document), media_type="application/json", content_schema_ref="rpnh/owner_control/v1",
            summary="Adopted owner graph edit", lifetime_ref=owner.bootstrap_ref,
            descriptors={"owner_control_kind": "result", "command_id": edit.command_id},
            derived_from=(request.owner_command_ref, request.candidate_ref), idempotency_key=edit.command_id)
        staged = stage_owner_adoption(owner._core, tx, request, result=result)
        tx.commit()
        self.queue.pop(0)
        owner.publication = edit.publication
        owner.admission_paused = bool(self.queue)
        return {"status": "ADOPTED", "net_ref": ref_payload(request.candidate_net_ref),
                "checkpoint_ref": ref_payload(request.checkpoint_ref),
                "result_ref": ref_payload(staged.owner_command_result_ref.as_version_ref()),
                "transaction_id": str(staged.transaction_id)}


__all__ = ("OwnerEdits",)
